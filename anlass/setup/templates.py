"""The files ``anlass init`` writes into a fresh profile.

These are skeletons to fill in, not documentation. The documented, fully worked example
stays in ``profile.example/`` - it is read, this is written. They are kept apart on
purpose: a skeleton that arrives pre-filled with somebody else's facts is the fastest
way to end up sending somebody else's facts.

The skeleton is deliberately loadable as it stands (one complete placeholder entry), so
the tool starts up and says something useful instead of failing on an empty file.
"""

from __future__ import annotations

__all__ = ["CRITERIA_TEMPLATE", "FACTS_SKELETON", "SOURCES_SKELETON", "VOICE_TEMPLATE"]

FACTS_SKELETON = """# Faktenbasis - die einzige Quelle, aus der ein Entwurf schoepfen darf.
#
# Jeder Eintrag hat drei Pflichtangaben:
#   id      Kennung, kurz und stabil. Der Entwurf verweist je Absatz darauf.
#   claim   Die Aussage, so wie sie im Text auftauchen darf.
#   source  Die Fundstelle. Ohne Fundstelle ist es keine Aussage, sondern eine
#           Erinnerung.
#
# Die Pruefstufe schlaegt an, sobald der Text etwas behauptet, das hier nicht steht -
# Zahlen und Namen ausdruecklich eingeschlossen. Was hier fehlt, kann nicht geschrieben
# werden. Das ist der Sinn der Datei.
#
# Der Eintrag unten ist ein Platzhalter. Ersetze ihn durch deinen ersten echten.

facts:
  - id: platzhalter
    claim: "Ersetze diesen Satz durch eine Aussage, die du belegen kannst."
    source: "Wo steht das? Datei, Adresse, Messprotokoll, Datum."
"""

CRITERIA_TEMPLATE = """# Kriterien - dein Regelwerk fuer die Bewertung (Stufe 4).
#
# Die Bewertung ist ein Regelwerk und kein Modell: nur so ist die Punktzahl
# reproduzierbar, erklaerbar und testbar, und nur so laesst sich zu jeder Ablehnung
# sagen, an welchem Kriterium sie lag.
#
#   name    Kennung des Kriteriums. Taucht in der Begruendung auf.
#   weight  Punkte, die es beitraegt, wenn 'check' zutrifft.
#   check   Bedingung ueber die Felder des Leads.
#
# 'limits' ist die Regel gegen Massenversand. Die Werte sind ueberschreibbar, aber
# nicht abschaltbar: unterhalb von min_score wird nicht angeschrieben, auch wenn du
# willst. Wer die Schwelle aendert, aendert sie hier, und das ist eine bewusste
# Handlung.
#
# Die Werte unten sind ein Anfang. Schreib deine eigenen hin.

criteria:
  - name: remote_or_nearby
    weight: 3
    check: "remote == true or distance_km(location, home_locations) < 60"

  - name: build_it_yourself
    weight: 3
    check: "text_contains_any([bauen, implementieren, eigenverantwortlich])"

  - name: contact_person_known
    weight: 1
    check: "contact_name != null"

exclusions:
  - "score < 6"

limits:
  max_sends_per_day: 5
  days_between_same_organization: 30
  min_score: 6
"""

SOURCES_SKELETON = """# Quellen - Stufe 1 und 2. Woher die Ausschreibungen kommen.
#
# Eine Quelle braucht Angaben: welche Datei, welcher Feed, welche Seite. Deshalb
# steht sie hier und nicht als blosser Name in der Konfiguration.
#
#   type   waehlt die Quelle aus: datei, url, rss, karriereseite, jobapi
#   alles Weitere wird unveraendert an die Quelle weitergereicht
#
# Kein LinkedIn-Modul. Die Schnittstelle ist offen, wer es will, schreibt es sich;
# mitgeliefert und mitverantwortet wird es nicht.
#
# Die Liste ist leer, bis du eine Quelle eintraegst - 'anlass fetch' sagt dir das
# und verweist hierher. Ein vollstaendiges Beispiel mit allen mitgelieferten
# Quellen steht in 'profile.example/sources.example.yaml'.

sources: []
"""

VOICE_TEMPLATE = """# Stimme

Wie du klingst, und was du nie schreibst. Diese Datei geht als Ganzes an die
Entwurfsstufe. Sie steuert den Ton, nicht den Inhalt: was an Tatsachen im Text steht,
kommt ausschliesslich aus `facts.yaml`.

## Tonlage

Schreib hier zwei bis drei Saetze darueber, wie du in einer Mail klingst. Am besten
nimmst du eine Mail, die du selbst geschrieben hast, und beschreibst sie.

## Aufbau, den du benutzt

1. Ein Satz zum Anlass, mit Bezug auf die Stelle, an der er steht.
2. Zwei bis drei Saetze dazu, was du gebaut hast und wie es ausging.
3. Eine Frage, die man mit einem Satz beantworten kann.

## Was du nie schreibst

- Superlative ueber dich selbst.
- Zahlen ohne Messung dahinter.
- Behauptungen ueber die Empfaengerorganisation, die nicht aus ihrer eigenen
  Ausschreibung stammen.
- Ausrufezeichen, Emojis, Marketingsprache.

## Anrede und Gruss

Trag deine Anrede und deinen Gruss ein, damit sie nicht erfunden werden.
"""
