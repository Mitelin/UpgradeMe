# UpgradeMe

Samostatný, pouze čtecí přehled kvality video knihovny. Python 3.10+ a `ffprobe` z FFmpeg, bez Python balíčků, CDN, telemetrie či externích API. České UI, SQLite a denní inkrementální sken v **05:00 podle časové zóny serveru**.

## Co umí

- Sledování více složek z UI: tlačítko **Přidat složku** otevře prohlížeč serverových složek. Nabízí disky / umístění, podsložky, drobečkovou navigaci, návrat výš a hledání v aktuální složce. Po výběru nastavíte typ (automaticky, filmy, seriály / anime) a kliknete **Přidat tuto složku**. Cestu není nutné psát; ruční zadání zůstává jako volba pro UNC/síťová umístění. Složky překrývající již sledované umístění nelze přidat dvakrát.
- Hlavní pohled: **SD, které nedosahuje HD (720p)**. Standardně výška < 720 **a** šířka < 1280 px. Širokoúhlý HD ořez 1280×544 tak není automaticky označen jako SD.
- Volitelný pohled pod Full HD: výška < 1080 a šířka < 1920 px. Přepínač „Posuzovat jen výšku“ zahrne také širokoúhlé ořezy. Jde o rozměry uložené video stopy; z nich nelze prokázat kvalitu původního zdroje ani detekovat upscale.
- Hledání názvu, cesty i poznámky, přirozené řazení názvů, podle rozlišení či velikosti; stránkování.
- **Obnovit výchozí filtry** vymaže hledání, vybere všechny sledované složky a vrátí SD pohled se skrytými výjimkami a bez širokoúhlých ořezů. Zvolené řazení se nemění. Stránkování se zobrazí až při více než 60 složkách; přechod na další stranu vrátí pozornost k nadpisu seznamu.
- Filmy se zobrazují v rozbalitelných skutečných nadřazených složkách, i když složka obsahuje jediný film. Seriály zachovávají seskupení přes sezóny. Videa uložená přímo v kořeni knihovny tvoří složku tohoto kořene.
- Klikací záhlaví **Název / složka, Rozlišení, Velikost, Výjimka** řadí složky i jejich soubory. Opakovaný klik obrací směr; aktivní záhlaví má šipku. Výběr i směr se pamatují po obnovení stránky. Na menší obrazovce je stejné řazení dostupné v rozbalovacím seznamu a tlačítku směru.
- Rozlišení složky se řadí podle jejího nejnižšího rozlišení, velikost podle součtu a výjimky v pořadí žádné → část souborů → všechny (nebo obráceně). Souhrny počítají soubory aktuálně odpovídající filtrům. Pro porovnání výjimek vypněte filtr „Skrýt výjimky“.
- Sbalitelné seriály s jednotlivými díly. Auto režim pozná S01E02, 1x02 a bezprostřední složky sezón Season / Sezóna / Série / S01, včetně názvu za číslem (např. `season 03 - africa`). Seriál je složka přímo nad dílem, nebo nad jeho rozpoznanou sezónou (`Název/Sezóna/díl`). Vyšší nadřazené kategorie (např. `seriali/czech`) se při seskupování ignorují, jejich videa se ale dál skenují. U jiných názvů epizod použijte typ „Seriály / anime“. Lze vybrat i přímo složku konkrétního seriálu obsahující sezóny či epizody.
- Výjimky jednotlivých souborů nebo libovolné nadřazené složky až po kořen knihovny, s poznámkou. Složkové výjimky zahrnují podsložky a budoucí soubory. Přednost má výjimka souboru, pak nejbližší nadřazené složky.
- Filtr „Skrýt výjimky“ je standardně zapnutý. Správa všech výjimek je dostupná i při skrytí souborů. Po odstranění výjimky souboru může dál platit nadřazená složková výjimka.
- Výjimky jsou vázané na cestu, přežijí restart i výměnu souboru na stejné cestě. Přejmenování je nepřenáší. Odebrání sledované složky smaže její cache, ale zachová výjimky.
- Odebrání sledované složky vyžaduje potvrzení. Během ukládání jsou ovládací prvky dialogu dočasně zablokované proti opakovanému odeslání; při chybě zůstává poznámka zachovaná. Úspěšné uložení a případná následná chyba obnovení přehledu se oznamují odděleně.
- Při výpadku spojení UI upozorní na možné zastaralé výsledky a automaticky opakuje načítání. Po obnovení spojení varování zmizí. Ruční sken nelze spustit bez sledované složky, bez `ffprobe` ani během jiného skenu.
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

## Data a testy

Nastavení, výjimky, cache a výsledky skenů jsou v `runtime/upgrademe.sqlite3` (nebo `--data-dir`). Pro zálohu zastavte službu a zkopírujte celý datový adresář. Zdrojové soubory nepřesouvá, nemaže a nevytváří u nich sidecary.

```sh
python3 -m unittest discover -s tests -v
```

Testy seskupování a řazení UI (potřebují Node.js pouze pro testování, nikoli provoz aplikace):

```sh
node --test tests/test_library_view.cjs
```

Integrační testy s reálnými videi potřebují `ffmpeg` a `ffprobe` na PATH; pokud chybí, jsou označeny jako přeskočené. Testovací média se generují výhradně v dočasné složce.
