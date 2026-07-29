"""``_html.html_to_text``: boilerplate stripped, the posting's own text kept.

Q11: navigation, footers, forms and cookie-consent banners used
to survive into ``page_text`` - 15.586 and 18.156 characters of it on the two
postings, none of it the posting itself.
"""

from __future__ import annotations

from anlass.sources._html import html_to_text

_POSTING = """
<html><head><title>Werkstudent Engineering</title></head><body>
<nav><a href="/impressum">Impressum</a><a href="/jobs">Weitere Jobs</a></nav>
<div id="onetrust-banner-sdk">
  <p>Wir verwenden Cookies, um dir die beste Erfahrung zu bieten. Alle akzeptieren.</p>
</div>
<header><h1>Werkstudent Engineering (m/w/d)</h1></header>
<article>
  <p>Talwerk sucht Verstaerkung im Team.</p>
  <p>Aufgaben: Plattform-Features testen, Fehler nachstellen.</p>
</article>
<aside>Aehnliche Stellenanzeigen in deiner Naehe</aside>
<form><label>Bewerbung hochladen</label><input type="file"></form>
<footer>&copy; 2026 Talwerk. Alle Rechte vorbehalten. Datenschutz | Impressum</footer>
</body></html>
"""


def test_navigation_is_dropped():
    assert "Weitere Jobs" not in html_to_text(_POSTING)
    assert "Impressum" not in html_to_text(_POSTING)


def test_cookie_consent_block_is_dropped():
    assert "Cookies" not in html_to_text(_POSTING)


def test_footer_and_aside_and_form_are_dropped():
    text = html_to_text(_POSTING)
    assert "Alle Rechte vorbehalten" not in text
    assert "Aehnliche Stellenanzeigen" not in text
    assert "Bewerbung hochladen" not in text


def test_the_postings_own_text_survives():
    text = html_to_text(_POSTING)
    assert "Werkstudent Engineering (m/w/d)" in text
    assert "Talwerk sucht Verstaerkung im Team." in text
    assert "Plattform-Features testen, Fehler nachstellen." in text


def test_an_unclosed_paragraph_does_not_swallow_what_follows():
    """Real-world postings rarely close every <p> - a lenient parser must not let a
    forgotten closing tag drag a later, unrelated element into the wrong skip state."""
    html = "<body><p>Erster Absatz ohne Ende.<p>Zweiter Absatz.</p><footer>weg</footer></body>"
    text = html_to_text(html)
    assert "Erster Absatz ohne Ende." in text
    assert "Zweiter Absatz." in text
    assert "weg" not in text
