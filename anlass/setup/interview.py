"""The setup interview: four questions, and the configuration they produce.

Asking is separated from the questions themselves (:class:`Asker`), for two reasons.
A test must be able to run the whole interview without a terminal, and a second run must
be able to offer the previous answers as defaults - both are impossible if the questions
call :func:`input` directly.

That separation is also what makes a setup without a keyboard possible at all, and there
are two of them: :class:`DefaultAsker` answers every question with its default and names
the first one that has none, and :func:`config_from_mapping` skips the questions entirely
and takes a finished configuration. Both exist because the interview must not be the only
door - a tool that cannot be set up from a pipe cannot be set up in a container or in CI.

Nothing secret is ever asked for or written down. Where a key or a password is needed
the interview asks for the *name of the environment variable* it is read from at call
time, never for its value.
"""

from __future__ import annotations

import platform
import shlex
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping, Protocol, Sequence

import yaml

from ..errors import ConfigError, SetupError
from ..llm.local import DEFAULT_HOST, DEFAULT_MODEL

__all__ = [
    "Asker",
    "ConsoleAsker",
    "DefaultAsker",
    "ScriptedAsker",
    "ask_llm",
    "config_from_file",
    "config_from_mapping",
    "run_interview",
]


class Asker(Protocol):
    """How the interview talks to whoever runs it."""

    def ask(self, question: str, *, default: str = "", choices: Sequence[str] = ()) -> str:
        """Ask one question and return the answer, or ``default`` if nothing was said."""

    def say(self, text: str) -> None:
        """Tell the user something without asking anything."""


#: What a user is told when there is no keyboard behind standard input. Named here
#: because both ways out of that situation have to be in the same sentence as the
#: problem - a message that only says "input ended" leaves the reader stuck.
_NO_KEYBOARD = (
    "Fuer eine Einrichtung ohne Tastatur: 'anlass init --aus-datei <datei.yaml>' mit "
    "denselben Schluesseln wie die erzeugte 'config.yaml', oder "
    "'anlass init --ohne-rueckfragen', wenn jede Frage eine Vorgabe hat."
)


@dataclass
class ConsoleAsker:
    """The terminal implementation. The only one that reads from standard input.

    Both ways a terminal can end an answer are errors of this package, not accidents:
    an ended input (``EOFError``, what a pipe or a closed stdin produces) and an
    interrupt (Ctrl-C). Letting either through would end ``anlass init`` in a Python
    stack trace, which tells a user nothing and looks like a crash - so both become a
    German sentence that also says how to set the tool up without a keyboard.
    """

    def ask(self, question: str, *, default: str = "", choices: Sequence[str] = ()) -> str:
        suffix = f" [{'/'.join(choices)}]" if choices else ""
        shown = f" ({default})" if default else ""
        while True:
            try:
                answer = input(f"{question}{suffix}{shown}: ").strip()
            except EOFError as exc:
                raise SetupError(
                    f"Die Eingabe ist zu Ende, '{question}' blieb unbeantwortet. "
                    f"{_NO_KEYBOARD}"
                ) from exc
            except KeyboardInterrupt as exc:
                raise SetupError(
                    "Abgebrochen. Es wurde nichts geschrieben."
                ) from exc
            if not answer:
                answer = default
            if not answer:
                self.say("Bitte etwas eintragen.")
                continue
            if choices and answer not in choices:
                self.say(f"Moeglich ist: {', '.join(choices)}.")
                continue
            return answer

    def say(self, text: str) -> None:
        print(text)


@dataclass
class DefaultAsker:
    """Answers every question with its default. The interview without a keyboard.

    A question without a default is a mandatory entry, and this is where the difference
    to :class:`ConsoleAsker` shows: at a terminal an empty answer repeats the question,
    which is right in front of a person and an endless loop in front of a pipe. Here it
    stops and says which entry is missing, so a scripted setup fails by name instead of
    hanging or inventing a value.
    """

    said: list[str] = field(default_factory=list)

    def ask(self, question: str, *, default: str = "", choices: Sequence[str] = ()) -> str:
        if not default:
            raise SetupError(
                f"'{question}' ist eine Pflichtangabe und hat keine Vorgabe, also ist "
                f"sie ohne Rueckfragen nicht zu beantworten. {_NO_KEYBOARD}"
            )
        return default

    def say(self, text: str) -> None:
        self.said.append(text)


@dataclass
class ScriptedAsker:
    """Answers from a list. For tests and for a run that must not block.

    Runs out of answers loudly instead of falling back to a default: a silent default
    would let a test pass while the interview asked something entirely different.
    """

    answers: list[str] = field(default_factory=list)
    asked: list[str] = field(default_factory=list)
    said: list[str] = field(default_factory=list)

    def ask(self, question: str, *, default: str = "", choices: Sequence[str] = ()) -> str:
        self.asked.append(question)
        if not self.answers:
            raise AssertionError(f"Keine Antwort mehr hinterlegt fuer: {question}")
        answer = self.answers.pop(0).strip()
        return answer or default

    def say(self, text: str) -> None:
        self.said.append(text)


PROVIDER_CHOICES = ("lokal", "schluessel", "abo")
TRANSPORT_CHOICES = ("datei", "smtp")


def _previous(config: Mapping[str, Any], *path: str, default: str = "") -> str:
    """Value from a previous configuration, as a string, for use as a default."""
    node: Any = config
    for key in path:
        if not isinstance(node, Mapping) or key not in node:
            return default
        node = node[key]
    if isinstance(node, (list, tuple)):
        return ", ".join(str(entry) for entry in node)
    return default if node is None else str(node)


def _previous_command(config: Mapping[str, Any]) -> str:
    """The stored ``command`` as one line again.

    A list must not come back comma-joined the way a list of sources does: the answer
    is fed through :func:`shlex.split`, so ``["python", "/pfad/mit raum.py"]`` has to
    turn back into a line that splits into exactly those two arguments again.
    """
    node: Any = config
    for key in ("llm", "default", "command"):
        if not isinstance(node, Mapping) or key not in node:
            return ""
        node = node[key]
    if isinstance(node, (list, tuple)):
        return shlex.join(str(entry) for entry in node)
    return "" if node is None else str(node)


def run_interview(asker: Asker, previous: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """Ask the four questions and return the configuration they describe.

    Args:
        asker: Where the answers come from.
        previous: An earlier configuration. Its answers become the defaults, so a
            second run is a matter of pressing enter.
    """
    old = dict(previous or {})
    if old:
        asker.say("Die Antworten des letzten Laufs stehen als Vorgabe in Klammern.")

    config: dict[str, Any] = {"version": 1}

    # 1 - operating system
    detected = platform.system().lower() or "unbekannt"
    config["os"] = asker.ask(
        "Betriebssystem", default=_previous(old, "os", default=detected)
    )

    # 2 - model provider
    config["llm"] = ask_llm(asker, old)

    # 3 - mailbox
    config["mailbox"] = _ask_mailbox(asker, old)

    # 4 - sources
    raw_sources = asker.ask(
        "Quellen, durch Komma getrennt (mitgeliefert: datei, url, rss, karriereseite, jobapi)",
        default=_previous(old, "sources", default="datei"),
    )
    config["sources"] = [name.strip() for name in raw_sources.split(",") if name.strip()]

    config["profile"] = {"path": _previous(old, "profile", "path", default="profile")}
    config["storage"] = {"path": _previous(old, "storage", "path", default="anlass.db")}
    return config


def config_from_file(path: str | Path) -> dict[str, Any]:
    """Read a setup file and return the configuration it describes.

    Raises:
        anlass.errors.ConfigError: The file is missing, is not valid YAML, or lacks a
            mandatory entry.
    """
    source = Path(path).expanduser()
    if not source.is_file():
        raise ConfigError(f"Die Einrichtungsdatei '{source}' gibt es nicht.")
    try:
        data = yaml.safe_load(source.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise ConfigError(f"Die Datei '{source}' ist kein gueltiges YAML: {exc}") from exc
    if not isinstance(data, Mapping):
        raise ConfigError(
            f"Die Datei '{source}' muss ein Objekt mit denselben Schluesseln wie die "
            "erzeugte 'config.yaml' enthalten."
        )
    return config_from_mapping(data)


def config_from_mapping(data: Mapping[str, Any]) -> dict[str, Any]:
    """The configuration a setup file describes, checked the way the interview checks.

    Same keys as the file ``anlass init`` writes, so a working installation can hand its
    own ``config.yaml`` to the next machine. What is missing is named, never guessed: a
    setup that quietly invents a mailbox is worse than one that stops, because the
    invention only shows up at the first real send.

    Entries that have a sensible default in the interview keep it here (``profile``,
    ``storage``, ``sources``, ``os``); the two that decide where something goes - the
    model block and the mailbox - do not.

    Raises:
        anlass.errors.ConfigError: A mandatory entry is missing.
    """
    config = dict(data)
    missing: list[str] = []

    llm = config.get("llm")
    if not isinstance(llm, Mapping) or not isinstance(llm.get("default"), Mapping):
        missing.append("llm.default (der Modellanbieter, wie in der erzeugten config.yaml)")

    mailbox = config.get("mailbox")
    transport = ""
    if not isinstance(mailbox, Mapping):
        missing.append("mailbox")
    else:
        transport = str(mailbox.get("transport", "")).strip().lower()
        if not transport:
            missing.append("mailbox.transport (datei oder smtp)")
        elif transport not in TRANSPORT_CHOICES:
            missing.append(
                f"mailbox.transport: '{transport}' ist keiner der moeglichen Wege "
                f"({', '.join(TRANSPORT_CHOICES)})"
            )
        if transport == "smtp":
            for key in ("host", "sender"):
                if not str(mailbox.get(key, "") or "").strip():
                    missing.append(f"mailbox.{key} (fuer den Versand ueber SMTP)")

    if missing:
        raise ConfigError(
            "In der Einrichtungsdatei fehlen Pflichtangaben: " + "; ".join(missing) + "."
        )

    assert isinstance(llm, Mapping) and isinstance(mailbox, Mapping)  # guarded above
    config["llm"] = {**llm, "default": dict(llm["default"])}
    config["llm"].setdefault("stages", {})
    config["mailbox"] = dict(mailbox)
    config.setdefault("version", 1)
    config.setdefault("os", platform.system().lower() or "unbekannt")
    config.setdefault("sources", [])
    config["profile"] = dict(config.get("profile") or {"path": "profile"})
    config["storage"] = dict(config.get("storage") or {"path": "anlass.db"})
    return config


def ask_llm(asker: Asker, old: Mapping[str, Any]) -> dict[str, Any]:
    """The model block, in the shape :func:`anlass.llm.router.build_router` expects."""
    old_provider = _previous(old, "llm", "default", "provider", default="ollama")
    kind_default = {
        "ollama": "lokal",
        "local": "lokal",
        "anthropic": "schluessel",
        "openai": "schluessel",
        "subscription": "abo",
    }.get(old_provider, "lokal")

    asker.say(
        "Woher kommt das Sprachmodell? 'lokal' laeuft ueber Ollama auf diesem Rechner, "
        "kostet nichts und die Daten bleiben hier. 'schluessel' rechnet je Aufruf ab. "
        "'abo' ruft eine schon installierte Agenten-Befehlszeile auf, die du ohnehin bezahlst."
    )
    kind = asker.ask("Modellanbieter", default=kind_default, choices=PROVIDER_CHOICES)

    if kind == "lokal":
        block: dict[str, Any] = {
            "provider": "ollama",
            "model": asker.ask(
                "Modell", default=_previous(old, "llm", "default", "model", default=DEFAULT_MODEL)
            ),
            "host": asker.ask(
                "Adresse von Ollama",
                default=_previous(old, "llm", "default", "host", default=DEFAULT_HOST),
            ),
        }
    elif kind == "schluessel":
        provider = asker.ask(
            "Anbieter",
            default=old_provider if old_provider in ("anthropic", "openai") else "anthropic",
            choices=("anthropic", "openai"),
        )
        default_model = "claude-sonnet-5" if provider == "anthropic" else "gpt-4o-mini"
        default_env = "ANTHROPIC_API_KEY" if provider == "anthropic" else "OPENAI_API_KEY"
        block = {
            "provider": provider,
            "model": asker.ask(
                "Modell", default=_previous(old, "llm", "default", "model", default=default_model)
            ),
            "api_key_env": asker.ask(
                "Name der Umgebungsvariablen mit dem Schluessel (der Schluessel selbst wird "
                "nicht abgefragt und nicht gespeichert)",
                default=_previous(old, "llm", "default", "api_key_env", default=default_env),
            ),
        }
    else:
        command = asker.ask(
            "Aufzurufender Befehl, so wie du ihn im Terminal eingibst",
            default=_previous_command(old),
        )
        block = {
            "provider": "subscription",
            "command": shlex.split(command),
            "prompt_via": asker.ask(
                "Wie bekommt der Befehl den Text",
                default=_previous(old, "llm", "default", "prompt_via", default="stdin"),
                choices=("stdin", "argument"),
            ),
        }

    result: dict[str, Any] = {"default": block}
    stages = old.get("llm", {}).get("stages") if isinstance(old.get("llm"), Mapping) else None
    result["stages"] = dict(stages) if isinstance(stages, Mapping) else {}
    return result


def _ask_mailbox(asker: Asker, old: Mapping[str, Any]) -> dict[str, Any]:
    asker.say(
        "Postfach. 'datei' legt ein freigegebenes Schreiben in einen Ordner und tut nichts "
        "nach aussen - das ist die Voreinstellung."
    )
    transport = asker.ask(
        "Versandweg",
        default=_previous(old, "mailbox", "transport", default="datei"),
        choices=TRANSPORT_CHOICES,
    )
    mailbox: dict[str, Any] = {
        "transport": transport,
        "sender": asker.ask(
            "Absenderadresse", default=_previous(old, "mailbox", "sender", default="")
        ),
    }
    if transport == "datei":
        mailbox["directory"] = asker.ask(
            "Ordner fuer den Ausgang",
            default=_previous(old, "mailbox", "directory", default="ausgang"),
        )
    else:
        mailbox["host"] = asker.ask(
            "SMTP-Server", default=_previous(old, "mailbox", "host", default="")
        )
        mailbox["port"] = int(
            asker.ask("Port", default=_previous(old, "mailbox", "port", default="587"))
        )
        mailbox["username"] = asker.ask(
            "Benutzername", default=_previous(old, "mailbox", "username", default="")
        )
        mailbox["password_env"] = asker.ask(
            "Name der Umgebungsvariablen mit dem Passwort (das Passwort selbst wird nicht "
            "abgefragt und nicht gespeichert)",
            default=_previous(old, "mailbox", "password_env", default="ANLASS_SMTP_PASSWORT"),
        )
    return mailbox
