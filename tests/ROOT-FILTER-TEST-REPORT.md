# Filtr sledovaných složek a rozpoznání vnořených dílů

Ověřeno 23. 10. 2026.

- 40 Python testů prošlo, včetně skutečných videí, inkrementální cache, HTTP API a nového seskupení dílů uvnitř vlastní složky pod Season.
- 12 JavaScript testů prošlo: přesná shoda sledované složky, jedna/více/všechny/žádná složka, změny seznamu knihoven, kombinace s hledáním a výjimkami, stávající řazení a seskupování.
- Interaktivně v odděleném serveru na portu 5067 se syntetickou knihovnou: žádná složka, pouze seriály, zapamatování po reloadu, více složek, hledání, skrývání výjimek, řazení, pouze filmy, všechny složky a prázdná knihovna.
- Reálné API po nasazení: 24 416 evidovaných videí, 4 sledované složky, žádný aktivní sken. Všech 8 kandidátů Parks and Recreation má společnou skupinu seriálu; žádný nemá chybějící skupinu.
- Reálný prohlížeč: výběr serialy + hledání Parks and Recreation zobrazí jedinou skupinu „Seriál · 8 dílů“. Následně obnoven původní prázdný dotaz a všechny složky (8 002 videí v pohledu), bez chybové hlášky.

Výběr složek je preference prohlížeče; nemění konfiguraci skenu. První dvě souhrnné karty respektují vybrané složky, celkový počet knihovny zůstává globální. Výslovně nastavený typ knihovny Filmy má nadále přednost před automatickým rozpoznáváním. Seskupování se počítá z existující databáze bez opakovaného čtení videí.
