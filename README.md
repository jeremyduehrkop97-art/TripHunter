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

**Nicht** enthalten in MVP 0.1: echte/kostenpflichtige APIs, Zugangsdaten, Frontend,
Zahlungen, Nutzerkonten, Mobile App.

## Ausführen

Voraussetzung: Python 3.9 oder neuer.

```bash
cd vacation-hunter
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"

# Tests ausführen
pytest

# Demo ausführen
python -m vacation_hunter.demo
```
