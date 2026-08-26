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
