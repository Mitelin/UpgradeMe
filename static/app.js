'use strict';
const viewCollator = new Intl.Collator('cs', {numeric: true, sensitivity: 'base'});
function parentFolder(path) {
  const sep = path.includes('\\') ? '\\' : '/';
  const parent = path.slice(0, path.lastIndexOf(sep));
  return /^[a-z]:$/i.test(parent) ? parent + sep : parent || sep;
}
function filterByRoots(files, selectedRoots) {
  if (selectedRoots === null) return files;
  const selected = new Set(selectedRoots);
  return files.filter(file => selected.has(file.root));
}
function reconcileRootSelection(selectedRoots, roots) {
  if (selectedRoots === null) return null;
  const available = new Set(roots.map(root => root.path));
  return selectedRoots.filter(path => available.has(path));
}
function isBelowThreshold(file, height, includeCropped = false) {
  return !file.error && file.height > 0 && file.width > 0 &&
    file.height < height && (includeCropped || file.width < (height === 720 ? 1280 : 1920));
}
function counted(count, one, few, many) {
  return count.toLocaleString('cs') + ' ' + (count === 1 ? one : count >= 2 && count <= 4 ? few : many);
}
function buildGroups(files) {
  const groups = new Map();
  for (const file of files) {
    const path = file.group || parentFolder(file.path);
    if (!groups.has(path)) groups.set(path, {
      path, root: file.root, name: path.split(/[\\/]/).filter(Boolean).pop() || path,
      files: [], size: 0, height: Infinity, width: Infinity, maxHeight: 0,
      maxWidth: 0, exceptionCount: 0, isSeries: false
    });
    const group = groups.get(path);
    group.files.push(file); group.size += file.size;
    if (file.height < group.height || (file.height === group.height && file.width < group.width)) {
      group.height = file.height; group.width = file.width;
    }
    group.maxHeight = Math.max(group.maxHeight, file.height);
    group.maxWidth = Math.max(group.maxWidth, file.width);
    group.exceptionCount += Number(!!file.exception); group.isSeries ||= !!file.group;
  }
  return [...groups.values()];
}
function exceptionRank(item) {
  if (!item.files) return item.exception ? 2 : 0;
  return item.exceptionCount === 0 ? 0 : item.exceptionCount === item.files.length ? 2 : 1;
}
function compareEntries(a, b, column = 'name', direction = 'asc') {
  let result = 0;
  if (column === 'quality') result = a.height - b.height || a.width - b.width;
  if (column === 'size') result = a.size - b.size;
  if (column === 'exception') result = exceptionRank(a) - exceptionRank(b);
  if (column === 'name') result = viewCollator.compare(a.name, b.name);
  return result * (direction === 'desc' ? -1 : 1) || viewCollator.compare(a.name, b.name) || viewCollator.compare(a.path, b.path);
}
function scanButtonLabel(active, pending = false) {
  if (active?.stopping) return 'Zastavování skenu…';
  if (active?.finishing) return 'Dokončování skenu…';
  if (active) return 'Sken probíhá…';
  return pending ? 'Spouštění skenu…' : '↻ Skenovat nyní';
}
if (typeof module !== 'undefined' && module.exports) module.exports = {parentFolder, buildGroups, compareEntries, filterByRoots, reconcileRootSelection, isBelowThreshold, counted, scanButtonLabel};
if (typeof document !== 'undefined') {
const $ = id => document.getElementById(id);
const collator = new Intl.Collator('cs', {numeric: true, sensitivity: 'base'});
let library = {files: [], exceptions: [], total: 0};
let status = null, page = 0, lastVersion = null, targets = [];
let folderState = null, folderRequest = 0, folderBusy = false;
let libraryLoadedAt = 0;
let libraryReady = false, libraryDirty = true, libraryRequest = 0, scanPending = false, stopPending = false;
let exceptionOpener = null;
let updatePending = false, updateWatching = false, loadedInstance = null;
let sortDirection = 'asc';
// null follows all current and future roots; [] deliberately shows none.
let selectedRoots = null, rootFilterVersion = null;
try {
  const savedRoots = JSON.parse(localStorage.getItem('upgrademe-selected-roots'));
  if (Array.isArray(savedRoots) && savedRoots.every(path => typeof path === 'string')) selectedRoots = savedRoots;
} catch (_) {}
const opened = new Set();
const pageSize = 60;

function node(tag, text, className) {
  const n = document.createElement(tag);
  if (text !== undefined) n.textContent = text;
  if (className) n.className = className;
  return n;
}
function button(text, action, className = 'quiet') {
  const b = node('button', text, className); b.type = 'button';
  b.addEventListener('click', async e => {
    e.preventDefault(); e.stopPropagation();
    if (b.disabled) return;
    try {
      const result = action();
      b.disabled = true;
      await result;
    } catch (error) { showError(error); }
    finally { b.disabled = false; }
  });
  return b;
}
function showError(error) { $('notice-text').textContent = error.message || String(error); $('notice').hidden = false; }
function feedback(message) { $('notice').hidden = true; $('feedback').textContent = message; }
async function api(path, data) {
  const res = await fetch('/api/' + path, data === undefined ? {} : {
    method: 'POST', headers: {'Content-Type': 'application/json', 'X-UpgradeMe': '1'}, body: JSON.stringify(data)
  });
  if (!res.headers.get('content-type')?.includes('application/json')) {
    throw new Error(`Server vrátil neočekávanou odpověď (HTTP ${res.status}). Zkuste to znovu.`);
  }
  const body = await res.json();
  if (!res.ok) throw new Error(body.error || 'Server není dostupný.');
  return body;
}
function bytes(n) {
  const units = ['B', 'KB', 'MB', 'GB', 'TB']; let i = 0;
  while (n >= 1024 && i < units.length - 1) { n /= 1024; i++; }
  return n.toLocaleString('cs', {maximumFractionDigits: i ? 1 : 0}) + ' ' + units[i];
}
function basename(path) { return path.split(/[\\/]/).filter(Boolean).pop() || path; }
function empty(target, title, description) {
  const box = node('div', undefined, 'empty');
  box.append(node('span', '◎', 'empty-symbol'), node('strong', title), node('p', description));
  target.append(box);
}
function belowThreshold(f) {
  return isBelowThreshold(f, Number($('quality').value), $('include-cropped').checked);
}
function filtered() {
  const query = $('search').value.trim().toLocaleLowerCase('cs');
  return filterByRoots(library.files, selectedRoots).filter(f => belowThreshold(f) &&
    (!$('hide-exceptions').checked || !f.exception) &&
    (!query || (f.path + ' ' + (f.exception?.note || '')).toLocaleLowerCase('cs').includes(query)));
}
function compare(a, b) {
  return compareEntries(a, b, $('sort').value, sortDirection);
}
function updateSortHeaders() {
  for (const control of document.querySelectorAll('[data-sort]')) {
    const selected = control.dataset.sort === $('sort').value;
    control.classList.toggle('active-sort', selected);
    control.querySelector('.sort-arrow').textContent = selected ? (sortDirection === 'asc' ? '↑' : '↓') : '↕';
    control.setAttribute('aria-label', control.dataset.label + (selected ? (sortDirection === 'asc' ? ', vzestupně. Seřadit sestupně' : ', sestupně. Seřadit vzestupně') : ', seřadit'));
  }
  $('sort-direction').textContent = sortDirection === 'asc' ? '↑' : '↓';
  $('sort-direction').setAttribute('aria-label', sortDirection === 'asc' ? 'Vzestupně. Změnit na sestupně' : 'Sestupně. Změnit na vzestupně');
}
function saveSort() {
  page = 0;
  try { localStorage.setItem('upgrademe-sort', $('sort').value); localStorage.setItem('upgrademe-sort-direction', sortDirection); } catch (_) {}
  render();
}
function row(f) {
  const r = node('div', undefined, 'file-row' + (f.exception ? ' excluded' : ''));
  const main = node('div', undefined, 'file-main'), text = node('div');
  text.append(node('span', f.name, 'file-name'), node('p', f.path, 'path'));
  if (f.exception) text.append(node('p', (f.exception.scope === 'folder' ? 'Výjimka složky' : 'Výjimka souboru') + ': ' + (f.exception.note || 'Bez poznámky'), 'exception-note'));
  main.append(node('span', '▻', 'file-icon'), text);
  const edit = button(f.exception ? 'Upravit výjimku' : '+ Výjimka', () => editException(f));
  edit.dataset.focusKey = 'file:' + f.path;
  edit.setAttribute('aria-label', (f.exception ? 'Upravit výjimku: ' : 'Přidat výjimku: ') + f.name);
  r.append(main, node('span', f.width + ' × ' + f.height, 'resolution'), node('span', bytes(f.size), 'size'), edit);
  return r;
}
function render() {
  if (!libraryReady) return;
  const focusKey = document.activeElement?.dataset.focusKey;
  const allLow = filterByRoots(library.files, selectedRoots).filter(belowThreshold);
  const threshold = Number($('quality').value);
  $('list-title').textContent = threshold === 720 ? 'SD videa · pod HD' : 'Videa pod Full HD';
  $('quality-label').textContent = threshold === 720 ? 'SD VIDEA K UPGRADU' : 'VIDEA POD FULL HD';
  $('quality-rule').textContent = `Výška pod ${threshold} px` + ($('include-cropped').checked ? '' : ` a šířka pod ${threshold === 720 ? 1280 : 1920} px`);
  const files = filtered();
  $('count-low').textContent = allLow.filter(f => !f.exception).length.toLocaleString('cs');
  $('count-visible').textContent = counted(files.length, 'video', 'videa', 'videí') + ' v aktuálním pohledu';
  $('count-excluded').textContent = allLow.filter(f => f.exception).length.toLocaleString('cs');
  $('count-total').textContent = library.total.toLocaleString('cs');
  $('result-count').textContent = files.length.toLocaleString('cs');
  const entries = buildGroups(files);
  entries.sort(compare);
  const pages = Math.max(1, Math.ceil(entries.length / pageSize)); page = Math.min(page, pages - 1);
  const results = $('results'); results.replaceChildren();
  $('reset-filters').disabled = !$('search').value && $('quality').value === '720' &&
    $('hide-exceptions').checked && !$('include-cropped').checked && selectedRoots === null;
  if (!files.length) {
    if (!status?.roots.length) {
      empty(results, 'Začněte přidáním složky', 'Vyberte knihovnu na serveru. Vaše média zůstanou beze změny.');
      results.lastElementChild.append(button('+ Přidat složku', openFolderDialog, 'primary'));
    }
    else if (selectedRoots?.length === 0) empty(results, 'Není vybraná žádná složka', 'Zaškrtněte složky nad seznamem nebo zvolte Všechny.');
    else if (!library.total) {
      empty(results, status.active ? 'Probíhá první sken' : 'Zatím tu nejsou žádná videa',
        status.active ? 'Výsledky se zde budou průběžně objevovat. Stránku není potřeba obnovovat.' : 'Spusťte sken a zkontrolujte dostupnost sledovaných složek.');
      if (!status.active) {
        const scan = button('Skenovat nyní', () => startScan(), 'primary');
        scan.disabled = scanPending || !status.ffprobe_available;
        results.lastElementChild.append(scan);
      }
    }
    else empty(results, 'Tento pohled je prázdný', 'Žádná ověřená videa neodpovídají filtrům. Můžete zobrazit výjimky nebo změnit hledání.');
  }
  for (const e of entries.slice(page * pageSize, (page + 1) * pageSize)) {
    const details = node('details', undefined, 'series'); details.open = opened.has(e.path); details.dataset.path = e.path;
    const summary = node('summary', undefined, 'group-row');
    summary.dataset.focusKey = 'group:' + e.path;
    const title = node('div', undefined, 'group-title'), names = node('div');
    names.append(node('span', e.name, 'file-name'), node('p', e.path, 'path'));
    const count = e.files.length;
    const noun = e.isSeries ? (count === 1 ? 'díl' : count < 5 ? 'díly' : 'dílů') : (count === 1 ? 'soubor' : count < 5 ? 'soubory' : 'souborů');
    names.append(node('span', (e.isSeries ? 'Seriál' : 'Filmy') + ' · ' + count + ' ' + noun, 'group-meta'));
    title.append(node('span', '›', 'chevron'), names);
    const resolution = node('span', e.height === e.maxHeight && e.width === e.maxWidth ? `${e.width} × ${e.height}` : e.height === e.maxHeight ? `${e.height}p · různé` : `${e.height}–${e.maxHeight}p`, 'resolution');
    resolution.title = 'Řazení podle nejnižšího rozlišení ve složce.';
    const exceptionCell = node('div', undefined, 'exception-cell');
    const edit = button('Výjimka složky', () => editFolder(e.path, e.root));
    edit.dataset.focusKey = 'folder:' + e.path;
    edit.setAttribute('aria-label', 'Výjimka složky: ' + e.path);
    exceptionCell.append(node('span', e.exceptionCount ? `Výjimka ${e.exceptionCount}/${e.files.length}` : 'Bez výjimky', 'exception-status' + (e.exceptionCount ? ' has-exception' : '')), edit);
    summary.append(title, resolution, node('span', bytes(e.size), 'size'), exceptionCell);
    details.append(summary);
    e.files.sort(compare).forEach(f => details.append(row(f)));
    details.addEventListener('toggle', () => {
      if (!details.isConnected) return;
      details.open ? opened.add(e.path) : opened.delete(e.path);
      updateExpandButton();
    });
    results.append(details);
  }
  $('page-info').textContent = `Strana ${page + 1} / ${pages} · ` + counted(entries.length, 'složka', 'složky', 'složek');
  $('pagination').hidden = pages === 1;
  $('previous').disabled = page === 0; $('following').disabled = page >= pages - 1;
  updateSortHeaders(); updateExpandButton(); renderExceptions(); renderErrors();
  if (focusKey) {
    const replacement = [...document.querySelectorAll('[data-focus-key]')].find(el => el.dataset.focusKey === focusKey);
    (replacement || results).focus({preventScroll: true});
  }
}
function updateExpandButton() {
  const series = [...document.querySelectorAll('.series')];
  $('expand').disabled = !series.length;
  $('expand').textContent = series.length && series.every(d => d.open) ? 'Sbalit složky' : 'Rozbalit složky';
}
function renderExceptions() {
  $('exception-count').textContent = library.exceptions.length;
  const el = $('exceptions'); el.replaceChildren();
  if (!library.exceptions.length) el.append(node('p', 'Zatím žádné výjimky.', 'hint'));
  for (const ex of library.exceptions) {
    const r = node('div', undefined, 'record'), text = node('div');
    text.append(node('strong', ex.scope === 'folder' ? 'Celá složka' : 'Soubor'), node('p', ex.path, 'path'), node('p', ex.note || 'Bez poznámky', 'exception-note'));
    const edit = button('Upravit', () => openDialog([{path: ex.path, scope: ex.scope}], ex));
    edit.dataset.focusKey = 'exception:' + ex.scope + ':' + ex.path;
    edit.setAttribute('aria-label', 'Upravit výjimku: ' + ex.path);
    r.append(text, edit);
    el.append(r);
  }
}
function renderErrors() {
  const files = filterByRoots(library.files, selectedRoots).filter(f => f.error), warnings = status?.last?.result?.warnings || [];
  $('errors-panel').hidden = !files.length && !warnings.length;
  $('error-count').textContent = files.length + warnings.length;
  const el = $('errors'); el.replaceChildren();
  for (const warning of warnings) el.append(node('p', warning, 'hint'));
  for (const f of files) {
    const r = node('div', undefined, 'record'), text = node('div');
    text.append(node('strong', f.name), node('p', f.path, 'path'), node('p', f.error, 'hint')); r.append(text); el.append(r);
  }
}
function parentPaths(path, root, includeSelf = false) {
  const paths = [], sep = path.includes('\\') ? '\\' : '/';
  let p = includeSelf ? path : parentFolder(path);
  if (!p && root === '/') p = '/';
  while (p && p.length >= root.length) {
    paths.push(p); if (p === root) break;
    const next = p.slice(0, p.lastIndexOf(sep));
    p = next || (root === '/' ? '/' : '');
    if (p === root.slice(0, -1) && root.endsWith(sep)) p = root;
  }
  return paths;
}
function editException(f) {
  const options = [{path: f.path, scope: 'file'}, ...parentPaths(f.path, f.root).map(path => ({path, scope: 'folder'}))];
  openDialog(options, f.exception);
}
function editFolder(path, root) {
  const options = parentPaths(path, root, true).map(path => ({path, scope: 'folder'}));
  openDialog(options, library.exceptions.find(e => e.path === path && e.scope === 'folder'));
}
function openDialog(options, existing) {
  exceptionOpener = document.activeElement?.dataset.focusKey;
  $('exception-error').hidden = true;
  targets = options; const select = $('exception-target'); select.replaceChildren();
  options.forEach((t, i) => { const o = node('option', (t.scope === 'file' ? 'Tento soubor: ' : 'Celá složka: ') + basename(t.path)); o.value = i; select.append(o); });
  select.value = Math.max(0, options.findIndex(t => t.path === existing?.path && t.scope === existing?.scope));
  targetChanged(); $('exception-dialog').showModal();
}
function targetChanged() {
  const target = targets[Number($('exception-target').value)];
  const existing = library.exceptions.find(e => e.path === target.path && e.scope === target.scope);
  $('exception-path').textContent = target.path;
  $('exception-note').value = existing?.note || '';
  $('delete-exception').hidden = !existing;
  $('dialog-title').textContent = existing ? 'Upravit výjimku' : 'Přidat výjimku';
}
async function loadLibrary() {
  const request = ++libraryRequest;
  libraryDirty = true;
  const result = await api('library');
  if (request !== libraryRequest) return;
  library = result; libraryReady = true; libraryDirty = false; libraryLoadedAt = Date.now(); render();
}
function saveRootSelection() {
  try { localStorage.setItem('upgrademe-selected-roots', JSON.stringify(selectedRoots)); } catch (_) {}
}
function updateRootChecks() {
  const roots = status?.roots || [];
  const count = selectedRoots === null ? roots.length : selectedRoots.length;
  $('root-filter-all').checked = roots.length > 0 && count === roots.length;
  $('root-filter-all').indeterminate = count > 0 && count < roots.length;
  $('root-filter-all').disabled = !roots.length;
  for (const input of $('root-filter-options').querySelectorAll('input')) {
    input.checked = selectedRoots === null || selectedRoots.includes(input.value);
  }
  $('root-filter-summary').textContent = !roots.length ? 'Zatím žádné sledované složky' :
    count === roots.length ? 'Všechny sledované složky' : `Vybráno ${count} z ${roots.length}`;
}
function renderRootFilter(roots) {
  const version = JSON.stringify(roots.map(root => root.path));
  if (version !== rootFilterVersion) {
    rootFilterVersion = version;
    selectedRoots = reconcileRootSelection(selectedRoots, roots); saveRootSelection();
    const options = $('root-filter-options'); options.replaceChildren();
    for (const root of roots) {
      const label = node('label', undefined, 'root-filter-chip'); label.title = root.path;
      const input = node('input'); input.type = 'checkbox'; input.value = root.path;
      const duplicateName = roots.filter(other => basename(other.path) === basename(root.path)).length > 1;
      label.append(input, node('span', duplicateName ? root.path : basename(root.path)));
      input.addEventListener('change', () => {
        selectedRoots = [...options.querySelectorAll('input:checked')].map(control => control.value);
        if (selectedRoots.length === roots.length) selectedRoots = null;
        page = 0; saveRootSelection(); updateRootChecks(); render();
      });
      options.append(label);
    }
  }
  updateRootChecks();
}
function renderStatus(s) {
  status = s;
  renderRootFilter(s.roots);
  $('count-roots').textContent = counted(s.roots.length, 'sledovaná složka', 'sledované složky', 'sledovaných složek');
  const afterMaintenance = s.schedule_mode === 'maintenance';
  $('next-time').textContent = afterMaintenance ? 'Po údržbě' : '05:00';
  $('next-time').classList.toggle('after-maintenance', afterMaintenance);
  $('next').textContent = afterMaintenance ? 'Po Media Maintenance, i po běhu s chybou' :
    s.next_scan.slice(0, 10).split('-').reverse().join('.') + ' · čas serveru (' + s.next_scan.slice(-6) + ')';
  $('schedule-title').textContent = afterMaintenance ? 'Po dokončení Media Maintenance' : 'Každý den v 5:00';
  $('schedule-detail').textContent = afterMaintenance ?
    'Automatický i ruční sken počká na uvolnění společného zámku údržby. Na samostatný Jellyfin refresh se nečeká.' :
    'Po pozdějším startu aplikace se zmeškaný denní sken doplní.';
  const updating = updatePending || !!s.update?.busy;
  $('scan').disabled = $('retry').disabled = updating || scanPending || !!s.active || !s.roots.length || !s.ffprobe_available;
  $('scan').textContent = scanButtonLabel(s.active, scanPending);
  $('stop-scan').disabled = stopPending || !s.active || !!s.active.stopping || !!s.active.finishing;
  $('stop-scan').textContent = stopPending || s.active?.stopping ? 'Zastavování…' : 'Zastavit sken';
  $('stop-scan').title = s.active ? 'Zastaví pouze sken UpgradeMe. Údržba a web zůstanou běžet.' : 'Právě neprobíhá žádný sken.';
  $('scan').title = !s.roots.length ? 'Nejprve přidejte sledovanou složku.' : !s.ffprobe_available ? 'Na serveru chybí ffprobe.' : '';
  $('update-app').disabled = updating || scanPending || !!s.active || !s.update?.enabled;
  $('update-app').textContent = updating ? 'Aktualizace probíhá…' : 'Aktualizovat aplikaci';
  $('update-app').title = s.active ? 'Nejprve dokončete nebo zastavte sken.' :
    !s.update?.enabled ? 'Aktualizační služba není na serveru nastavená.' : 'Stáhne main z Gitu a restartuje UpgradeMe.';
  const updateLabels = {fetching: 'Stahování aktualizace…', waiting: 'Čekání na dokončení Media Maintenance…',
    installing: 'Zálohování a instalace…', restarting: 'Restart aplikace…',
    complete: 'Aplikace byla aktualizována a restartována.', failed: 'Aktualizace se nezdařila.'};
  const result = s.update?.result;
  $('update-detail').textContent = updating && ['complete', 'failed', undefined].includes(result?.status) ?
    'Čekání na aktualizační službu…' : (updateLabels[result?.status] || '') +
    (result?.error ? ' ' + result.error : '') + (result?.revision ? ' Verze: ' + result.revision.slice(0, 7) : '');
  $('update-detail').className = result?.status === 'failed' && !updating ? 'inline-error' : 'hint';
  loadedInstance ??= s.instance_id;
  if (s.update?.busy) updateWatching = true;
  if (updateWatching && !updating && result?.status === 'complete' && loadedInstance !== s.instance_id) {
    location.reload();
  }
  const labels = {complete: 'Sken dokončen', warning: 'Sken dokončen s upozorněním', failed: 'Sken se nezdařil', interrupted: 'Předchozí sken byl přerušen', cancelled: 'Sken zastaven uživatelem', running: 'Sken probíhá'};
  const current = s.active, last = s.last?.result;
  $('status-title').textContent = current ? 'Sken probíhá · ' + current.phase : s.last ? (labels[s.last.status] || s.last.status) : 'Připraveno na první sken';
  $('status-dot').className = 'dot' + (current ? ' busy' : ['warning', 'failed', 'interrupted'].includes(s.last?.status) ? ' warning' : '');
  $('current').hidden = !current?.current; $('current').textContent = current?.current || '';
  const counts = current || last;
  $('status-detail').textContent = counts ? `${counts.seen} nalezeno · ${counts.probed} analyzováno · ${counts.cached} z cache · ${counts.deferred} odloženo · ${counts.errors} chyb` : 'Při opakovaném skenu se analyzují jen nové a změněné soubory.';
  if (!s.ffprobe_available) $('status-detail').textContent = 'Na serveru chybí ffprobe. Nainstalujte FFmpeg nebo nastavte cestu při spuštění.';
  $('scan-time').textContent = s.last?.finished ? new Date(s.last.finished).toLocaleString('cs') + (last ? ` · ${last.seconds} s` : '') : '';
  const rootList = $('roots'), rootVersion = JSON.stringify(s.roots) + !!s.active;
  if (rootList.dataset.version !== rootVersion) {
    rootList.dataset.version = rootVersion; rootList.replaceChildren();
    if (!s.roots.length) rootList.append(node('p', 'Zatím nesledujete žádnou složku.', 'hint'));
    for (const root of s.roots) {
      const r = node('div', undefined, 'root-row'), text = node('div');
      text.append(node('span', ({auto: 'AUTOMATICKY', movie: 'FILMY', series: 'SERIÁLY / ANIME'})[root.kind], 'root-kind'), node('p', root.path, 'path'));
      const remove = button('Odebrat', async () => {
        if (!confirm(`Odebrat sledovanou složku?\n\n${root.path}\n\nSmaže se pouze její cache. Média ani uložené výjimky se nesmažou.`)) return;
        await api('roots/remove', {id: root.id});
        libraryDirty = true;
        feedback('Sledovaná složka byla odebrána. Média a výjimky zůstaly zachované.');
        await refresh(true);
      }); remove.disabled = !!s.active;
      remove.setAttribute('aria-label', 'Odebrat sledovanou složku: ' + root.path);
      r.append(text, remove); rootList.append(r);
    }
  }
  $('add-folder').disabled = !!s.active;
  updateFolderSelection();
}
function updateFolderSelection() {
  const saving = !!$('folder-dialog').dataset.busy;
  $('select-folder').disabled = saving || folderBusy || !folderState?.selectable || !!status?.active;
  $('folder-up').disabled = saving || folderBusy || !folderState?.path;
}
function folderError(error) {
  $('folder-error').textContent = error.message || String(error);
  $('folder-error').hidden = false;
}
async function browseFolder(path = null) {
  const request = ++folderRequest;
  folderBusy = true; folderState = null; updateFolderSelection();
  $('folder-error').hidden = true;
  $('folder-selected').textContent = 'Načítání…'; $('folder-reason').textContent = '';
  $('folder-search').value = ''; $('folder-list').replaceChildren(node('p', 'Načítání složek…', 'hint'));
  try {
    const result = await api('folders/list', {path});
    if (request !== folderRequest || !$('folder-dialog').open) return;
    folderState = result;
    $('folder-selected').textContent = result.path || 'Vyberte umístění vlevo';
    $('folder-reason').textContent = result.reason;
    $('folder-address').value = result.path || '';
    const locations = $('folder-locations'); locations.replaceChildren();
    locations.append(button('⌂ Všechna umístění', () => browseFolder()));
    for (const location of result.locations) locations.append(button(location.name, () => browseFolder(location.path)));
    const crumbs = $('folder-breadcrumbs'); crumbs.replaceChildren();
    for (const crumb of result.breadcrumbs) crumbs.append(button(crumb.name, () => browseFolder(crumb.path)));
    if (!result.path) crumbs.append(node('span', 'Disky a umístění', 'hint'));
    renderFolders();
    $('folder-search').focus({preventScroll: true});
  } catch (error) {
    if (request !== folderRequest || !$('folder-dialog').open) return;
    $('folder-selected').textContent = 'Složka není dostupná';
    $('folder-list').replaceChildren(); folderError(error);
    $('folder-search').focus({preventScroll: true});
  } finally {
    if (request === folderRequest) { folderBusy = false; updateFolderSelection(); }
  }
}
function renderFolders() {
  const list = $('folder-list'); list.replaceChildren();
  if (!folderState) return;
  const search = $('folder-search').value.trim().toLocaleLowerCase('cs');
  const folders = (folderState.path ? folderState.folders : folderState.locations)
    .filter(f => f.name.toLocaleLowerCase('cs').includes(search)).sort((a, b) => collator.compare(a.name, b.name));
  for (const f of folders) {
    const b = button('▱  ' + f.name + '  ›', () => browseFolder(f.path), 'folder-item');
    b.title = f.path; list.append(b);
  }
  if (!folders.length) list.append(node('p', search ? 'Žádná složka neodpovídá hledání.' : 'Žádné podsložky. Tuto složku můžete přidat tlačítkem dole.', 'hint'));
}
async function refresh(force = false) {
  const s = await api('status'); renderStatus(s);
  const version = JSON.stringify([s.last?.id, s.last?.finished, s.roots]);
  if (force || libraryDirty || version !== lastVersion || (s.active && Date.now() - libraryLoadedAt >= 5000)) {
    await loadLibrary(); lastVersion = version;
  }
}
async function startScan(retry_errors = false) {
  if (!status || scanPending || updatePending || status.update?.busy || status.active || !status.roots.length || !status.ffprobe_available) return;
  scanPending = true; renderStatus(status);
  try { await api('scan', {retry_errors}); feedback('Sken byl spuštěn. Výsledky se aktualizují automaticky.'); await refresh(true); }
  finally { scanPending = false; renderStatus(status); }
}
$('scan').addEventListener('click', () => startScan().catch(showError));
async function stopScan() {
  if (stopPending || !status?.active || status.active.stopping || status.active.finishing) return;
  stopPending = true; renderStatus(status);
  try {
    await api('scan/stop', {});
    feedback('Požadavek na zastavení byl přijat. Průběžné výsledky zůstanou uložené.');
    await refresh(true);
  } finally { stopPending = false; renderStatus(status); }
}
$('stop-scan').addEventListener('click', () => stopScan().catch(showError));
async function updateApp() {
  if (!status?.update?.enabled || status.active || scanPending || updatePending || status.update.busy) return;
  if (!confirm('Stáhnout aktuální main z Gitu a restartovat UpgradeMe? Nastavení a výsledky zůstanou zachované.')) return;
  updatePending = true; renderStatus(status);
  try {
    await api('update', {});
    updateWatching = true;
    feedback('Aktualizace byla vyžádána. Při restartu se spojení krátce přeruší; stránka se pak obnoví.');
    await refresh();
  } finally { updatePending = false; renderStatus(status); }
}
$('update-app').addEventListener('click', () => updateApp().catch(showError));
$('retry').addEventListener('click', () => startScan(true).catch(showError));
function openFolderDialog() {
  $('root-kind').value = 'auto'; $('folder-dialog').showModal(); browseFolder();
}
$('add-folder').addEventListener('click', openFolderDialog);
$('dismiss-notice').addEventListener('click', () => { $('notice').hidden = true; });
for (const id of ['close-folder', 'cancel-folder']) $(id).addEventListener('click', () => $('folder-dialog').close());
$('folder-dialog').addEventListener('close', () => { folderRequest++; folderBusy = false; });
$('folder-search').addEventListener('input', renderFolders);
$('folder-up').addEventListener('click', () => browseFolder(folderState?.parent || null));
$('folder-address-form').addEventListener('submit', e => { e.preventDefault(); browseFolder($('folder-address').value); });
async function mutateDialog(dialogId, errorId, route, data, message) {
  const dialog = $(dialogId);
  if (dialog.dataset.busy) return;
  dialog.dataset.busy = 'true'; dialog.setAttribute('aria-busy', 'true'); $(errorId).hidden = true;
  const controls = [...dialog.querySelectorAll('button, input, select, textarea')].filter(el => !el.disabled);
  const focused = document.activeElement;
  controls.forEach(el => { el.disabled = true; });
  try { await api(route, data); }
  catch (error) {
    $(errorId).textContent = error.message; $(errorId).hidden = false;
    return;
  } finally {
    controls.forEach(el => { el.disabled = false; });
    delete dialog.dataset.busy; dialog.removeAttribute('aria-busy');
    if (dialogId === 'folder-dialog') updateFolderSelection();
    if (dialog.contains(focused)) focused.focus({preventScroll: true});
  }
  libraryDirty = true;
  dialog.close(); feedback(message);
  try { await refresh(true); }
  catch (error) { showError(new Error('Změna je uložená, ale přehled se nepodařilo obnovit: ' + error.message)); }
}
for (const id of ['folder-dialog', 'exception-dialog']) {
  $(id).addEventListener('cancel', e => { if ($(id).dataset.busy) e.preventDefault(); });
}
$('select-folder').addEventListener('click', () => {
  if (folderBusy || !folderState?.selectable) return;
  mutateDialog('folder-dialog', 'folder-error', 'roots/add', {path: folderState.path, kind: $('root-kind').value},
    'Složka byla přidána. Spusťte sken pro načtení jejích videí.').catch(showError);
});
$('exception-target').addEventListener('change', targetChanged);
$('close-dialog').addEventListener('click', () => $('exception-dialog').close());
$('exception-dialog').addEventListener('close', () => {
  const opener = [...document.querySelectorAll('[data-focus-key]')].find(el => el.dataset.focusKey === exceptionOpener);
  (opener || $('library-heading')).focus({preventScroll: true});
});
$('exception-form').addEventListener('submit', e => {
  e.preventDefault();
  mutateDialog('exception-dialog', 'exception-error', 'exceptions/save',
    {...targets[Number($('exception-target').value)], note: $('exception-note').value}, 'Výjimka byla uložena.').catch(showError);
});
$('delete-exception').addEventListener('click', () => {
  mutateDialog('exception-dialog', 'exception-error', 'exceptions/delete',
    targets[Number($('exception-target').value)], 'Výjimka byla zrušena. Případné nadřazené výjimky zůstávají platné.').catch(showError);
});
try {
  const storedSort = localStorage.getItem('upgrademe-sort');
  if (['name', 'quality', 'size', 'exception'].includes(storedSort)) $('sort').value = storedSort;
  const storedDirection = localStorage.getItem('upgrademe-sort-direction');
  sortDirection = ['asc', 'desc'].includes(storedDirection) ? storedDirection : $('sort').value === 'size' ? 'desc' : 'asc';
} catch (_) {}
$('sort').addEventListener('change', () => { sortDirection = $('sort').value === 'size' ? 'desc' : 'asc'; saveSort(); });
$('root-filter-all').addEventListener('change', () => {
  selectedRoots = $('root-filter-all').checked ? null : [];
  page = 0; saveRootSelection(); updateRootChecks(); render();
});
$('sort-direction').addEventListener('click', () => { sortDirection = sortDirection === 'asc' ? 'desc' : 'asc'; saveSort(); });
for (const control of document.querySelectorAll('[data-sort]')) control.addEventListener('click', () => {
  if ($('sort').value === control.dataset.sort) sortDirection = sortDirection === 'asc' ? 'desc' : 'asc';
  else { $('sort').value = control.dataset.sort; sortDirection = control.dataset.sort === 'size' ? 'desc' : 'asc'; }
  saveSort();
});
for (const id of ['search', 'quality', 'hide-exceptions', 'include-cropped']) {
  const el = $(id);
  try { const saved = localStorage.getItem('upgrademe-' + id); if (saved !== null && (id !== 'quality' || ['720', '1080'].includes(saved))) el.type === 'checkbox' ? el.checked = saved === 'true' : el.value = saved; } catch (_) { /* Optional browser preference storage. */ }
  el.addEventListener('input', () => { page = 0; try { localStorage.setItem('upgrademe-' + id, el.type === 'checkbox' ? el.checked : el.value); } catch (_) {} render(); });
}
$('reset-filters').addEventListener('click', () => {
  $('search').value = ''; $('quality').value = '720'; $('hide-exceptions').checked = true; $('include-cropped').checked = false;
  selectedRoots = null; page = 0; saveRootSelection(); updateRootChecks();
  for (const id of ['search', 'quality', 'hide-exceptions', 'include-cropped']) {
    try { localStorage.removeItem('upgrademe-' + id); } catch (_) {}
  }
  render(); $('search').focus();
});
function changePage(delta) {
  page += delta; render();
  $('library-heading').scrollIntoView({block: 'start'}); $('library-heading').focus({preventScroll: true});
}
$('previous').addEventListener('click', () => changePage(-1));
$('following').addEventListener('click', () => changePage(1));
$('expand').addEventListener('click', () => {
  const series = [...document.querySelectorAll('.series')], expand = series.some(d => !d.open);
  for (const details of series) {
    details.open = expand;
    expand ? opened.add(details.dataset.path) : opened.delete(details.dataset.path);
  }
  updateExpandButton();
});
async function poll() {
  try { await refresh(); $('connection-error').hidden = true; }
  catch (error) {
    $('connection-error').textContent = 'Spojení se serverem se nezdařilo. ' +
      (libraryReady ? 'Zobrazené výsledky mohou být zastaralé. ' : 'Přehled zatím není načtený. ') +
      'Zkusíme to automaticky znovu. ' + error.message;
    $('connection-error').hidden = false;
  }
  setTimeout(poll, 3000);
}
poll();
}
