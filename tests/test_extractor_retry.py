"""Retry policy of app.extractor.extract(), with a fake Anthropic client (no network)."""

import json
from pathlib import Path
from types import SimpleNamespace

import anthropic
import httpx2
import pydantic
import pytest

from app import extractor
from app.schema import ExtractedDocument, WireDocument

ROOT = Path(__file__).resolve().parent.parent
PDF = (ROOT / "samples" / "orden-compra-andina.pdf").read_bytes()
TRUTH = json.loads((ROOT / "samples" / "truth" / "orden-compra-andina.json").read_text(encoding="utf-8"))
DOC = ExtractedDocument.model_validate({k: v for k, v in TRUTH.items() if not k.startswith("_")})
REQ = httpx2.Request("POST", "https://api.anthropic.com/v1/messages")


def ok_response():
    return SimpleNamespace(stop_reason="end_turn", parsed_output=WireDocument.from_document(DOC), model="fake-model",
                           usage=SimpleNamespace(input_tokens=10, output_tokens=5))


class FakeClient:
    def __init__(self, outcomes):
        self.outcomes = list(outcomes)
        self.calls = 0
        self.messages = SimpleNamespace(parse=self._parse)

    def _parse(self, **kwargs):
        self.calls += 1
        # The request must carry the document block and the structured-output schema
        assert kwargs["messages"][0]["content"][0]["type"] == "document"
        assert kwargs["output_format"] is WireDocument
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


@pytest.fixture(autouse=True)
def no_sleep(monkeypatch):
    monkeypatch.setattr(extractor.time, "sleep", lambda s: None)


@pytest.fixture(autouse=True)
def fake_key(monkeypatch):
    # The fake client never uses it; extract() only checks that a key is configured.
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")


def use(monkeypatch, outcomes):
    fake = FakeClient(outcomes)
    monkeypatch.setattr(extractor, "_client", lambda: fake)
    return fake


def test_success_first_try(monkeypatch):
    fake = use(monkeypatch, [ok_response()])
    result = extractor.extract(PDF, "po.pdf")
    assert result.attempts == 1 and fake.calls == 1
    assert result.document.document_number == "OC-2026-0917"


def test_timeout_is_retried_once(monkeypatch):
    fake = use(monkeypatch, [anthropic.APITimeoutError(request=REQ), ok_response()])
    result = extractor.extract(PDF, "po.pdf")
    assert result.attempts == 2 and fake.calls == 2


def test_invalid_response_is_retried_once(monkeypatch):
    try:
        ExtractedDocument.model_validate_json('{"document_type": "nope"}')
    except pydantic.ValidationError as exc:
        bad = exc
    fake = use(monkeypatch, [bad, ok_response()])
    assert extractor.extract(PDF, "po.pdf").attempts == 2
    assert fake.calls == 2


def test_truncated_response_is_retried(monkeypatch):
    truncated = SimpleNamespace(stop_reason="max_tokens", parsed_output=None, model="m", usage=None)
    use(monkeypatch, [truncated, ok_response()])
    assert extractor.extract(PDF, "po.pdf").attempts == 2


def test_gives_up_after_two_attempts(monkeypatch):
    fake = use(monkeypatch, [anthropic.APITimeoutError(request=REQ), anthropic.APITimeoutError(request=REQ)])
    with pytest.raises(extractor.ExtractionError) as err:
        extractor.extract(PDF, "po.pdf")
    assert fake.calls == 2 and err.value.status_code == 504
    assert "2 attempts" in str(err.value)


def test_auth_error_is_not_retried(monkeypatch):
    resp = httpx2.Response(401, request=REQ, json={"error": {"message": "invalid x-api-key"}})
    fake = use(monkeypatch, [anthropic.AuthenticationError("invalid", response=resp, body=None)])
    with pytest.raises(extractor.ExtractionError) as err:
        extractor.extract(PDF, "po.pdf")
    assert fake.calls == 1 and err.value.status_code == 401


def test_invalid_file_never_reaches_the_api(monkeypatch):
    fake = use(monkeypatch, [ok_response()])
    with pytest.raises(extractor.InvalidFileError):
        extractor.extract(b"PK\x03\x04 a zip file", "invoice.pdf")
    assert fake.calls == 0


@pytest.mark.parametrize("value", [None, "", "   "])
def test_missing_or_blank_key_fails_before_any_call(monkeypatch, value):
    if value is None:
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    else:
        monkeypatch.setenv("ANTHROPIC_API_KEY", value)
    fake = use(monkeypatch, [ok_response()])
    with pytest.raises(extractor.ExtractionError) as err:
        extractor.extract(PDF, "po.pdf")
    assert err.value.status_code == 401 and "ANTHROPIC_API_KEY is not set" in str(err.value)
    assert fake.calls == 0


def test_refusal_is_not_retried(monkeypatch):
    refusal = SimpleNamespace(stop_reason="refusal", parsed_output=None, model="m", usage=None)
    fake = use(monkeypatch, [refusal, ok_response()])
    with pytest.raises(extractor.ExtractionError) as err:
        extractor.extract(PDF, "po.pdf")
    assert fake.calls == 1 and err.value.status_code == 422


def test_other_4xx_is_not_retried(monkeypatch):
    resp = httpx2.Response(422, request=REQ, json={"error": {"message": "unprocessable"}})
    fake = use(monkeypatch, [anthropic.UnprocessableEntityError("unprocessable", response=resp, body=None),
                             ok_response()])
    with pytest.raises(extractor.ExtractionError) as err:
        extractor.extract(PDF, "po.pdf")
    assert fake.calls == 1 and err.value.status_code == 400 and "422" in str(err.value)


def test_server_error_is_retried_once(monkeypatch):
    resp = httpx2.Response(529, request=REQ, json={"error": {"message": "overloaded"}})
    fake = use(monkeypatch, [anthropic.APIStatusError("overloaded", response=resp, body=None), ok_response()])
    assert extractor.extract(PDF, "po.pdf").attempts == 2 and fake.calls == 2


def test_retry_after_as_http_date_falls_back(monkeypatch):
    waits = []
    monkeypatch.setattr(extractor.time, "sleep", waits.append)
    resp = httpx2.Response(429, request=REQ, headers={"retry-after": "Fri, 25 Sep 2026 17:00:00 GMT"},
                           json={"error": {"message": "rate limited"}})
    use(monkeypatch, [anthropic.RateLimitError("rate limited", response=resp, body=None), ok_response()])
    assert extractor.extract(PDF, "po.pdf").attempts == 2
    assert waits == [5.0]
