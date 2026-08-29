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
| `OWN_HISTORICAL_BASELINE` | Unsere eigene (aktuell: Mock-)Preishistorie für die Route. |
| `PROVIDER_PRICE_INSIGHT` | Eine Preiseinschätzung eines Drittanbieters (z. B. Google Flights), keine eigene Historie. |
| `NO_BASELINE` | Kein Vergleichswert irgendeiner Art verfügbar. |

Das ist bewusst von `DealType` getrennt: `DealType` sagt, *was* gefunden wurde,
`BaselineSource` sagt, *wie sehr* man dem Vergleichswert dahinter vertrauen sollte.
Eine `PROVIDER_PRICE_INSIGHT`-Baseline ist **keine eigene historische Vacation-Hunter-
Baseline** – wir bauen in MVP 0.2.1 noch keine eigene Price-Intelligence-Datenbank.

**Ablauf pro Flug** (in `DealEngine`):

1. Eigene Baseline (`get_typical_price`) vorhanden? → `OWN_HISTORICAL_BASELINE`,
   normale Deal-Klassifizierung (siehe oben, inkl. `ERROR_FARE` möglich).
2. Keine eigene Baseline, aber Provider liefert eine nutzbare Price Insight
   (typische Preisspanne)? → `PROVIDER_PRICE_INSIGHT`. Klassifizierung **gedeckelt**:
   maximal `FLIGHT_DROP` oder `UNUSUALLY_LOW`, niemals `ERROR_FARE` (siehe unten).
   Zeigt die Insight keine nennenswerte Ersparnis, bleibt der Deal-Typ
   `BASELINE_UNAVAILABLE` – die Vergleichszahlen werden trotzdem transparent
   mitgegeben (Current Price, typische Spanne), nur eben nicht als "Deal" gewertet.
3. Weder eigene Baseline noch Price Insight? → `NO_BASELINE`, `BASELINE_UNAVAILABLE`,
   keine Zahlen erfunden.

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

## Price Completeness (seit MVP 0.2.2)

Ein zweites, von der Baseline unabhängiges Problem: Bevor wir überhaupt einen Preis mit
irgendeinem Vergleichswert vergleichen dürfen, müssen wir sicher sein, dass dieser Preis
den **vollständigen relevanten Trip-Preis** abbildet.

**Konkreter Fund (echter Live-Test, HAM → PMI, 02.10.–07.10.2026):** Unser Provider
meldete für den günstigsten Flug 184 EUR, während Googles `price_insights` eine typische
Roundtrip-Preisspanne von 205–385 EUR zeigte. Die Deal Engine hat daraus fälschlich
`FLIGHT_DROP` (38 % Ersparnis) berechnet.

Der Grund: SerpApis Google-Flights-Engine liefert Roundtrip-Ergebnisse zweistufig – eine
erste Suche liefert Hinflug-Optionen plus einen `departure_token` pro Option; erst ein
**zweiter, kreditkostender** Request mit diesem Token liefert die passenden
Rückflug-Optionen. Wir recherchierten in SerpApis eigener Dokumentation sowie mehreren
unabhängigen Drittquellen, ob der `price`-Wert aus Schritt 1 bereits der vollständige
Roundtrip-Preis ist – **keine Quelle gab dazu eine eindeutige, autoritative Bestätigung.**

**Deshalb gilt als feste Regel (analog zum Baseline-Problem):** Ein Flugangebot trägt ein
Flag `price_confirmed_complete`. Ist es `False` (aktuell: jeder Roundtrip von
`SerpApiGoogleFlightsProvider`, da wir den zweiten Request bewusst nicht ausführen – siehe
"API Credit Safety"), wird der Preis **mit keinem Vergleichswert verglichen** – weder
eigener Baseline noch Provider Price Insight. Die Deal Engine bricht die Bewertung für
dieses Angebot sofort mit `PRICE_INCOMPLETE` ab, bevor überhaupt eine Baseline
nachgeschlagen wird. Ein-Weg-Suchen haben diese Zweideutigkeit nicht und bleiben
`price_confirmed_complete=True`.

Diese Prüfung steht bewusst **vor** der Baseline-Prüfung: Ein unvollständiger Preis mit
falscher Baseline wäre kein bisschen besser als ein unvollständiger Preis mit korrekter
Baseline – der Vergleich selbst ist ungültig, unabhängig von der Baseline-Qualität.

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
