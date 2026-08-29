# Product Spec – Vacation Hunter

## Produktidee

Andere Dienste finden günstige Flüge, Error Fares oder Preisstürze. Vacation Hunter geht
einen Schritt weiter: Wir wollen nicht nur wissen, ob ein Flug günstig ist, sondern ob eine
**komplette Reise** – Flug + Unterkunft für dieselben Reisedaten – ungewöhnlich günstig ist.

Ablauf:

1. Außergewöhnlich günstigen Flug erkennen
2. Passende Reisedaten daraus bestimmen (Hin-/Rückflugdatum)
3. Für genau diese Reisedaten Unterkünfte am Zielort untersuchen
4. Erkennen, ob auch die Unterkunft außergewöhnlich günstig ist
5. Flug und Unterkunft zu einem Gesamttrip kombinieren
6. Bewerten, wie außergewöhnlich günstig der **gesamte** Trip ist (Trip Score)

## Deal-Typen

| Typ | Bedeutung |
|---|---|
| `FLIGHT_DROP` | Ein Flug ist deutlich günstiger als üblich. |
| `HOTEL_DROP` | Eine Unterkunft ist deutlich günstiger als üblich. |
| `ERROR_FARE` | Sehr wahrscheinlich ein fehlerhafter Flugpreis. |
| `UNUSUALLY_LOW` | Preis ist auffällig niedrig, ohne dass wir "Error Fare" behaupten. |
| `COMBINED_TRIP_DROP` | Flug + Unterkunft ergeben zusammen einen außergewöhnlich günstigen Gesamttrip. **Unser wichtigstes Merkmal.** |
| `BASELINE_UNAVAILABLE` | Wir kennen den aktuellen Preis, aber keinen Vergleichswert – wir können (noch) nicht beurteilen, ob er günstig ist. Seit MVP 0.2, siehe "Baseline-Problem" unten. |
| `PRICE_INCOMPLETE` | Wir sind nicht sicher, dass der Preis den vollständigen relevanten Trip-Preis abbildet (z. B. ein unbestätigter Roundtrip-Preis). Seit MVP 0.2.2, siehe "Price Completeness" unten. |

### Wie ein Deal-Typ bestimmt wird (MVP 0.1)

Jeder Schwellenwert vergleicht die **Ersparnis in Prozent** gegenüber einem "üblichen"
Preis (Baseline), den der jeweilige Provider liefert. Die Werte sind bewusst einfache,
benannte Konstanten im Code (`engine/flight_deal_detector.py`,
`engine/hotel_deal_detector.py`, `engine/deal_engine.py`) – sie lassen sich später leicht
anpassen, sobald echte Preisdaten vorliegen.

**Flug-Ebene** (verglichen mit dem üblichen Preis auf dieser Route):

- `>= 70 %` günstiger → `ERROR_FARE`
- `>= 30 %` günstiger → `FLIGHT_DROP`
- `>= 15 %` günstiger → `UNUSUALLY_LOW`
- darunter → kein Flug-Deal, wird nicht weiterverfolgt

**Hotel-Ebene** (verglichen mit dem üblichen Gesamtpreis für denselben Zeitraum):

- `>= 25 %` günstiger → `HOTEL_DROP`
- darunter → kein Hotel-Deal (fließt aber trotzdem in die Gesamtrechnung des Trips ein)

**Trip-Ebene** – ein gefundener Flug-Deal wird zu `COMBINED_TRIP_DROP` hochgestuft, wenn
**beide** Bedingungen erfüllt sind:

- Gesamtersparnis (Flug + Unterkunft) `>= 30 %`
- Absolute Gesamtersparnis `>= 100 €`

Beide Bedingungen zusammen verhindern zwei Fehlerfälle: eine kleine prozentuale Ersparnis
auf einem bereits günstigen Trip (nicht spannend), und eine hohe prozentuale Ersparnis auf
einem winzigen Betrag (z. B. 10 € statt 20 € – auch nicht spannend).

Ein Flug-Deal wird nur weiterverfolgt (Unterkunftssuche etc.), wenn er mindestens
`UNUSUALLY_LOW` erreicht hat. Findet sich keine passende Unterkunft für die Reisedaten,
bleibt der Deal auf Flug-Ebene stehen (z. B. `FLIGHT_DROP` ohne Kombination).

## Trip Score (0–100)

MVP 0.1 nutzt eine **transparente, regelbasierte** Formel – kein Machine Learning. Jeder
Punkt lässt sich exakt zurückverfolgen (siehe `DealScore.breakdown` im Code).

| Faktor | Maximale Punkte | Wann volle Punktzahl |
|---|---|---|
| Gesamt-Trip-Ersparnis in % | 50 | ab 60 % Ersparnis |
| Flug-Ersparnis in % | 20 | ab 60 % Ersparnis |
| Hotel-Ersparnis in % | 15 | ab 60 % Ersparnis |
| Absolute Ersparnis in € | 10 | ab 300 € gespart |
| Direktflug-Bonus | 5 | 0 Umstiege (2 Punkte bei 1 Umstieg, 0 bei 2+) |

Summe = Trip Score, begrenzt auf 0–100.

Für spätere Versionen sind weitere Faktoren vorgesehen (siehe Anforderungsliste):
Flugdauer, Flugzeiten, Gepäck, Hotelbewertung, Hotellage, Saison, Reisedauer,
Verfügbarkeit, Deal Confidence. Diese sind bewusst noch nicht implementiert, um MVP 0.1
einfach zu halten – die Formel ist so aufgebaut, dass neue Faktoren als zusätzliche
Gewichte ergänzt werden können, ohne bestehende Logik umzubauen.

## Provider-Wechsel: Amadeus → SerpApi Google Flights (MVP 0.2.1)

Amadeus hat sein Self-Service-Developer-Portal für **neue** Entwickler eingestellt –
ein Zugang ist damit für uns nicht mehr ohne Weiteres verfügbar. `AmadeusFlightProvider`
bleibt vollständig im Projekt erhalten (Architektur-/Historiengründe, austauschbares
Beispiel für die Provider-Abstraktion), ist aber **nicht mehr der aktive MVP-Provider**.

**Aktiver MVP-Provider seit 0.2.1:** `SerpApiGoogleFlightsProvider`, über die
kostenlose SerpApi-Google-Flights-Engine. Warum SerpApi: einfache API (ein API Key als
Query-Parameter, kein OAuth-Flow), kostenloses monatliches Freikontingent, reine
Such-API, gute Abdeckung für europäische Flüge, und zusätzlich liefert sie – anders als
Amadeus – teilweise **Price Insights** (siehe unten), die uns beim Baseline-Problem
helfen.

## Price Insights (Google Flights über SerpApi)

Google Flights zeigt in der eigenen Oberfläche manchmal eine Einschätzung wie "günstig"
oder eine typische Preisspanne für eine Route. SerpApi kann diese Daten unter
`price_insights` mitliefern – **aber nicht immer**. Wenn sie fehlen, erfinden wir sie
nicht.

Diese Information landet in einem eigenen, providerunabhängigen Modell `PriceInsight`
(`provider_lowest_price`, `typical_price_low`, `typical_price_high`, `price_level`,
`source`) – bewusst nicht als lose SerpApi-Felder im Code verteilt. Die Deal Engine kennt
nur `PriceInsight`, nicht Google oder SerpApi. Später könnte z. B. auch ein anderer
Flug-Provider eigene Price Insights liefern, ohne dass sich an der Engine etwas ändert.

**Namensgebung `provider_lowest_price` (statt ursprünglich `current_price`):** Dieser Wert
ist Googles/SerpApis **eigener** "niedrigster verfolgter Preis" für die Route – ein
Marktwert des Providers, **nicht** der aktuelle Preis des von uns gefundenen konkreten
Flugs (`FlightOffer.price`). Beide können und werden auseinanderlaufen (im echten Test:
unser günstigstes Angebot 184 EUR vs. `provider_lowest_price` 232 EUR – beides plausibel
richtig, nur unterschiedliche Dinge). Der alte Name `current_price` legte fälschlich nahe,
es handle sich um denselben Wert.

## Baseline-Herkunft: `BaselineSource`

Seit MVP 0.2.1 unterscheiden wir, **welche Art** von Vergleichswert hinter einem Deal
steckt:

| `BaselineSource` | Bedeutung |
|---|---|
| `OWN_HISTORICAL_BASELINE` | Unsere eigene Preishistorie für die Route – seit MVP 0.3 **echt** (siehe "Historical Price Intelligence" unten), sofern genug eigene Beobachtungen vorliegen; sonst die Mock-Provider-Daten. |
| `PROVIDER_PRICE_INSIGHT` | Eine Preiseinschätzung eines Drittanbieters (z. B. Google Flights), keine eigene Historie. |
| `NO_BASELINE` | Kein Vergleichswert irgendeiner Art verfügbar. |

Das ist bewusst von `DealType` getrennt: `DealType` sagt, *was* gefunden wurde,
`BaselineSource` sagt, *wie sehr* man dem Vergleichswert dahinter vertrauen sollte.
Eine `PROVIDER_PRICE_INSIGHT`-Baseline ist **keine eigene historische Vacation-Hunter-
Baseline**.

**Ablauf pro Flug** (in `DealEngine`, aktualisiert in MVP 0.3):

0. `price_confirmed_complete == False`? → sofort `PRICE_INCOMPLETE`, **vor** jedem
   Baseline-Mechanismus, auch vor der eigenen Historie (siehe "Price Completeness" oben).
1. Genug eigene, echte Beobachtungen in der Preishistorie (siehe unten)? →
   `OWN_HISTORICAL_BASELINE` (Median aus der Historie), normale Deal-Klassifizierung
   (inkl. `ERROR_FARE` möglich).
2. Keine ausreichende eigene Historie, aber der Provider selbst liefert eine eigene
   Baseline (`get_typical_price`, aktuell nur der Mock-Provider)? → ebenfalls
   `OWN_HISTORICAL_BASELINE`, normale Klassifizierung.
3. Weder eigene Historie noch Provider-Baseline, aber eine nutzbare Price Insight
   (typische Preisspanne)? → `PROVIDER_PRICE_INSIGHT`. Klassifizierung **gedeckelt**:
   maximal `FLIGHT_DROP` oder `UNUSUALLY_LOW`, niemals `ERROR_FARE` (siehe unten).
   Zeigt die Insight keine nennenswerte Ersparnis, bleibt der Deal-Typ
   `BASELINE_UNAVAILABLE` – die Vergleichszahlen werden trotzdem transparent
   mitgegeben, nur eben nicht als "Deal" gewertet.
4. Nichts davon verfügbar? → `NO_BASELINE`, `BASELINE_UNAVAILABLE`, keine Zahlen erfunden.

## Historical Price Intelligence (MVP 0.3)

**Ziel:** Vacation Hunter soll künftig selbst beantworten können: "Was kostet diese
Route normalerweise?" – aus tatsächlich selbst beobachteten Preisen, nicht aus
Mock-Daten oder der Einschätzung eines Drittanbieters.

### `PriceObservation` – ein beobachteter Preis, keine Schätzung

Eine `PriceObservation` repräsentiert **"einen tatsächlich beobachteten Preis zu einem
bestimmten Zeitpunkt"** – nicht eine Schätzung, keinen Durchschnitt, keine Vorhersage.
Provider-unabhängig: Herkunft (Route, Reisedaten, Trip-Typ, Preis, Währung, Provider-Name,
Stopps, Airline, Beobachtungszeitpunkt), aber **keine** rohen API-Antworten und **keine**
API-spezifischen Tokens (z. B. kein `departure_token`).

### Observation Semantics (MVP 0.3.1) – Kernregel

**One search snapshot = at most one market-price observation for a comparison group.**

Bevor echte Beobachtungen gesammelt werden, musste geklärt werden: Eine einzelne Suche
liefert typischerweise mehrere `FlightOffer` (z. B. 9 Angebote für dieselbe Route/
Reisedaten: 184, 205, 227, 249, 279, 303 EUR, …). Würden wir **alle** davon als
gleichwertige historische Beobachtungen speichern, wäre der spätere Median der Median
**aller Angebote einer Suche** – nicht der Median der **günstigsten Preise über mehrere
Zeitpunkte**. Das ist für einen Deal-Hunter konzeptionell falsch, aus zwei Gründen:

1. **Statistische Verzerrung:** Eine Suche mit 30 Ergebnissen bekäme 30× so viel Gewicht
   wie eine Suche mit 3 Ergebnissen – Zeitpunkte würden nicht gleich gewichtet.
2. **Falsches Konzept:** Wir wollen wissen "wie günstig war der Markt zu diesem
   Zeitpunkt erreichbar", nicht "wie ist die Preisspanne innerhalb einer Suche verteilt".

**Deshalb gilt:** Eine `PriceObservation` repräsentiert **"der günstigste valide,
vollständig bepreiste und vergleichbare Flug, den Vacation Hunter bei EINER Suche für
eine bestimmte Vergleichsgruppe zu einem bestimmten Zeitpunkt beobachtet hat"** – nicht
neun (oder dreißig) gleichwertige Preisbeobachtungen.

```
Search Snapshot #1 (Tag 1): 9 Angebote gefunden, günstigstes valides = 184 EUR
    → EINE historische Marktbeobachtung: 184 EUR

Search Snapshot #2 (Tag 2): günstigstes valides Angebot = 191 EUR
    → EINE historische Marktbeobachtung: 191 EUR

Historie über mehrere Tage: 184, 191, 178, 186, 195, 181, ...
    → daraus wird der Median berechnet
```

**Architekturentscheidung:** Kein neues Modell (`MarketPriceObservation` o. ä.) – dafür
wäre `PriceObservation` bereits die richtige Semantik ("ein beobachteter Preis zu einem
Zeitpunkt", siehe oben). Stattdessen ein providerunabhängiger Auswahl-Helfer:
`observation_from_search_results(offers: list[FlightOffer], comparison_group,
observed_at) -> PriceObservation | None` (`price_history_repository.py`). Details zur
Auswahl-Logik: siehe "Explicit Comparison Groups" unten.

### Explicit Comparison Groups (MVP 0.3.2) – Kernregel

**Vacation Hunter wählt niemals anhand der Anzahl der Ergebnisse, welche Vergleichsgruppe
gemeint war. Der Caller definiert die Comparison Group explizit; der Auswahl-Helfer wählt
nur innerhalb dieser Gruppe den günstigsten validen, vollständig bepreisten Preis.**

Die erste Version von `observation_from_search_results(...)` (MVP 0.3.1) gruppierte
Angebote automatisch nach Route/Reisedaten/Currency und behielt die **größte** Gruppe.
Das ist deterministisch, aber fachlich riskant: Eine Liste könnte z. B. enthalten

```
HAM → PMI
02.10.–07.10.: 3 Angebote
03.10.–08.10.: 7 Angebote
04.10.–09.10.: 5 Angebote
```

Wollten wir eigentlich 02.10.–07.10. beobachten, hätte die "größte Gruppe gewinnt"-Regel
automatisch die 7er-Gruppe gewählt – die falsche Vergleichsgruppe, nur weil sie zufällig
mehr Ergebnisse hatte. Das `FlightProvider`-Interface erlaubt explizit Datumsfenster
(`earliest_departure`..`latest_departure`), ein zukünftiger Discovery-Request könnte also
durchaus mehrere Reisedaten in einer Antwort liefern (Audit-Ergebnis MVP 0.3.2: Aktuell
tut das kein aktiver Provider – `SerpApiGoogleFlightsProvider` fragt immer nur ein exaktes
Datum ab –, aber das Interface sieht es architektonisch vor).

**Deshalb:** Ein neues, providerunabhängiges Modell `FlightComparisonGroup`
(`origin`, `destination`, `departure_date`, `return_date`, `trip_type`, `currency`) –
die explizite Aussage des Aufrufers, welche Angebote vergleichbar sind. Keine Heuristik
mehr. `observation_from_search_results(offers, comparison_group, observed_at)`:

1. verwirft Angebote mit `price_confirmed_complete=False` (siehe "Price Completeness")
2. behält **ausschließlich** Angebote, die exakt zur angegebenen `comparison_group`
   passen (`FlightComparisonGroup.matches(...)`: Route, exaktes `departure_date`,
   exaktes `return_date`, Currency)
3. wählt darunter das **günstigste** Angebot
4. erzeugt daraus **genau eine** `PriceObservation`
5. gibt `None` zurück, wenn **kein** Angebot exakt zur Gruppe passt – keine
   "größte Gruppe", keine "erste Gruppe", keine Mehrheitsentscheidung, kein Raten

`FlightComparisonGroup` validiert außerdem sich selbst: `trip_type=ONE_WAY` verlangt
`return_date == departure_date` (unsere etablierte Konvention), `trip_type=ROUND_TRIP`
verlangt einen echten späteren `return_date` – ein Aufrufer kann keine in sich
widersprüchliche Gruppe konstruieren (z. B. `ROUND_TRIP` mit `return_date ==
departure_date`, was versehentlich Ein-Weg-Angebote in eine Roundtrip-Baseline mischen
könnte).

**Search-Snapshot-Semantik präzisiert:** Nicht "ein API-Request = genau eine
Observation", sondern **"ein Search Snapshot erzeugt maximal eine Observation PRO
EXPLIZITER Comparison Group"**. Liefert ein zukünftiger Discovery-Request mehrere
Reisedaten (z. B. 02.10.–07.10., 03.10.–08.10., 04.10.–09.10.), dürfen das drei getrennte
Comparison Groups mit drei getrennten Observations sein – sie dürfen aber niemals
gegeneinander ausgespielt werden ("größte Gruppe gewinnt").

Der bereits bestehende `observation_from_flight_offer(...)`-Helfer bleibt als
Low-Level-Baustein erhalten (reine FlightOffer→PriceObservation-Konvertierung für ein
bereits ausgewähltes Angebot) – `observation_from_search_results(...)` nutzt ihn intern.
**Wichtig:** `observation_from_flight_offer(...)` darf **nicht** in einer Schleife über
alle Angebote einer Suche aufgerufen werden – das wäre der ursprüngliche MVP-0.3.1-
Bias-Fehler. Der Docstring warnt davor ausdrücklich.

**Stops:** Weiterhin bewusst `cheapest_any` – das günstigste valide, zur Gruppe passende
Angebot gewinnt, unabhängig von Stopps. Ein 150-EUR-Umsteigeflug schlägt einen
190-EUR-Direktflug, auch wenn Letzterer ein hervorragender eigener Deal sein könnte.
Stops bleiben als Metadatum an der Beobachtung erhalten, sind aber bewusst **nicht** Teil
von `FlightComparisonGroup`. Spätere Versionen könnten getrennte Baselines führen
(`cheapest_any`, `nonstop`, `max_1_stop`) – aktuell gibt es nur `cheapest_any`.

**Airline:** Ebenfalls bewusst **nicht** Teil von `FlightComparisonGroup` – wir
beobachten den Marktpreis der Route, nicht einen airline-spezifischen Median. Bleibt als
Metadatum gespeichert.

**Cabin Class:** `PriceObservation.cabin_class` existiert bereits im Modell, wird aber
von keinem aktuellen Provider befüllt (immer `None`) und ist deshalb noch **nicht** Teil
von `FlightComparisonGroup`. Sobald ein Provider Cabin-Class-Daten liefert, **muss**
dieses Feld in die Gruppe aufgenommen werden – Economy und Business dürfen niemals
dieselbe historische Baseline bilden.

### Persistenz: SQLite

MVP 0.3 nutzt SQLite (`PriceHistoryRepository`, Standardpfad `data/vacation_hunter.db`):
lokal, eine einzelne Datei, kein Server, robust, später migrierbar. Alle SQL-Zugriffe
sind in dieser einen Klasse gekapselt – die restliche Business-Logik schreibt kein SQL.
Die DB-Datei ist git-ignoriert, genau wie `data/cache/` – sie ist lokaler Laufzeitzustand,
keine Projektdaten.

### Real vs Fixture Data Hygiene (MVP 0.4.1) – Kernregel

**Demo-/Fixture-Daten dürfen niemals eine echte `OWN_HISTORICAL_BASELINE` beeinflussen –
unabhängig davon, ob Route, Reisedaten, Currency oder Preis plausibel gleich aussehen.**

**Konkreter Fund:** Nachdem die erste echte SerpApi-Beobachtung (184 EUR, HAM→PMI,
02.10.–07.10.2026) gespeichert wurde, lagen in derselben lokalen `data/vacation_hunter.db`
bereits 6 Beobachtungen mit `provider="demo_fixture"` für **exakt dieselbe** Comparison
Group – aus früheren Demo-Läufen, die versehentlich dieselbe Runtime-DB nutzten. Ein
Audit (Code-Lesen + tatsächliche DB-Abfrage, nicht nur vermutet) bewies:
`PriceHistoryRepository.get_observations(...)` filtert nach Route, Reisedaten, Trip-Typ
und Currency – **nicht** nach `provider`. `get_route_statistics(...)` und
`get_historical_baseline(...)` werteten deshalb **alle 7 Beobachtungen gemeinsam** aus
(`observation_count=7`, `median=182.0`) und meldeten fälschlich eine verfügbare Baseline,
obwohl nur 1 echte Beobachtung existierte.

**Lösung: physische Trennung, keine Query-Filter-Krücke.** Ein Filter wie "ignoriere
`provider='demo_fixture'` in der Abfrage" wäre fragil (jeder neue Fixture-Provider-Name
müsste manuell ausgeschlossen werden) und hätte Section-7-Implikationen (siehe unten)
vermischt. Stattdessen: `historical_price_demo.py` schreibt seit MVP 0.4.1 **nie mehr**
in `data/vacation_hunter.db`, sondern ausschließlich in eine physisch getrennte
`data/demo_vacation_hunter.db`. Beide Dateien sind git-ignoriert. Eine spätere Query kann
die beiden Datensätze so gar nicht mehr versehentlich vermischen, weil sie nie in
derselben Datei liegen.

**Bereinigung (einmalig, MVP 0.4.1):** Die 6 `demo_fixture`-Zeilen wurden gezielt aus der
echten `data/vacation_hunter.db` gelöscht (kein vollständiges Leeren der Datenbank). Die
echte Beobachtung (184 EUR, `serpapi_google_flights`) blieb erhalten. Danach:
`observation_count=1` für diese Gruppe, `OWN_HISTORICAL_BASELINE` korrekt wieder **nicht**
verfügbar (`MIN_HISTORY_OBSERVATIONS=5`).

**Wichtige Abgrenzung – REAL vs. FIXTURE ist nicht dasselbe wie "ein Provider vs. ein
anderer":** `provider` bleibt ein reines Herkunfts-Metadatum, keine
Vergleichsgruppen-Dimension (siehe "Route Baseline" oben – Airline/Stops/Provider
fragmentieren die Gruppe bewusst nicht). Langfristig **sollen** echte Beobachtungen
verschiedener echter Provider (SerpApi, später ggf. Aviasales, Skyscanner, …) durchaus
gemeinsam in eine Marktbaseline einfließen können, wenn die Preise vergleichbar sind –
das ist erwünscht, kein Fehler. Das eigentliche Problem war ausschließlich REAL vs.
FIXTURE, nicht SerpApi vs. ein anderer echter Anbieter. Es gibt deshalb bewusst **kein**
neues "Source-Type"-Datenmodell (z. B. `is_real: bool` als Spalte) – die physische
Trennung der Demo-Datenbank löst das eigentliche Problem bereits vollständig, ohne die
Tür für spätere Multi-Provider-Baselines zuzuschlagen.

### Deduplikation

Dieselbe Beobachtung (gleiche Route, gleiche Reisedaten, gleicher Trip-Typ, gleiche
Währung, gleicher Provider, gleicher Preis) wird am selben Kalendertag nicht erneut
gespeichert – über einen `UNIQUE`-Constraint in SQLite (`INSERT OR IGNORE`). Ändert sich
der Preis am selben Tag, oder beginnt ein neuer Tag, wird eine neue Beobachtung
gespeichert. Das verhindert, dass wiederholtes Lesen derselben gecachten Suche die
Historie unnötig aufbläht – ohne komplizierte Event-Sourcing-Architektur.

**Audit-Bestätigung (MVP 0.3.1):** Diese Regel bleibt unverändert korrekt, auch nachdem
eine Beobachtung jetzt "der günstigste Preis EINES Search Snapshots" statt "irgendein
Angebot" bedeutet (siehe "Observation Semantics" oben) – maximal eine identische
Beobachtung pro Kalendertag; ein anderer Preis am selben Tag (z. B. eine erneute Suche
mit geändertem Marktpreis) erzeugt weiterhin bewusst eine neue Zeile. Wie oft künftig
tatsächlich gesucht/gemessen wird, ist noch nicht entschieden – kein Scheduler in
MVP 0.3.1.

**Frequency-Bias-Gefahr (Audit MVP 0.3.2, noch nicht gelöst):** Unsere Historie gewichtet
aktuell **Search Snapshots**, nicht Kalendertage. Wird an manchen Tagen zehnmal gesucht
(zehn unterschiedliche Preise möglich) und an anderen nur einmal, bekommen diese Tage im
Median unterschiedlich viel Gewicht – nicht weil sich der Markt anders verhalten hat,
sondern weil unterschiedlich oft gemessen wurde. Das ist für MVP 0.3.2 kein Problem, weil
es noch keine automatische Datensammlung gibt (jede Beobachtung entsteht aktuell manuell/
kontrolliert). **Architekturhinweis für später:** Sobald eine automatische Datensammlung
kommt, muss ein fester Sampling-Rhythmus definiert werden (z. B. eine Observation pro
Comparison Group pro geplantem Messzeitpunkt, nicht "so oft wie zufällig gesucht wird").
Kein Scheduler in MVP 0.3.2 – nur dokumentiert.

### Statistik-Engine

`engine/price_statistics.py` berechnet für eine Gruppe von Beobachtungen: Anzahl,
Minimum, Maximum, Mean, Median, 25./75. Perzentil, Standardabweichung. Kein Machine
Learning.

**Warum Median, nicht Mean:** Flugpreise haben Ausreißer. Beispiel:

```
Beobachtungen: 170, 180, 175, 185, 800
Mean:   302   (stark verzerrt durch den Ausreißer)
Median: 180   (bleibt sinnvoll)
```

Die `OWN_HISTORICAL_BASELINE` verwendet deshalb den Median als Vergleichswert.

### Route Baseline – bewusst strenge Vergleichsgruppe (MVP 0.3)

Beobachtungen werden **nicht** einfach alle für "HAM → PMI" in einen Topf geworfen. Die
Vergleichsgruppe für MVP 0.3 ist bewusst streng:

```
gleiche Route (Origin + Destination)
+ exakt gleiches departure_date
+ exakt gleiches return_date
+ gleicher Trip-Typ (One-way vs. Roundtrip)
+ gleiche Currency
```

Das ist strenger als nötig für viele reale Fälle (z. B. "gleicher Monat" oder "ähnliche
Aufenthaltsdauer" wären für mehr Daten pro Gruppe sinnvoll), aber für MVP 0.3 die
sicherste, am wenigsten fehleranfällige Wahl. Eine spätere Version kann diese Gruppierung
lockern.

### Mindestanzahl an Beobachtungen

Vacation Hunter behauptet nicht nach zwei Beobachtungen "das ist der Normalpreis".
`MIN_HISTORY_OBSERVATIONS = 5` (`engine/price_statistics.py`) ist die Mindestanzahl für
eine `OWN_HISTORICAL_BASELINE`. Darunter: keine Baseline, kein Raten – `get_historical_baseline(...)`
gibt `None` zurück, die Deal Engine fällt auf die nächste Baseline-Quelle zurück (siehe
"Ablauf pro Flug" oben).

### `historical_position`

Zusätzlich zur reinen Ersparnis wird eingeordnet, wo der aktuelle Preis relativ zur
eigenen Historie liegt (Interquartilsabstand p25–p75):

| `HistoricalPosition` | Bedeutung |
|---|---|
| `BELOW_HISTORY` | Preis liegt unter dem 25. Perzentil unserer Historie. |
| `WITHIN_HISTORY` | Preis liegt im mittleren Bereich (p25–p75). |
| `ABOVE_HISTORY` | Preis liegt über dem 75. Perzentil unserer Historie. |

Zusätzlich: `percent_diff_from_median` (negativ = günstiger als unser Median). Beide
Werte sind rein informativ – noch keine Änderung an der Trip-Score-Formel.

### Noch keine automatische Datensammlung

`observation_from_search_results(...)` (empfohlen) bzw. `observation_from_flight_offer(...)`
(Low-Level-Baustein, siehe "Observation Semantics" oben) in `price_history_repository.py`
wandeln FlightOffer-Daten in `PriceObservation` um – als vorbereitete Hooks für später.
Weder MVP 0.3 noch MVP 0.3.1 ruft diese Funktionen **automatisch** bei jeder echten Suche
auf; das würde erst mal nur beweisen wollen, dass die Datenhaltung funktioniert. Kein
Hintergrundjob, kein Scheduler.

### Controlled Historical Sampling (MVP 0.4.2)

Seit MVP 0.4.2 gibt es einen **manuellen** Befehl, um genau einen kontrollierten
Messpunkt zu erzeugen:

```bash
python -m vacation_hunter.record_price_snapshot \
  --origin HAM \
  --destination PMI \
  --departure 2026-10-02 \
  --return 2026-10-07 \
  --currency EUR
```

Ablauf pro Ausführung: eine echte Suche → eine explizite `FlightComparisonGroup` (aus
genau diesen Parametern, keine Heuristik) → `observation_from_search_results(...)`
(niemals `observation_from_flight_offer(...)` in einer Schleife) → höchstens eine
`PriceObservation` → `data/vacation_hunter.db`. Kein Scheduler, keine automatische
Wiederholung, keine Routen-Schleifen.

**Validierung vor dem Speichern:** Es wird nur gespeichert, wenn mindestens ein
gefundenes Angebot exakt zur Comparison Group passt (Route, Departure, Return, Currency)
**und** `price_confirmed_complete=True` ist. Sonst: keine Observation, mit klarer
Begründung in der Ausgabe.

**Dedup:** Nutzt ausschließlich die bestehende Repository-Regel (siehe "Deduplikation"
oben). Eine exakte Wiederholung am selben Kalendertag erhöht den Observation Count nicht
– die Ausgabe zeigt dann `Stored: NO` mit Begründung.

**Live vs. Cache erkennbar:** Die Ausgabe zeigt `Source: LIVE RESPONSE` oder
`Source: CACHE HIT`, ermittelt durch einen nicht-destruktiven Blick in den bestehenden
Datei-Cache (`FileCache.get(...)`, derselbe Cache-Key wie der Provider intern
verwendet) **bevor** die eigentliche Suche läuft – kein Eingriff in die
Provider-Implementierung nötig.

**Provider Price Insight** wird, falls vorhanden, separat und deutlich als
"context only – not stored as our data" angezeigt (Provider Lowest Price, Typical
Range, Price Level) – niemals als eigene `PriceObservation` gespeichert.

**Wichtiger Sampling-Hinweis:** Der Befehl ist bewusst **manuell**. Mehrfaches
Ausführen kurz hintereinander (z. B. fünfmal in wenigen Minuten) ist **nicht** der
vorgesehene Weg, eine `OWN_HISTORICAL_BASELINE` aufzubauen – das erzeugt entweder nur
Duplikate (Dedup greift) oder täuscht bei echten Preisschwankungen eine Historie über
Zeit vor, wo eigentlich nur wenige Minuten vergangen sind. Sinnvolle Historie entsteht
durch zeitlich getrennte Search Snapshots (z. B. an fünf verschiedenen Tagen), nicht
durch fünf Requests hintereinander. MVP 0.4.2 erzwingt noch keinen festen Rhythmus –
das bleibt bewusst der Nutzerin/dem Nutzer überlassen, bis ein späterer, expliziter
Scheduler-Schritt das übernimmt.

**Architekturhinweis Multi-Provider (noch nicht implementiert):** Fragt ein späterer,
einzelner geplanter Messzeitpunkt mehrere echte Provider ab (z. B. SerpApi + Aviasales +
Skyscanner), dürfen diese nicht automatisch als mehrere unabhängige Zeitbeobachtungen
gezählt werden – sonst entsteht derselbe Bias wie bei "alle Angebote einer Suche als
eigene Beobachtung" (siehe "Observation Semantics" oben), nur eine Ebene höher. Ein
Measurement Snapshot über mehrere Provider sollte langfristig vermutlich ebenfalls auf
den einen günstigsten vergleichbaren Marktpreis dieses Zeitpunkts reduziert werden. Für
MVP 0.4.2 nur dokumentiert, nicht gebaut.

## Deal Detection aus Price Insights: bewusst vorsichtig

Beispiel: Google zeigt eine typische Preisspanne von 160–220 EUR, der aktuelle Preis
liegt bei 89 EUR. Das ist offensichtlich auffällig günstig – aber:

- Eine Price Insight ist eine Schätzung eines Drittanbieters, keine belastbare eigene
  Statistik. Deshalb darf sie **niemals allein** zu `ERROR_FARE` führen, selbst bei
  extremen Abweichungen (getestet: 90 % unter der typischen Spanne ergibt weiterhin nur
  `FLIGHT_DROP`, nicht `ERROR_FARE`).
- Die bestehenden Schwellenwerte (`>= 30 %` → `FLIGHT_DROP`, `>= 15 %` → `UNUSUALLY_LOW`)
  werden wiederverwendet, angewendet auf die Mitte der typischen Preisspanne als
  Vergleichswert.
- Eine eigene, strengere Error-Fare-Heuristik ist bewusst **nicht** Teil von MVP 0.2.1.

## Price Completeness (seit MVP 0.2.2, verifiziert in MVP 0.2.3)

Ein zweites, von der Baseline unabhängiges Problem: Bevor wir überhaupt einen Preis mit
irgendeinem Vergleichswert vergleichen dürfen, müssen wir sicher sein, dass dieser Preis
den **vollständigen relevanten Trip-Preis** abbildet.

**Ursprünglicher Fund (echter Live-Test, HAM → PMI, 02.10.–07.10.2026):** Unser Provider
meldete für den günstigsten Flug 184 EUR, während Googles `price_insights` eine typische
Roundtrip-Preisspanne von 205–385 EUR zeigte. Die Deal Engine hat daraus zunächst
`FLIGHT_DROP` (38 % Ersparnis) berechnet – ohne dass zu diesem Zeitpunkt gesichert war, ob
184 EUR wirklich der vollständige Roundtrip-Preis ist.

Der Grund für die Unsicherheit: SerpApis Google-Flights-Engine liefert Roundtrip-Ergebnisse
zweistufig – eine erste Suche liefert Optionen plus einen `departure_token` pro Option;
erst ein zweiter Request mit diesem Token liefert passende Rückflug-Optionen. Weder SerpApis
eigene Dokumentation noch mehrere unabhängige Drittquellen gaben eine eindeutige,
autoritative Bestätigung, ob der `price`-Wert aus Schritt 1 bereits der vollständige
Roundtrip-Preis ist. Wir haben deshalb vorübergehend **kein** Angebot mit irgendeinem
Vergleichswert verglichen (`PRICE_INCOMPLETE`), bis das geklärt ist.

**Verifikation (kontrollierter, vom Nutzer freigegebener Live-Test, 2 SerpApi-Credits,
HAM → PMI, 02.10.–07.10.2026):**

- Preis aus Schritt 1: 184 EUR
- Günstigste passende Rückflugoption aus dem `departure_token`-Follow-up: 184 EUR
- Differenz: 0 EUR
- Andere Rückflugoptionen im selben Follow-up zeigten jeweils eigene, bereits vollständige
  Roundtrip-Gesamtpreise (z. B. 279 EUR, 303 EUR) – keine Aufpreise auf 184 EUR.

**Ergebnis:** Verified for the currently observed and supported SerpApi Google Flights
round-trip response format – der `price`-Wert aus Schritt 1 ist bereits der vollständige
Roundtrip-Gesamtpreis für die günstigste passende Rückflugoption. Das ist **keine**
allgemeine Aussage über SerpApi oder Google Flights insgesamt, sondern eine Verifikation
für genau das Antwortformat, das `SerpApiGoogleFlightsProvider` aktuell normalisiert.
Ausdrücklich **nicht** gemeint ist: "SerpApi prices are always complete."

**Aktuelles Verhalten:** Jedes Angebot, das `SerpApiGoogleFlightsProvider` erfolgreich
normalisiert (valider, parsebarer `price`-Wert), trägt `price_confirmed_complete=True` –
sowohl Ein-Weg- als auch Roundtrip-Suchen. Kann ein Preis nicht geparst werden, wird das
Angebot weiterhin verworfen (nicht als "vollständig" behandelt).

**`PRICE_INCOMPLETE` bleibt bestehen** – als providerübergreifende Sicherheitsregel für:
- andere/zukünftige Provider ohne diese Verifikation
- unklare oder neue Response-Formate
- jede Datenquelle mit unbestätigter Preisvollständigkeit

Ein Flugangebot trägt dafür weiterhin ein Flag `price_confirmed_complete`. Ist es `False`,
wird der Preis **mit keinem Vergleichswert verglichen** – weder eigener Baseline noch
Provider Price Insight. Die Deal Engine bricht die Bewertung für dieses Angebot sofort mit
`PRICE_INCOMPLETE` ab, bevor überhaupt eine Baseline nachgeschlagen wird. Diese Prüfung
steht bewusst **vor** der Baseline-Prüfung: Ein unvollständiger Preis mit falscher Baseline
wäre kein bisschen besser als einer mit korrekter Baseline – der Vergleich selbst ist
ungültig, unabhängig von der Baseline-Qualität.

**Alte Cache-Einträge** (vor MVP 0.2.2 geschrieben) enthalten das Feld
`price_confirmed_complete` nicht. Beim Laden aus dem Cache wird ein fehlendes Feld
weiterhin defensiv als `False` behandelt – nicht automatisch als `True` –, weil unklar ist,
unter welcher Provider-/Codeversion so ein Eintrag entstanden ist. Neue Cache-Einträge von
`SerpApiGoogleFlightsProvider` tragen das Feld explizit mit `True`.

**Randnotiz Rückflugdatum (kein aktueller Blocker):** Beim Verifikationstest kam eine
Rückflugoption zurück, die am 07.10. abfliegt, aber wegen Nachtverbindung erst am 08.10.
ankommt. "Return search date" (das angefragte Rückreisedatum) und "final arrival date"
(tatsächliche Ankunftszeit des letzten Segments) sind unterschiedliche Konzepte. Das ist
aktuell unproblematisch, da wir keinen vollautomatischen `departure_token`-Flow bauen –
falls das später kommt, muss dieser Unterschied bei der Modellierung von `return_date`
berücksichtigt werden.

## Baseline-Problem (seit MVP 0.2)

Unsere Deal Detection braucht immer einen Vergleichswert: den "üblichen" Preis
(`expected_flight_price` / Baseline), gegen den wir den **aktuellen Preis**
(`current_price`, im Modell `FlightOffer.price`) prüfen. Diese beiden Dinge sind
bewusst getrennt:

- **Current Price** – das, was eine Flugsuche gerade jetzt zurückgibt. Das kann jeder
  Provider liefern, auch ein einfacher Suchdienst ohne Historie.
- **Expected/Baseline Price** – der "normale" Preis für diese Route, basierend auf
  historischen Daten. Das kann **nur** ein Provider liefern, der solche Historie
  tatsächlich hat.

Unsere Mock-Provider tun so, als hätten sie diese Historie (sie ist für Testzwecke
hart hinterlegt). Eine echte Flugsuch-API wie Amadeus liefert das **nicht** – sie
zeigt nur aktuelle Angebote, keine Preisstatistik.

**Deshalb gilt als feste Regel:** Wenn `FlightProvider.get_typical_price(...)` `None`
zurückgibt (kein Baseline-Wert bekannt), wird der Flug **niemals** als `FLIGHT_DROP`,
`ERROR_FARE` oder `UNUSUALLY_LOW` eingestuft – unabhängig davon, wie günstig er
aussieht. Stattdessen bekommt er den Deal-Typ `BASELINE_UNAVAILABLE`. Das ist absichtlich
"unspektakulär": Wir zeigen ehrlich, dass wir es nicht wissen, statt zu raten. Das ist für
die spätere Produktqualität entscheidend – ein System, das ständig falsche Alarme auslöst,
verliert das Vertrauen der Nutzer sehr schnell.

`AmadeusFlightProvider.get_typical_price(...)` gibt deshalb aktuell immer `None` zurück.
Ein echter Preisvergleich für reale Flüge ist erst möglich, sobald wir selbst historische
Preisdaten sammeln (z. B. durch wiederholte Suchen über die Zeit) – das ist bewusst
**kein** Teil von MVP 0.2.

## MVP 0.1 – Umfang

**Enthalten:**
- Datenmodelle, Provider-Interfaces, Mock-Provider mit Testdaten
- Regelbasierte Deal Detection (Flug, Hotel, kombiniert)
- Regelbasierter Trip Score
- Automatisierte Tests
- Terminal-Demo

**Explizit nicht enthalten:**
- Echte/kostenpflichtige APIs (Amadeus, Booking.com, …)
- Zugangsdaten/Secrets
- Frontend/UI
- Zahlungen
- Nutzerkonten
- Mobile App
- Machine Learning

## MVP 0.2 – Umfang

**Ziel:** Vacation Hunter kann erstmals echte Flugdaten verarbeiten, ohne die
Provider-Unabhängigkeit aufzugeben.

**Enthalten:**
- `AmadeusFlightProvider` – ein echter, austauschbarer `FlightProvider`
- Normalisierung von Amadeus-API-Antworten in unser `FlightOffer`-Modell
- Konfiguration/Secrets ausschließlich über `VACATION_HUNTER_*` Environment-Variablen
- Einfaches lokales Datei-Caching mit konfigurierbarer TTL (Standard: 24 Stunden)
- Fehlerbehandlung für Timeout, HTTP-Fehler, Rate Limit, kaputtes JSON, fehlende Felder
- Klare Trennung Current Price vs. Baseline Price, `BASELINE_UNAVAILABLE` (siehe oben)
- Zweiter Terminal-Demo-Flow für echte Flugdaten (`real_flight_demo`)

**Explizit nicht enthalten:**
- Echte Hotel-API
- Website/Frontend, Nutzerkonten, Zahlungen, Push Notifications, Newsletter, Mobile App
- Datenbankserver, Redis, Message Queues, Microservices
- Machine Learning, Web Scraping
- Automatisches massenhaftes Scannen vieler Flughäfen/Routen
- Error-Fare-Heuristik ohne echte Baseline (siehe Baseline-Problem oben)

## MVP 0.2.1 – Umfang

**Ziel:** Amadeus' Self-Service-Zugang ist für neue Entwickler weggefallen; Vacation
Hunter bekommt einen neuen, aktiven Real-Flight-Provider (SerpApi Google Flights) und
kann dessen Price Insights als vorsichtige Zusatz-Baseline nutzen – ohne die
Provider-Unabhängigkeit der Deal Engine aufzugeben.

**Enthalten:**
- `SerpApiGoogleFlightsProvider` – neuer aktiver `FlightProvider`
- `AmadeusFlightProvider` bleibt erhalten, ist aber nicht mehr aktiv genutzt
- `PriceInsight`-Modell, providerunabhängig
- `BaselineSource` (`OWN_HISTORICAL_BASELINE` / `PROVIDER_PRICE_INSIGHT` / `NO_BASELINE`)
- Vorsichtige, gedeckelte Deal Detection aus Price Insights (siehe oben)
- `VACATION_HUNTER_SERPAPI_KEY` Konfiguration, `.env.example` aktualisiert
- Cache-Key erweitert um Currency; API-Credit-Sicherheit (siehe unten)
- Dritter Terminal-Demo-Flow: `serpapi_flight_demo`

**API Credit Safety:** SerpApi hat ein begrenztes monatliches Suchkontingent. Deshalb
gilt für MVP 0.2.1 strikt:
- Kein automatisches Scannen mehrerer Flughäfen oder Datumsvarianten.
- Keine Lasttests gegen die echte API.
- Ein Demo-Aufruf verbraucht **höchstens eine** Live-Suche (weniger bei Cache-Treffer):
  `search_flights(...)` und `get_price_insight(...)` teilen sich denselben Cache-Eintrag
  für dieselbe Suche, statt zwei separate Anfragen auszulösen.
- Alle Tests laufen ausschließlich gegen gemockte HTTP-Antworten, nie gegen die echte API.

**Explizit nicht enthalten:**
- Echte Hotel-API, Aviasales-Integration
- Automatische Routen-Scans, Hintergrundjobs
- Datenbank, Nutzerkonten, Website, Zahlungen, Newsletter
- Machine Learning, eigene historische Price Intelligence
- Error-Fare-Heuristik (auch nicht aus Price Insights, siehe oben)

## MVP 0.2.2 – Umfang

**Ziel:** Der erste echte Live-Test deckte einen Widerspruch auf (184 EUR vs. eine
typische Roundtrip-Spanne von 205–385 EUR) und führte zur Erkenntnis, dass wir die
Vollständigkeit des Roundtrip-Preises aus SerpApis erstem Suchschritt nicht verifizieren
konnten. MVP 0.2.2 behebt ausschließlich diese Preissemantik-Frage – siehe "Price
Completeness" oben.

**Enthalten:**
- Neuer Deal-Typ `PRICE_INCOMPLETE` (Sicherheitsregel: inkompatible Preisarten werden nie
  verglichen)
- `FlightOffer.price_confirmed_complete` (Default `True`, für SerpApi-Roundtrips `False`)
- Umbenennung `PriceInsight.current_price` → `provider_lowest_price` (klarere Semantik,
  siehe oben)
- Korrektur des Rückflugdatum-Fallbacks (`return_date` statt `departure_date`, siehe
  Commit-Historie) – hatte nebenbei einen ungewollten zweiten Live-Call verursacht
- Regressionstests mit anonymisierter echter Response-Struktur

**Explizit nicht enthalten:**
- Der zweite, `departure_token`-basierte SerpApi-Request zur Bestätigung des vollen
  Roundtrip-Preises (bewusst nicht automatisch ausgeführt – Kreditkosten, siehe API
  Credit Safety)
- Alles aus "Noch nicht implementieren" der vorherigen MVPs

## MVP 0.2.3 – Umfang

**Ziel:** Die in MVP 0.2.2 offene Frage (ist der SerpApi-Roundtrip-Preis aus Schritt 1
vollständig?) wurde durch einen einmaligen, vom Nutzer explizit freigegebenen und
kreditbegrenzten `departure_token`-Live-Test verifiziert – siehe "Price Completeness"
oben.

**Enthalten:**
- `SerpApiGoogleFlightsProvider` setzt `price_confirmed_complete=True` für jedes
  erfolgreich normalisierte Angebot (Ein-Weg und Roundtrip)
- Aktualisierte Dokumentation des Verifikationsergebnisses (Price Completeness)
- Tests, die die verifizierte Semantik sowie das weiterhin bestehende
  `PRICE_INCOMPLETE`-Sicherheitsnetz für generische/zukünftige Fälle abdecken

**Explizit nicht enthalten:**
- Entfernung von `DealType.PRICE_INCOMPLETE`, `FlightOffer.price_confirmed_complete` oder
  der Sicherheitsprüfung in der Deal Engine – diese bleiben als providerübergreifendes
  Sicherheitsnetz bestehen
- Ein automatisierter `departure_token`-Flow (weiterhin kreditkostend, weiterhin manuell)
- Weitere Live-Calls über die genau 2 im Verifikationstest hinaus
- Alles aus "Noch nicht implementieren" der vorherigen MVPs

## MVP 0.3 – Umfang

**Ziel:** Grundlage für eine echte, eigene `OWN_HISTORICAL_BASELINE` schaffen – siehe
"Historical Price Intelligence" oben. Kein Hotel-API, kein automatisches Scannen, kein
Scheduler, kein Newsletter, kein Frontend, keine Payments.

**Enthalten:**
- `PriceObservation`, `PriceStatistics`, `HistoricalPosition`, `HistoricalBaseline`
  (provider-unabhängige Modelle, `models.py`)
- `PriceHistoryRepository` (SQLite, `price_history_repository.py`): `add_observation`,
  `get_observations`, `get_route_statistics`, Deduplikation
- `engine/price_statistics.py`: `compute_statistics`, `classify_position`,
  `percent_diff_from_median`, `get_historical_baseline`, `MIN_HISTORY_OBSERVATIONS = 5`
- `DealEngine` nutzt eigene Historie mit Priorität vor Provider Price Insight;
  `PRICE_INCOMPLETE` bleibt allen Baseline-Mechanismen übergeordnet
- Vierter Demo-Flow ganz ohne Live-API: `historical_price_demo`
- Vorbereiteter (nicht aktiver) Hook `observation_from_flight_offer(...)`

**Explizit nicht enthalten:**
- Automatisches Speichern von Beobachtungen bei jeder echten Suche
- Hintergrundjobs, Scheduler, automatische Routen-Scans
- Hotel-API, Aviasales-Integration, Frontend, Payments, Newsletter, Nutzerkonten
- Machine Learning
- Lockerung der Vergleichsgruppe (z. B. "gleicher Monat") – bewusst für später
  zurückgestellt

## MVP 0.3.1 – Umfang

**Ziel:** Vor der ersten echten Datensammlung klären, was eine `PriceObservation`
tatsächlich repräsentiert – siehe "Observation Semantics" oben. Ausgelöst durch einen
Audit: Der bestehende Hook hätte, naiv über alle Angebote einer Suche angewendet, die
Baseline zum Median **aller Angebote einer Suche** statt zum Median der **günstigsten
Preise über mehrere Zeitpunkte** gemacht – statistisch verzerrt und konzeptionell falsch
für einen Deal-Hunter.

**Enthalten:**
- Kernregel dokumentiert: ein Search Snapshot → maximal eine Marktbeobachtung
- Neuer Helfer `observation_from_search_results(offers, trip_type, observed_at)` –
  filtert unvollständige/nicht-vergleichbare Angebote, wählt das günstigste, erzeugt
  genau eine `PriceObservation`
- `observation_from_flight_offer(...)` bleibt als Low-Level-Baustein bestehen, jetzt mit
  ausdrücklicher Warnung im Docstring vor Schleifen-Missbrauch
- Bewusste Entscheidung dokumentiert: `cheapest_any` (Stops/Airline fragmentieren die
  Vergleichsgruppe nicht), Cabin Class vorbereitet, aber noch nicht genutzt
- Demo zeigt jetzt explizit mehrere Tages-Snapshots mit mehreren Angeboten pro Tag statt
  sechs beliebiger Einzelwerte
- Regressionstests für Snapshot-Reduktion (u. a.: 30 Angebote in einer Suche zählen nicht
  als 30 Zeitbeobachtungen)

**Explizit nicht enthalten:**
- Neues Datenmodell (`MarketPriceObservation` o. ä.) – bewusst nicht, da
  `PriceObservation` bereits die richtige Semantik trägt
- Live-API-Aufrufe, automatische Datensammlung, Scheduler, Hotel-API
- Tatsächliche Nutzung von Cabin Class in der Gruppierung (kein Provider liefert sie)

## MVP 0.3.2 – Umfang

**Ziel:** Die "größte Gruppe gewinnt"-Heuristik aus MVP 0.3.1 durch eine explizite
Comparison Group ersetzen – siehe "Explicit Comparison Groups" oben. Ausgelöst durch
einen weiteren Audit: Eine Liste mit Angeboten für mehrere Reisedaten hätte automatisch
die zahlenmäßig größte Gruppe gewinnen lassen – fachlich potenziell die falsche.

**Enthalten:**
- Neues Modell `FlightComparisonGroup` (`origin`, `destination`, `departure_date`,
  `return_date`, `trip_type`, `currency`) mit Selbstvalidierung (`trip_type` muss zu den
  Daten passen)
- `observation_from_search_results(offers, comparison_group, observed_at)` – keine
  Heuristik mehr, ausschließlich exakte Übereinstimmung mit der expliziten Gruppe;
  `None`, wenn nichts passt
- Präzisierte Search-Snapshot-Regel: maximal eine Observation **pro expliziter
  Comparison Group**, nicht pro API-Request
- Frequency-Bias-Risiko für künftige automatische Datensammlung dokumentiert (siehe
  "Deduplikation" oben) – noch nicht gelöst, kein Scheduler
- Demo zeigt die Comparison Group jetzt explizit vor den Search Snapshots
- Regressionstests: explizite Gruppe schlägt größere fremde Gruppe, keine passende Gruppe
  → `None`, falsche Route/Reisedaten/Currency werden je einzeln ignoriert

**Explizit nicht enthalten:**
- Live-API-Aufrufe, automatische Datensammlung, Scheduler, Hotel-API
- Lösung des Frequency-Bias-Problems (nur dokumentiert)
- Tatsächliche Nutzung von Cabin Class in der Gruppierung

## MVP 0.4 – Umfang

**Ziel:** Erstmals genau eine echte historische Preisbeobachtung aus einem echten
SerpApi Search Snapshot speichern – kontrollierter End-to-End-Beweis der gesamten
Pipeline (echte Suche → mehrere FlightOffers → explizite FlightComparisonGroup →
`observation_from_search_results(...)` → genau eine `PriceObservation` → SQLite).

**Ergebnis:** HAM → PMI, 02.10.–07.10.2026, 184 EUR, Vueling, 1 Stopp,
`provider=serpapi_google_flights`, erfolgreich in `data/vacation_hunter.db` gespeichert.
Kein Code musste geändert werden – die bestehende Pipeline funktionierte wie entworfen.
Nebenbefund (führte zu MVP 0.4.1): In derselben DB lagen bereits 6 `demo_fixture`-Zeilen
für dieselbe Comparison Group.

**Explizit nicht enthalten:** Automatische Sammlung, Scheduler, Route-Schleifen, Hotels,
weitere Live-Calls.

## MVP 0.4.1 – Umfang

**Ziel:** Verhindern, dass Demo-/Fixture-Daten jemals eine echte `OWN_HISTORICAL_BASELINE`
verfälschen – siehe "Real vs Fixture Data Hygiene" oben. Ausgelöst durch den in MVP 0.4
entdeckten Nebenbefund.

**Enthalten:**
- Audit bewiesen (Code + echte DB-Abfrage): `get_observations(...)` filtert nicht nach
  `provider` – 7 gemischte Beobachtungen wurden fälschlich gemeinsam ausgewertet
- `historical_price_demo.py` nutzt jetzt eine physisch getrennte
  `data/demo_vacation_hunter.db`, niemals mehr die echte Runtime-DB
- Bestehende `data/vacation_hunter.db` bereinigt: 6 `demo_fixture`-Zeilen entfernt, die 1
  echte Beobachtung (184 EUR) erhalten
- Abgrenzung dokumentiert: REAL vs. FIXTURE ≠ SerpApi vs. anderer echter Provider –
  `provider` bleibt reines Metadatum, kein neues Source-Type-Modell
- Cache-Versionierungsproblem aus MVP 0.4 dokumentiert (siehe `docs/ARCHITECTURE.md`)
- Regressionstests für die kontaminierte/bereinigte Szenarien

**Explizit nicht enthalten:**
- Live-API-Aufrufe, automatische Datensammlung, Scheduler
- Neues Source-Type-Datenmodell
- Cache-Schema-Versionierung im Code (nur dokumentiert, siehe `docs/ARCHITECTURE.md`)

## MVP 0.4.2 – Umfang

**Ziel:** Einen sauberen, manuellen Sampling-Befehl bauen, mit dem später einzelne,
kontrollierte Messpunkte erzeugt werden können – siehe "Controlled Historical Sampling"
oben. Kein Scheduler, keine automatische Wiederholung, keine Routen-Schleifen.

**Enthalten:**
- `python -m vacation_hunter.record_price_snapshot --origin ... --destination ...
  --departure ... --return ... --currency ...` (argparse, kein zusätzliches
  CLI-Framework)
- Baut aus genau diesen Parametern eine explizite `FlightComparisonGroup`
- Genau eine logische Suche pro Ausführung, `observation_from_search_results(...)`
  (nie `observation_from_flight_offer(...)` in einer Schleife)
- Sichtbare Unterscheidung `Source: LIVE RESPONSE` / `Source: CACHE HIT` über einen
  nicht-destruktiven Cache-Key-Check vor der Suche
- Speichert ausschließlich nach `data/vacation_hunter.db`, nie nach der isolierten
  Demo-DB aus MVP 0.4.1 (keine Import-Abhängigkeit zu `historical_price_demo.py`)
- Nutzt bestehende Dedup-Regel und bestehende `get_historical_baseline(...)`-Logik
  unverändert; zeigt `N / 5 required` und `AVAILABLE`/`NOT AVAILABLE YET`
- Provider Price Insight wird angezeigt, aber getrennt von "our historical data" und
  niemals als `PriceObservation` gespeichert
- Sampling-Hinweis (manuell, kein Ersatz für zeitlich verteilte Messungen) und
  Multi-Provider-Architekturhinweis dokumentiert, nicht implementiert

**Explizit nicht enthalten:**
- Live-API-Aufrufe während der Implementierung/Tests (vollständig gemockt)
- Scheduler, Cron, Hintergrundjob, automatische Wiederholung
- Multi-Provider-Messzeitpunkt-Logik (nur dokumentiert)
- Routen- oder Datums-Schleifen

## Beispiel-Szenario (aus der Anforderung)

```
Origin: HAM   Destination: PMI   Dates: 02.10.–07.10.

Flight price: 79 EUR        Expected flight price: 180 EUR
Hotel total: 205 EUR        Expected hotel total: 340 EUR
Trip total: 284 EUR         Expected trip total: 520 EUR

Savings: 236 EUR (45 %)
Result: COMBINED_TRIP_DROP
```

Dieses Szenario ist 1:1 als Testdatensatz in den Mock-Providern hinterlegt
(`providers/mock_flight_provider.py`, `providers/mock_accommodation_provider.py`) und wird
von einem Integrationstest sowie vom Demo-Flow abgedeckt.
