"""Model binding. No socket is opened: the HTTP layer is replaced per test.

The only process started here is a Python one-liner, to prove the subscription
provider really drives a foreign CLI.
"""

from __future__ import annotations

import sys

import pytest

from anlass.errors import ConfigError, LLMError
from anlass.llm import _http, build_llm, build_router
from anlass.llm.apikey import AnthropicLLM, OpenAILLM
from anlass.llm.fake import FakeLLM
from anlass.llm.local import OllamaLLM
from anlass.llm.router import LLMRouter, Stage
from anlass.llm.subscription import SubscriptionCLI


@pytest.fixture
def capture(monkeypatch):
    """Replace the HTTP layer and record what would have been sent."""
    calls: list[dict] = []

    def fake_post(url, payload, *, headers=None, timeout=120.0):
        calls.append({"url": url, "payload": payload, "headers": dict(headers or {}), "timeout": timeout})
        return calls[-1].get("_answer", fake_post.answer)

    fake_post.answer = {}
    monkeypatch.setattr(_http, "post_json", fake_post)
    return calls, fake_post


# ------------------------------------------------------------------------ local

def test_ollama_sends_prompt_and_reads_answer(capture):
    calls, fake_post = capture
    fake_post.answer = {"response": "  Antwort  "}
    llm = OllamaLLM(model="qwen3:8b", host="http://127.0.0.1:11434")
    assert llm.name == "ollama:qwen3:8b"
    assert llm.complete("Frage", system="Regeln", temperature=0.1, max_tokens=64) == "Antwort"
    call = calls[0]
    assert call["url"] == "http://127.0.0.1:11434/api/generate"
    assert call["payload"]["prompt"] == "Frage"
    assert call["payload"]["system"] == "Regeln"
    assert call["payload"]["stream"] is False
    assert call["payload"]["options"] == {"temperature": 0.1, "num_predict": 64}


def test_ollama_reports_an_unusable_answer(capture):
    _, fake_post = capture
    fake_post.answer = {"done": True}
    with pytest.raises(LLMError):
        OllamaLLM().complete("Frage")


def test_ollama_availability_is_false_when_unreachable(monkeypatch):
    def boom(*args, **kwargs):
        raise LLMError("nicht erreichbar")

    monkeypatch.setattr(_http, "get_json", boom)
    assert OllamaLLM().available() is False


def test_ollama_availability_checks_for_the_configured_model(monkeypatch):
    monkeypatch.setattr(_http, "get_json", lambda *a, **k: {"models": [{"name": "qwen3:8b"}]})
    assert OllamaLLM(model="qwen3:8b").available() is True
    assert OllamaLLM(model="anderes:8b").available() is False


# ----------------------------------------------------------------------- api key

def test_anthropic_without_a_key_explains_where_it_looks(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    llm = AnthropicLLM()
    assert llm.available() is False
    with pytest.raises(LLMError) as excinfo:
        llm.complete("Frage")
    assert "ANTHROPIC_API_KEY" in str(excinfo.value)


def test_anthropic_reads_the_key_from_the_environment(capture, monkeypatch):
    calls, fake_post = capture
    fake_post.answer = {"content": [{"type": "text", "text": "Antwort"}]}
    monkeypatch.setenv("ANTHROPIC_API_KEY", "geheim")
    llm = AnthropicLLM(model="claude-sonnet-5")
    assert llm.complete("Frage", system="Regeln") == "Antwort"
    assert calls[0]["headers"]["x-api-key"] == "geheim"
    assert calls[0]["payload"]["system"] == "Regeln"
    assert llm.name == "anthropic:claude-sonnet-5"


def test_openai_reads_the_key_and_parses_the_answer(capture, monkeypatch):
    calls, fake_post = capture
    fake_post.answer = {"choices": [{"message": {"content": "Antwort"}}]}
    monkeypatch.setenv("OPENAI_API_KEY", "geheim")
    assert OpenAILLM().complete("Frage", system="Regeln") == "Antwort"
    assert calls[0]["headers"]["Authorization"] == "Bearer geheim"
    assert calls[0]["payload"]["messages"][0] == {"role": "system", "content": "Regeln"}


# ------------------------------------------------------------------ subscription

def test_subscription_passes_the_prompt_on_stdin():
    llm = SubscriptionCLI(
        command=[sys.executable, "-c", "import sys; sys.stdout.write(sys.stdin.read().upper())"],
        label="testcli",
    )
    assert llm.complete("hallo") == "HALLO"
    assert llm.name == "subscription:testcli"
    assert llm.available() is True


def test_subscription_fills_the_placeholder_in_the_arguments():
    llm = SubscriptionCLI(
        command=[sys.executable, "-c", "import sys; print(sys.argv[1])", "{prompt}"],
        prompt_via="argument",
    )
    assert llm.complete("hallo") == "hallo"


def test_subscription_appends_the_prompt_without_a_placeholder():
    llm = SubscriptionCLI(command=["cli", "--print"], prompt_via="argument")
    argv, stdin = llm._build_call("hallo", None)
    assert argv == ["cli", "--print", "hallo"]
    assert stdin is None


def test_subscription_does_not_duplicate_the_system_prompt():
    llm = SubscriptionCLI(command=["cli", "--system", "{system}"])
    argv, stdin = llm._build_call("hallo", "Regeln")
    assert argv == ["cli", "--system", "Regeln"]
    assert stdin == "hallo"


def test_subscription_prepends_the_system_prompt_when_the_cli_has_no_slot():
    llm = SubscriptionCLI(command=["cli"])
    _, stdin = llm._build_call("hallo", "Regeln")
    assert stdin == "Regeln\n\nhallo"


def test_subscription_reports_a_failing_command():
    llm = SubscriptionCLI(command=[sys.executable, "-c", "import sys; sys.stderr.write('kaputt'); sys.exit(3)"])
    with pytest.raises(LLMError) as excinfo:
        llm.complete("hallo")
    assert "kaputt" in str(excinfo.value)


def test_subscription_reports_a_missing_command():
    llm = SubscriptionCLI(command=["gibt-es-sicher-nicht-12345"])
    assert llm.available() is False
    with pytest.raises(LLMError):
        llm.complete("hallo")


def test_subscription_rejects_an_unknown_prompt_mode():
    with pytest.raises(ValueError):
        SubscriptionCLI(command=["cli"], prompt_via="telepathie")


# ------------------------------------------------------------------------- fake

def test_fake_is_deterministic_and_records_prompts():
    llm = FakeLLM(answers=["eins", "zwei"])
    assert [llm.complete("a"), llm.complete("b"), llm.complete("c")] == ["eins", "zwei", "zwei"]
    assert llm.prompts == ["a", "b", "c"]


def test_fake_answers_by_rule_and_can_fail():
    assert FakeLLM(rules={"Faktenliste": "gefunden"}).complete("... Faktenliste ...") == "gefunden"
    with pytest.raises(LLMError):
        FakeLLM(fail_with="Anbieter aus").complete("a")


# ------------------------------------------------------------------ per stage

def test_router_hands_out_the_model_configured_for_a_stage():
    default, drafting = FakeLLM(label="lokal"), FakeLLM(label="stark")
    router = LLMRouter(default=default, per_stage={Stage.DRAFT: drafting})
    assert router.for_stage(Stage.DRAFT) is drafting
    assert router.for_stage("draft") is drafting
    assert router.for_stage(Stage.ENRICH) is default
    assert router.describe()["verify"] == "fake:lokal"


def test_router_is_built_from_configuration():
    router = build_router(
        {
            "default": {"provider": "ollama", "model": "qwen3:8b"},
            "stages": {
                "draft": {"provider": "anthropic", "model": "claude-sonnet-5"},
                "verify": {"provider": "subscription", "command": ["cli"]},
            },
        }
    )
    assert router.for_stage(Stage.ENRICH).name == "ollama:qwen3:8b"
    assert router.for_stage(Stage.DRAFT).name == "anthropic:claude-sonnet-5"
    assert router.for_stage(Stage.VERIFY).name == "subscription:cli"


@pytest.mark.parametrize(
    "config",
    [
        {"stages": {}},
        {"default": {"provider": "gibtsnicht"}},
        {"default": {"provider": "ollama"}, "stages": {"malen": {"provider": "ollama"}}},
        {"default": {"provider": "ollama", "unbekannt": 1}},
    ],
)
def test_bad_configuration_says_what_is_wrong(config):
    with pytest.raises(ConfigError):
        build_router(config)


def test_build_llm_needs_a_provider():
    with pytest.raises(ConfigError):
        build_llm({"model": "qwen3:8b"})
