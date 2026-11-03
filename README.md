# UpgradeMe

Samostatný, pouze čtecí přehled kvality video knihovny. Python 3.10+ a `ffprobe` z FFmpeg, bez Python balíčků, CDN, telemetrie či externích API. České UI, SQLite a denní inkrementální sken v **05:00 podle časové zóny serveru**, nebo na Linuxu sken navazující na **Media Maintenance**.

## Co umí

- Sledování více složek z UI: tlačítko **Přidat složku** otevře prohlížeč serverových složek. Nabízí disky / umístění, podsložky, drobečkovou navigaci, návrat výš a hledání v aktuální složce. Po výběru nastavíte typ (automaticky, filmy, seriály / anime) a kliknete **Přidat tuto složku**. Cestu není nutné psát; ruční zadání zůstává jako volba pro UNC/síťová umístění. Složky překrývající již sledované umístění nelze přidat dvakrát.
- Hlavní pohled: **SD, které nedosahuje HD (720p)**. Standardně výška < 720 **a** šířka < 1280 px. Širokoúhlý HD ořez 1280×544 tak není automaticky označen jako SD.
- Volitelný pohled pod Full HD: výška < 1080 a šířka < 1920 px. Přepínač „Posuzovat jen výšku“ zahrne také širokoúhlé ořezy. Jde o rozměry uložené video stopy; z nich nelze prokázat kvalitu původního zdroje ani detekovat upscale.
- Hledání názvu, cesty i poznámky, přirozené řazení názvů, podle rozlišení či velikosti; stránkování.
- **Obnovit výchozí filtry** vymaže hledání, vybere všechny sledované složky a vrátí SD pohled se skrytými výjimkami a bez širokoúhlých ořezů. Zvolené řazení se nemění. Stránkování se zobrazí až při více než 60 složkách; přechod na další stranu vrátí pozornost k nadpisu seznamu.
- Filmy se zobrazují v rozbalitelných skutečných nadřazených složkách, i když složka obsahuje jediný film. Seriály zachovávají seskupení přes sezóny. Videa uložená přímo v kořeni knihovny tvoří složku tohoto kořene.
- U každé zobrazené složky je v samostatném sloupci vpravo vedle tlačítka **Výjimka složky** odkaz **Hledat na SKTorrentu**. Obě akce jsou vedle sebe i na mobilu. V nové kartě otevře hledání celého zobrazeného názvu složky, nikoli serverové cesty nebo názvu epizody. Mezery, diakritika a speciální znaky se bezpečně zakódují. Externí stránka se kontaktuje až po kliknutí; odkaz neposílá adresu přehledu jako referrer.
- Klikací záhlaví **Název / složka, Rozlišení, Velikost, Výjimka** řadí složky i jejich soubory. Opakovaný klik obrací směr; aktivní záhlaví má šipku. Výběr i směr se pamatují po obnovení stránky. Na menší obrazovce je stejné řazení dostupné v rozbalovacím seznamu a tlačítku směru.
- Rozlišení složky se řadí podle jejího nejnižšího rozlišení, velikost podle součtu a výjimky v pořadí žádné → část souborů → všechny (nebo obráceně). Souhrny počítají soubory aktuálně odpovídající filtrům. Pro porovnání výjimek vypněte filtr „Skrýt výjimky“.
- Sbalitelné seriály s jednotlivými díly. Auto režim pozná S01E02, 1x02 a bezprostřední složky sezón Season / Sezóna / Série / S01, včetně názvu za číslem (např. `season 03 - africa`). Seriál je složka přímo nad dílem, nebo nad jeho rozpoznanou sezónou (`Název/Sezóna/díl`). Vyšší nadřazené kategorie (např. `seriali/czech`) se při seskupování ignorují, jejich videa se ale dál skenují. U jiných názvů epizod použijte typ „Seriály / anime“. Lze vybrat i přímo složku konkrétního seriálu obsahující sezóny či epizody.
- Výjimky jednotlivých souborů nebo libovolné nadřazené složky až po kořen knihovny, s poznámkou. Složkové výjimky zahrnují podsložky a budoucí soubory. Přednost má výjimka souboru, pak nejbližší nadřazené složky.
- Filtr „Skrýt výjimky“ je standardně zapnutý. Správa všech výjimek je dostupná i při skrytí souborů. Po odstranění výjimky souboru může dál platit nadřazená složková výjimka.
- Výjimky jsou vázané na cestu, přežijí restart i výměnu souboru na stejné cestě. Přejmenování je nepřenáší. Odebrání sledované složky smaže její cache, ale zachová výjimky.
- Odebrání sledované složky vyžaduje potvrzení. Během ukládání jsou ovládací prvky dialogu dočasně zablokované proti opakovanému odeslání; při chybě zůstává poznámka zachovaná. Úspěšné uložení a případná následná chyba obnovení přehledu se oznamují odděleně.
- Při výpadku spojení UI upozorní na možné zastaralé výsledky a automaticky opakuje načítání. Po obnovení spojení varování zmizí. Ruční sken nelze spustit bez sledované složky, bez `ffprobe` ani během jiného skenu.
- Tlačítko **Skenovat nyní** spustí ruční sken bez čekání na další plánovaný běh (společný zámek údržby stále platí). **Zastavit sken** zruší aktuální sken i čekání na tento zámek a ukončí právě běžící ffprobe. Průběžné výsledky se uloží, neprohlédnutá cache ani média se nemažou. Web a plánování zůstávají běžet; po zastavení lze znovu kliknout na **Skenovat nyní**. Probíhající systémové čtení z nedostupného NAS může zastavení oddálit, dokud se toto čtení nevrátí.
- Během ručního i automatického skenu se text tlačítka změní na **Sken probíhá…**, při zastavování a ukládání na odpovídající stav.
- **Aktualizovat aplikaci** po potvrzení stáhne `origin/main` a restartuje web. Během skenu je zakázané; nejprve sken dokončete nebo zastavte. Vyžaduje jednorázovou instalaci aktualizační služby popsanou níže.
- Mobilní rozložení má větší dotykové ovládání a posuvné dialogy. Klávesnicový fokus v seznamu se zachovává při průběžném načítání výsledků i po zavření editoru výjimky.

## Spuštění

Linux (Debian/Ubuntu): nainstalujte `python3` a `ffmpeg`. Na Windows musí být dostupný skutečný Python 3.10+ a FFmpeg.

```sh
python3 upgrademe.py
```

Ve Windows použijte `python upgrademe.py`. Otevřete http://127.0.0.1:5066 a přidejte skutečné **serverové** cesty (např. `/mnt/nas/filmy`, ne klientské `Z:`). Projekt neskenuje žádnou knihovnu, dokud ji nepřidáte.

```sh
python3 upgrademe.py --host 127.0.0.1 --port 5066 --data-dir /var/lib/upgrademe --ffprobe /usr/bin/ffprobe
```

Pro přímý přístup z důvěryhodné LAN nastavte `--host` na konkrétní LAN IP serveru. Pro přístup přes DNS doplňte `--allowed-host muj-server`. Při bindu na `0.0.0.0` musíte zvlášť povolit IP/jméno, které bude v URL, pomocí `--allowed-host`. UI nemá účty ani hesla; pro veřejný přístup použijte autentizující reverzní proxy. Výchozí bind je pouze localhost. Z jiného počítače lze použít SSH tunel `ssh -L 5066:127.0.0.1:5066 user@server`.

## Inkrementální sken a ochrana knihovny

Každý sken projde adresáře a porovná **cestu, velikost a nanosekundový čas změny**. Nezměněná videa znovu neotevírá přes ffprobe, nepočítá jejich hash ani je nepřekóduje. Adresáře a metadata je nutné projít, aby byly odhaleny změny i hluboko ve stromu. První sken přečte hlavičky všech videí; další jsou závislé především na rychlosti výpisu NAS, ne na délce filmů. Obsah změněný při zachování velikosti i času změny tento mechanismus nepozná.

Výsledky se zobrazují **už během skenu**: UI průběžně načítá databázi přibližně po 6 sekundách (třísekundový polling, nejméně 5 s mezi načteními). Skener zapisuje dávku nejpozději po dokončení souboru, pokud uplynuly 2 s od posledního zápisu, nebo po 200 záznamech. Nečeká se na dokončení celé knihovny. Cesta předaná ffprobe zachovává původní název souboru z adresáře; normalizovaná cesta slouží jako klíč cache.

Analýza probíhá po jednom souboru s limitem 45 s na soubor. Soubory upravené před méně než dvěma minutami se odloží na další sken. Změna během analýzy vede k odložení výsledku. Čistě čtecí proces nemůže plně synchronizovat souběžné zápisy cizích aplikací. Neznámé rozlišení není považováno za SD: je v panelu upozornění. Nezměněný poškozený soubor se znovu neanalyzuje; tlačítko „Zopakovat i chybné soubory“ umožní ruční opakování.

Odpojené/nečitelné složky nevymažou cache. Ani náhle prázdná dříve naplněná složka ji nevymaže, protože může jít o odpojený NAS. Pokud jste záměrně smazali všechna videa v kořeni, odeberte a znovu přidejte sledovanou složku. Při úspěšném neprázdném skenu se odstraní z cache skutečně zmizelé soubory. Varování může znamenat, že zobrazený přehled obsahuje starší výsledky.

Symbolické odkazy se nesledují. Koše NAS a pracovní složky AutoRipperu jsou ignorované. Přípony: MKV, MP4, AVI, MOV, WMV, M4V, TS, M2TS, MTS, MPG, MPEG, WEBM, VOB, FLV, OGV, 3GP, ASF, DIVX. Při více video stopách se posuzuje největší obrazová stopa; přebal `attached_pic` se vynechá.

## Trvalý provoz na serveru

Šablona je v `deploy/upgrademe.service`. Před instalací nastavte cesty, účet a přístup k médiím podle serveru. Služba používá časovou zónu Europe/Prague, takže 05:00 zůstává 05:00 i po změně letního času.

1. Zkopírujte projekt do `/opt/upgrademe` a vytvořte systémového uživatele `upgrademe` pouze s právem čtení médií.
2. Nainstalujte Python a FFmpeg. Udělte uživateli přístup k potřebným mountům/skupinám; nepouštějte službu jako root jen kvůli médiím.
3. Do `[Unit]` doplňte `RequiresMountsFor=` se skutečnými mounty NAS. Pokud NAS poskytuje dočasně prázdné či neúplné adresáře, ověřte také konfiguraci mountu.
4. Zvolte localhost + SSH/proxy, nebo konkrétní LAN IP v `ExecStart`.
5. Nainstalujte unit do `/etc/systemd/system/upgrademe.service`, proveďte `systemctl daemon-reload` a `systemctl enable --now upgrademe`.
6. Ověřte `systemctl status upgrademe`, `journalctl -u upgrademe` a skutečný sken z UI.

Tyto kroky jsou návod pro nasazení, nikoli tvrzení, že služba už byla na serveru nainstalovaná.

Scheduler běží uvnitř aplikace. Jednou denně od 05:00 zahájí automatický sken; po pozdním startu doplní dnešní zmeškaný sken. Běžící skeny se nepřekrývají. Denní pokus se eviduje i při chybě, další pokus lze spustit ručně. Vypnutý server se sám neprobudí. Jednu datovou složku chrání zámek proti souběhu dvou instancí.

### Napojení na Media Maintenance (Linux/systemd)

Web běží trvale, sken se spouští po skončení služby údržby **i při jejím selhání nebo timeoutu**. Na dokončení samostatného Jellyfin refresh se v tomto režimu **nečeká**. Zámek chrání vzájemný souběh UpgradeMe a maintenance služby, nikoli nezávislé úlohy Jellyfinu nebo jiných aplikací.

1. V `ExecStart` služby UpgradeMe přidejte:

   ```text
   --maintenance-trigger /var/lib/upgrademe/maintenance.json --shared-scan-lock /var/lib/upgrademe/media-maintenance.lock
   ```

   Tyto volby **nahrazují** vlastní rozvrh v 05:00; nepřidávají druhý časovač. UI místo času zobrazuje „Po údržbě“.

2. Před spuštěním obou služeb připravte `/var/lib/upgrademe` s právy pro oba procesy. Na media serveru obě služby běží jako `mitelin`; pokud používáte oddělené účty, nastavte společnou skupinu a práva pro zámek i signál. Soubor zámku nikdy za provozu nemažte ani nenahrazujte.

3. Šablonu [media-transfer-maintenance.conf](deploy/media-transfer-maintenance.conf) nainstalujte jako `/etc/systemd/system/media-transfer-maintenance.service.d/upgrademe.conf`. Upravte cestu k existujícímu maintenance skriptu i instalaci UpgradeMe podle serveru. Drop-in obalí dosavadní `ExecStart` pomocí `flock`; samotný maintenance skript, jeho časovač i konfigurace zůstávají beze změny.

4. `ExecStopPost` volá [notify_maintenance.py](deploy/notify_maintenance.py), který atomicky uloží identifikátor dokončeného běhu a `SERVICE_RESULT`. Funguje i při zastaveném webu UpgradeMe. Po `systemctl daemon-reload` zapněte UpgradeMe; kvůli instalaci není nutné spouštět skutečnou údržbu.

Scheduler kontroluje signál po 20 sekundách. Každý dokončený běh vyvolá jeden pokus o sken; několik běhů během odstávky webu se sloučí do jednoho aktuálního skenu. Identifikátor se v databázi potvrdí až po skončení skenu, takže sken přerušený ukončením aplikace se po restartu zopakuje. Výslovné zastavení tlačítkem **Zastavit sken** se naopak považuje za vyřízený pokus a stejný signál jej znovu nespustí; další běh údržby funguje normálně. Chybný signál se hlásí v logu a nesmí se potichu ignorovat.

Automatický **i ruční** sken drží stejný OS zámek po celou dobu práce. Pokud běží údržba, UI oznámí čekání; pokud skenuje UpgradeMe, údržba počká. Existující timeout maintenance služby se nemění a zahrnuje i toto čekání. Přímé spuštění jejího skriptu mimo systemd drop-in zámek obchází, proto používejte časovač nebo stávající tlačítko Media Maintenance.

Na homepage lze přidat běžný odkaz na port 5066 a `siteMonitor` na LAN adresu. Odkaz není autentizace; službu zpřístupňujte jen v důvěryhodné LAN / VPN.

### Aktualizace z webu (Linux/systemd)

Aktualizace je volitelná. Web sám nemá sudo oprávnění ani právo přepisovat svůj kód.
Pouze vytvoří požadavek v datovém adresáři; samostatná systemd služba provede
fast-forward čisté větve `main`, zálohu SQLite do `/var/backups` a restart.
Git a kontrola syntaxe běží pod neprivilegovaným účtem aplikace. Instalujte pouze
důvěryhodné revize z nastaveného `origin`; přístup k tomuto tlačítku mají všichni
uživatelé povolené LAN/VPN stejně jako k ostatnímu ovládání.

1. Nainstalujte **rootem vlastněnou kopii** [update_app.py](deploy/update_app.py)
   do `/usr/local/libexec/upgrademe-update.py` (root:root, 0755).
   Nespouštějte tento privilegovaný pomocný program přímo z uživatelsky zapisovatelného Git checkoutu.
2. Nainstalujte [upgrademe-update.service](deploy/upgrademe-update.service) a
   [upgrademe-update.path](deploy/upgrademe-update.path) do `/etc/systemd/system/`.
   V `ExecStart` nastavte `--user` na vlastníka checkoutu (na media serveru `mitelin`).
   Při jiných cestách/portu nastavte také `--repo`, `--data-dir`, `--port`
   a odpovídající `PathExists`.
3. V datovém adresáři musí existovat soubor `media-maintenance.lock`, zapisovatelný
   pro účet aplikace. Existující soubor nemažte ani nenahrazujte.
4. Do drop-in služby `upgrademe.service` přidejte
   `Environment=UPGRADEME_ENABLE_UPDATES=1` v sekci `[Service]`.
   Proveďte `systemctl daemon-reload`, `systemctl enable --now upgrademe-update.path`
   a restartujte UpgradeMe, když neběží sken.

Požadavek na aktualizaci blokuje nové ruční i plánované skeny a změny přes API,
i přes restart aplikace. Aktualizátor čeká až pět minut na společný zámek
Media Maintenance; při překročení limitu ohlásí chybu a lze jej spustit znovu.
Existující údržbu nepřerušuje. Nastavení, výjimky a cache se při aktualizaci nemění.
Po restartu UI automaticky obnoví stránku. I při aktuální revizi proběhne restart.
Lokální změny, jiná větev a ne-fast-forward historie aktualizaci zablokují.
Při chybě instalace se obnoví předchozí kód pomocí `git reset --keep` a služba se
znovu spustí; databáze se automaticky nevrací ze zálohy. Chyby jsou viditelné v UI
a v `journalctl -u upgrademe-update.service`. Při selhání návratu je nutný zásah správce.

Privilegovaný pomocník a systemd jednotky se z webu samy nepřepisují.
Jejich případné změny vyžadují novou instalaci správcem. Po násilném ukončení
aktualizátoru zkontrolujte journal a stav služby před ručním odstraněním
osiřelého `update.request`.

## Data a testy

Nastavení, výjimky, cache a výsledky skenů jsou v `runtime/upgrademe.sqlite3` (nebo `--data-dir`). Pro zálohu zastavte službu a zkopírujte celý datový adresář. Zdrojové soubory nepřesouvá, nemaže a nevytváří u nich sidecary.

```sh
python3 -m unittest discover -s tests -v
```

Testy seskupování a řazení UI (potřebují Node.js pouze pro testování, nikoli provoz aplikace):

```sh
node --test tests/test_library_view.cjs
```

Testy signálů údržby jsou součástí Python sady. Testy vzájemného `flock` zámku se na Windows přeskakují; před nasazením je spusťte také na Linuxu.

Integrační testy s reálnými videi potřebují `ffmpeg` a `ffprobe` na PATH; pokud chybí, jsou označeny jako přeskočené. Testovací média se generují výhradně v dočasné složce.
