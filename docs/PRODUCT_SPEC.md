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
