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
    │       └── MockFlightProvider          (MVP 0.1)
    │       └── AmadeusFlightProvider        (MVP 0.2)
    │       └── ...weitere Anbieter          (später)
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

    providers/
        flight_provider.py         Interface FlightProvider
        accommodation_provider.py  Interface AccommodationProvider
        errors.py                  Fehlerklassen für externe Provider (Timeout,
                                    HTTP-Fehler, Rate Limit, kaputte Antwort)
        mock_flight_provider.py    Testdaten-Implementierung
        mock_accommodation_provider.py
        null_accommodation_provider.py  Platzhalter ohne Hotel-Daten (MVP 0.2)
        amadeus_client.py          Rohes HTTP/OAuth2 gegen die Amadeus-API,
                                    kennt kein FlightOffer
        amadeus_flight_provider.py Echter FlightProvider: normalisiert Amadeus-
                                    JSON zu FlightOffer, nutzt den Cache

    engine/
        flight_deal_detector.py    Regelbasierte Bewertung einzelner Flugangebote
                                    (inkl. BASELINE_UNAVAILABLE-Fall)
        hotel_deal_detector.py     Regelbasierte Bewertung einzelner Unterkunftsangebote
        trip_combiner.py           Kombiniert Flug + Unterkunft zu einem Trip,
                                    berechnet Gesamtersparnis
        scoring.py                 Berechnet den Trip Score (0–100)
        deal_engine.py             Orchestriert den gesamten Ablauf (siehe unten)

    demo.py                        Terminal-Demo mit Mock-Daten (MVP 0.1)
    real_flight_demo.py            Terminal-Demo mit echten Amadeus-Flugdaten (MVP 0.2)

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
    │       a. FlightProvider.get_typical_price(...)  → Baseline-Preis oder None
    │       b. flight_deal_detector.assess_flight(...)→ Deal-Typ + Ersparnis
    │       c. Kein Deal? → verwerfen, nächstes Angebot
    │       c'. Baseline None? → Deal mit BASELINE_UNAVAILABLE, keine Kombination,
    │             kein Score (siehe Baseline-Problem in PRODUCT_SPEC.md)
    │       d. Deal gefunden (mit bekannter Baseline):
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

## Environment-Variablen

API-Schlüssel gehören niemals in den Code. Vacation Hunter liest ausschließlich
`VACATION_HUNTER_*`-Variablen (`config.py`):

| Variable | Pflicht | Bedeutung |
|---|---|---|
| `VACATION_HUNTER_FLIGHT_API_KEY` | ja | Amadeus API Key (Client ID) |
| `VACATION_HUNTER_FLIGHT_API_SECRET` | ja | Amadeus API Secret (Client Secret) |
| `VACATION_HUNTER_FLIGHT_API_BASE_URL` | nein | Standard: Amadeus-Testumgebung |
| `VACATION_HUNTER_CACHE_TTL_SECONDS` | nein | Standard: 86400 (24 Stunden) |
| `VACATION_HUNTER_REQUEST_TIMEOUT_SECONDS` | nein | Standard: 10 |

Diese Werte gehören in eine lokale `.env`-Datei (kopiert von `.env.example`), die von
`config.py` beim ersten Import automatisch geladen wird, sofern vorhanden. `.env` ist in
`.gitignore` eingetragen und darf niemals committet werden. Fehlen Key oder Secret, wirft
`load_flight_api_config()` einen `MissingConfigError` mit verständlicher Meldung statt
irgendwo tief im Code mit einem kryptischen Fehler abzustürzen.

## Caching

Um unnötige (kosten- und ratenlimit-relevante) API-Aufrufe zu vermeiden, cacht
`AmadeusFlightProvider` Suchergebnisse lokal über `caching.py`:

- Jeder Cache-Eintrag ist eine JSON-Datei unter `data/cache/` (Standardpfad), benannt nach
  einem Hash des Cache-Keys.
- Der Cache-Key besteht aus Origin, Destination, Departure Date und Return Date.
- Jeder Eintrag hat einen Zeitstempel; die TTL ist zentral konfigurierbar
  (`VACATION_HUNTER_CACHE_TTL_SECONDS`, Standard 24 Stunden).
- Kein Redis, keine Datenbank – für MVP 0.2 reicht eine einfache lokale Struktur.
- `data/cache/` ist git-ignoriert: Cache-Dateien sind Wegwerf-Daten, keine Projektdaten.

## Fehlerbehandlung

Eine externe API darf die Deal Engine niemals zum Absturz bringen. `providers/errors.py`
definiert eine bewusst flache Fehlerhierarchie:

- `FlightProviderTimeoutError` – Anfrage/Token-Request hat nicht rechtzeitig geantwortet
- `FlightProviderRateLimitedError` – HTTP 429
- `FlightProviderHTTPError` – sonstiger HTTP-Fehlerstatus (inkl. Statuscode)
- `FlightProviderResponseError` – Antwort ist kein gültiges JSON oder fehlt erwartete Felder

Diese werden in `amadeus_client.py` an den entsprechenden Stellen ausgelöst und im
Demo-Flow (`real_flight_demo.py`) zentral abgefangen und als verständliche Meldung
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
