const {test} = require('node:test');
const assert = require('node:assert/strict');
const {parentFolder, buildGroups, compareEntries, filterByRoots, reconcileRootSelection, isBelowThreshold, counted, scanButtonLabel, folderSearchUrl} = require('../static/app.js');
test('folder search matches the requested site and encodes the whole name as one query', () => {
  assert.equal(folderSearchUrl('aa'), 'https://sktorrent.eu/torrent/torrents_v2.php?search=aa');
  for (const name of ['Film (2024)', 'Žluťoučký kůň', 'A & B + C #1?', 'https://example.invalid/?x=1&search=other']) {
    const url = new URL(folderSearchUrl(name));
    assert.equal(url.origin, 'https://sktorrent.eu');
    assert.equal(url.pathname, '/torrent/torrents_v2.php');
    assert.equal(url.searchParams.get('search'), name);
    assert.equal(url.searchParams.size, 1);
    assert.equal(url.hash, '');
  }
});
test('folder search uses the displayed movie or series name, not a server path or episode', () => {
  const groups = buildGroups([
    file('/media/Film (2024)/video.mkv'),
    file('/media/Show/Season 1/S01E01.mkv', 576, 100, null, '/media/Show')
  ]);
  assert.deepEqual(groups.map(group => new URL(folderSearchUrl(group.name)).searchParams.get('search')),
    ['Film (2024)', 'Show']);
});
test('scan label follows active scans, including scheduled runs and stopping', () => {
  assert.equal(scanButtonLabel(null), '↻ Skenovat nyní');
  assert.equal(scanButtonLabel(null, true), 'Spouštění skenu…');
  assert.equal(scanButtonLabel({phase: 'scan'}), 'Sken probíhá…');
  assert.equal(scanButtonLabel({phase: 'waiting'}, true), 'Sken probíhá…');
  assert.equal(scanButtonLabel({stopping: true}), 'Zastavování skenu…');
  assert.equal(scanButtonLabel({finishing: true}), 'Dokončování skenu…');
});
function file(path, height=576, size=100, exception=null, group=null, width=720) {
  return {path, name:path.split('/').pop(), root:'/media', height, width, size, exception, group};
}
function sorted(items, column, direction) { return [...items].sort((a,b)=>compareEntries(a,b,column,direction)); }

test('root filter supports all, none, one and multiple exact roots',()=>{
  const rows = ['/media/filmy','/media/serialy','/media/filmy extra'].map((root,i)=>({...file(root+'/video.mkv'),root,size:i+1}));
  assert.equal(filterByRoots(rows,null).length,3);
  assert.deepEqual(filterByRoots(rows,[]),[]);
  assert.deepEqual(filterByRoots(rows,['/media/filmy']),[rows[0]]);
  assert.deepEqual(filterByRoots(rows,['/media/serialy','/media/filmy']),[rows[0],rows[1]]);
  assert.equal(rows.length,3);
});
test('root selection survives library updates and does not silently select a new library',()=>{
  const roots = [{path:'/new/filmy'},{path:'/media/serialy'}];
  assert.equal(reconcileRootSelection(null,roots),null);
  assert.deepEqual(reconcileRootSelection(['/old/filmy','/media/serialy'],roots),['/media/serialy']);
  assert.deepEqual(reconcileRootSelection(['/removed'],roots),[]);
  assert.deepEqual(reconcileRootSelection([],roots),[]);
});
test('root filter combines with search, exception hiding, grouping and sorting',()=>{
  const rows = [
    {...file('/media/serialy/Show/Season 1/a.mkv',576,40,null,'/media/serialy/Show'),root:'/media/serialy'},
    {...file('/media/serialy/Show/Season 2/b.mkv',576,60,{note:'keep'},'/media/serialy/Show'),root:'/media/serialy'},
    {...file('/media/filmy/Show/movie.mkv',576,200),root:'/media/filmy'}
  ];
  const selected = filterByRoots(rows,['/media/serialy']).filter(f=>!f.exception && f.path.includes('Show'));
  const groups = sorted(buildGroups(selected),'size','desc');
  assert.equal(groups.length,1); assert.equal(groups[0].size,40); assert.equal(groups[0].files.length,1);
  assert.equal(groups[0].isSeries,true);
});

test('movies are grouped by actual parent, including a one-file movie',()=>{
  const result=buildGroups([file('/media/Film A/a.mkv'),file('/media/Film B/b.mkv'),file('/media/Film A/extra.mkv')]);
  assert.equal(result.length,2); assert.equal(result[0].files.length,2); assert.equal(result[1].files.length,1);
  assert.equal(result[1].path,'/media/Film B'); assert.equal(result[1].isSeries,false);
});
test('equal folder names at different paths stay separate',()=>{
  assert.equal(buildGroups([file('/media/A/Film/a.mkv'),file('/media/B/Film/b.mkv')]).length,2);
});
test('series keep their show folder across seasons',()=>{
  const result=buildGroups([file('/media/Show/Season 1/a.mkv',576,100,null,'/media/Show'),file('/media/Show/Season 2/b.mkv',480,50,null,'/media/Show')]);
  assert.equal(result.length,1); assert.equal(result[0].isSeries,true); assert.equal(result[0].height,480); assert.equal(result[0].maxHeight,576); assert.equal(result[0].size,150);
});
test('parent folder supports POSIX root, drive root and UNC',()=>{
  assert.equal(parentFolder('/video.mkv'),'/'); assert.equal(parentFolder('C:\\video.mkv'),'C:\\');
  assert.equal(parentFolder('\\\\nas\\media\\Film\\video.mkv'),'\\\\nas\\media\\Film');
});
test('name sorting is natural in both directions without mutating inputs',()=>{
  const rows=[file('/media/Film 10/a.mkv'),file('/media/Film 2/b.mkv')];
  const groups=buildGroups(rows);
  assert.deepEqual(sorted(groups,'name','asc').map(x=>x.name),['Film 2','Film 10']);
  assert.deepEqual(sorted(groups,'name','desc').map(x=>x.name),['Film 10','Film 2']);
  assert.equal(rows[0].path,'/media/Film 10/a.mkv');
});
test('resolution sorts by height then width, in either direction',()=>{
  const rows=[file('/media/c.mkv',576,1,null,null,720),file('/media/b.mkv',480,1,null,null,720),file('/media/a.mkv',480,1,null,null,640)];
  assert.deepEqual(sorted(rows,'quality','asc').map(x=>x.name),['a.mkv','b.mkv','c.mkv']);
  assert.deepEqual(sorted(rows,'quality','desc').map(x=>x.name),['c.mkv','b.mkv','a.mkv']);
});
test('folder size sorting uses sum, not largest file',()=>{
  const groups=buildGroups([file('/media/A/one.mkv',576,80),file('/media/A/two.mkv',576,80),file('/media/B/movie.mkv',576,120)]);
  assert.equal(sorted(groups,'size','desc')[0].name,'A'); assert.equal(sorted(groups,'size','asc')[0].name,'B');
});
test('exceptions sort none, partial, all and reverse',()=>{
  const ex={scope:'file',note:'Test'};
  const groups=buildGroups([file('/media/All/a.mkv',576,1,ex),file('/media/None/a.mkv'),file('/media/Partial/a.mkv',576,1,ex),file('/media/Partial/b.mkv')]);
  assert.deepEqual(sorted(groups,'exception','asc').map(x=>x.name),['None','Partial','All']);
  assert.deepEqual(sorted(groups,'exception','desc').map(x=>x.name),['All','Partial','None']);
  assert.equal(sorted([file('/media/a.mkv',576,1,ex),file('/media/b.mkv')],'exception','asc')[0].name,'b.mkv');
});
test('aggregates follow filtered input and file data remain unchanged',()=>{
  const original=file('/media/A/a.mkv',576,100,{note:'keep'}); const snapshot=JSON.stringify(original);
  const groups=buildGroups([original]); assert.equal(groups[0].exceptionCount,1); assert.equal(groups[0].size,100);
  assert.equal(JSON.stringify(original),snapshot); assert.deepEqual(buildGroups([]),[]);
});

test('quality thresholds exclude exact HD and Full HD boundaries',()=>{
  assert.equal(isBelowThreshold(file('/a',719,1,null,null,1279),720),true);
  assert.equal(isBelowThreshold(file('/a',720,1,null,null,1279),720),false);
  assert.equal(isBelowThreshold(file('/a',719,1,null,null,1280),720),false);
  assert.equal(isBelowThreshold(file('/a',1079,1,null,null,1919),1080),true);
  assert.equal(isBelowThreshold(file('/a',1080,1,null,null,1919),1080),false);
  assert.equal(isBelowThreshold(file('/a',1079,1,null,null,1920),1080),false);
});
test('cropped filter includes wide video but never failed or unknown dimensions',()=>{
  const cropped=file('/a',544,1,null,null,1280);
  assert.equal(isBelowThreshold(cropped,720),false);
  assert.equal(isBelowThreshold(cropped,720,true),true);
  assert.equal(isBelowThreshold({...cropped,error:'ffprobe failed'},720,true),false);
  for (const dimension of [null,0,-1,undefined]) {
    assert.equal(isBelowThreshold({...cropped,height:dimension},720,true),false);
    assert.equal(isBelowThreshold({...cropped,width:dimension},720,true),false);
  }
});
test('Czech count labels distinguish one, few and many, including 21',()=>{
  assert.equal(counted(0,'video','videa','videí'),'0 videí');
  assert.equal(counted(1,'video','videa','videí'),'1 video');
  assert.equal(counted(4,'video','videa','videí'),'4 videa');
  assert.equal(counted(5,'video','videa','videí'),'5 videí');
  assert.equal(counted(21,'složka','složky','složek'),'21 složek');
});
