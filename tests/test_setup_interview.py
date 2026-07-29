"""The setup: interview, test call, scaffolding.

Nothing in here touches a real configuration directory, a real model or the network.
The one provider that is actually reached is a Python one-liner this file starts itself,
driven through the subscription provider - that is a real subprocess and a real test
call, and it stays on this machine.
"""

from __future__ import annotations

import sys

import pytest
import yaml

from anlass.errors import ConfigError, LLMError, SetupError
from anlass.llm.fake import FakeLLM
from anlass.llm.router import build_router
from anlass.setup import (
    ConsoleAsker,
    DefaultAsker,
    Paths,
    ScriptedAsker,
    ask_llm,
    backup_file,
    config_from_file,
    config_from_mapping,
    load_config,
    probe_llm,
    resolve_paths,
    run_interview,
    save_config,
    scaffold,
)
from anlass.setup.config import HOME_ENV

LOCAL_ANSWERS = ["linux", "lokal", "qwen3:8b", "http://127.0.0.1:11434",
                 "datei", "post@beispiel.example", "ausgang", "datei, rss"]


def echo_command() -> str:
    """A subscription provider that is a Python one-liner. No network, no model."""
    return "{} -c {}".format(sys.executable, repr("print('bereit')"))


# ------------------------------------------------------------------------ interview


def test_the_interview_asks_the_four_things_and_writes_a_usable_block():
    asker = ScriptedAsker(list(LOCAL_ANSWERS))
    config = run_interview(asker)

    assert config["os"] == "linux"
    assert config["llm"]["default"] == {
        "provider": "ollama",
        "model": "qwen3:8b",
        "host": "http://127.0.0.1:11434",
    }
    assert config["mailbox"]["transport"] == "datei"
    assert config["sources"] == ["datei", "rss"]
    assert not asker.answers, "es blieben Antworten uebrig"


def test_the_model_block_is_what_the_router_expects():
    config = run_interview(ScriptedAsker(list(LOCAL_ANSWERS)))
    router = build_router(config["llm"])
    assert router.default.name == "ollama:qwen3:8b"


def test_a_second_run_offers_the_previous_answers_as_defaults():
    """Answers are kept, so a second run is a matter of pressing enter."""
    first = run_interview(ScriptedAsker(list(LOCAL_ANSWERS)))
    empty = ScriptedAsker([""] * len(LOCAL_ANSWERS))
    second = run_interview(empty, first)

    assert second["os"] == first["os"]
    assert second["llm"]["default"] == first["llm"]["default"]
    assert second["mailbox"] == first["mailbox"]
    assert second["sources"] == first["sources"]


def test_the_key_itself_is_never_asked_for_and_never_written_down():
    answers = ["darwin", "schluessel", "anthropic", "claude-sonnet-5", "MEIN_SCHLUESSEL",
               "datei", "post@beispiel.example", "ausgang", "datei"]
    asker = ScriptedAsker(answers)
    config = run_interview(asker)

    assert config["llm"]["default"]["api_key_env"] == "MEIN_SCHLUESSEL"
    assert "api_key" not in config["llm"]["default"]
    written = yaml.safe_dump(config)
    assert "sk-" not in written
    for question in asker.asked:
        assert "Schluessel selbst" not in question or "nicht gespeichert" in question


def test_the_smtp_password_is_only_a_variable_name():
    answers = ["darwin", "lokal", "qwen3:8b", "http://127.0.0.1:11434",
               "smtp", "post@beispiel.example", "mail.beispiel.example", "587",
               "postfach", "MEIN_PASSWORT", "datei"]
    config = run_interview(ScriptedAsker(answers))

    assert config["mailbox"]["password_env"] == "MEIN_PASSWORT"
    assert "password" not in config["mailbox"]
    assert config["mailbox"]["port"] == 587


def test_the_subscription_command_is_split_into_arguments():
    answers = ["darwin", "abo", echo_command(), "stdin",
               "datei", "post@beispiel.example", "ausgang", "datei"]
    config = run_interview(ScriptedAsker(answers))

    block = config["llm"]["default"]
    assert block["provider"] == "subscription"
    assert block["command"][0] == sys.executable
    assert block["command"][1] == "-c"


def test_ask_llm_alone_does_not_ask_the_other_three_questions():
    asker = ScriptedAsker(["lokal", "qwen3:8b", "http://127.0.0.1:11434"])
    block = ask_llm(asker, {})

    assert block["default"]["provider"] == "ollama"
    assert not any("Postfach" in question for question in asker.asked)


def test_a_scripted_asker_runs_out_loudly_instead_of_guessing():
    with pytest.raises(AssertionError):
        run_interview(ScriptedAsker(["linux"]))


# ---------------------------------------------------------------------- test call


def test_the_test_call_reaches_a_real_command():
    config = run_interview(
        ScriptedAsker(["darwin", "abo", echo_command(), "stdin",
                       "datei", "post@beispiel.example", "ausgang", "datei"])
    )
    result = probe_llm(build_router(config["llm"]).default)

    assert result.ok is True
    assert "bereit" in result.answer
    assert "OK" in result.line()


def test_a_provider_that_is_not_there_fails_the_test_call_without_raising():
    config = {"default": {"provider": "subscription", "command": ["gibt-es-nicht-hoffentlich"]}}
    result = probe_llm(build_router(config).default)

    assert result.ok is False
    assert "meldet sich nicht" in result.detail


def test_a_provider_that_answers_with_nothing_fails_the_test_call():
    result = probe_llm(FakeLLM(answers=["   "]))
    assert result.ok is False
    assert "leeren" in result.detail


def test_reachable_but_not_answering_is_told_apart_from_not_reachable():
    """The two failures need different sentences: one is a service, one is a call."""
    def explode(prompt: str, system: str | None) -> str:
        raise LLMError("das Modell brach den Aufruf ab")

    reachable = probe_llm(FakeLLM(handler=explode))
    assert reachable.ok is False
    assert "antwortete aber nicht" in reachable.detail

    unreachable = probe_llm(FakeLLM(fail_with="Ollama ist nicht erreichbar"))
    assert unreachable.ok is False
    assert "meldet sich nicht" in unreachable.detail


# ------------------------------------------------------------------------ paths


def test_an_explicit_directory_wins(tmp_path):
    assert resolve_paths(tmp_path).home == tmp_path


def test_an_explicit_configuration_file_names_its_directory(tmp_path):
    assert resolve_paths(tmp_path / "config.yaml").home == tmp_path


def test_the_environment_variable_is_used_when_nothing_is_passed(tmp_path, monkeypatch):
    monkeypatch.setenv(HOME_ENV, str(tmp_path / "woanders"))
    assert resolve_paths().home == tmp_path / "woanders"


def test_paths_are_derived_from_the_home_directory(tmp_path):
    paths = Paths(home=tmp_path)
    assert paths.config == tmp_path / "config.yaml"
    assert paths.profile == tmp_path / "profile"
    assert paths.database == tmp_path / "anlass.db"


# -------------------------------------------------------------------- scaffolding


def test_scaffolding_writes_configuration_and_a_profile_skeleton(tmp_path):
    paths = Paths(home=tmp_path)
    config = run_interview(ScriptedAsker(list(LOCAL_ANSWERS)))
    result = scaffold(paths, config)

    assert paths.config.is_file()
    for name in ("facts.yaml", "criteria.yaml", "sources.yaml", "voice.md"):
        assert (paths.profile / name).is_file()
    assert result.backed_up == []
    assert (tmp_path / "ausgang").is_dir()


def test_the_sources_skeleton_is_loadable_and_empty(tmp_path):
    """It parses as it stands and configures nothing - 'anlass fetch' then says where."""
    from anlass.wiring import load_source_specs

    paths = Paths(home=tmp_path)
    scaffold(paths, run_interview(ScriptedAsker(list(LOCAL_ANSWERS))))

    assert load_source_specs(paths.profile) == []
    assert "linkedin" in (paths.profile / "sources.yaml").read_text(encoding="utf-8").lower()


def test_the_written_skeleton_can_be_loaded(tmp_path):
    from anlass.profile import load_profile

    paths = Paths(home=tmp_path)
    scaffold(paths, run_interview(ScriptedAsker(list(LOCAL_ANSWERS))))
    profile = load_profile(paths.profile)

    assert profile.facts, "das Geruest muss ladbar sein"
    assert profile.criteria["limits"]["min_score"] >= 1
    assert profile.voice


def test_the_skeleton_carries_no_foreign_facts(tmp_path):
    """A skeleton pre-filled with somebody else's facts is how they get sent."""
    paths = Paths(home=tmp_path)
    scaffold(paths, run_interview(ScriptedAsker(list(LOCAL_ANSWERS))))
    facts = (paths.profile / "facts.yaml").read_text(encoding="utf-8")

    assert "platzhalter" in facts
    assert "3,8 Sekunden" not in facts


def test_a_second_run_does_not_destroy_an_existing_profile(tmp_path):
    paths = Paths(home=tmp_path)
    config = run_interview(ScriptedAsker(list(LOCAL_ANSWERS)))
    scaffold(paths, config)
    (paths.profile / "facts.yaml").write_text(
        "facts:\n  - id: meins\n    claim: Meine Aussage.\n    source: Mein Beleg.\n",
        encoding="utf-8",
    )

    result = scaffold(paths, config)

    assert (paths.profile / "facts.yaml").read_text(encoding="utf-8").count("meins") == 1
    assert paths.profile / "facts.yaml" in result.kept
    assert any(path.name.startswith("config.yaml.bak-") for path in result.backed_up)


def test_replacing_the_profile_puts_the_old_one_aside_first(tmp_path):
    paths = Paths(home=tmp_path)
    config = run_interview(ScriptedAsker(list(LOCAL_ANSWERS)))
    scaffold(paths, config)
    (paths.profile / "facts.yaml").write_text(
        "facts:\n  - id: meins\n    claim: Meine Aussage.\n    source: Mein Beleg.\n",
        encoding="utf-8",
    )

    result = scaffold(paths, config, replace_profile=True)
    saved = [p for p in result.backed_up if p.name.startswith("facts.yaml.bak-")]

    assert saved, "die alte Faktenbasis muss gesichert sein"
    assert "meins" in saved[0].read_text(encoding="utf-8")
    assert "platzhalter" in (paths.profile / "facts.yaml").read_text(encoding="utf-8")


def test_two_backups_of_the_same_file_do_not_overwrite_each_other(tmp_path):
    target = tmp_path / "facts.yaml"
    target.write_text("eins", encoding="utf-8")
    first = backup_file(target)
    second = backup_file(target)

    assert first is not None and second is not None and first != second


def test_backing_up_something_that_is_not_there_is_not_an_error(tmp_path):
    assert backup_file(tmp_path / "gibtsnicht.yaml") is None


# ------------------------------------------- a setup without a keyboard (BEFUND 8)


def test_an_ended_input_is_a_sentence_and_not_a_stack_trace(monkeypatch):
    """``printf '' | anlass init`` used to end in ``EOFError`` from ``interview.py``."""
    def closed_stdin(prompt: str = "") -> str:
        raise EOFError("EOF when reading a line")

    monkeypatch.setattr("builtins.input", closed_stdin)
    with pytest.raises(SetupError) as excinfo:
        ConsoleAsker().ask("Absenderadresse")

    message = str(excinfo.value)
    assert "Absenderadresse" in message
    assert "--aus-datei" in message, "die Meldung muss den Ausweg nennen"


def test_an_interrupt_ends_the_setup_without_writing(monkeypatch):
    def interrupted(prompt: str = "") -> str:
        raise KeyboardInterrupt

    monkeypatch.setattr("builtins.input", interrupted)
    with pytest.raises(SetupError) as excinfo:
        ConsoleAsker().ask("Betriebssystem", default="darwin")
    assert "Abgebrochen" in str(excinfo.value)


def test_without_questions_every_default_is_taken():
    config = run_interview(DefaultAsker(), {"mailbox": {"sender": "post@beispiel.example"}})

    assert config["llm"]["default"]["provider"] == "ollama"
    assert config["mailbox"]["sender"] == "post@beispiel.example"
    assert config["sources"] == ["datei"]


def test_a_mandatory_entry_without_a_default_is_named_instead_of_looping():
    """At a terminal an empty answer repeats the question; in a pipe that never ends."""
    with pytest.raises(SetupError) as excinfo:
        run_interview(DefaultAsker())
    assert "Absenderadresse" in str(excinfo.value)


def test_a_setup_file_has_the_same_keys_as_the_written_configuration(tmp_path):
    written = run_interview(ScriptedAsker(list(LOCAL_ANSWERS)))
    path = tmp_path / "einrichtung.yaml"
    path.write_text(yaml.safe_dump(written, allow_unicode=True), encoding="utf-8")

    from_file = config_from_file(path)
    assert from_file["llm"]["default"] == written["llm"]["default"]
    assert from_file["mailbox"] == written["mailbox"]
    assert from_file["sources"] == written["sources"]


def test_a_setup_file_fills_in_what_has_a_sensible_default():
    config = config_from_mapping(
        {"llm": {"default": {"provider": "ollama", "model": "qwen3:8b"}},
         "mailbox": {"transport": "datei"}}
    )

    assert config["profile"] == {"path": "profile"}
    assert config["storage"] == {"path": "anlass.db"}
    assert config["llm"]["stages"] == {}
    assert config["sources"] == []


@pytest.mark.parametrize(
    "data, expected",
    [
        ({"mailbox": {"transport": "datei"}}, "llm.default"),
        ({"llm": {"default": {"provider": "ollama"}}}, "mailbox"),
        ({"llm": {"default": {"provider": "ollama"}}, "mailbox": {}}, "mailbox.transport"),
        (
            {"llm": {"default": {"provider": "ollama"}}, "mailbox": {"transport": "smtp"}},
            "mailbox.host",
        ),
        (
            {"llm": {"default": {"provider": "ollama"}}, "mailbox": {"transport": "brieftaube"}},
            "brieftaube",
        ),
    ],
)
def test_a_missing_mandatory_entry_is_named_never_guessed(data, expected):
    with pytest.raises(ConfigError) as excinfo:
        config_from_mapping(data)
    assert expected in str(excinfo.value)


def test_a_setup_file_that_is_not_there_is_named(tmp_path):
    with pytest.raises(ConfigError) as excinfo:
        config_from_file(tmp_path / "gibtsnicht.yaml")
    assert "gibtsnicht.yaml" in str(excinfo.value)


# ------------------------------------------------------------------ configuration


def test_a_missing_configuration_reads_as_empty(tmp_path):
    assert load_config(Paths(home=tmp_path)) == {}


def test_a_broken_configuration_is_named(tmp_path):
    paths = Paths(home=tmp_path)
    paths.home.mkdir(exist_ok=True)
    paths.config.write_text("- eine Liste, kein Objekt\n", encoding="utf-8")

    with pytest.raises(ConfigError) as excinfo:
        load_config(paths)
    assert "config.yaml" in str(excinfo.value)


def test_what_was_saved_comes_back(tmp_path):
    paths = Paths(home=tmp_path)
    config = run_interview(ScriptedAsker(list(LOCAL_ANSWERS)))
    save_config(paths, config)

    assert load_config(paths) == config


def test_the_written_configuration_says_that_it_holds_no_access_data(tmp_path):
    paths = Paths(home=tmp_path)
    save_config(paths, run_interview(ScriptedAsker(list(LOCAL_ANSWERS))))
    assert "keine Zugangsdaten" in paths.config.read_text(encoding="utf-8")
