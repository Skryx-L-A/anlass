"""Assembling the chain: every slot filled, and nothing filled that must not be.

This is the phase-3 seam. Until now the pipeline had ten slots and nobody put anything
in them; these tests check that the wiring does, that it does it from the user's own
files, and that the two providers which reach the network stay out when they are told
to. No test here opens a socket or starts a model.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from anlass.errors import AnlassError, ConfigError
from anlass.gate.gate import RuleGate
from anlass.gate.limits import StoreSendLog
from anlass.llm.fake import FakeLLM
from anlass.models import Fact
from anlass.pipeline import Stage
from anlass.profile import Profile
from anlass.setup.sample import SAMPLE_CRITERIA, SampleSource
from anlass.store.sqlite import SqliteStore
from anlass.transport.file import FileTransport
from anlass.transport.smtp import SmtpTransport
from anlass.wiring import (
    build_enrichers,
    build_pipeline,
    build_sources,
    build_transport,
    describe_pipeline,
    load_source_specs,
)

CONFIG = {
    "llm": {"default": {"provider": "fake"}},
    "mailbox": {"transport": "datei", "directory": "ausgang", "sender": "ich@beispiel.example"},
}


@pytest.fixture
def profile() -> Profile:
    return Profile(
        facts=[Fact(id="eins", claim="Eine belegbare Aussage.", source="Datei X, 01.01.2026")],
        voice="Sachlich und knapp.",
        criteria=dict(SAMPLE_CRITERIA),
    )


@pytest.fixture
def store():
    with SqliteStore() as instance:
        instance.migrate()
        yield instance


# ------------------------------------------------------------------- every slot


def test_every_stage_of_the_plan_is_filled(profile, store, tmp_path):
    """All ten stages, from the source to the return channel, with nothing left open."""
    config = dict(CONFIG, mailbox=dict(CONFIG["mailbox"], imap_host="imap.beispiel.example"))
    pipeline = build_pipeline(
        profile=profile,
        config=config,
        store=store,
        source=SampleSource(),
        home=tmp_path,
    )

    described = "\n".join(pipeline.describe())
    assert "noch nicht eingesetzt" not in described, described
    for stage in Stage:
        assert stage.label in described
    assert isinstance(pipeline.gate, RuleGate)
    assert pipeline.store is store


def test_without_an_imap_host_the_return_channel_stays_empty(profile, store, tmp_path):
    """No stand-in for stage 10: 'nobody answered' must not be said without looking."""
    pipeline = build_pipeline(profile=profile, config=CONFIG, store=store, home=tmp_path)
    assert pipeline.tracker is None
    assert "Rueckkanal     noch nicht eingesetzt" in "\n".join(pipeline.describe())


def test_the_source_slot_may_stay_open(profile, store, tmp_path):
    """A command working on stored leads has no source, and that is not a defect."""
    pipeline = build_pipeline(profile=profile, config=CONFIG, store=store, home=tmp_path)
    assert pipeline.source is None
    assert Stage.SOURCE.label in "\n".join(pipeline.describe())


def test_the_gate_counts_out_of_the_store_not_out_of_memory(profile, store, tmp_path):
    pipeline = build_pipeline(profile=profile, config=CONFIG, store=store, home=tmp_path)
    assert isinstance(pipeline.gate._log, StoreSendLog)


def test_the_limits_come_from_the_users_criteria_file(profile, store, tmp_path):
    profile.criteria = dict(profile.criteria, limits={
        "max_sends_per_day": 1, "days_between_same_organization": 90, "min_score": 12
    })
    pipeline = build_pipeline(profile=profile, config=CONFIG, store=store, home=tmp_path)
    assert pipeline.gate._limits.min_score == 12
    assert pipeline.gate._limits.max_sends_per_day == 1


def test_criteria_without_limits_are_refused(profile, store, tmp_path):
    """Falling back to a hidden default would make the rule depend on nobody's number."""
    profile.criteria = {"criteria": profile.criteria["criteria"]}
    with pytest.raises(AnlassError):
        build_pipeline(profile=profile, config=CONFIG, store=store, home=tmp_path)


def test_describe_names_the_stage_a_bad_configuration_actually_blocks(profile, store, tmp_path):
    """FINDINGS BEFUND 9. The one failing builder used to blank all ten stages via a
    single ``except`` around the whole assembly; the other nine must still say what
    they are. Uses a different failing builder than the one measured in the CLI test
    (missing criteria limits, not a locked transport) to show the fix is not specific
    to that one case - and this one raises the bare ``AnlassError``, not
    ``ConfigError``, which the first version of this fix (wrongly) did not catch."""
    profile.criteria = {"criteria": profile.criteria["criteria"]}
    described = "\n".join(describe_pipeline(profile=profile, config=CONFIG, store=store, home=tmp_path))
    assert "Freigabe       gesperrt: In der Kriteriendatei fehlen die Grenzen" in described
    assert "Entwurf        fact-grounded:fake" in described
    assert "Pruefung       grounding" in described


def test_describe_still_works_when_the_configuration_is_fine(profile, store, tmp_path):
    """The common path costs nothing extra: same lines as ``Pipeline.describe()``."""
    pipeline = build_pipeline(profile=profile, config=CONFIG, store=store, home=tmp_path)
    described = describe_pipeline(profile=profile, config=CONFIG, store=store, home=tmp_path)
    assert described == pipeline.describe()


def test_a_configuration_without_a_model_says_so(profile, store, tmp_path):
    with pytest.raises(ConfigError):
        build_pipeline(profile=profile, config={"mailbox": {}}, store=store, home=tmp_path)


# ---------------------------------------------------------------- the two layers


def test_offline_leaves_out_everything_that_would_fetch_a_page():
    from anlass.llm.router import LLMRouter

    router = LLMRouter(default=FakeLLM())
    names = [p.name for p in build_enrichers(router, offline=True)]
    assert names == [f"extract:{FakeLLM().name}"]
    assert "page" not in names and "orgsite" not in names


def test_the_waterfall_puts_the_verbatim_providers_before_the_model():
    """First provider to fill a field keeps it, so the cheap and literal ones run first."""
    from anlass.llm.router import LLMRouter

    names = [p.name for p in build_enrichers(LLMRouter(default=FakeLLM()))]
    assert names[:2] == ["orgsite", "page"]
    assert names[-1].startswith("extract:")


def test_the_model_layer_of_the_verification_is_off_unless_asked_for(profile, store, tmp_path):
    """Measured decision, not caution - see the README table and the wiring docstring."""
    off = build_pipeline(profile=profile, config=CONFIG, store=store, home=tmp_path)
    on = build_pipeline(
        profile=profile, config=CONFIG, store=store, home=tmp_path, verify_with_model=True
    )
    assert off.verifier.llm is None
    assert on.verifier.llm is not None


# --------------------------------------------------------------------- transport


def test_the_file_transport_lands_inside_the_installation(tmp_path):
    transport = build_transport(CONFIG, home=tmp_path)
    assert isinstance(transport, FileTransport)
    assert transport.directory == tmp_path / "ausgang"


def test_an_absolute_outbox_is_left_alone(tmp_path):
    config = {"mailbox": {"transport": "datei", "directory": str(tmp_path / "woanders")}}
    assert build_transport(config, home=tmp_path).directory == tmp_path / "woanders"


def test_smtp_is_built_from_the_configuration_and_reads_no_secret(tmp_path):
    # draft_only defaults to True and refuses SMTP outright, so every test that wants a
    # real SmtpTransport has to switch it off the same way a user would.
    config = {
        "mailbox": {
            "transport": "smtp",
            "draft_only": False,
            "host": "smtp.beispiel.example",
            "port": 2525,
            "sender": "ich@beispiel.example",
            "password_env": "ANLASS_TEST_PASSWORT",
        }
    }
    transport = build_transport(config, home=tmp_path)
    assert isinstance(transport, SmtpTransport)
    assert transport.host == "smtp.beispiel.example" and transport.port == 2525
    assert transport.password_env == "ANLASS_TEST_PASSWORT"


def test_smtp_without_a_host_is_refused(tmp_path):
    # draft_only off, so the missing host is what this test is actually about.
    with pytest.raises(ConfigError) as excinfo:
        build_transport(
            {"mailbox": {"transport": "smtp", "draft_only": False}}, home=tmp_path
        )
    assert "mailbox.host" in str(excinfo.value)


def test_an_unknown_transport_names_the_ones_that_exist(tmp_path):
    with pytest.raises(ConfigError) as excinfo:
        build_transport({"mailbox": {"transport": "brieftaube"}}, home=tmp_path)
    assert "datei, smtp" in str(excinfo.value)


# ----------------------------------------------------------------------- sources


def test_sources_come_from_the_profile_file(tmp_path):
    (tmp_path / "sources.yaml").write_text(
        "sources:\n  - type: file\n    path: ./stellen.json\n", encoding="utf-8"
    )
    specs = load_source_specs(tmp_path)
    assert specs == [{"type": "file", "path": "./stellen.json"}]
    assert [s.name for s in build_sources(tmp_path)] == ["file"]


def test_a_relative_source_path_resolves_against_the_profile_not_the_working_directory(
    tmp_path, monkeypatch
):
    """FINDINGS BEFUND 3 (Phase 9): 'path: ausschreibungen.json' next to 'facts.yaml'
    in the profile was resolved against the process's current working directory
    instead, so it was never found unless the profile happened to also be the
    directory ``anlass`` was started from."""
    (tmp_path / "sources.yaml").write_text(
        "sources:\n  - type: file\n    path: stellen.json\n", encoding="utf-8"
    )
    (tmp_path / "stellen.json").write_text("[]", encoding="utf-8")
    elsewhere = tmp_path / "anderswo"
    elsewhere.mkdir()
    monkeypatch.chdir(elsewhere)

    sources = build_sources(tmp_path)

    assert len(sources) == 1
    assert Path(sources[0].path) == tmp_path / "stellen.json"


def test_an_absolute_source_path_is_left_alone(tmp_path):
    absolute = tmp_path / "anderswo" / "stellen.json"
    absolute.parent.mkdir()
    absolute.write_text("[]", encoding="utf-8")
    (tmp_path / "sources.yaml").write_text(
        f"sources:\n  - type: file\n    path: {absolute}\n", encoding="utf-8"
    )

    sources = build_sources(tmp_path)

    assert Path(sources[0].path) == absolute


def test_the_german_names_the_interview_offers_are_translated(tmp_path):
    """The interview asks in German, the registry is English. One seam, not two lists."""
    (tmp_path / "sources.yaml").write_text(
        "sources:\n"
        "  - type: datei\n    path: ./stellen.json\n"
        "  - type: karriereseite\n    url: https://beispiel.example/karriere\n",
        encoding="utf-8",
    )
    assert [spec["type"] for spec in load_source_specs(tmp_path)] == ["file", "careerpage"]


def test_a_missing_sources_file_is_not_an_error(tmp_path):
    assert load_source_specs(tmp_path) == []
    assert build_sources(tmp_path) == []


def test_a_malformed_sources_file_says_what_is_missing(tmp_path):
    (tmp_path / "sources.yaml").write_text("quellen: []\n", encoding="utf-8")
    with pytest.raises(ConfigError) as excinfo:
        load_source_specs(tmp_path)
    assert "sources" in str(excinfo.value)


def test_an_unknown_source_type_names_the_known_ones(tmp_path):
    (tmp_path / "sources.yaml").write_text("sources:\n  - type: linkedin\n", encoding="utf-8")
    with pytest.raises(ConfigError) as excinfo:
        build_sources(tmp_path)
    assert "Unbekannte Quelle" in str(excinfo.value)


# --------------------------------------------------------------- draft-only is default


def test_smtp_is_refused_while_draft_only_stands():
    """The default must be draft-only: asking for SMTP is an error, not a send."""
    import pytest
    from anlass.errors import ConfigError
    from anlass.wiring import build_transport

    config = {"mailbox": {"transport": "smtp", "host": "smtp.example.org"}}
    with pytest.raises(ConfigError) as caught:
        build_transport(config)
    assert "draft_only" in str(caught.value)


def test_smtp_needs_draft_only_switched_off_deliberately():
    from anlass.transport.smtp import SmtpTransport
    from anlass.wiring import build_transport

    config = {
        "mailbox": {"transport": "smtp", "host": "smtp.example.org", "draft_only": False}
    }
    assert isinstance(build_transport(config), SmtpTransport)


def test_default_transport_writes_files_and_needs_no_switch(tmp_path):
    from anlass.transport.file import FileTransport
    from anlass.wiring import build_transport

    transport = build_transport({"mailbox": {"sender": "a@example.org"}}, home=tmp_path)
    assert isinstance(transport, FileTransport)
