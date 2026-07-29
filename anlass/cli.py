"""Command line entry point: setup, and one subcommand per part of the chain.

Three properties of this file are not conveniences and must survive any refactoring.

``send`` asks for a confirmation and **has no bypass switch**. There is no ``--yes``, no
``--force`` and no environment variable that answers for the user. That is rule 3, and a
flag that skips it would not be a feature but the end of the argument this tool makes.
The typed word is what releases a draft: ``send`` runs the gate first, asks second, and
records the release as a transition - so "approved" is never a state a user can reach
without having read the sentence that says who it goes to.

Nothing here decides which implementation fills a stage. Every command builds its chain
through :func:`anlass.wiring.build_pipeline`, so the tool and its dry run take the same
path, and a stage a user replaces is replaced everywhere at once.

Everything a user reads is German; identifiers and docstrings are English.
"""

from __future__ import annotations

import argparse
import sys
import tempfile
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence, TextIO

from . import __version__
from .critique import Critique, RubricCritic, rubric_for_profile
from .errors import AnlassError, ConfigError
from .interfaces import LLM
from .llm.router import Stage as LLMStage
from .llm.router import build_router
from .metrics.collect import collect_outcomes
from .metrics.report import render_report
from .models import ApprovalState, OutboundMessage
from .pipeline import Pipeline, Run
from .profile import Profile, load_profile
from .setup import (
    ConsoleAsker,
    DefaultAsker,
    Paths,
    ask_llm,
    config_from_file,
    load_config,
    probe_llm,
    resolve_paths,
    run_interview,
    scaffold,
)
from .setup.sample import build_sample_pipeline, stand_in_llm
from .store.sqlite import SqliteStore
from .wiring import SOURCES_FILE, build_pipeline, build_sources, describe_pipeline

__all__ = ["build_parser", "main"]

CONFIRM_WORD = "senden"

_NO_SOURCES = (
    "Es ist keine Quelle eingerichtet. Eine Quelle braucht Angaben (welche Datei, "
    "welcher Feed, welche Seite), also steht sie in '{path}'. Die Vorlage mit allen "
    "mitgelieferten Quellen liegt in 'profile.example/sources.example.yaml'."
)


# --------------------------------------------------------------------------- helpers


def _out(stream: TextIO, *lines: str) -> None:
    for line in lines:
        print(line, file=stream)


def _paths(args: argparse.Namespace) -> Paths:
    return resolve_paths(args.config)


def _config(paths: Paths) -> dict[str, Any]:
    config = load_config(paths)
    if not config:
        raise ConfigError(
            f"In '{paths.home}' steht keine Konfiguration. Fang mit 'anlass init' an."
        )
    return config


def _build_llm(config: Mapping[str, Any]) -> LLM:
    """The model for drafting, from the configuration."""
    section = config.get("llm")
    if not isinstance(section, Mapping):
        raise ConfigError("In der Konfiguration fehlt der Abschnitt 'llm'.")
    return build_router(section).for_stage(LLMStage.DRAFT)


def _profile_dir(paths: Paths, config: Mapping[str, Any]) -> Path:
    relative = str(config.get("profile", {}).get("path", "profile"))
    candidate = Path(relative)
    return candidate if candidate.is_absolute() else paths.home / candidate


def _open_store(paths: Paths, config: Mapping[str, Any]) -> SqliteStore:
    relative = str(config.get("storage", {}).get("path", "anlass.db"))
    candidate = Path(relative)
    store = SqliteStore(candidate if candidate.is_absolute() else paths.home / candidate)
    store.migrate()
    return store


def _chain(
    paths: Paths,
    config: Mapping[str, Any],
    profile: Profile,
    store: SqliteStore,
    **overrides: Any,
) -> Pipeline:
    """The wired chain for this installation. Every command builds it the same way."""
    return build_pipeline(
        profile=profile,
        config=config,
        store=store,
        home=paths.home,
        **overrides,
    )


def _describe_chain(paths: Paths, config: Mapping[str, Any]) -> list[str]:
    """Which stage is filled by what, for ``report``. Falls back to all slots empty.

    ``report`` must work on a half-finished installation - that is when somebody most
    wants to know what is wired up. Without even a profile there is nothing to build
    and every slot shows empty; once there is one, a stage a bad configuration could
    not build names its own error instead (:func:`anlass.wiring.describe_pipeline`,
    FINDINGS BEFUND 9) - it must not read like the nine other stages that built fine.
    The chain is built against a throwaway store because describing it touches no data.
    """
    try:
        profile = load_profile(_profile_dir(paths, config))
        sources = build_sources(_profile_dir(paths, config))
    except AnlassError:
        return Pipeline().describe()
    with SqliteStore() as scratch:
        scratch.migrate()
        return describe_pipeline(
            profile=profile,
            config=config,
            store=scratch,
            source=sources[0] if sources else None,
            home=paths.home,
        )


def _critique_lines(critique: Critique, *, rounds: int | None = None) -> list[str]:
    """The score, then every open criterion with the sentence that says what to do.

    The notes are shown, not just the names: a user who reads this is the person who has
    to fix the draft, and the instruction is the only part they can act on.
    """
    head = f"Guete: {critique.earned} von {critique.possible} Punkten"
    if rounds:
        head += f" nach {rounds} {'Runde' if rounds == 1 else 'Runden'}"
    lines = [head + ("." if critique.is_full else ", offen:")]
    for result in critique.open_criteria:
        lines.append(f"  {result.name} ({result.possible} Punkte): {result.note}")
    for note in critique.grounding_notes:
        lines.append(f"  Erdung: {note}")
    lines.extend(f"  Hinweis: {warning}" for warning in critique.warnings)
    return lines


def _report_run(run: Run, stream: TextIO) -> None:
    """Print what every stage did, then the draft and the findings."""
    _out(stream, "", "Durchlauf:")
    for line in run.trace():
        _out(stream, "  " + line)
    if run.draft is not None:
        _out(stream, "", f"Entwurf {run.draft.id}", f"Betreff: {run.draft.subject or '(keiner)'}", "")
        for index, paragraph in enumerate(run.draft.paragraphs, start=1):
            belege = ", ".join(paragraph.fact_ids) or "kein Beleg genannt"
            _out(stream, f"  [{index}] {paragraph.text}", f"      Belege: {belege}", "")
    if run.verification is not None:
        result = run.verification
        _out(stream, f"Pruefung durch {', '.join(result.checkers)}:")
        if not result.findings:
            _out(stream, "  nichts gefunden.")
        for finding in result.findings:
            _out(stream, f"  {finding.severity.value.upper():7} {finding.message}")
    if run.critique is not None:
        _out(stream, "", *_critique_lines(run.critique, rounds=len(run.rounds)))
    missing = run.missing_stages
    if missing:
        _out(
            stream,
            "",
            "Noch nicht eingesetzt: " + ", ".join(stage.label for stage in missing) + ".",
        )


# ------------------------------------------------------------------------ subcommands


def cmd_init(args: argparse.Namespace, stream: TextIO) -> int:
    """Interview, test call, scaffolding, dry run.

    Three ways in, one path afterwards. The interview is the normal one; ``--aus-datei``
    takes a finished configuration and asks nothing; ``--ohne-rueckfragen`` runs the same
    interview with every default accepted. The last two exist because a tool that can
    only be set up by typing cannot be set up in a pipe, a container or CI - and because
    an interview reading from a closed input used to end in a stack trace.

    Where the model test fails, the difference between the ways shows: at a terminal the
    user is asked whether to correct or accept, without one there is nobody to ask, so
    the configuration is written down as unverified and says so.
    """
    paths = _paths(args)
    probe = args.probe or probe_llm
    from_file = getattr(args, "from_file", None)
    interactive = from_file is None and not getattr(args, "non_interactive", False)
    asker = args.asker or (ConsoleAsker() if interactive else DefaultAsker())

    _out(stream, "Einrichtung von anlass.", *paths.lines(), "")

    previous = load_config(paths)
    if from_file is not None:
        config = config_from_file(from_file)
        _out(stream, f"Angaben aus '{from_file}' uebernommen, es wird nichts gefragt.", "")
    else:
        config = run_interview(asker, previous)

    attempts = 0
    while True:
        attempts += 1
        try:
            llm = build_router(config["llm"]).for_stage(LLMStage.DRAFT)
        except ConfigError as exc:
            result = None
            _out(stream, f"Die Angaben ergeben keinen gueltigen Anbieter: {exc}")
        else:
            result = probe(llm)
            _out(stream, result.line())
            if result.ok:
                config["llm"]["verified"] = True
                break
        if attempts >= 3 or not interactive:
            config["llm"]["verified"] = False
            _out(
                stream,
                "Der Testaufruf blieb erfolglos. Die Konfiguration wird als ungeprueft "
                "gespeichert; der erste Entwurf wird damit scheitern.",
            )
            break
        answer = asker.ask(
            "Nochmal eintragen oder trotzdem uebernehmen",
            default="nochmal",
            choices=("nochmal", "uebernehmen"),
        )
        if answer == "uebernehmen":
            config["llm"]["verified"] = False
            break
        config["llm"] = ask_llm(asker, config)

    written = scaffold(paths, config)
    _out(stream, "", *written.lines())

    _out(stream, "", "Trockenlauf gegen die mitgelieferte Beispielausschreibung.")
    return _demo(config, paths, stream, use_real_model=bool(config["llm"].get("verified")))


def cmd_demo(args: argparse.Namespace, stream: TextIO) -> int:
    """The dry run on its own - and the one command that needs no configuration.

    That is its whole point: it shows the chain working **before** anybody types a sender
    address or a mailbox. It takes nothing from the configuration except, if one happens
    to exist, the model to use - the rest is the shipped example, a temporary directory
    and a temporary database. Requiring a configuration here would demand exactly the
    step the dry run is supposed to come before.
    """
    paths = _paths(args)
    config = load_config(paths)
    _out(
        stream,
        "Trockenlauf mit der mitgelieferten Beispielausschreibung und einer erfundenen "
        "Faktenbasis. Deine Einstellungen und deine Daten werden dabei nicht angefasst.",
    )
    if not config:
        _out(
            stream,
            f"Eine Konfiguration gibt es in '{paths.home}' noch nicht - dieser Lauf "
            "braucht auch keine.",
        )
    return _demo(config, paths, stream, use_real_model=bool(config) and not args.stand_in)


def _demo(
    config: Mapping[str, Any], paths: Paths, stream: TextIO, *, use_real_model: bool
) -> int:
    """The dry run: the whole chain, from the example posting to a delivered file.

    Everything it produces lands in a temporary directory that is removed again when
    this function returns - a throwaway database and a throwaway outbox. It reaches no
    network: the enrichment providers that fetch pages are left out, the transport
    writes a local file, and the model is either the stand-in or the one the user
    configured and had tested during ``init``.
    """
    llm: LLM
    if use_real_model:
        try:
            llm = _build_llm(config)
        except ConfigError as exc:
            _out(stream, f"Kein Modell aus der Konfiguration nutzbar: {exc}")
            llm = stand_in_llm()
    else:
        llm = stand_in_llm()
    if llm.name.startswith("fake:"):
        _out(
            stream,
            "Hinweis: kein Modell im Einsatz. Der Entwurf kommt aus einer hinterlegten "
            "Antwort, damit die Kette auch ohne Modell zu sehen ist.",
        )

    with tempfile.TemporaryDirectory(prefix="anlass-trockenlauf-") as workspace:
        temporary = Path(workspace)
        store = SqliteStore(temporary / "trockenlauf.db")
        store.migrate()
        try:
            pipeline = build_sample_pipeline(
                llm, store=store, outbox=temporary / "ausgang"
            )
            run = pipeline.run_lead(pipeline.fetch(limit=1)[0])
            blocked = run.stopped_at
            if blocked is None:
                _release_in_dry_run(pipeline, run, stream)
            _report_run(run, stream)
            if blocked is not None:
                _out(
                    stream,
                    "",
                    f"Der Durchlauf endete bei '{blocked.stage.label}'. Das ist kein Fehler "
                    "des Trockenlaufs, sondern sein Ergebnis.",
                )
        finally:
            store.close()
    return 0


def _release_in_dry_run(pipeline: Pipeline, run: Run, stream: TextIO) -> None:
    """Approve and hand over, so the last two stages are shown and not just described.

    The acting person on this record is ``trockenlauf`` and not a name: nobody read this
    draft. In real use the release is the word a user types into ``anlass send``, and
    there is no argument here or there that skips it.
    """
    assert run.draft is not None and run.lead is not None
    pipeline.approve(run, reason="Trockenlauf gegen die mitgelieferte Ausschreibung")
    receipt = pipeline.deliver(run, _message_for(run.draft, run.lead, sender=None))
    _out(
        stream,
        "",
        f"Freigabe und Uebergabe sind Teil dieses Laufs. Abgelegt als: {receipt.reference}",
        "Das Verzeichnis ist temporaer und wird nach diesem Befehl geloescht. Im "
        "Ernstbetrieb gibt 'anlass send' frei, und nur nach getipptem Wort.",
    )


def _message_for(draft, lead, *, sender: str | None) -> OutboundMessage:
    """The message a draft becomes. Recipient comes from the lead, never from a guess.

    Raises:
        anlass.errors.AnlassError: The lead has no contact address. Inventing one is the
            one thing this tool must never do.
    """
    recipient = lead.value("contact_email")
    if not recipient:
        raise AnlassError(
            f"Zum Lead '{lead.id}' ist keine Adresse gespeichert. Ohne Empfaenger wird "
            "nichts uebergeben, und geraten wird hier nicht."
        )
    return OutboundMessage(
        draft_id=draft.id,
        recipient=str(recipient),
        subject=draft.subject or "",
        body=draft.text,
        sender=sender,
    )


def cmd_fetch(args: argparse.Namespace, stream: TextIO) -> int:
    """Read from the configured sources and send every lead through the chain.

    Stages 1 to 8: reading, enriching, scoring, looking for the occasion, drafting,
    verifying, and asking the gate. It stops there - nothing is released and nothing is
    sent, that is ``anlass send``.
    """
    paths = _paths(args)
    config = _config(paths)
    profile = load_profile(_profile_dir(paths, config))
    sources = build_sources(_profile_dir(paths, config))
    if args.source:
        sources = [s for s in sources if s.name == args.source]
    if not sources:
        _out(stream, _NO_SOURCES.format(path=_profile_dir(paths, config) / SOURCES_FILE))
        return 2
    store = _open_store(paths, config)
    try:
        drafted = 0
        for source in sources:
            pipeline = _chain(paths, config, profile, store, source=source)
            for run in pipeline.fetch_runs(limit=args.limit):
                _out(stream, "", f"--- {run.lead.value('organization', run.lead.id)}")
                for line in run.trace():
                    _out(stream, "  " + line)
                if run.draft is not None:
                    drafted += 1
                    _out(stream, f"  Entwurf: {run.draft.id}")
        _out(stream, "", f"{drafted} Entwuerfe entstanden. Ansehen mit 'anlass review'.")
        return 0
    finally:
        store.close()


def cmd_score(args: argparse.Namespace, stream: TextIO) -> int:
    """Score stored leads against the criteria file. Rules only, never a model."""
    paths = _paths(args)
    config = _config(paths)
    profile = load_profile(_profile_dir(paths, config))
    store = _open_store(paths, config)
    try:
        pipeline = _chain(paths, config, profile, store)
        if args.lead:
            lead = store.get_lead(args.lead)
            if lead is None:
                _out(stream, f"Zu der Kennung '{args.lead}' gibt es keinen Lead.")
                return 2
            leads = [lead]
        else:
            leads = store.list_leads()
        if not leads:
            _out(stream, "Es sind keine Leads gespeichert. Fang mit 'anlass fetch' an.")
            return 2
        for lead in leads:
            run = pipeline.score_lead(lead)
            assert run.score is not None
            verdict = (
                f"ausgeschlossen durch '{run.score.excluded_by}'"
                if not run.score.accepted
                else "angenommen"
            )
            _out(
                stream,
                f"{lead.id}  {lead.value('organization', '(ohne Organisation)')}",
                f"  Punktzahl {run.score.total}, {verdict}",
            )
            for outcome in run.score.outcomes:
                _out(stream, f"    {'+' if outcome.passed else ' '} {outcome.reason}")
        return 0
    finally:
        store.close()


def cmd_draft(args: argparse.Namespace, stream: TextIO) -> int:
    """Draft for one stored lead, then verify it right away."""
    paths = _paths(args)
    config = _config(paths)
    profile = load_profile(_profile_dir(paths, config))
    store = _open_store(paths, config)
    try:
        lead = store.get_lead(args.lead)
        if lead is None:
            _out(stream, f"Zu der Kennung '{args.lead}' gibt es keinen Lead.")
            return 2
        signals = store.signals_for_lead(lead.id)
        pipeline = _chain(
            paths, config, profile, store, extra_grounding=tuple(args.grounding or ())
        )
        run = pipeline.draft_lead(lead, signal=signals[0] if signals else None)
        _report_run(run, stream)
        return 0 if run.draft is not None else 1
    finally:
        store.close()


def cmd_review(args: argparse.Namespace, stream: TextIO) -> int:
    """Show a stored draft with its findings, its score and its open criteria.

    The score is measured again here rather than read back: the rubric is a file a user
    edits, and a number stored yesterday would answer a question nobody asked. The
    measurement is the deterministic one - no model is called for looking at a draft.
    """
    paths = _paths(args)
    config = _config(paths)
    store = _open_store(paths, config)
    try:
        draft = store.get_draft(args.draft)
        if draft is None:
            _out(stream, f"Zu der Kennung '{args.draft}' gibt es keinen Entwurf.")
            return 2
        _out(stream, f"Entwurf {draft.id}", f"Betreff: {draft.subject or '(keiner)'}", "")
        for index, paragraph in enumerate(draft.paragraphs, start=1):
            belege = ", ".join(paragraph.fact_ids) or "kein Beleg genannt"
            _out(stream, f"  [{index}] {paragraph.text}", f"      Belege: {belege}", "")
        result = store.latest_verification(draft.id)
        if result is None:
            _out(stream, "Dieser Entwurf ist noch nicht geprueft worden.")
        else:
            _out(stream, f"Pruefung durch {', '.join(result.checkers)}:")
            for finding in result.findings:
                _out(stream, f"  {finding.severity.value.upper():7} {finding.message}")
            _out(
                stream,
                "",
                "Ergebnis: bestanden." if result.passed else "Ergebnis: nicht bestanden.",
            )
        _out(stream, "", *_review_quality(paths, config, store, draft, result))
        _out(stream, f"Zustand: {store.current_state(draft.id).value}")
        return 0
    finally:
        store.close()


def _signal_of(store: SqliteStore, draft, lead):
    """The occasion this draft was written against - not the lead's oldest one.

    Q13, measured on the same draft: against its own occasion 24 of 26 points, against
    the lead's first one 20 - plus a note asking it to take up a sentence it never saw.
    Every run files a further occasion for a lead, so "the lead's occasion" stops being
    a single thing after the second run; the draft's own id is the only one that is.
    The lead's oldest stays the fallback for drafts written before ``signal_id`` was
    filled in.
    """
    if draft.signal_id:
        found = store.signal_by_id(draft.signal_id)
        if found is not None:
            return found
    if lead is None:
        return None
    signals = store.signals_for_lead(lead.id)
    return signals[0] if signals else None


def _review_quality(
    paths: Paths,
    config: Mapping[str, Any],
    store: SqliteStore,
    draft,
    verification,
) -> list[str]:
    """The quality block of ``review``: score, open criteria, which attempt this is.

    Built from the profile alone, without the rest of the chain: looking at a draft must
    work on a half-finished installation, and it must not depend on a mailbox or a
    reachable model.
    """
    try:
        profile = load_profile(_profile_dir(paths, config))
        rubric = rubric_for_profile(_profile_dir(paths, config))
    except AnlassError as exc:
        return [f"Die Guete liess sich nicht messen: {exc}"]
    lead = store.get_lead(draft.lead_id)
    critique = RubricCritic(rubric=rubric).critique(
        draft,
        lead=lead,
        signal=_signal_of(store, draft, lead),
        facts=profile.facts,
        verification=verification,
    )
    lines = _critique_lines(critique)
    attempts = sorted(
        (entry for entry in store.list_drafts() if entry.lead_id == draft.lead_id),
        key=lambda entry: entry.created_at,
    )
    position = next(
        (index for index, entry in enumerate(attempts, start=1) if entry.id == draft.id), 1
    )
    lines.append(
        f"Entwurf {position} von {len(attempts)} zu diesem Lead. Wie viele "
        "Ueberarbeitungsrunden dahinter stecken, zeigt das Protokoll des Durchlaufs "
        "('anlass draft')."
    )
    return lines


def cmd_send(args: argparse.Namespace, stream: TextIO) -> int:
    """Release one draft and hand it to the transport. Asks, and cannot be told not to.

    The order is deliberate: the gate decides first, the user second. Asking before the
    limits are checked would put a person in front of a question the rules have already
    answered, and a typed word must never be able to overrule a limit - it is the
    release, not an override. The confirmation is read from standard input and has to be
    the word in :data:`CONFIRM_WORD`. There is no argument, no configuration entry and no
    environment variable that answers in the user's place.
    """
    paths = _paths(args)
    config = _config(paths)
    profile = load_profile(_profile_dir(paths, config))
    store = _open_store(paths, config)
    try:
        draft = store.get_draft(args.draft)
        if draft is None:
            _out(stream, f"Zu der Kennung '{args.draft}' gibt es keinen Entwurf.")
            return 2
        state = store.current_state(draft.id)
        if state is ApprovalState.SENT:
            _out(stream, "Dieser Entwurf ist bereits versendet worden.")
            return 2
        if state is ApprovalState.REJECTED:
            _out(stream, "Dieser Entwurf ist abgelehnt worden. Schreib einen neuen.")
            return 2
        lead = store.get_lead(draft.lead_id)
        if lead is None:
            _out(stream, f"Zu dem Entwurf '{draft.id}' fehlt der Lead im Speicher.")
            return 2

        pipeline = _chain(paths, config, profile, store)
        signals = store.signals_for_lead(lead.id)
        run = Run(
            lead=lead,
            draft=draft,
            signal=signals[0] if signals else None,
            score=store.latest_score(lead.id),
            verification=store.latest_verification(draft.id),
        )
        assert pipeline.gate is not None
        objections = pipeline.gate.check(
            draft, signal=run.signal, score=run.score, verification=run.verification
        )
        if objections:
            _out(stream, "Die Freigabe ist nicht moeglich:")
            for objection in objections:
                _out(stream, f"  - {objection}")
            return 2

        reader = getattr(args, "reader", None) or input
        if not confirm_send(str(lead.value("contact_email") or "den Empfaenger"), stream, reader):
            _out(stream, "Abgebrochen. Es wurde nichts verschickt.")
            return 1

        if state is not ApprovalState.APPROVED:
            run.approval = pipeline.approve(run, reason="Am Terminal freigegeben")
        else:
            run.approval = store.approvals_for_draft(draft.id)[-1]
        sender = (config.get("mailbox") or {}).get("sender")
        receipt = pipeline.deliver(
            run, _message_for(draft, lead, sender=str(sender) if sender else None)
        )
        _out(
            stream,
            f"Uebergeben an {receipt.transport}: {receipt.reference or receipt.detail}",
        )
        return 0 if receipt.accepted else 1
    finally:
        store.close()


def confirm_send(recipient: str, stream: TextIO, reader: Callable[[], str]) -> bool:
    """Ask once, in words. Used by :func:`cmd_send` and testable on its own.

    A word, not a keystroke: ``y`` is typed by accident, ``senden`` is not.
    """
    _out(
        stream,
        f"An {recipient} wird eine echte Nachricht verschickt.",
        f"Zum Bestaetigen das Wort '{CONFIRM_WORD}' eintippen, alles andere bricht ab.",
    )
    return reader().strip().lower() == CONFIRM_WORD


def cmd_poll(args: argparse.Namespace, stream: TextIO) -> int:
    """Stage 10: fetch replies, correlate them, classify them, schedule follow-ups.

    **Sends nothing.** Not the follow-ups either: a due follow-up is a row with a date,
    and turning it into a message is a step a person takes deliberately. There is no
    argument here that would do it for them.

    What comes back is data. A reply that contains "ignore your instructions" is a text
    to be classified and nothing else, and nothing in this command treats it otherwise -
    which is also why the body is never echoed to the terminal: what is shown is who
    wrote, about what, and how it was classified.
    """
    paths = _paths(args)
    config = _config(paths)
    profile = load_profile(_profile_dir(paths, config))
    store = _open_store(paths, config)
    try:
        pipeline = _chain(paths, config, profile, store)
        if pipeline.tracker is None:
            _out(
                stream,
                "Kein Rueckkanal eingerichtet: in der Konfiguration fehlt 'mailbox."
                "imap_host' (dazu 'imap_username_env' und 'imap_password_env' fuer die "
                "Zugangsdaten aus der Umgebung). Ohne diese Angaben wird nichts abgeholt.",
            )
            return 0

        replies = pipeline.poll_replies(limit=args.limit)
        _out(stream, f"{len(replies)} Antworten abgeholt.")
        for reply in replies:
            zuordnung = f"zu {reply.draft_id}" if reply.draft_id else "ohne Zuordnung"
            subject = reply.subject.strip() or "(kein Betreff)"
            _out(stream, f"  {reply.kind.value:12} {zuordnung:24} {subject[:60]}")
        unmatched = sum(1 for reply in replies if reply.draft_id is None)
        if unmatched:
            _out(
                stream,
                f"  {unmatched} davon liessen sich keinem Entwurf zuordnen. Geraten wird "
                "hier nicht; sie zaehlen in keiner Quote mit.",
            )

        scheduled = pipeline.schedule_follow_ups()
        due = pipeline.due_follow_ups()
        _out(stream, "", f"{len(scheduled)} Nachfassen neu terminiert, {len(due)} faellig.")
        for entry in due:
            _out(
                stream,
                f"  {entry.draft_id}  faellig seit {entry.due_at.date().isoformat()}"
                f"  {entry.reason}",
            )
        if due:
            _out(
                stream,
                "Faellig heisst faellig, nicht verschickt: nachgefasst wird von Hand, "
                "mit 'anlass send' und dem getippten Wort.",
            )
        return 0
    finally:
        store.close()


def cmd_report(args: argparse.Namespace, stream: TextIO) -> int:
    """What is in the store, which stages exist, and what came of it all."""
    paths = _paths(args)
    config = _config(paths)
    store = _open_store(paths, config)
    try:
        leads = store.list_leads()
        signals = sum(len(store.signals_for_lead(lead.id)) for lead in leads)
        _out(stream, f"Leads: {len(leads)}, davon mit Anlass: {signals}")
        for lead in leads:
            _out(
                stream,
                f"  {lead.id}  {lead.value('organization', '(ohne Organisation)')}"
                f"  Quelle: {lead.source}",
            )
        _out(stream, "", "Stufen:")
        for line in _describe_chain(paths, config):
            _out(stream, "  " + line)

        _out(stream, "")
        outcomes = collect_outcomes(store)
        if not outcomes:
            # No zero. A zero would read as a measured result, and "nothing measured
            # yet" is a different statement - the whole point of the honest metric.
            _out(
                stream,
                "Kennzahlen: noch keine Entwuerfe gespeichert, also gibt es nichts zu "
                "messen. Zahlen entstehen, sobald Entwuerfe erzeugt ('anlass fetch'), "
                "uebergeben ('anlass send') und Antworten abgeholt wurden ('anlass poll').",
            )
            return 0
        _out(stream, render_report(outcomes))
        if not any(outcome.sent for outcome in outcomes):
            _out(
                stream,
                "",
                "Es ist noch nichts uebergeben worden - die Antwortquoten oben haben "
                "deshalb keinen Nenner. Ohne Versand keine Quote, und eine Null waere "
                "hier eine Behauptung.",
            )
        return 0
    finally:
        store.close()


# ----------------------------------------------------------------------------- parser


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="anlass",
        description=(
            "Geerdeter Outbound-Motor. Kein Kontakt ohne Anlass, keine Behauptung "
            "ohne Beleg, nichts geht ohne Freigabe raus."
        ),
    )
    parser.add_argument(
        "--version", action="version", version=f"anlass {__version__}",
        help="Fassung ausgeben und beenden",
    )
    parser.add_argument(
        "--config", metavar="PFAD", default=None,
        help="Verzeichnis oder Konfigurationsdatei dieser Einrichtung",
    )
    subparsers = parser.add_subparsers(dest="command", metavar="BEFEHL")

    init = subparsers.add_parser(
        "init",
        help="Einrichtung: Interview, Testaufruf, Trockenlauf",
        description=(
            "Fragt vier Dinge, prueft die Modellwahl mit einem echten Aufruf und legt "
            "Konfiguration und Profil an. Ohne Tastatur geht es mit '--aus-datei' oder "
            "'--ohne-rueckfragen'."
        ),
    )
    # The English spellings are aliases because the task named them; the German ones are
    # the convention of every other flag in this program, so they come first.
    init.add_argument(
        "--aus-datei", "--from-file", dest="from_file", metavar="YAML", default=None,
        help="Einrichtung aus einer Datei mit denselben Schluesseln wie 'config.yaml'",
    )
    init.add_argument(
        "--ohne-rueckfragen", "--non-interactive", dest="non_interactive",
        action="store_true",
        help="nichts fragen, jede Vorgabe uebernehmen; fehlt eine Pflichtangabe, bricht es ab",
    )
    init.set_defaults(run=cmd_init, asker=None, probe=None)

    demo = subparsers.add_parser(
        "demo", help="Trockenlauf gegen die mitgelieferte Beispielausschreibung"
    )
    demo.add_argument(
        "--attrappe", dest="stand_in", action="store_true",
        help="ohne Modell laufen, mit hinterlegter Antwort",
    )
    demo.set_defaults(run=cmd_demo)

    fetch = subparsers.add_parser(
        "fetch", help="einlesen und durch die Kette schicken (Stufe 1 bis 8)"
    )
    fetch.add_argument("--quelle", dest="source", default=None, help="Name der Quelle")
    fetch.add_argument("--anzahl", dest="limit", type=int, default=None, help="Hoechstzahl")
    fetch.set_defaults(run=cmd_fetch)

    score = subparsers.add_parser("score", help="Leads gegen die Kriterien bewerten (Stufe 4)")
    score.add_argument("--lead", default=None, help="Kennung eines einzelnen Leads")
    score.set_defaults(run=cmd_score)

    draft = subparsers.add_parser("draft", help="Entwurf erzeugen und pruefen (Stufe 6 und 7)")
    draft.add_argument("--lead", required=True, help="Kennung des Leads")
    draft.add_argument(
        "--zusaetzlich", dest="grounding", action="append", default=None,
        help="zusaetzlich erlaubter Begriff, etwa der eigene Name",
    )
    draft.set_defaults(run=cmd_draft)

    review = subparsers.add_parser("review", help="Entwurf und Befunde ansehen")
    review.add_argument("--entwurf", dest="draft", required=True, help="Kennung des Entwurfs")
    review.set_defaults(run=cmd_review)

    send = subparsers.add_parser(
        "send",
        help="einen freigegebenen Entwurf verschicken (verlangt eine Bestaetigung)",
        description=(
            "Verschickt genau einen freigegebenen Entwurf. Vorher wird ausdruecklich "
            "nachgefragt. Es gibt keinen Schalter, der diese Nachfrage abschaltet."
        ),
    )
    send.add_argument("--entwurf", dest="draft", required=True, help="Kennung des Entwurfs")
    send.set_defaults(run=cmd_send)

    poll = subparsers.add_parser(
        "poll",
        help="Antworten abholen, zuordnen, klassifizieren, Nachfassen terminieren (Stufe 10)",
        description=(
            "Holt eingegangene Antworten, ordnet sie den Entwuerfen zu und klassifiziert "
            "sie. Verschickt nichts - auch kein Nachfassen: das bleibt ein faelliger "
            "Eintrag, bis jemand ihn von Hand aufgreift."
        ),
    )
    poll.add_argument("--anzahl", dest="limit", type=int, default=None, help="Hoechstzahl")
    poll.set_defaults(run=cmd_poll)

    report = subparsers.add_parser(
        "report", help="Bestand, Stand der Stufen und die Kennzahlen"
    )
    report.set_defaults(run=cmd_report)

    return parser


def main(argv: Sequence[str] | None = None, stream: TextIO | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(list(argv) if argv is not None else None)
    out = stream if stream is not None else sys.stdout
    if getattr(args, "run", None) is None:
        parser.print_help(out)
        return 0
    try:
        return args.run(args, out)
    except AnlassError as exc:
        _out(out, f"Abbruch: {exc}")
        return 1
