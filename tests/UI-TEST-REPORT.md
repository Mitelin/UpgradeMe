# Ověření UI

## Kompletní průchod — 3. 11. 2026

Testováno v integrovaném Chromium na odděleném localhost:5077, s vlastní dočasnou databází a generovaným videem 640 × 360. Skutečná knihovna uživatele nebyla měněna. Rozsáhlejší seznam a nedostupnost serveru byly simulované přes zachycené HTTP odpovědi.

| Scénář | Výsledek |
| --- | --- |
| První spuštění, přidání složky přímo z prázdného seznamu | OK |
| Serverový výběr složky, ruční cesta, typ Filmy | OK |
| Neexistující cesta a následný návrat na dostupná umístění | Chyba viditelná v dialogu; neplatnou složku nelze přidat, zotavení funguje |
| Skutečný sken generovaného videa, rozlišení 640 × 360 | OK |
| Uložení poznámky, skrytí výjimky, opětovné otevření a smazání | OK |
| Dvojí odeslání skenu / uložení výjimky ve stejném okamžiku | Právě 1 HTTP požadavek pro každou akci |
| Chyba při ukládání | Dialog zůstává otevřený, poznámka zachovaná, lze opakovat |
| Úspěšné uložení následované HTTP 503 při načítání knihovny | Opraveno: chyba je viditelná mimo zavřený dialog; úspěch uložení je oznámen zvlášť |
| HTML odpověď HTTP 502 a následné zotavení | Srozumitelná chyba s HTTP stavem; varování spojení po zotavení zmizí |
| Hledání, SD / Full HD, širokoúhlý ořez, skrytí výjimek | Očekávané počty 64 / 65 / 66 / 67 v testovací sadě |
| Výběr žádného / jednoho / všech kořenů a reset filtrů | OK |
| Stránkování 64 složek | 60 na první, 4 na druhé straně; správný fokus nadpisu |
| Jednostránkový výsledek | Zbytečné stránkování skryté |
| Řazení podle velikosti, rozbalení / sbalení, Enter na složce | OK |
| Klávesnicový fokus po zavření dialogu a při průběžné obnově za skenu | OK |
| Zrušení potvrzení odebrání a následné potvrzené odebrání | Kořen zůstane při zrušení; po potvrzení odebrán, výjimka i zdrojové video zachovány |
| Chybějící ffprobe a neplatná uložená hranice rozlišení | Sken zablokovaný s vysvětlením; neplatná preference se vrátí na 720p |
| Šířky 320, 375, 768, 1024 a 1440 px | Bez vodorovného přetečení stránky |
| Dialog výjimky 320 × 568, dlouhá poznámka; výběr složky 375 × 812 | Bez vodorovného přetečení, akce dosažitelné |

Automaticky: **15 JavaScript testů prošlo**, kontrola syntaxe JavaScriptu prošla, **40 Python testů prošlo**, včetně skutečných videí a HTTP API. Python testy vypsaly `ResourceWarning` při úklidu některých testovacích HTTP chyb; testy neselhaly.

Vzhled byl kontrolován screenshoty desktopu i mobilního viewportu. Mobilní viewport není testem fyzického telefonu; Safari, Firefox ani Linux/systemd nebyly v tomto kole ověřovány. Backend se neměnil, úpravy UI se načtou obnovením stránky.

## Předchozí ověření — 23. 10. 2026

### Doplnění: filmové složky a klikací řazení

Na odděleném localhost:5067 ověřeno s 9 generovanými videi:

- Filmy s jedním i více soubory jsou rozbalitelné složky; seriál zůstává seskupený přes sezóny.
- Kliknutí na každé ze čtyř záhlaví a opakované kliknutí: všech **8 kontrol pořadí** odpovídá očekávání.
- Velikost používá součet souborů složky; rozlišení její minimum; výjimky rozlišují žádnou, částečnou a úplnou výjimku.
- Obousměrné řazení se projeví i na souborech rozbaleného filmu.
- Rozbalená i ručně sbalená složka zachovává stav při změně řazení.
- Filtr výjimek odstraní odpovídající soubory a prázdné skupiny; po vypnutí je vrátí.
- Obnovení stránky zachová sloupec a směr. Enter na záhlaví funguje stejně jako kliknutí.
- Tlačítko filmové složkové výjimky otevře správnou cestu a existující poznámku.
- Konzole prohlížeče bez chyb / varování. Vizuálně ověřen skutečný vykreslený seznam.
- `node --check static/app.js`: OK. `node --test tests/test_library_view.cjs`: **9 testů prošlo** (seskupování, Windows/UNC/POSIX cesty, přirozené řazení, agregace a směry).

Změněné chování je čistě ve webovém UI; není nutný restart běžícího skeneru.

Testováno v integrovaném Chromium prohlížeči. Změny dat a chybové scénáře proběhly na odděleném localhost:5067 s generovanými médii. Na localhost:5066 byla pouze opravena a obnovena webová stránka při běžícím skutečném skenu; běžící skener nebyl při této opravě restartován.

## Výsledky

| Scénář | Výsledek |
| --- | --- |
| Přidat složku otevře vizuální dialog bez zadávání cesty | OK |
| Výběr umístění, otevření podsložek a návrat o úroveň výš | OK |
| Hledání v aktuálním adresáři, včetně prázdného výsledku | OK |
| Přidání prázdné složky pouze klikáním, typ Filmy | OK, počet kořenů 1 → 2 |
| Již sledovaná složka i její nadřazená složka blokují duplicitní přidání | OK |
| Neexistující cesta zobrazí chybu v dialogu, potvrzení není dostupné | OK |
| Po chybě lze vybrat jiné umístění; Zrušit zavře dialog | OK |
| Rozbalit vše → ručně sbalit seriál → změnit řazení | OK, seriál zůstává sbalený; původní chyba reprodukována před opravou |
| Výjimka s poznámkou se uloží a skryje soubor | OK, 4 → 3 výsledky |
| Vypnutí filtru a hledání podle poznámky | OK, nalezen jeden odpovídající soubor |
| Obnovení stránky zachová poznámku i nastavení filtrů | OK |
| Zrušení výjimky a změna hranice SD / Full HD | OK, rozšířený pohled zobrazil 7 z 8 testovacích videí |
| Stránkování při 69 videích a 64 položkách / seriálech | OK, další i předchozí strana a správné zneaktivnění krajních tlačítek |
| Odebrání sledované testovací složky | OK, všech 61 zdrojových testovacích souborů zůstalo na místě |
| Souběžně odebraný kořen před uložením rozepsané výjimky | OK, chyba zůstala v otevřeném dialogu a rozepsaná poznámka se zachovala |
| Konzole při standardních operacích | Bez JavaScript chyb či varování |
| Skutečný dlouhý sken: zobrazení výsledků ještě za běhu | OK, po opravě UI 13 526 záznamů a 2 899 SD kandidátů, sken dál aktivní |

## Automatické ověření

`python -m unittest discover -s tests -q`: **33 testů prošlo**, včetně skutečných SD/HD/Full HD videí, cache, výjimek, scheduleru, folder API, původních Unicode názvů a zápisu výsledků před dokončením kořene. `node --check static/app.js`: OK.

## Rozsah ověření

Ověřen desktopový Chromium náhled na Windows. Serverové nasazení Linux/systemd a fyzický mobilní prohlížeč nebyly v tomto kole testovány. Nové UI a průběžné obnovování jsou účinné po obnovení stránky. Změny skeneru (častější zápisy a předávání původních názvů) se načtou při příštím spuštění procesu; existující dlouhý sken používá dosavadní načtený kód.

Při diagnostice 186 chyb byly 3 případy chybějících MP4 metadat, 182 chyb otevření cesty a 1 jiná chyba. Samostatné pozdější ffprobe otevření jednoho původně neotevřeného souboru uspělo. To nedokazuje poškození těchto souborů ani jedinou společnou příčinu všech chyb cest. Ruční opakování chyb je dostupné po skončení skenu.
