# Architecture – Vacation Hunter

## Warum Python für MVP 0.1

- Datenmodelle als `dataclasses` sind fast wie eine lesbare Tabelle – gut geeignet, wenn
  man (wie wir hier) technische Entscheidungen für einen Nicht-Techniker nachvollziehbar
  halten will.
- `pytest` ist Industriestandard und braucht kaum Konfiguration.
- Keine zusätzliche Infrastruktur nötig: alles läuft im Arbeitsspeicher, keine Datenbank,
  kein Webserver, kein Framework. Das MVP soll die Kernidee beweisen, nicht Infrastruktur
  aufbauen.
- Lässt sich später sauber erweitern (z. B. FastAPI für eine API, PostgreSQL für
  Persistenz), ohne die Kernlogik neu zu schreiben – weil diese von Providern und
  Speicherung entkoppelt ist (siehe unten).

## Grundprinzip: Provider-Abstraktion

Die Business-Logik (Deal Detection, Scoring, Trip-Kombination) kennt **keine** konkrete
Datenquelle. Sie spricht ausschließlich mit zwei Interfaces:

- `FlightProvider` – liefert Flugangebote und einen "üblichen" Preis pro Route.
- `AccommodationProvider` – liefert Unterkunftsangebote und einen "üblichen" Preis pro
  Zielort/Aufenthaltsdauer.

```
DealEngine
    ├── FlightProvider (Interface)
    │       └── MockFlightProvider              (MVP 0.1)
    │       └── AmadeusFlightProvider            (MVP 0.2 - nicht mehr aktiv genutzt,
    │       │                                      siehe PRODUCT_SPEC.md)
    │       └── SerpApiGoogleFlightsProvider     (MVP 0.2.1 - aktiver Real-Flight-Provider)
    │       └── ...weitere Anbieter              (später)
    │
    └── AccommodationProvider (Interface)
            └── MockAccommodationProvider    (MVP 0.1)
            └── NullAccommodationProvider    (MVP 0.2 – Platzhalter, solange
            │                                  es noch keine echte Hotel-API gibt)
            └── BookingAccommodationProvider  (später)
            └── ...weitere Anbieter          (später)
```

Ein neuer Anbieter bedeutet: eine neue Klasse schreiben, die das Interface implementiert.
Die Deal Engine, die Scoring-Logik und alle Tests bleiben unverändert. Das ist der Grund,
warum diese Abstraktion von Anfang an existiert, obwohl wir noch keine echte API
anbinden – ein späterer Wechsel oder das Hinzufügen weiterer Quellen soll kein Rewrite
der Kernlogik erzwingen.

## Warum die Baseline-Preise Teil des Providers sind

Um zu wissen, ob ein Preis "außergewöhnlich günstig" ist, braucht man einen
Vergleichswert – den "üblichen" Preis für Route/Zielort. In der Realität kommt dieser aus
historischen Preisdaten der jeweiligen Quelle. Deshalb liefert jeder Provider neben den
aktuellen Angeboten auch `get_typical_price(...)` bzw. `get_typical_total_price(...)`.
Die Deal Engine selbst weiß nicht, *wie* dieser Wert zustande kommt – nur, dass sie ihn
zum Vergleich nutzen kann. Das hält die Berechnungslogik unabhängig von der Datenquelle.

## Modulübersicht

```
src/vacation_hunter/
    models.py                      Datenmodelle: FlightOffer, AccommodationOffer,
                                    Trip, DealType, Deal, DealScore
    config.py                      Liest VACATION_HUNTER_* Environment-Variablen,
                                    lädt optional eine lokale .env-Datei
    caching.py                     Einfacher lokaler Datei-Cache mit TTL
    price_history_repository.py    SQLite-Speicher für eigene Preisbeobachtungen
                                    (MVP 0.3) - die einzige Stelle mit SQL

    providers/
        flight_provider.py         Interface FlightProvider
        accommodation_provider.py  Interface AccommodationProvider
        errors.py                  Fehlerklassen für externe Provider (Timeout,
                                    HTTP-Fehler, Rate Limit, kaputte Antwort)
        mock_flight_provider.py    Testdaten-Implementierung
        mock_accommodation_provider.py
        null_accommodation_provider.py  Platzhalter ohne Hotel-Daten (MVP 0.2)
        amadeus_client.py          Rohes HTTP/OAuth2 gegen die Amadeus-API,
                                    kennt kein FlightOffer (nicht mehr aktiv genutzt)
        amadeus_flight_provider.py Amadeus-FlightProvider (nicht mehr aktiv genutzt,
                                    siehe PRODUCT_SPEC.md)
        serpapi_client.py          Rohes HTTP gegen die SerpApi Google-Flights-Engine,
                                    kennt weder FlightOffer noch PriceInsight
        serpapi_flight_provider.py Aktiver FlightProvider: normalisiert SerpApi-JSON zu
                                    FlightOffer/PriceInsight, nutzt den Cache für beides

    engine/
        flight_deal_detector.py    Regelbasierte Bewertung einzelner Flugangebote
                                    (inkl. BASELINE_UNAVAILABLE-Fall)
        hotel_deal_detector.py     Regelbasierte Bewertung einzelner Unterkunftsangebote
        trip_combiner.py           Kombiniert Flug + Unterkunft zu einem Trip,
                                    berechnet Gesamtersparnis
        scoring.py                 Berechnet den Trip Score (0–100)
        price_statistics.py        Statistik über eigene Preisbeobachtungen: Median,
                                    Perzentile, Mindestanzahl, Historical Position (MVP 0.3)
        deal_engine.py             Orchestriert den gesamten Ablauf (siehe unten)

    demo.py                        Terminal-Demo mit Mock-Daten (MVP 0.1)
    real_flight_demo.py            Terminal-Demo mit echten Amadeus-Flugdaten
                                    (MVP 0.2, nicht mehr der aktive Real-Flight-Demo)
    serpapi_flight_demo.py         Terminal-Demo mit echten Google-Flights-Daten über
                                    SerpApi (MVP 0.2.1, aktiver Real-Flight-Demo)
    historical_price_demo.py       Terminal-Demo für die eigene Preishistorie, komplett
                                    ohne Live-API (MVP 0.3)

tests/                             Automatisierte Tests (pytest), ein Test pro Modul.
                                    Externe HTTP-Aufrufe werden in Tests immer gemockt.
docs/                               Diese Dokumente
```

## Ablauf innerhalb der `DealEngine`

```
find_trip_deals(origin, destination, Datumsfenster)
    │
    ├─ 1. FlightProvider.search_flights(...)         → Liste von FlightOffer
    │
    ├─ 2. Für jedes FlightOffer:
    │       0. price_confirmed_complete == False? → sofort PRICE_INCOMPLETE,
    │          vor jedem Baseline-Mechanismus (auch vor der eigenen Historie)
    │       a. PriceHistoryRepository vorhanden und genug eigene Beobachtungen
    │          (get_historical_baseline, MVP 0.3)? → Median als Baseline.
    │          Sonst: FlightProvider.get_typical_price(...) → Baseline oder None
    │       b. flight_deal_detector.assess_flight(...)→ Deal-Typ + Ersparnis
    │       c. Kein Deal (eigene Baseline sagt "nicht interessant")? → verwerfen
    │       c'. Baseline None?
    │             → FlightProvider.get_price_insight(...) als Fallback abfragen
    │             → Insight nutzbar? assess_flight_price_insight(...), gedeckelt auf
    │               FLIGHT_DROP/UNUSUALLY_LOW, BaselineSource=PROVIDER_PRICE_INSIGHT
    │             → sonst BaselineSource=NO_BASELINE
    │             → Kein Deal-Typ dabei herausgekommen? Deal mit
    │               BASELINE_UNAVAILABLE, Vergleichszahlen transparent mitgegeben
    │               falls vorhanden, kein Score (siehe Baseline-Problem in
    │               PRODUCT_SPEC.md)
    │       d. Deal gefunden (mit eigener oder Provider-Baseline):
    │            - AccommodationProvider.search_accommodations(
    │                  destination, departure_date, return_date)
    │            - günstigstes Angebot auswählen
    │            - AccommodationProvider.get_typical_total_price(...)
    │            - hotel_deal_detector.assess_accommodation(...)
    │            - trip_combiner.combine(...) → Gesamtpreis, Gesamtersparnis
    │            - Erfüllt Kombination die COMBINED_TRIP_DROP-Schwellen?
    │                  → Deal-Typ hochstufen
    │            - scoring.score_trip(...) → Trip Score
    │            - Deal-Objekt erzeugen
    │
    └─ 3. Liste aller gefundenen Deal-Objekte zurückgeben
```

## Real Flight Provider: Amadeus (MVP 0.2)

`AmadeusFlightProvider` bindet die kostenlose **Amadeus Self-Service Flight Offers
Search API** an. Warum Amadeus: automatischer Selbstregistrierungs-Zugang (kein manuelles
Freischalten nötig), kostenlose Testumgebung, reine Such-API ohne Buchungspflicht, gute
Abdeckung für europäische Flüge – passend für einen frühen Prototyp.

Der Datenfluss ist strikt geschichtet, damit die Business-Logik nie mit fremdem JSON in
Berührung kommt:

```
Amadeus API (rohes JSON)
    → amadeus_client.py       (HTTP + OAuth2, kennt kein FlightOffer)
    → amadeus_flight_provider.py  (Normalisierung: JSON → FlightOffer)
    → FlightProvider-Interface
    → DealEngine / Deal Detection (kennt nur FlightOffer, nie Amadeus)
```

**Warum wir überhaupt normalisieren:** Jede API hat ihre eigene JSON-Struktur, eigene
Feldnamen, eigene Nebenläufigkeiten (z. B. Airport- vs. City-Codes). Würde die Deal Engine
direkt mit Amadeus-JSON arbeiten, wäre sie an Amadeus gekettet – ein Wechsel oder
zusätzlicher Anbieter (z. B. Aviasales) würde die Kernlogik erzwingen, sich zu ändern. Die
Normalisierung in `_normalize_offer(...)` ist die einzige Stelle, die Amadeus' Struktur
kennt.

Ein einzelnes fehlerhaftes Angebot in einer Ergebnisliste führt nicht zum Abbruch: nicht
parsebare Einträge werden übersprungen (`_normalize_offer` gibt `None` zurück), der Rest
der Liste bleibt nutzbar.

`AmadeusFlightProvider.get_typical_price(...)` gibt immer `None` zurück – siehe
"Baseline-Problem" in `docs/PRODUCT_SPEC.md`.

**Status:** Amadeus hat sein Self-Service-Portal für neue Entwickler eingestellt.
`AmadeusFlightProvider` bleibt als Beispiel-Implementierung im Projekt, ist aber nicht
mehr der aktive MVP-Provider (siehe unten).

## Real Flight Provider: SerpApi Google Flights (MVP 0.2.1, aktiv)

`SerpApiGoogleFlightsProvider` löst `AmadeusFlightProvider` als aktiven Provider ab.
Gleiches Schichtungsprinzip wie bei Amadeus:

```
SerpApi (rohes JSON, engine=google_flights)
    → serpapi_client.py            (HTTP, kennt weder FlightOffer noch PriceInsight)
    → serpapi_flight_provider.py   (Normalisierung: JSON → FlightOffer + PriceInsight)
    → FlightProvider-Interface
    → DealEngine / Deal Detection  (kennt nur FlightOffer/PriceInsight, nie SerpApi)
```

**Price Insights:** SerpApi liefert bei Google-Flights-Suchen manchmal ein
`price_insights`-Objekt (typische Preisspanne, Preisniveau). `_extract_price_insight(...)`
ist die einzige Stelle im Code, die dieses Feld kennt; alles danach arbeitet nur mit dem
providerunabhängigen `PriceInsight`-Modell (siehe `docs/PRODUCT_SPEC.md`). Fehlt das Feld
in der Antwort, liefert `get_price_insight(...)` `None` – niemals einen erfundenen Wert.

**Bekannte Unsicherheit (bitte beim ersten Live-Test verifizieren):** SerpApi kennzeichnet
Hin- und Rückflug-Segmente in `flights` nicht explizit. `_split_outbound_return(...)` in
`serpapi_flight_provider.py` erkennt den Rückflug daran, dass ein Segment vom gesuchten
Zielort abfliegt. Sollte ein echter Response anders aufgebaut sein, betrifft eine
Korrektur ausschließlich diese eine Funktion – der Rest der Pipeline bleibt unberührt.
Aktuell fällt die Erkennung defensiv auf "kein Rückflug erkannt" zurück (kein Absturz,
aber `return_time` bleibt dann leer).

**Kein Booking Link:** SerpApi liefert für einen direkten Buchungslink einen
`booking_token`, der eine **zweite, kreditkostende** Anfrage erfordern würde. Das machen
wir bewusst nicht (siehe API Credit Safety unten) – `FlightOffer.booking_link` bleibt bei
diesem Provider `None`.

## API Credit Safety (SerpApi, MVP 0.2.1)

SerpApi hat ein begrenztes monatliches Suchkontingent. Deshalb:

- `search_flights(...)` und `get_price_insight(...)` teilen sich denselben internen
  `_search(...)`-Aufruf und denselben Cache-Eintrag (Route + Daten + Currency). Ruft
  `DealEngine` beide für dieselbe Suche auf (Normalfall: erst Flüge suchen, dann für
  BASELINE_UNAVAILABLE-Flüge eine Price Insight nachfragen), passiert das **maximal
  einmal** live gegen die echte API - der zweite Zugriff ist ein Cache-Treffer.
- `serpapi_flight_demo.py` sucht für genau eine Route/ein Datumspaar, kein Loop über
  mehrere Flughäfen oder Datumsvarianten.
- Tests verwenden ausschließlich gemockte HTTP-Antworten (siehe `tests/test_serpapi_*`),
  nie die echte API.
- Der API Key wird nur als Query-Parameter an SerpApi übergeben, nie geloggt oder in
  eine Fehlermeldung eingebettet (`serpapi_client.py` gibt nur `response.text`, also die
  Antwort der API, in Fehlern aus - niemals die Request-Parameter).

## Historical Price Intelligence: SQLite Storage Layer (MVP 0.3)

`price_history_repository.py` ist die **einzige** Stelle im Code, die SQL schreibt. Die
restliche Business-Logik (`DealEngine`, `engine/price_statistics.py`, Demos) spricht
ausschließlich mit `PriceHistoryRepository`s Methoden:

```
PriceHistoryRepository
    ├── add_observation(observation)        → bool (True = neu gespeichert)
    ├── get_observations(route, dates, ...) → list[PriceObservation]
    └── get_route_statistics(route, ...)    → PriceStatistics | None
```

`get_route_statistics(...)` delegiert die eigentliche Berechnung an
`engine/price_statistics.compute_statistics(...)` (per lokalem, nicht Modul-Import, um
einen Zirkelbezug `price_history_repository → engine → price_history_repository` zu
vermeiden) – die Repository-Schicht speichert nur, die Statistik-Logik lebt in `engine/`.

**Schema:** eine Tabelle `price_observations`, ein `UNIQUE`-Constraint über
(Route, Reisedaten, Trip-Typ, Currency, Provider, Preis, Beobachtungstag) für die
Deduplikation, ein Index über die üblichen Abfragefelder.

**Warum SQLite:** lokal, eine Datei, kein Server/Infrastruktur-Aufwand, robust genug für
MVP-Datenmengen, jederzeit auf eine echte Datenbank migrierbar, falls das Volumen das
später rechtfertigt.

**Speicherort:** `data/vacation_hunter.db` (Standard, konfigurierbar über den Konstruktor
von `PriceHistoryRepository`). Git-ignoriert (`.gitignore`), aus demselben Grund wie
`data/cache/`: lokaler Laufzeitzustand, keine Projektdaten, die committet werden sollten.

**Real vs. Demo-DB (MVP 0.4.1):** `get_observations(...)`/`get_route_statistics(...)`
filtern nach Route/Reisedaten/Trip-Typ/Currency, **nicht** nach `provider` – ein
Query-Filter wäre also kein verlässlicher Schutz gegen vermischte Demo- und Real-Daten.
`historical_price_demo.py` verwendet deshalb eine physisch eigene
`data/demo_vacation_hunter.db` statt `DEFAULT_DB_PATH` – siehe "Real vs Fixture Data
Hygiene" in `docs/PRODUCT_SPEC.md`. Jeder künftige Code, der `PriceHistoryRepository`
mit Test-/Demo-Daten befüllt, **muss** ebenfalls einen eigenen DB-Pfad verwenden statt
`DEFAULT_DB_PATH`.

**`DealEngine`-Integration:** `DealEngine` bekommt optional ein
`price_history_repository`-Argument (Standard: `None`, vollständig rückwärtskompatibel –
bestehender Code und alle bisherigen Tests laufen unverändert weiter). Ist es gesetzt,
prüft `_evaluate_flight(...)` zuerst die eigene Historie (via
`engine/price_statistics.get_historical_baseline(...)`), bevor es auf
`FlightProvider.get_typical_price(...)` und danach `get_price_insight(...)` zurückfällt.
Die `price_confirmed_complete`-Prüfung aus MVP 0.2.2 bleibt davon komplett unberührt und
steht weiterhin ganz am Anfang.

## Observation Semantics: von Search-Ergebnissen zu Beobachtungen (MVP 0.3.1/0.3.2)

Ein Audit vor der ersten echten Datensammlung ergab: Eine Suche liefert typischerweise
mehrere `FlightOffer` gleichzeitig. Würde man den ursprünglichen Hook
(`observation_from_flight_offer(...)`) naiv in einer Schleife über alle Angebote einer
Suche aufrufen, würde jedes Angebot zu einer eigenen `PriceObservation` – die spätere
Baseline wäre dann der Median **aller Angebote einer Suche**, nicht der Median der
**günstigsten Preise über mehrere Zeitpunkte**. Details und Begründung:
"Observation Semantics" in `docs/PRODUCT_SPEC.md`.

```
price_history_repository.py
    ├── observation_from_flight_offer(flight, trip_type, observed_at)
    │       Low-Level: EIN bereits ausgewähltes FlightOffer → EINE PriceObservation.
    │       Docstring warnt ausdrücklich vor Schleifen-Missbrauch.
    │
    └── observation_from_search_results(offers, comparison_group, observed_at)
            Empfohlener Einstiegspunkt: list[FlightOffer] (ein Search-Snapshot)
            + FlightComparisonGroup (explizite Aufrufer-Angabe) → PriceObservation | None.
            1. Angebote mit price_confirmed_complete=False verwerfen
            2. NUR Angebote behalten, die exakt zu comparison_group passen
               (FlightComparisonGroup.matches(...): Route, exaktes departure_date,
               exaktes return_date, Currency) - keine Heuristik, kein Raten
            3. Günstigstes Angebot der verbliebenen Menge wählen
            4. Delegiert an observation_from_flight_offer(...) für die Konvertierung
            5. Passt nichts exakt? → None
```

**MVP 0.3.2:** Die ursprüngliche "größte Gruppe gewinnt"-Heuristik (MVP 0.3.1) wurde
entfernt. Sie war deterministisch, aber fachlich riskant: Eine Liste mit Angeboten für
mehrere Reisedaten (z. B. weil ein künftiger Discovery-Request mehrere Datumskombinationen
in einer Antwort liefert) hätte automatisch die zahlenmäßig größte Gruppe gewinnen lassen
– nicht notwendigerweise die fachlich gemeinte. Audit-Ergebnis: Aktuell nutzt kein
aktiver Provider dieses Risiko aus (`SerpApiGoogleFlightsProvider` fragt SerpApi immer nur
mit einem exakten `outbound_date` ab, `latest_departure` wird dabei nicht verwendet), aber
`FlightProvider.search_flights(...)` erlaubt architektonisch ein Datumsfenster – das
Risiko ist latent. Jetzt: `FlightComparisonGroup` (`models.py`) macht die Vergleichsgruppe
explizit, inkl. Selbstvalidierung (`trip_type` muss zu `departure_date`/`return_date`
passen). Siehe "Explicit Comparison Groups" in `docs/PRODUCT_SPEC.md`.

Keine neue Provider-Abhängigkeit: Der Helfer kennt nur `FlightOffer`/`FlightComparisonGroup`,
nicht SerpApi/Amadeus/Mock. Stops und Airline sind bewusst **nicht** Teil von
`FlightComparisonGroup` (weiterhin `cheapest_any`, keine getrennten Nonstop-/Airline-
Baselines) – siehe "Stops" und "Airline" in `docs/PRODUCT_SPEC.md`.

## Controlled Historical Sampling: `record_price_snapshot.py` (MVP 0.4.2)

Manueller CLI-Befehl (argparse, kein zusätzliches Framework) für genau einen
kontrollierten Messpunkt. Zwei Funktionen, bewusst getrennt für Testbarkeit:

```
record_price_snapshot.py
    ├── run(argv)              CLI-Einstieg: parst Argumente, lädt Config, baut den
    │                          echten SerpApiGoogleFlightsProvider und IMMER
    │                          PriceHistoryRepository(db_path=DEFAULT_DB_PATH) -
    │                          nie den Demo-DB-Pfad. Delegiert an _record_snapshot.
    │
    └── _record_snapshot(provider, repository, comparison_group, source_label,
                          currency, observed_at=None)
            Provider-unabhängige Kernlogik. Nimmt FlightProvider und
            PriceHistoryRepository als Parameter entgegen - Tests injizieren einen
            Fake-Provider und ein tmp_path-Repository, ohne echten HTTP-Call.
```

**Live vs. Cache Hit:** Vor dem eigentlichen `search_flights(...)`-Aufruf wird
`FileCache.get(...)` mit demselben Cache-Key (`flight_search_cache_key(...)`, den
`SerpApiGoogleFlightsProvider` intern verwendet) einmalig, nicht-destruktiv geprüft.
Das ist ohne jede Änderung an der Provider-Klasse möglich, weil `flight_search_cache_key`
und `FileCache.get` bereits öffentliche, seiteneffektfreie Bausteine sind. Einschränkung:
Diese Erkennung dupliziert die Cache-Key-Berechnung an zwei Stellen (Provider intern,
Command außen) – ändert sich künftig, wie der Provider seinen Cache-Key bildet, muss
`record_price_snapshot.py` entsprechend mitgezogen werden. Für MVP 0.4.2 keine größere
Refaktorierung (z. B. ein `last_call_was_cache_hit`-Property am Provider), nur
dokumentiert.

**Demo-DB-Trennung bleibt hart:** `record_price_snapshot.py` hat **keine**
Import-Abhängigkeit zu `historical_price_demo.py` – der Demo-DB-Pfad ist aus diesem
Modul heraus nicht einmal erreichbar (siehe Test `test_module_has_no_import_dependency_
on_the_demo_module`).

## Environment-Variablen

API-Schlüssel gehören niemals in den Code. Vacation Hunter liest ausschließlich
`VACATION_HUNTER_*`-Variablen (`config.py`):

| Variable | Pflicht | Bedeutung |
|---|---|---|
| `VACATION_HUNTER_SERPAPI_KEY` | ja (aktiver Provider) | SerpApi API Key |
| `VACATION_HUNTER_SERPAPI_CURRENCY` | nein | Standard: `EUR` |
| `VACATION_HUNTER_FLIGHT_API_KEY` | nur für Amadeus | Amadeus API Key (Client ID) |
| `VACATION_HUNTER_FLIGHT_API_SECRET` | nur für Amadeus | Amadeus API Secret (Client Secret) |
| `VACATION_HUNTER_FLIGHT_API_BASE_URL` | nein | Standard: Amadeus-Testumgebung |
| `VACATION_HUNTER_CACHE_TTL_SECONDS` | nein | Standard: 86400 (24 Stunden), gilt für alle Provider |
| `VACATION_HUNTER_REQUEST_TIMEOUT_SECONDS` | nein | Standard: 10, gilt für alle Provider |

Diese Werte gehören in eine lokale `.env`-Datei (kopiert von `.env.example`), die von
`config.py` beim ersten Import automatisch geladen wird, sofern vorhanden. `.env` ist in
`.gitignore` eingetragen und darf niemals committet werden. Fehlt ein Pflichtwert, werfen
`load_serpapi_config()` bzw. `load_flight_api_config()` einen `MissingConfigError` mit
verständlicher Meldung statt irgendwo tief im Code mit einem kryptischen Fehler
abzustürzen. API Keys werden nie geloggt, nie in Tests hartkodiert und nie im Terminal
ausgegeben.

## Caching

Um unnötige (kosten- und ratenlimit-relevante) API-Aufrufe zu vermeiden, cachen sowohl
`AmadeusFlightProvider` als auch `SerpApiGoogleFlightsProvider` Suchergebnisse lokal über
`caching.py`:

- Jeder Cache-Eintrag ist eine JSON-Datei unter `data/cache/` (Standardpfad), benannt nach
  einem Hash des Cache-Keys.
- Der Cache-Key besteht aus Origin, Destination, Departure Date, Return Date und
  (seit MVP 0.2.1) Currency.
- Bei `SerpApiGoogleFlightsProvider` liegt in einem Cache-Eintrag sowohl die Flugliste
  als auch die Price Insight - beide stammen aus derselben Suchanfrage (siehe API Credit
  Safety oben).
- Jeder Eintrag hat einen Zeitstempel; die TTL ist zentral konfigurierbar
  (`VACATION_HUNTER_CACHE_TTL_SECONDS`, Standard 24 Stunden).
- Kein Redis, keine Datenbank – eine einfache lokale Struktur reicht.
- `data/cache/` ist git-ignoriert: Cache-Dateien sind Wegwerf-Daten, keine Projektdaten.

**Nebenbefund (MVP 0.4, dokumentiert, noch nicht gelöst):** Beim ersten echten Live-Test
für die Historical-Price-Pipeline lieferte ein noch vorhandener, nicht abgelaufener
Cache-Eintrag normalisierte `FlightOffer`-Daten nach dem **alten** Normalisierungsstand
(veralteter `return_date`-Fallback, `price_confirmed_complete=False` aus der Zeit vor
MVP 0.2.3). Unsere Sicherheitsprüfung (`observation_from_search_results(...)`) hat das
korrekt erkannt und die Speicherung verweigert – kein Datenfehler, aber ein
Architekturhinweis:

> Cached normalized objects are tied to the normalization/schema version. When
> normalization semantics change, stale cache entries may need explicit invalidation.

Für MVP 0.4.1 bewusst **keine** Cache-Schema-Versionierung im Code (keine unnötige
Refaktorierung für ein bisher einmalig aufgetretenes, von der Sicherheitsprüfung bereits
abgefangenes Problem) – nur dokumentiert. Eine mögliche spätere Lösung: eine
Schema-Version als Teil des Cache-Keys (`flight_search_cache_key(...)` in `caching.py`),
sodass ein Normalisierungs-Update alte Einträge automatisch als Cache-Miss behandelt.

## Fehlerbehandlung

Eine externe API darf die Deal Engine niemals zum Absturz bringen. `providers/errors.py`
definiert eine bewusst flache Fehlerhierarchie, die von **beiden** Real-Flight-Providern
(Amadeus und SerpApi) genutzt wird - kein Provider bekommt eigene Exception-Klassen:

- `FlightProviderTimeoutError` – Anfrage/Token-Request hat nicht rechtzeitig geantwortet
- `FlightProviderRateLimitedError` – HTTP 429
- `FlightProviderHTTPError` – sonstiger HTTP-Fehlerstatus (inkl. Statuscode)
- `FlightProviderResponseError` – Antwort ist kein gültiges JSON, fehlt erwartete Felder,
  oder enthält ein API-eigenes Fehlerfeld (z. B. SerpApis `{"error": "..."}` bei HTTP 200)

Diese werden in `amadeus_client.py`/`serpapi_client.py` an den entsprechenden Stellen
ausgelöst und in den Demo-Flows zentral abgefangen und als verständliche Meldung
ausgegeben statt als Stacktrace. Einzelne fehlerhafte Angebote innerhalb einer sonst
gültigen Antwort führen nicht zu einem Fehler, sondern werden übersprungen (siehe oben).

## Bewusste Vereinfachungen in MVP 0.1

- Es wird pro Flugangebot nur die **günstigste** passende Unterkunft betrachtet, nicht
  alle Kombinationen. Ausreichend, um die Kernidee zu beweisen; später leicht erweiterbar.
- Baseline-Preise sind fixe Werte pro Route/Zielort (kein Saisonmodell, keine Statistik).
  In `PRODUCT_SPEC.md` sind weitere, spätere Score-Faktoren (Saison, Hotellage, …)
  bewusst als "noch nicht implementiert" markiert.
- Keine Persistenz: alles läuft in einem einzelnen Prozessdurchlauf (Demo/Tests). Für ein
  späteres "laufend neue Deals finden"-System kommt eine Speicherschicht hinzu, ohne dass
  Deal-Detection oder Scoring sich ändern müssen.
