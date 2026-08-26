# Vacation Hunter

**Vacation Hunter** findet außergewöhnlich günstige *komplette* Reisen – nicht nur günstige Flüge.

## Das Problem

Es gibt viele Dienste, die günstige Flüge, Error Fares oder Preisstürze bei Flügen finden.
Was fehlt: ein System, das erkennt, ob eine **gesamte Reise** – Flug **und** Unterkunft
zusammen – für genau diesen Zeitraum ungewöhnlich günstig ist. Ein Traumflug für 79 €
nützt wenig, wenn die Unterkunft am Zielort für dieselben Daten überteuert ist.

## Unser USP

> "Find unusually cheap complete trips." – nicht "cheap flights and cheap hotels."

Vacation Hunter erkennt einen günstigen Flug, sucht **für exakt diese Reisedaten**
Unterkünfte am Zielort, prüft ob auch die Unterkunft günstig ist, und bewertet dann den
**gesamten kombinierten Trip**. Der wichtigste Deal-Typ ist deshalb `COMBINED_TRIP_DROP` –
alles andere (einzelne Flug- oder Hotel-Deals) ist ein Nebenprodukt auf dem Weg dorthin.

## Wie es funktioniert (MVP 0.1)

```
Flugdaten
  → Flug-Deal-Erkennung
  → passende Reisedaten
  → Unterkunftssuche für genau diese Daten
  → Hotel-Deal-Erkennung
  → Kombination zu einem Trip
  → Trip Score (0–100)
  → Deal-Ergebnis
```

- Details zu Deal-Typen und Trip Score: [docs/PRODUCT_SPEC.md](docs/PRODUCT_SPEC.md)
- Details zur Architektur (Provider-Abstraktion, Module, Warum Python): [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md)

## Was MVP 0.1 enthält

- Datenmodelle: `FlightOffer`, `AccommodationOffer`, `Trip`, `Deal`, `DealScore`
- Provider-Interfaces: `FlightProvider`, `AccommodationProvider` (austauschbar, noch keine echten APIs)
- Mock-Provider mit realistischen Testdaten
- Regelbasierte, transparente Deal-Detection-Engine (noch kein Machine Learning)
- Regelbasierter Trip Score
- Automatisierte Tests (`pytest`)
- Ausführbarer Demo-Flow im Terminal

## Was MVP 0.2 zusätzlich enthält

- Einen **echten** `FlightProvider`: `AmadeusFlightProvider`, angebunden an die
  kostenlose Amadeus Self-Service Flight-Offers-Search-API
- Saubere Trennung von Konfiguration/Secrets über `VACATION_HUNTER_*`
  Environment-Variablen (`.env`, niemals im Code)
- Einfaches lokales Datei-Caching für API-Antworten (TTL-basiert, kein Redis)
- Robuste Fehlerbehandlung für Timeouts, HTTP-Fehler, Rate Limits, kaputte Antworten
- Sauberen Umgang mit dem **Baseline-Problem**: ein echter aktueller Preis ohne
  Vergleichswert wird als `BASELINE_UNAVAILABLE` markiert, niemals als Preisfall
  erfunden (siehe `docs/PRODUCT_SPEC.md`)
- Einen zweiten Demo-Flow: `python -m vacation_hunter.real_flight_demo`

**Nicht** enthalten in MVP 0.2: echte Hotel-API, Frontend, Zahlungen, Nutzerkonten,
Mobile App, Datenbankserver, Machine Learning, Web Scraping, massenhaftes Scannen.

## Ausführen

Voraussetzung: Python 3.9 oder neuer.

```bash
cd vacation-hunter
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"

# Tests ausführen
pytest

# Mock-Demo ausführen (keine Internetverbindung/API-Key nötig)
python -m vacation_hunter.demo

# Echte Flugdaten ausführen (benötigt einen Amadeus API Key, siehe unten)
cp .env.example .env   # dann echte Werte eintragen
python -m vacation_hunter.real_flight_demo
python -m vacation_hunter.real_flight_demo HAM PMI 2026-10-02 2026-10-07
```

## Echte Flugdaten: Amadeus API Key

`real_flight_demo` braucht einen kostenlosen Amadeus-Zugang:

1. Kostenlos registrieren auf [developers.amadeus.com](https://developers.amadeus.com)
2. Eine "App" anlegen → du erhältst einen **API Key** und ein **API Secret**
3. Beide Werte in deine lokale `.env`-Datei eintragen (siehe `.env.example`)

Ohne diese Werte gibt `real_flight_demo` eine klare Meldung aus und bricht kontrolliert
ab – es stürzt nicht ab und es werden niemals erfundene Zugangsdaten verwendet.
Details: [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md#environment-variablen).
