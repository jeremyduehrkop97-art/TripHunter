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
    │       └── AmadeusFlightProvider        (später)
    │       └── ...weitere Anbieter          (später)
    │
    └── AccommodationProvider (Interface)
            └── MockAccommodationProvider    (MVP 0.1)
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

    providers/
        flight_provider.py         Interface FlightProvider
        accommodation_provider.py  Interface AccommodationProvider
        mock_flight_provider.py    Testdaten-Implementierung
        mock_accommodation_provider.py

    engine/
        flight_deal_detector.py    Regelbasierte Bewertung einzelner Flugangebote
        hotel_deal_detector.py     Regelbasierte Bewertung einzelner Unterkunftsangebote
        trip_combiner.py           Kombiniert Flug + Unterkunft zu einem Trip,
                                    berechnet Gesamtersparnis
        scoring.py                 Berechnet den Trip Score (0–100)
        deal_engine.py             Orchestriert den gesamten Ablauf (siehe unten)

    demo.py                        Ausführbarer Terminal-Demo-Flow

tests/                             Automatisierte Tests (pytest), ein Test pro Modul
docs/                               Diese Dokumente
```

## Ablauf innerhalb der `DealEngine`

```
find_trip_deals(origin, destination, Datumsfenster)
    │
    ├─ 1. FlightProvider.search_flights(...)         → Liste von FlightOffer
    │
    ├─ 2. Für jedes FlightOffer:
    │       a. FlightProvider.get_typical_price(...)  → Baseline-Preis
    │       b. flight_deal_detector.assess_flight(...)→ Deal-Typ + Ersparnis
    │       c. Kein Deal? → verwerfen, nächstes Angebot
    │       d. Deal gefunden:
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

## Bewusste Vereinfachungen in MVP 0.1

- Es wird pro Flugangebot nur die **günstigste** passende Unterkunft betrachtet, nicht
  alle Kombinationen. Ausreichend, um die Kernidee zu beweisen; später leicht erweiterbar.
- Baseline-Preise sind fixe Werte pro Route/Zielort (kein Saisonmodell, keine Statistik).
  In `PRODUCT_SPEC.md` sind weitere, spätere Score-Faktoren (Saison, Hotellage, …)
  bewusst als "noch nicht implementiert" markiert.
- Keine Persistenz: alles läuft in einem einzelnen Prozessdurchlauf (Demo/Tests). Für ein
  späteres "laufend neue Deals finden"-System kommt eine Speicherschicht hinzu, ohne dass
  Deal-Detection oder Scoring sich ändern müssen.
