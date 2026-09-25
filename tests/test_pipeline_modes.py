"""Pipeline modes (offline / replay / live failures) and the real SDK request shape.
No network: the live path goes through the real `anthropic` client with a mock transport."""

import dataclasses
import json
from pathlib import Path

import anthropic
import httpx2
import pytest

from app import extractor, pipeline
from app.extractor import ExtractionError
from app.schema import ExtractedDocument, WireDocument

ROOT = Path(__file__).resolve().parent.parent
SAMPLES = ROOT / "samples"


@pytest.fixture
def isolated(monkeypatch, tmp_path):
    """Point runs/ and the replay folder at empty temp dirs."""
    cfg = dataclasses.replace(pipeline.settings, runs_dir=tmp_path / "runs", replay_dir=tmp_path / "saved")
    monkeypatch.setattr(pipeline, "settings", cfg)
    return cfg


@pytest.mark.parametrize("name", ["danfe-exemplo-industrial.pdf", "orden-compra-andina.pdf",
                                  "foto-danfe-parafusos.jpg"])
def test_offline_mode_uses_the_answer_key_and_says_so(isolated, name):
    result = pipeline.process((SAMPLES / name).read_bytes(), name, mode="offline")
    truth = json.loads((SAMPLES / "truth" / f"{Path(name).stem}.json").read_text(encoding="utf-8"))
    assert result["mode"] == "offline"
    assert result["model"] == pipeline.OFFLINE_LABEL and "no Claude call" in result["model"]
    assert result["usage"] == {} and result["attempts"] == 0
    assert result["document"]["document_number"] == truth["document_number"]
    out = Path(result["out_dir"])
    assert (out / result["files"]["xlsx"]).is_file() and (out / result["files"]["pdf"]).is_file()


def test_offline_photo_flags_the_deliberate_typo(isolated):
    name = "foto-danfe-parafusos.jpg"
    v = pipeline.process((SAMPLES / name).read_bytes(), name, mode="offline")["validation"]
    warned = {c["title"] for c in v["checks"] if c["status"] == "warning"}
    assert v["status"] != "ok" and warned == {"Line totals", "Items vs subtotal"}


def test_offline_mode_refuses_other_files_and_leaves_no_folder(isolated):
    data = (SAMPLES / "orden-compra-andina.pdf").read_bytes() + b"\n% edited\n%%EOF\n"
    with pytest.raises(ExtractionError) as err:
        pipeline.process(data, "other.pdf", mode="offline")
    assert err.value.status_code == 404 and "answer key" in str(err.value)
    assert not isolated.runs_dir.exists()


def test_replay_without_saved_results_explains_what_to_do(isolated):
    name = "orden-compra-andina.pdf"
    with pytest.raises(ExtractionError) as err:
        pipeline.process((SAMPLES / name).read_bytes(), name, mode="replay")
    assert "No saved live result" in str(err.value) and "run_samples.py" in str(err.value)
    assert not isolated.runs_dir.exists()


def test_replay_ignores_saved_results_that_are_not_live(isolated):
    name = "orden-compra-andina.pdf"
    offline = pipeline.process((SAMPLES / name).read_bytes(), name, isolated.replay_dir / "x", mode="offline")
    assert offline["mode"] == "offline"
    with pytest.raises(ExtractionError, match="No saved live result"):
        pipeline.process((SAMPLES / name).read_bytes(), name, mode="replay")


def test_failed_live_call_leaves_no_empty_run_folder(isolated, monkeypatch):
    def fail(data, filename):
        raise ExtractionError("ANTHROPIC_API_KEY is missing or invalid.", status_code=401)

    monkeypatch.setattr(pipeline, "extract", fail)
    name = "orden-compra-andina.pdf"
    with pytest.raises(ExtractionError):
        pipeline.process((SAMPLES / name).read_bytes(), name, mode="live")
    assert not isolated.runs_dir.exists() or not any(isolated.runs_dir.iterdir())


def test_unknown_mode_is_rejected(isolated):
    with pytest.raises(ExtractionError, match="Unknown EXTRACTOR_MODE"):
        pipeline.process(b"%PDF-", "x.pdf", mode="demo")


def test_live_request_shape_through_the_real_sdk(monkeypatch):
    """Runs extract() through the real anthropic client (mock HTTP transport) to check
    the request body the SDK builds and that parsed_output comes back typed."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")  # checked by extract(); never sent anywhere
    truth = json.loads((SAMPLES / "truth" / "orden-compra-andina.json").read_text(encoding="utf-8"))
    doc = ExtractedDocument.model_validate({k: v for k, v in truth.items() if not k.startswith("_")})
    answer = WireDocument.from_document(doc).model_dump(mode="json")  # what Claude sends back
    seen = {}

    def handler(request):
        seen["body"] = json.loads(request.content)
        return httpx2.Response(200, json={
            "id": "msg_test", "type": "message", "role": "assistant", "model": seen["body"]["model"],
            "content": [{"type": "text", "text": json.dumps(answer)}],
            "stop_reason": "end_turn", "stop_sequence": None,
            "usage": {"input_tokens": 1200, "output_tokens": 600},
        })

    client = anthropic.Anthropic(api_key="test-key", max_retries=0,
                                 http_client=httpx2.Client(transport=httpx2.MockTransport(handler)))
    monkeypatch.setattr(extractor, "_client", lambda: client)
    result = extractor.extract((SAMPLES / "orden-compra-andina.pdf").read_bytes(), "po.pdf")

    body = seen["body"]
    assert [c["type"] for c in body["messages"][0]["content"]] == ["document", "text"]
    assert body["messages"][0]["content"][0]["source"]["media_type"] == "application/pdf"
    assert body["output_config"]["effort"] == extractor.settings.effort
    assert body["output_config"]["format"]["type"] == "json_schema"
    assert "items" in json.dumps(body["output_config"]["format"]["schema"])
    assert isinstance(result.document, ExtractedDocument)
    assert result.document.document_number == truth["document_number"]
    assert result.usage == {"input_tokens": 1200, "output_tokens": 600}


def test_wire_schema_round_trip_and_simplicity():
    """The schema sent to Claude has no optional fields and few nullable unions (the API
    rejects complex schemas); converting back must give the same document."""
    from anthropic.lib._parse._transform import transform_schema

    schema = json.dumps(transform_schema(WireDocument))
    assert schema.count("anyOf") <= 12

    def optional_fields(node):
        if isinstance(node, dict):
            own = len(set(node.get("properties", {})) - set(node.get("required", [])))
            return own + sum(optional_fields(v) for v in node.values())
        if isinstance(node, list):
            return sum(optional_fields(v) for v in node)
        return 0

    assert optional_fields(transform_schema(WireDocument)) == 0
    for truth_file in sorted((SAMPLES / "truth").glob("*.json")):
        raw = json.loads(truth_file.read_text(encoding="utf-8"))
        doc = ExtractedDocument.model_validate({k: v for k, v in raw.items() if not k.startswith("_")})
        assert WireDocument.from_document(doc).to_document() == doc


# --- live mode without a key: clear message, CLI exit 2, JSON 401 --------------------

@pytest.fixture
def live_without_key(isolated, monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    cfg = dataclasses.replace(isolated, mode="live")
    monkeypatch.setattr(pipeline, "settings", cfg)
    return cfg


def test_cli_without_key_exits_2_with_a_clear_message(live_without_key, capsys):
    import extract as cli

    code = cli.main([str(SAMPLES / "orden-compra-andina.pdf")])
    err = capsys.readouterr().err
    assert code == 2
    assert "ANTHROPIC_API_KEY is not set" in err and "Traceback" not in err
    assert not live_without_key.runs_dir.exists()


def test_api_without_key_answers_json_401(live_without_key):
    from fastapi.testclient import TestClient

    from app.server import app

    name = "orden-compra-andina.pdf"
    with TestClient(app) as client:
        res = client.post("/api/extract", files={"file": (name, (SAMPLES / name).read_bytes(), "application/pdf")})
    assert res.status_code == 401
    assert res.headers["content-type"].startswith("application/json")
    assert "ANTHROPIC_API_KEY is not set" in res.json()["error"]


def test_api_unexpected_error_still_answers_json(monkeypatch):
    from fastapi.testclient import TestClient

    from app import server

    def boom(*args, **kwargs):
        raise RuntimeError("something unexpected")

    monkeypatch.setattr(server, "process", boom)
    name = "orden-compra-andina.pdf"
    with TestClient(server.app, raise_server_exceptions=False) as client:
        res = client.post("/api/extract", files={"file": (name, (SAMPLES / name).read_bytes(), "application/pdf")})
        missing = client.get("/api/does-not-exist")
    assert res.status_code == 500 and "RuntimeError" in res.json()["error"]
    assert missing.status_code == 404 and "error" in missing.json()


def test_user_file_outputs_are_not_labelled_fictitious(isolated, monkeypatch):
    """A file that is not a bundled sample (other SHA-256) keeps DEMO but drops 'fictitious'."""
    truth = json.loads((SAMPLES / "truth" / "orden-compra-andina.json").read_text(encoding="utf-8"))
    doc = ExtractedDocument.model_validate({k: v for k, v in truth.items() if not k.startswith("_")})
    monkeypatch.setattr(pipeline, "extract", lambda data, filename: extractor.ExtractionResult(
        document=doc, model="fake", attempts=1, seconds=0.1, usage={}, stop_reason="end_turn"))
    data = (SAMPLES / "orden-compra-andina.pdf").read_bytes() + b"\n% edited\n%%EOF\n"
    result = pipeline.process(data, "mine.pdf", mode="live")
    assert "fictitious" not in result["notice"] and "DEMO" in result["notice"]
    sample = pipeline.process((SAMPLES / "orden-compra-andina.pdf").read_bytes(), "po.pdf", mode="live")
    assert "fictitious" in sample["notice"]
