"""Assembling the chain: which implementation fills which slot of the pipeline.

:mod:`anlass.pipeline` is programmed against the protocols and knows no class names.
This module is the one place that does: it reads the user's configuration and profile
and hands the pipeline a finished set of stages. Keeping the two apart is what lets a
test run the whole chain against stand-ins, and what lets a user replace one stage
without touching the chain.

Where each stage comes from
---------------------------

===== ================== ============================================================
Stufe Slot               Quelle der Wahl
===== ================== ============================================================
1+2   ``source``         ``sources.yaml`` im Profil, ueber ``anlass.sources.build_source``
3     ``enrichers``      fest: Seitenabruf, Organisationswebsite, Extraktion per Modell
4     ``scorer``         ``criteria.yaml`` im Profil
5     ``detector``       die Faktenbasis (ein Anlass ist ein Treffer gegen einen Fakt)
6+7   ``drafter``/``verifier`` das Modell aus ``llm``, je Stufe waehlbar
6b    ``critic``         ``rubric.yaml`` im Profil, sonst die mitgelieferte Rubrik
8     ``gate``           ``limits`` aus ``criteria.yaml``, Zaehlung aus dem Speicher
9     ``transport``      ``mailbox`` aus der Konfiguration
10    ``tracker``        ``mailbox`` aus der Konfiguration, nur bei IMAP-Angaben
===== ================== ============================================================

Two decisions worth stating, because they are choices and not necessities:

**The enrichment providers that reach the network are only wired in when the caller
asks for it.** ``build_pipeline(offline=True)`` leaves them out entirely, which is what
the dry run uses; the model-backed extraction stays either way because it talks to the
configured model and to nothing else.

**A source needs arguments, so it cannot come from a bare name.** The interview's
``sources`` entry records which kinds the user intends; the actual blocks (which file,
which feed, which page) live in ``sources.yaml`` next to the profile, in the shape
``profile.example/sources.example.yaml`` documents. A missing or empty file is not an
error here - it is reported where a user can act on it.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Mapping, Sequence

import yaml

from .critique import RubricCritic, rubric_for_profile
from .draft.generate import FactGroundedDrafter
from .draft.verify import GroundingVerifier
from .enrich.providers.extract import ExtractionProvider
from .enrich.providers.orgsite import OrgWebsiteProvider
from .enrich.providers.page import PageTextProvider
from .errors import AnlassError, ConfigError
from .gate.gate import RuleGate
from .gate.limits import SendLimits, StoreSendLog
from .interfaces import LLM, EnrichProvider, Source, Store, Tracker, Transport
from .llm.router import Stage as LLMStage
from .llm.router import LLMRouter, build_router
from .pipeline import Pipeline, Stage
from .profile import Profile
from .score.rules import RuleScorer
from .signal.detect import JobRequirementSignalDetector
from .sources import build_source
from .track.imap import ImapTracker
from .transport.file import FileTransport
from .transport.smtp import SmtpTransport

__all__ = [
    "SOURCES_FILE",
    "build_enrichers",
    "build_pipeline",
    "build_transport",
    "describe_pipeline",
    "load_source_specs",
]

#: Where the source blocks live, next to ``facts.yaml`` in the profile directory.
SOURCES_FILE = "sources.yaml"

#: German names the interview offers, mapped onto the registry keys of
#: :mod:`anlass.sources`. The interview asks in German, the registry is English; this
#: is that seam and not a second registry.
_SOURCE_ALIASES = {
    "datei": "file",
    "karriereseite": "careerpage",
    "jobapi": "openapi",
}


def load_source_specs(profile_dir: str | Path) -> list[dict[str, Any]]:
    """Read the source blocks of ``sources.yaml``. A missing file yields an empty list.

    Raises:
        anlass.errors.ConfigError: The file exists but is not a mapping with a list
            under ``sources``, or an entry is not a block.
    """
    path = Path(profile_dir) / SOURCES_FILE
    if not path.is_file():
        return []
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise ConfigError(f"Die Datei '{path}' ist kein gueltiges YAML: {exc}") from exc
    if data is None:
        return []
    if not isinstance(data, Mapping) or not isinstance(data.get("sources"), list):
        raise ConfigError(f"In '{path}' fehlt die Liste 'sources'.")
    specs: list[dict[str, Any]] = []
    for index, entry in enumerate(data["sources"], start=1):
        if not isinstance(entry, Mapping):
            raise ConfigError(f"Quelle {index} in '{path}' ist kein Objekt.")
        spec = dict(entry)
        kind = str(spec.get("type", "")).strip().lower()
        spec["type"] = _SOURCE_ALIASES.get(kind, kind)
        specs.append(spec)
    return specs


def build_sources(profile_dir: str | Path) -> list[Source]:
    """Every source configured in ``sources.yaml``, in file order.

    A relative ``path`` in a source block resolves against ``profile_dir``, because
    that is where the block naming it lives - not against the process's current
    working directory, which depends on wherever ``anlass`` happens to be run from
    and so silently fails to find a file the profile ships right next to itself
    (FINDINGS BEFUND 3 of Phase 9). Absolute paths are left untouched.
    """
    profile_dir = Path(profile_dir)
    return [
        build_source(_resolve_source_path(spec, profile_dir))
        for spec in load_source_specs(profile_dir)
    ]


def _resolve_source_path(spec: Mapping[str, Any], profile_dir: Path) -> dict[str, Any]:
    if "path" not in spec:
        return dict(spec)
    resolved = dict(spec)
    candidate = Path(str(resolved["path"]))
    resolved["path"] = str(candidate if candidate.is_absolute() else profile_dir / candidate)
    return resolved


def build_enrichers(router: LLMRouter, *, offline: bool = False) -> list[EnrichProvider]:
    """Stage 3 in waterfall order: cheap and verbatim first, model last.

    Order is the whole point of a waterfall - the first provider to fill a field keeps
    it, so the ones whose values come straight out of a document run before the one
    that reads prose and reports a confidence below 1.0.

    Args:
        offline: Leave out the two providers that fetch pages. The dry run sets this;
            so should anything that must not touch the network.
    """
    providers: list[EnrichProvider] = []
    if not offline:
        providers.append(OrgWebsiteProvider())
        providers.append(PageTextProvider())
    providers.append(ExtractionProvider(llm=router.for_stage(LLMStage.ENRICH)))
    return providers


def build_transport(config: Mapping[str, Any], *, home: Path | None = None) -> Transport:
    """Stage 9 from the ``mailbox`` section.

    ``datei`` is the default and does nothing to the outside world; ``smtp`` is the one
    that leaves the machine, and it reads its credentials from the environment at call
    time, never from the configuration.

    **Draft-only is the default.** ``mailbox.draft_only`` defaults to ``True``, and while
    it holds, asking for ``smtp`` is a configuration error rather than a send. Writing a
    draft to disk and putting a message on the wire differ by one word in a YAML file
    otherwise, and that is too thin a barrier for the one action in this tool that
    cannot be taken back. Turning it off is a deliberate edit to the configuration -
    there is no flag, no environment variable and no argument that does it for you.

    Raises:
        anlass.errors.ConfigError: Unknown transport, SMTP without a host, or SMTP while
            draft-only stands.
    """
    mailbox = config.get("mailbox") or {}
    if not isinstance(mailbox, Mapping):
        raise ConfigError("Der Abschnitt 'mailbox' muss eine Zuordnung sein.")
    kind = str(mailbox.get("transport", "datei")).strip().lower()
    draft_only = bool(mailbox.get("draft_only", True))
    if draft_only and kind == "smtp":
        raise ConfigError(
            "Versand ueber SMTP ist gesperrt: 'mailbox.draft_only' steht auf true, und "
            "damit werden Nachrichten nur als Entwurf abgelegt. Wenn wirklich verschickt "
            "werden soll, setze 'mailbox.draft_only: false' - bewusst, in der "
            "Konfigurationsdatei, wo die Aenderung sichtbar bleibt."
        )
    if kind in ("datei", "file"):
        directory = Path(str(mailbox.get("directory", "ausgang")))
        if not directory.is_absolute() and home is not None:
            directory = home / directory
        return FileTransport(directory=directory)
    if kind == "smtp":
        host = str(mailbox.get("host", "")).strip()
        if not host:
            raise ConfigError("Fuer den Versand ueber SMTP fehlt der Eintrag 'mailbox.host'.")
        return SmtpTransport(
            host=host,
            port=int(mailbox.get("port", 587)),
            sender=mailbox.get("sender") or None,
            password_env=str(mailbox.get("password_env", "ANLASS_SMTP_PASSWORD")),
        )
    raise ConfigError(f"Unbekannter Versandweg '{kind}'. Moeglich sind: datei, smtp.")


def build_tracker(config: Mapping[str, Any]) -> Tracker | None:
    """Stage 10 from the ``mailbox`` section, or ``None`` if no IMAP host is configured.

    ``None`` rather than a stand-in: a return channel that answers "no replies" without
    having looked would be worse than an empty slot, and the pipeline reports an empty
    slot as such.
    """
    mailbox = config.get("mailbox") or {}
    if not isinstance(mailbox, Mapping):
        return None
    host = str(mailbox.get("imap_host", "")).strip()
    if not host:
        return None
    return ImapTracker(
        host=host,
        port=int(mailbox.get("imap_port", 993)),
        username_env=str(mailbox.get("imap_username_env", "ANLASS_IMAP_USER")),
        password_env=str(mailbox.get("imap_password_env", "ANLASS_IMAP_PASSWORD")),
    )


def build_pipeline(
    *,
    profile: Profile,
    config: Mapping[str, Any],
    store: Store,
    source: Source | None = None,
    home: Path | None = None,
    llm: LLM | None = None,
    transport: Transport | None = None,
    offline: bool = False,
    verify_with_model: bool = False,
    actor: str = "nutzer",
    extra_grounding: Sequence[str] = (),
) -> Pipeline:
    """The whole chain, wired from configuration and profile.

    Args:
        profile: Fact base, voice and criteria. The criteria carry both the scoring
            rules (stage 4) and the limits the gate enforces (stage 8) - the same file
            in both places on purpose, so a threshold cannot drift between them.
        config: The loaded configuration file.
        store: Persistence. Not optional here: without it the score cannot be looked up
            at stage 8 and the daily cap does not survive a restart, and a chain that
            silently loses both is not the chain this tool promises.
        source: Stage 1. ``None`` leaves the slot open, which is what a command that
            works on stored leads wants.
        home: Installation directory, used to resolve a relative outbox path.
        llm: Override for every model-backed stage. Used by the dry run to put the
            stand-in everywhere at once.
        transport: Override for stage 9, same reason.
        offline: Leave out the enrichment providers that fetch pages.
        verify_with_model: Whether stage 7 also asks the model. Off by default, and
            that is a measured decision, not caution: the layer works (see the README's
            table) and costs five to forty seconds per draft, while the deterministic
            floor runs in milliseconds and offline. The truncation this docstring used
            to name as the reason was fixed before phase 3 (FINDINGS, BEFUND 4); what is
            left is the time. The floor decides on its own either way; the model layer
            may only add findings, never clear any.
        actor: Who is recorded in approval records.
        extra_grounding: Additional terms the verification accepts as grounded, e.g.
            the sender's own name.

    Raises:
        anlass.errors.ConfigError: The configuration has no ``llm`` section, or the
            criteria file has no ``limits``.
    """
    section = config.get("llm")
    if llm is None and not isinstance(section, Mapping):
        raise ConfigError("In der Konfiguration fehlt der Abschnitt 'llm'.")
    router = _router(section, llm)

    limits = SendLimits.from_criteria(profile.criteria)
    rubric = rubric_for_profile(profile.path)
    return Pipeline(
        facts=tuple(profile.facts),
        voice=profile.voice,
        source=source,
        enrichers=tuple(build_enrichers(router, offline=offline)),
        scorer=RuleScorer(profile.criteria),
        detector=JobRequirementSignalDetector(profile.facts),
        drafter=FactGroundedDrafter(llm=router.for_stage(LLMStage.DRAFT)),
        verifier=GroundingVerifier(
            llm=router.for_stage(LLMStage.VERIFY) if verify_with_model else None,
            extra_grounding=tuple(extra_grounding),
        ),
        critic=RubricCritic(
            rubric=rubric,
            # The rubric's own file decides whether a model is asked, not a caller and
            # not a flag: it is the same decision as the weights, and it belongs where a
            # user can see it. The checking stage's model is used, not the drafting one -
            # a generator must not grade its own text.
            llm=router.for_stage(LLMStage.VERIFY) if rubric.use_model else None,
        ),
        max_revisions=rubric.max_revisions,
        gate=RuleGate(
            store,
            limits,
            send_log=StoreSendLog(
                store, lookback_days=max(limits.days_between_same_organization, 1)
            ),
        ),
        transport=transport if transport is not None else build_transport(config, home=home),
        tracker=build_tracker(config),
        store=store,
        actor=actor,
    )


def describe_pipeline(
    *,
    profile: Profile,
    config: Mapping[str, Any],
    store: Store,
    source: Source | None = None,
    home: Path | None = None,
) -> list[str]:
    """Same lines as :meth:`anlass.pipeline.Pipeline.describe`, but a stage a bad
    configuration could not build says so, instead of reading like an open slot.

    ``build_pipeline`` builds all ten stages as one expression on purpose: a real run
    must fail loudly and immediately on a bad configuration, not limp along with half
    the stages missing. ``report`` has the opposite job - it must say what is wired on
    a half-finished install - and the single ``except AnlassError`` this used to sit
    behind blanked every stage the moment any *one* of them failed to build, even the
    nine that were fine (FINDINGS, BEFUND 9: ``mailbox.transport: smtp`` while
    ``draft_only`` holds made stage 9 report "noch nicht eingesetzt", the same text an
    honestly empty slot gets, and hid the configuration error that caused it).

    So the common case here is exactly ``build_pipeline(...).describe()`` - most
    installs build cleanly and this costs nothing extra. Only when that raises does it
    retry stage by stage, so the one stage a bad configuration actually blocks reports
    the message that blocked it, and the rest still show what they are. Every builder
    involved documents itself as raising :class:`~anlass.errors.ConfigError` except
    :meth:`~anlass.gate.limits.SendLimits.from_criteria`, which raises the bare
    :class:`~anlass.errors.AnlassError` - caught here rather than missed.
    """
    try:
        return build_pipeline(
            profile=profile, config=config, store=store, source=source, home=home
        ).describe()
    except AnlassError:
        pass

    section = config.get("llm")
    router: LLMRouter | None
    router_error: str | None
    if not isinstance(section, Mapping):
        router, router_error = None, "In der Konfiguration fehlt der Abschnitt 'llm'."
    else:
        try:
            router, router_error = build_router(section), None
        except AnlassError as exc:
            router, router_error = None, str(exc)

    lines: dict[Stage, str] = {Stage.SOURCE: source.name if source else ""}

    if router_error:
        lines[Stage.ENRICH] = f"gesperrt: {router_error}"
    else:
        try:
            lines[Stage.ENRICH] = ", ".join(p.name for p in build_enrichers(router, offline=False))
        except AnlassError as exc:
            lines[Stage.ENRICH] = f"gesperrt: {exc}"

    try:
        lines[Stage.SCORE] = RuleScorer(profile.criteria).name
    except AnlassError as exc:
        lines[Stage.SCORE] = f"gesperrt: {exc}"

    try:
        lines[Stage.SIGNAL] = JobRequirementSignalDetector(profile.facts).name
    except AnlassError as exc:
        lines[Stage.SIGNAL] = f"gesperrt: {exc}"

    if router_error:
        lines[Stage.DRAFT] = f"gesperrt: {router_error}"
    else:
        try:
            lines[Stage.DRAFT] = FactGroundedDrafter(llm=router.for_stage(LLMStage.DRAFT)).name
        except AnlassError as exc:
            lines[Stage.DRAFT] = f"gesperrt: {exc}"

    try:
        lines[Stage.VERIFY] = GroundingVerifier(llm=None).name
    except AnlassError as exc:
        lines[Stage.VERIFY] = f"gesperrt: {exc}"

    try:
        rubric, rubric_error = rubric_for_profile(profile.path), None
    except AnlassError as exc:
        rubric, rubric_error = None, str(exc)
    if rubric_error:
        lines[Stage.CRITIQUE] = f"gesperrt: {rubric_error}"
    else:
        try:
            lines[Stage.CRITIQUE] = RubricCritic(rubric=rubric, llm=None).name
        except AnlassError as exc:
            lines[Stage.CRITIQUE] = f"gesperrt: {exc}"

    try:
        limits = SendLimits.from_criteria(profile.criteria)
        gate = RuleGate(
            store, limits,
            send_log=StoreSendLog(store, lookback_days=max(limits.days_between_same_organization, 1)),
        )
        lines[Stage.GATE] = gate.name
    except AnlassError as exc:
        lines[Stage.GATE] = f"gesperrt: {exc}"

    try:
        lines[Stage.SEND] = build_transport(config, home=home).name
    except AnlassError as exc:
        lines[Stage.SEND] = f"gesperrt: {exc}"

    tracker = build_tracker(config)
    lines[Stage.TRACK] = tracker.name if tracker else ""

    return [f"{stage.label:14} {lines.get(stage) or 'noch nicht eingesetzt'}" for stage in Stage]


def _router(section: Mapping[str, Any] | None, llm: LLM | None) -> LLMRouter:
    """A router that answers with ``llm`` everywhere, or the configured one."""
    if llm is not None:
        return LLMRouter(default=llm)
    assert section is not None  # guarded by the caller
    return build_router(section)
