# anlass

Ein Werkzeug fuer Erstansprachen, das weniger verschickt und dafuer jede einzelne
begruendet. Es liest Ausschreibungen ein, sucht darin einen zitierbaren Anlass, schreibt
einen Entwurf ausschliesslich aus einer Faktenbasis, die du selbst pflegst, und prueft
den fertigen Text anschliessend in einem getrennten Durchlauf gegen genau diese Fakten.

Drei Regeln stehen im Code und nicht in dieser Datei:

1. **Kein Kontakt ohne Anlass.** Es muss einen konkreten, aus der Quelle zitierbaren
   Grund geben, warum ausgerechnet dieser Empfaenger jetzt angesprochen wird.
2. **Keine Behauptung ohne Beleg.** Jede Tatsachenaussage im erzeugten Text muss auf
   einen Eintrag der Faktenbasis zeigen. Unbelegtes wird maschinell abgefangen.
3. **Nichts geht ohne Freigabe raus.** Und es gibt keinen Schalter, der das abschaltet.

Wo diese drei Regeln durchgesetzt werden, steht weiter unten unter
[Was laeuft](#stand-was-laeuft-was-noch-nicht) — mit der Datei, in der es passiert.

## Was es ausdruecklich nicht ist

Kein Massenbewerber. Die Mengenbegrenzung ist ein Merkmal, kein fehlendes Feature. Aus
dem Kopf von `anlass/gate/limits.py`:

> Jedes verbreitete Werkzeug in diesem Feld optimiert auf Menge, und die im Plan
> zitierten Zahlen sagen, dass genau das nicht funktioniert; eine harte Tagesgrenze, ein
> Mindestabstand je Organisation und eine Mindestpunktzahl als Sperre sind deshalb kein
> fehlendes Bequemlichkeitsmerkmal, sondern die Bedingung, unter der dieses Werkzeug
> ueberhaupt sein Versprechen haelt - ohne sie waere es nur ein weiterer Massenversender
> mit besserer Prosa. Es gibt bewusst keinen Schalter, der das abschaltet: keine
> `--force`-Option, kein `--yes-all`, keine Umgebungsvariable, die eine Pruefung hier
> uebergeht. Wer die Schwelle aendern will, aendert die Kriteriendatei - das ist eine
> bewusste, lesbare und im Zweifel im Diff sichtbare Handlung, kein verstecktes Flag.

Ebenfalls nicht mitgeliefert: ein Modul, das LinkedIn automatisiert abgreift. Die
Schnittstelle fuer eigene Quellen liegt offen; mitverantwortet wird so etwas nicht.

## Einrichtung

```
python -m venv .venv
.venv/bin/pip install -e ".[dev]"
.venv/bin/anlass init
```

`anlass init` fragt vier Dinge (Betriebssystem, Modellanbieter, Postfach, Quellen),
**prueft die Modellwahl sofort mit einem echten Testaufruf** und legt danach
Konfiguration, Faktenbasis-Geruest, Kriterienvorlage und ein leeres Quellenverzeichnis
an. Zum Schluss laeuft ein Trockenlauf gegen eine mitgelieferte Beispielausschreibung.
Ein zweiter Lauf fragt nichts doppelt und ueberschreibt nichts, ohne vorher eine Kopie
zur Seite zu legen.

Ohne Tastatur geht es auch — fuer eine Rohrleitung, einen Container oder CI:

```
.venv/bin/anlass init --aus-datei einrichtung.yaml   # dieselben Schluessel wie config.yaml
.venv/bin/anlass init --ohne-rueckfragen             # jede Vorgabe uebernehmen
```

Fehlt dabei eine Pflichtangabe, wird sie benannt (welcher Schluessel, welche Frage) und
nichts geschrieben. Endet die Eingabe mitten im Interview, ist das eine deutsche Meldung
und ein Abbruchcode, kein Python-Stacktrace.

**Den Trockenlauf kannst du sofort starten, bevor du irgendetwas einrichtest** — er
braucht keine Konfiguration, nimmt daraus hoechstens das Modell, wenn schon eines
eingetragen ist, und fasst deine Einstellungen nie an:

```
.venv/bin/anlass demo --attrappe
```

Er geht durch **die ganze Kette** und benutzt dafuer dieselbe Verdrahtung wie der
Ernstbetrieb (`anlass/wiring.py`) — nur mit einer hinterlegten Modellantwort, ohne die
Anreicherungsschritte, die Seiten abrufen, und mit einer temporaeren Datenbank und einem
temporaeren Ausgangsordner, die nach dem Befehl wieder verschwinden. Er tut nichts nach
aussen. So sieht ein Lauf aus:

```
Durchlauf:
  Quelle         ok       [beispiel] Lead aus 'beispiel-ausschreibung' uebernommen.
  Anreicherung   ok       [extract:fake:trockenlauf] Neu gefuellte Felder mit Herkunft: required_skills (extract:fake:trockenlauf), contact_channel (extract:fake:trockenlauf)
  Bewertung      ok       [rules] Punktzahl 11 aus 5 Kriterien.
  Anlass         ok       [job_requirement] Anlass 'requirement:kette-dauerbetrieb': "Auswertungen laufen heute als nachts gestartete Skripte; wir wollen daraus eine "
  Entwurf        ok       [fake:trockenlauf] 4 Absaetze, belegt mit 3 Kennungen.
  Pruefung       ok       [grounding] Nichts Unbelegtes gefunden, 0 Hinweise.
  Freigabe       ok       [rules] Nichts spricht gegen die Freigabe. Sie erfolgt erst auf Zuruf.
  Freigabe       ok       [rules] Freigegeben durch trockenlauf.
  Versand        ok       [file] Uebergeben an file: .../ausgang/draft_a0c35d6d0caf_msg_41a6b91a728e.eml
```

Die Freigabe im Trockenlauf traegt `trockenlauf` als Urheber und nicht einen Namen: den
Entwurf hat niemand gelesen. Im Ernstbetrieb ist die Freigabe das getippte Wort in
`anlass send`.

## Die Befehle

| Befehl | Stufen | Was er tut |
|---|---|---|
| `init` | — | Interview, Testaufruf, Geruest, Trockenlauf; ohne Tastatur mit `--aus-datei` oder `--ohne-rueckfragen` |
| `demo` | 1–9 | Trockenlauf gegen die Beispielausschreibung, in einem temporaeren Verzeichnis, **ohne Konfiguration** |
| `fetch` | 1–8 | Quellen einlesen und jeden Lead durch die Kette schicken; endet vor dem Versand |
| `score` | 4 | gespeicherte Leads gegen die Kriterien bewerten, mit Begruendung je Kriterium |
| `draft` | 6–7 | Entwurf fuer einen gespeicherten Lead erzeugen und pruefen |
| `review` | — | Entwurf, Befunde und Zustand ansehen |
| `send` | 8–9 | einen Entwurf freigeben und uebergeben, nach ausdruecklicher Nachfrage |
| `poll` | 10 | Antworten abholen, zuordnen, klassifizieren, Nachfassen terminieren; verschickt nie |
| `report` | 10 | Bestand, welche Stufe von was besetzt ist, und die Kennzahlen |

### Verschicken ist ausgeschaltet, bis du es ausdruecklich einschaltest

`mailbox.draft_only` steht in der Voreinstellung auf `true`. Solange das gilt, legt
`anlass send` die fertige Nachricht als Datei ab und **kann gar nicht ueber SMTP
verschicken** - der Versuch ist ein Konfigurationsfehler mit Begruendung, keine
stillschweigende Zustellung. Der Grund: zwischen "Entwurf auf der Platte" und "Nachricht
auf der Leitung" lag sonst ein Wort in einer YAML-Datei, und das ist zu duenn fuer die
einzige Handlung in diesem Werkzeug, die sich nicht zuruecknehmen laesst.

Wer wirklich verschicken will, setzt `mailbox.draft_only: false` - von Hand, in der
Konfiguration, wo die Aenderung sichtbar bleibt. Es gibt kein Flag, keine
Umgebungsvariable und kein Argument, das diesen Schritt abnimmt.

`send` fragt **nicht als erstes**. Zuerst entscheidet die Sperre; erst wenn nichts
dagegen spricht, wird gefragt. Unter der Mindestpunktzahl oder bei erreichter
Tagesgrenze bekommt man die Frage gar nicht zu sehen — ein getipptes Wort ist die
Freigabe, kein Ueberstimmen. Einen Schalter, der die Nachfrage uebergeht, gibt es nicht.

Quellen brauchen Angaben (welche Datei, welcher Feed, welche Seite) und stehen deshalb
in `profile/sources.yaml`, nicht als blosser Name in der Konfiguration. `anlass init`
legt die Datei leer an; `anlass fetch` verweist darauf, solange nichts drinsteht. Ein
vollstaendiges Beispiel mit allen mitgelieferten Quellen liegt in
`profile.example/sources.example.yaml`.

Das Modell laeuft in der Voreinstellung **lokal ueber Ollama** — kostenlos, ohne
Schluessel, die Daten bleiben auf der Maschine. Cloud-Anbieter und eine schon
installierte Agenten-Befehlszeile sind alternative Wahlmoeglichkeiten. Die Konfiguration
enthaelt keine Zugangsdaten; wo ein Schluessel noetig ist, steht dort nur der Name der
Variablen, aus der er zur Laufzeit gelesen wird.

## Die Pruefstufe

Der Teil, an dem das Projekt haengt. Sie laeuft in zwei Schichten:

- Eine **maschinelle Schicht ohne Modell** prueft die nachpruefbare Oberflaeche des
  Textes: Zahlen, Namen mit erkennbarer Form, nackte Domains, Marktschreierei,
  woertliche Zitate. Was sie findet, kann kein Modell wegreden. Diese Schicht laeuft
  immer.
- Eine **Modellschicht** liest ganze Saetze und findet umformulierte Erfindungen, die
  die erste Schicht nicht sehen kann. Sie darf nur hinzufuegen, nie freigeben. **In der
  verdrahteten Kette ist sie abgeschaltet** — nicht weil sie nicht funktioniert, sondern
  weil sie 9 bis 40 Sekunden je Entwurf kostet, waehrend die erste Schicht in
  Millisekunden und ohne Netz laeuft. Die Messung dazu steht unten.

Was die maschinelle Schicht bewusst nicht tut, steht im Kopf von
`anlass/draft/verify.py`: sie meldet nicht jedes grossgeschriebene Wort. Im Deutschen
ist jedes Substantiv gross, eine solche Regel wuerde auf normaler Prosa dauernd
anschlagen und die echten Funde zudecken. Ein erfundener Name in gewoehnlicher Wortform
bleibt deshalb der Modellschicht ueberlassen. Diese Luecke ist gemessen und steht in den
Tests.

### Die Modellschicht, gemessen statt behauptet

Gemessen am 29.07.2026 auf einem Apple M5 Pro gegen ein lokales `ornith:9b` ueber
Ollama, fuenf Faelle (zwei gedeckte, drei erfundene), je Konfiguration mehrfach
wiederholt. "Format" heisst: die Antwort war auswertbares JSON. "Erkannt" zaehlt die
erfundenen Faelle, "Fehlalarm" die gedeckten, die trotzdem gemeldet wurden.

| Konfiguration | Aufrufe | Format | Erkannt | Fehlalarme | Dauer je Aufruf |
|---|---|---|---|---|---|
| Voreinstellung (Denkschritt an, 1200 Token Antwortbudget) | 15 | 12/15 | 9/9 | 0/6 | Median 19,6 s (8,7 bis 43,7) |
| Denkschritt aus | 5 | 5/5 | 3/3 | **3/3** | 5,3 bis 16,0 s |
| Denkschritt an, 4000 Token Antwortbudget | 10 | 10/10 | 6/6 | 0/4 | 10,7 bis 40,8 s |

Was daraus folgt:

- **Die drei Formatfehler der Voreinstellung sind kein Unvermoegen des Modells, sondern
  ein Fehler in unserer Anbindung.** Ollama liefert bei einem Modell mit Denkschritt
  zwei Felder; `anlass/llm/local.py` liest nur `response` und begrenzt die Antwort auf
  1200 Token. Beim schwersten Fall gingen alle 1200 Token in den Denkschritt
  (`done_reason: length`, 4820 Zeichen Denktext), und `response` blieb leer. Mit 4000
  Token hielt das Format in allen zehn Aufrufen. **Diese Korrektur ist eingebaut**: der
  Verifizierer fordert 4000 Token, und ein am Limit abgeschnittener Denkschritt meldet
  sich jetzt als eigener Fehler mit lesbarem Grund statt als „keine auswertbare Antwort".

  Nachgemessen am selben Tag gegen ein zweites Modell (`qwen3-vl:8b`, drei Faelle): die
  gedeckte Aussage und die Hoeflichkeitsfloskel gingen durch, die Verschiebung von
  Mitarbeit zu Leitung wurde gefangen — drei von drei richtig, kein Formatfehler, 9 bis
  18 Sekunden je Aufruf.
- **Den Denkschritt abzuschalten macht das Format zuverlaessig und die Pruefung
  wertlos.** Ohne ihn meldete das Modell jeden gedeckten Absatz als unbelegt, bis hin zu
  der reinen Hoeflichkeitsfloskel "Haetten Sie Zeit fuer ein kurzes Gespraech?". Der
  Denkschritt ist also keine Bequemlichkeit, sondern Bedingung.
- **Die Modellschicht bleibt optional.** Sie faengt Dinge, die die maschinelle Schicht
  nicht sehen kann - die Verschiebung von Mitarbeit zu Leitung, einen erfundenen
  Zeitraum, einen erfundenen Namen in gewoehnlicher Wortform - kostet aber zwischen fuenf
  und vierzig Sekunden je Entwurf. Wenn sie nicht antwortet, ist das eine Warnung und
  keine Freigabe: der maschinelle Boden entscheidet allein.

Das ist eine Messung an einem Modell auf einer Maschine mit fuenf Faellen, kein
Vergleichstest. Sie sagt, dass die Schicht grundsaetzlich funktioniert, und wo sie
bricht - mehr nicht.

## Der Anlass wird nach Staerke gewaehlt, nicht nach Aehnlichkeit

Stufe 5 haelt jeden Satz der Ausschreibung gegen die Faktenbasis und gibt nur zurueck,
wo beide sich decken. Welcher Treffer oben landet, entscheiden drei Dinge: die
laengennormierte, seltenheitsgewichtete Ueberlappung (ein langer, allgemein
formulierter Fakt gewinnt nicht mehr allein dadurch, dass er mehr Stichwoerter hat),
ein Zuschlag fuer Saetze, die eine Anforderung nennen („Pflicht", „Voraussetzung"), und
ein Zuschlag fuer Fakten, die eine **Staerke** belegen.

Staerke heisst: etwas Gebautes, Ausgeliefertes, Veroeffentlichtes, Gemessenes — es
beantwortet „warum dieser Bewerber". Sprachen, Studienstatus oder Wohnort beantworten
nur „darf dieser Bewerber", und das beantworten alle anderen auch. Erkannt wird das an
einem Wortschatz (`anlass/critique/rubric.py`, `is_strength`), **nicht an einer Angabe
in `facts.yaml`** — die Faktenbasis muss dafuer nicht ausgezeichnet werden. Der
Wortschatz kann eine Staerke belegen, nie ausschliessen: ein unerkannter Fakt verliert
seinen Zuschlag, nicht seinen Platz.

Warum das zaehlt, gemessen am 29.07.2026: fuer eine Ausschreibung, die ausdruecklich
nach gebauten KI-Projekten fragte, gewann vorher der Satz „Verhandlungssicheres
Deutsch, sicheres Englisch" — weil er zu einem Sprachfakt woertlicher passte. Der
Entwurf daraus erreichte die volle Punktzahl der Rubrik und war trotzdem die
schwaechere Bewerbung: **eine Rubrik optimiert auf den gewaehlten Anlass, und ein
schwacher Anlass ergibt einen gut bewerteten, schlechten Text.**

## Die Gueteschleife

Nach der Pruefung („ist der Text wahr") misst eine Rubrik, ob er **gut** ist: verlangte
Form, Anrede, aufgegriffener Anlass, verteilte Belege, ein genanntes fertiges
Ergebnis, ein Gedanke je Absatz, konkreter Abschluss, kein Bewerbungsdeutsch,
einheitliche Schreibung, Laenge. Jedes offene Kriterium liefert **einen deutschen
Anweisungssatz**, der sagt, was zu tun ist und an welcher Stelle — mit dem Satz, an dem
zu teilen ist, oder dem Wort, das zu ersetzen ist, nicht mit einer Absatznummer. Diese
Notizen gehen woertlich in die naechste Runde zurueck.

Bei einer verlangten **Kurzform** verlangt der Anlass kein Zitat mehr. Wer drei Saetze
schreiben soll, verliert ein Drittel davon, wenn er den vierzehnwoertigen Anlass zitiert
— und beantwortet die Frage damit nicht, sondern wiederholt sie. Geprueft wird dort nur
noch, dass es einen Anlass gibt und dass er wirklich in der Ausschreibung steht; ob die
Antwort zum Thema gehoert, ist eine Ermessensfrage und bleibt der Modellschicht. Im Brief
ist Platz, dort bleibt es beim woertlichen Aufgreifen.

Weist die Entwurfsstufe ihre eigene Ausgabe zurueck (zu viele Absaetze, zu viele Saetze,
falsche Anrede), ist das **kein Ende der Schleife, sondern die naechste Notiz**: der
Grund ist maschinell geprueft und sagt genau, was zu tun ist. Zwei Dinge folgen daraus:

- **Der zurueckgewiesene Text bleibt erhalten.** Er wird als Runde gefuehrt, gemessen
  und gespeichert. Ein Durchlauf, der vier Modellaufrufe gemacht hat, hat vier Texte —
  der beste davon geht an den Menschen, mit der Verletzung als offenem Punkt. Nur wenn
  wirklich kein einziger Text entstanden ist, ist der Entwurf gescheitert. Eine Runde,
  die eine harte Vorgabe reisst, verliert dabei gegen **jede** Runde, die keine reisst,
  unabhaengig von der Punktzahl.
- **Sie kostet keine Ueberarbeitung**, bis zu zwei Mal. Das Budget ist dafuer da, einen
  Entwurf besser zu machen; eine Runde, die an einer Formalie gescheitert ist, kam dazu
  gar nicht. Die Zahl ist begrenzt, weil ein Modell, das dieselbe Satzgrenze dreimal
  reisst, sie beim vierten Mal auch nicht trifft — hoechstens `max_revisions + 3`
  Modellaufrufe je Lead.
- **Zweimal dieselbe Verletzung beendet die Schleife.** Scheitert eine Runde an genau der
  Regel, die ihr die vorige Notiz gerade genannt hat, wird nicht ein drittes Mal gefragt:
  der beste Versuch geht mit dem offenen Punkt raus. „Dieselbe" ist die gebrochene Regel,
  nicht der Wortlaut der Meldung — die Zahlen darin aendern sich von Runde zu Runde.

Erst wenn das Budget aufgebraucht ist, geht der beste Versuch mit seinen offenen Punkten
an den Menschen — und der Lauf sagt, welche Runden gescheitert sind und woran.

### Nennt die Ausschreibung eine Satzzahl, traegt die Ausgabeform sie

Zaehlen ist fuer ein Sprachmodell schwer, Aufzaehlen ist leicht. Gemessen ueber vier
frische Laeufe am 29.07.2026: bei einer Grenze von zwei Saetzen scheiterten in einem Lauf
**fuenf von sechs Runden an derselben Verletzung**, nachdem das Modell fuenfmal darauf
hingewiesen worden war. Eine Bitte im Prompt traegt das nicht.

Steht deshalb eine Satzgrenze am Lead, antwortet die Entwurfsstufe nicht mehr in
Absaetzen, sondern mit einer **Liste aus genau so vielen Eintraegen, wie Saetze erlaubt
sind** — je Eintrag ein Satz, je Eintrag die Kennungen, die er belegt. Die Anzahl ist
damit strukturell da und maschinell geprueft, bevor ein Wort davon gelesen wird; zu
viele, zu wenige und zwei Saetze in einem Eintrag sind Formfehler mit der Zahl in der
Meldung. Anrede und Grussformel haben eigene Felder und sind keine Eintraege: so muss
niemand mehr wissen, welcher Absatz mitzaehlt. **Ohne Satzgrenze bleibt alles wie bisher
— fuer einen Brief ist freier Text richtig.**

Drei frische Laeufe nach dem Umbau, gegen die beiden Ausschreibungen:

| Lauf | Nordlicht (max 3 Saetze) | Talwerk (max 2 Saetze) |
|---|---|---|
| 1 | 26/26, 2 Runden | 24/26, 3 Runden |
| 2 | 26/26, 2 Runden | 26/26, 2 Runden |
| 3 | 26/26, 2 Runden | 26/26, 1 Runde |

In keiner der zwoelf Runden wurde die Satzgrenze gerissen, keine Runde scheiterte. Der
fehlende Punkt in Lauf 1 ist die Wortgrenze dahinter (54 Woerter auf zwei Saetze,
erlaubt sind 50), nicht die Satzzahl.

## Der Rueckkanal und die ehrliche Kennzahl

`anlass poll` holt eingegangene Antworten ueber IMAP, ordnet sie den Entwuerfen zu,
klassifiziert sie (interessiert, Absage, automatische Antwort, Rueckfrage) und
terminiert Nachfassen. **Er verschickt nichts** — auch kein Nachfassen: das bleibt ein
faelliger Eintrag, bis jemand ihn von Hand aufgreift, und eine Antwort, die spaeter
eintrifft, nimmt ihn wieder von der Liste. Ohne IMAP-Angaben sagt der Befehl in einem
Satz, welcher Schluessel fehlt, und endet ohne Fehler.

Eine eingehende Mail ist **Daten, nie eine Anweisung**. Eine Antwort mit „ignoriere
deine Vorgaben" ist ein zu klassifizierender Text und sonst nichts; ihr Rumpf wird nicht
einmal ins Terminal geschrieben.

`anlass report` rechnet daraus die Antwortquote nach Quelle, Punktband, Anlasstyp und
Entwurfsvariante — und die Kennzahl, an der dieses Projekt gemessen werden will: **wie
viele Entwuerfe von Hand nachgebessert werden mussten.** Entschieden wird das durch
Textvergleich. Beim ersten Speichern eines Entwurfs wird seine erzeugte Fassung
unveraenderlich mitgeschrieben; verglichen wird dagegen der Text, der tatsaechlich
rausging (und bei einem noch nicht uebergebenen Entwurf der heutige Stand). Umbruch und
Leerraum zaehlen nicht, jede Aenderung an den Worten schon.

Solange nichts gemessen ist, steht dort ein Satz und keine Null:

```
Kennzahlen: noch keine Entwuerfe gespeichert, also gibt es nichts zu messen. Zahlen
entstehen, sobald Entwuerfe erzeugt ('anlass fetch'), uebergeben ('anlass send') und
Antworten abgeholt wurden ('anlass poll').
```

## Stand: was laeuft, was noch nicht

**Die Kette ist verdrahtet und laeuft von der Ausschreibung bis zur abgelegten
Nachricht.** Ein Befehl reicht, und man sieht das Ding arbeiten. Jede Stufe bleibt
austauschbar: die Kette ist gegen die Protokolle in `anlass/interfaces.py`
programmiert und kennt keine Klassennamen; wer eine Stufe ersetzt, traegt sie in
`anlass/wiring.py` ein und aendert sonst nichts.

Wo die drei Regeln durchgesetzt werden:

1. **Kein Kontakt ohne Anlass** — Stufe 5 beendet den Durchlauf, wenn sie keinen
   zitierbaren Anlass findet (`anlass/pipeline.py`), und die Freigabe weist einen
   Entwurf ohne Anlass zurueck (`anlass/gate/gate.py`).
2. **Keine Behauptung ohne Beleg** — die Pruefstufe laeuft als getrennter Durchlauf und
   haelt jeden Entwurf mit einem Fehlerbefund an (`anlass/draft/verify.py`). Der Test
   dazu ist `tests/test_poisoned_facts.py`: Faktenbasis manipuliert, Pruefung muss
   anschlagen.
3. **Nichts ohne Freigabe** — Freigabe ist ein gespeicherter Zustandsuebergang, und der
   Versand verlangt ihn (`anlass/pipeline.py`, `deliver`). Es gibt kein Argument, das
   daran vorbeikommt.

Die Anti-Massen-Regel greift dabei **ueber Programmlaeufe hinweg**, nicht nur innerhalb
eines Prozesses: die Freigaben des Tages werden aus der Datenbank gezaehlt
(`Store.approvals_since`), und die Punktzahl holt sich die Sperre selbst aus dem
Speicher (`Store.latest_score`), statt sie vom Aufrufer entgegenzunehmen. Von Hand
nachgefahren, drei getrennte Programmaufrufe, Tagesgrenze auf 1 gestellt:

```
Die Freigabe ist nicht moeglich:
  - Tagesgrenze erreicht: 1 von 1 erlaubten Versendungen heute.
  - An 'Halbinsel Datentechnik GmbH' zuletzt vor 0 Tag(en) geschrieben, Mindestabstand sind 30 Tage.
```

Gebaut und getestet sind ausserdem: die Kerntypen mit Herkunft je Feld, die Quellen
(Datei, URL, RSS, Karriereseite, offene Job-API), die Wasserfall-Anreicherung, das
Bewertungs-Regelwerk, die Anlass-Erkennung, Entwurf und Pruefung, die Modellanbindung
mit Wahl je Stufe, der SQLite-Speicher, das Einrichtungs-Interview und die Befehlszeile.

Seit Phase 4 haengt auch **Stufe 10 an der Kette**: was rausgeht, wird mit dem Text
gespeichert, der rausging (`Store.save_delivery`), was zurueckkommt, wird zugeordnet und
klassifiziert (`anlass poll`), und `anlass report` rechnet daraus die Quoten und die
ehrliche Kennzahl. Die Zuordnung einer Antwort ueberlebt einen Neustart, weil die
Versandkennungen im Speicher liegen und dem Rueckkanal vor jedem Abruf angeboten werden
— nicht in seinem Prozessgedaechtnis.

## Grenzen

- **Der Rueckkanal laeuft, aber er ist noch an keinem echten Postfach gemessen.** `anlass
  poll` holt, ordnet zu, klassifiziert und terminiert; getestet ist das gegen ein
  nachgebautes Postfach, nicht gegen einen echten IMAP-Server. Die Klassifikation ist
  eine deutsche Phrasenliste mit einem Modell als Ausweichweg — sie faengt die
  gelaeufigen Formulierungen und wird bei allem anderen ehrlich `unklar`.
- **Es gibt keinen Befehl, der einen Entwurf bearbeitet.** Die ehrliche Kennzahl misst
  Textaenderungen, aber vorgesehen ist dafuer bisher nur der Weg ueber den Speicher; wer
  von Hand nachbessert, tut das noch ausserhalb dieses Werkzeugs. Ein `anlass edit`
  waere der naechste Schritt.
- **Die Modellschicht der Pruefung ist abgeschaltet.** Siehe die Messung oben. Der
  maschinelle Boden traegt allein, faengt aber einen erfundenen Namen in gewoehnlicher
  deutscher Wortform nicht.
- **Die Anreicherung liest den Ausschreibungstext, den die Quelle liefert, und nur
  ersatzweise die abgerufene Seite.** Umgekehrt war es bis Phase 7, und es kostete die
  wichtigste Stelle: bei der Talwerk-Ausschreibung sind 18.353 Zeichen
  Cookie-Banner, Navigation und fremde Stellenanzeigen abgerufen worden, und
  „Keine Anschreiben", „zwei Saetze", die Zieladresse und der Ansprechpartner stehen
  hinter Zeichen 6.000 — dort schneidet die Uebergabe ans Modell ab. Der Seitenabruf
  bleibt fuer Quellen, die nur Ueberschrift und Link liefern.
- **Eine gescheiterte Anreicherung kostet Felder, nicht den Durchlauf.** Ein nicht
  erreichbarer Anbieter wird im Protokoll genannt, die Felder bleiben leer, und die
  Punktzahl faellt entsprechend niedriger aus. Das ist Absicht — aber es heisst auch,
  dass eine dauerhaft kaputte Anreicherung sich als niedrige Punktzahlen aeussert und
  nicht als Fehler.
- Ob lokale Modelle brauchbares Geschaeftsdeutsch **schreiben**, ist nicht gemessen.
  Gemessen ist bisher nur, ob eines den Pruef-Prompt beantwortet. Faellt das Schreiben
  durch, wird die Voreinstellung Cloud und lokal die Option, und dann steht das hier.
- Die ehrliche Kennzahl dieses Projekts ist, **wie viele Entwuerfe von Hand nachgebessert
  werden mussten**, nicht wie viele erzeugt wurden. Gerechnet wird sie jetzt (siehe oben),
  **veroeffentlicht ist sie noch nicht**: dafuer braucht es echte Anschreiben an echte
  Empfaenger, und die gab es bis heute nicht. Was hier steht, sobald es sie gibt, ist die
  Zahl aus `anlass report` — auch wenn sie unangenehm ist.

Jeder Befund aus den bisherigen Verifikationsrunden wurde in der Phase behoben, in der
er auffiel; was an Grenzen oben steht, ist der aktuelle, noch offene Stand.

## Das Profil

`profile.example/` ist die Vorlage: `facts.yaml` (Aussage plus Fundstelle),
`criteria.yaml` (Regelwerk fuer die Bewertung und die Grenzen), `sources.example.yaml`
(woher die Ausschreibungen kommen), `voice.md` (wie der Absender klingt). Englische
Schluessel, deutsche Werte. Dein eigenes Profil legt `anlass init` an; es gehoert nicht
in die Versionsverwaltung.

## Sprachen

Code, Bezeichner und Kommentare englisch. Dokumentation, Hilfetexte und Fehlermeldungen
deutsch.

## Tests

```
.venv/bin/pytest
```

## License

Source-available under the [PolyForm Noncommercial License 1.0.0](LICENSE).

- **Free to use** for personal and other noncommercial purposes, for study and
  research, by educational, public and nonprofit institutions, and by individuals
  for their own work, including professional work as freelancer or employee.
  Organizations may evaluate the software for 60 days. Details:
  [ADDITIONAL-PERMISSIONS.md](ADDITIONAL-PERMISSIONS.md).
- **Commercial license required** for use by an organization, such as rolling it
  out to staff, operating it for others or building it into a product or service:
  [COMMERCIAL-LICENSE.md](COMMERCIAL-LICENSE.md).
- Versions up to the tag `last-agpl` remain available under AGPL-3.0-only.

Contributions: see [CONTRIBUTING.md](CONTRIBUTING.md).
