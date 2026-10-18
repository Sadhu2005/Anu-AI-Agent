#!/usr/bin/env python3
"""V0.7.1: model gateway config + privacy + URL helpers (no live provider)."""

import importlib.util
import json
import os
import tempfile
from pathlib import Path
from unittest import mock


REPO = Path(__file__).resolve().parents[1]
MODULE = REPO / "bin" / "anu-model-gateway-v0.1.py"
CONFIG = REPO / "config" / "anu-model-gateway.yaml"


def load():
    spec = importlib.util.spec_from_file_location("model_gateway_v071", MODULE)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_load_repo_config():
    m = load()
    old = os.environ.get("ANU_BASE")
    os.environ["ANU_BASE"] = str(REPO)
    try:
        cfg = m.load_config()
        assert cfg["provider"] == "openai_compatible"
        assert cfg["model"] == "meta-llama-3.1-8b-instruct"
        assert "1234" in cfg["base_url"] or cfg["base_url"].endswith("/v1")
        assert cfg["api_key_env"] == "ANU_MODEL_API_KEY"
    finally:
        if old is None:
            os.environ.pop("ANU_BASE", None)
        else:
            os.environ["ANU_BASE"] = old
    print("PASS load repo anu-model-gateway.yaml")


def test_switch_provider_is_config_only():
    m = load()
    cfg = m.load_config(CONFIG)
    cfg["base_url"] = "https://api.openai.com/v1"
    cfg["model"] = "gpt-4o-mini"
    assert m._chat_url(cfg["base_url"]).endswith("/chat/completions")
    assert m._models_url(cfg["base_url"]).endswith("/models")
    assert cfg["provider"] == "openai_compatible"
    print("PASS provider switch via base_url/model only")


def test_privacy_blocks_secret():
    m = load()
    cfg = m.load_config(CONFIG)
    assert m.privacy_allowed(cfg, "INTERNAL") is True
    assert m.privacy_allowed(cfg, "SECRET") is False
    print("PASS SECRET privacy class blocked")


def test_api_key_from_env_and_file():
    m = load()
    cfg = m.load_config(CONFIG)
    old_key = os.environ.get("ANU_MODEL_API_KEY")
    old_file = os.environ.get("ANU_MODEL_API_KEY_FILE")
    tmp = None
    try:
        os.environ.pop("ANU_MODEL_API_KEY_FILE", None)
        os.environ["ANU_MODEL_API_KEY"] = "from-env"
        assert m.resolve_api_key(cfg) == "from-env"

        tmp = tempfile.NamedTemporaryFile("w", delete=False, encoding="utf-8")
        tmp.write("from-file\n")
        tmp.close()
        os.environ["ANU_MODEL_API_KEY_FILE"] = tmp.name
        assert m.resolve_api_key(cfg) == "from-file"
    finally:
        if old_key is None:
            os.environ.pop("ANU_MODEL_API_KEY", None)
        else:
            os.environ["ANU_MODEL_API_KEY"] = old_key
        if old_file is None:
            os.environ.pop("ANU_MODEL_API_KEY_FILE", None)
        else:
            os.environ["ANU_MODEL_API_KEY_FILE"] = old_file
        if tmp is not None:
            Path(tmp.name).unlink(missing_ok=True)
    print("PASS api key env + file resolution")


def test_reject_url_shaped_api_key():
    m = load()
    cfg = m.load_config(CONFIG)
    old_key = os.environ.get("ANU_MODEL_API_KEY")
    old_file = os.environ.get("ANU_MODEL_API_KEY_FILE")
    try:
        os.environ.pop("ANU_MODEL_API_KEY_FILE", None)
        os.environ["ANU_MODEL_API_KEY"] = "http://192.168.1.13:1234/v1"
        try:
            m.resolve_api_key(cfg)
            raise AssertionError("URL-shaped key should raise")
        except ValueError as exc:
            msg = str(exc)
            assert "base_url" in msg
            assert "ANU_MODEL_API_KEY" in msg
    finally:
        if old_key is None:
            os.environ.pop("ANU_MODEL_API_KEY", None)
        else:
            os.environ["ANU_MODEL_API_KEY"] = old_key
        if old_file is None:
            os.environ.pop("ANU_MODEL_API_KEY_FILE", None)
        else:
            os.environ["ANU_MODEL_API_KEY_FILE"] = old_file
    print("PASS reject URL-shaped API key")


def test_zero_cost_lan_hosts():
    m = load()
    cfg = m.load_config(CONFIG)
    cfg["base_url"] = "http://192.168.1.50:1234/v1"
    assert m.assume_zero_cost(cfg) is True
    cfg["base_url"] = "https://api.openai.com/v1"
    assert m.assume_zero_cost(cfg) is False
    print("PASS zero-cost detection for LAN vs cloud")


def test_extract_assistant_text():
    m = load()
    payload = {
        "choices": [
            {"message": {"role": "assistant", "content": "OK"}}
        ]
    }
    assert m.extract_assistant_text(payload) == "OK"
    print("PASS extract assistant text")


def test_request_chat_mock_provider():
    m = load()
    cfg = m.load_config(CONFIG)
    cfg["base_url"] = "http://127.0.0.1:1234/v1"
    body = {
        "choices": [{"message": {"content": "pong"}}],
    }

    class FakeResp:
        status = 200

        def read(self):
            return json.dumps(body).encode("utf-8")

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

    with mock.patch("urllib.request.urlopen", return_value=FakeResp()):
        result = m.request_chat(
            cfg,
            messages=[{"role": "user", "content": "ping"}],
            api_key="lm-studio",
        )
    assert result["ok"] is True
    assert result["text"] == "pong"
    print("PASS request_chat against mock provider")


if __name__ == "__main__":
    print("===== ANU V0.7.1 MODEL GATEWAY CONFIG =====")
    test_load_repo_config()
    test_switch_provider_is_config_only()
    test_privacy_blocks_secret()
    test_api_key_from_env_and_file()
    test_reject_url_shaped_api_key()
    test_zero_cost_lan_hosts()
    test_extract_assistant_text()
    test_request_chat_mock_provider()
    print("MODEL_GATEWAY_CONFIG_TEST_COMPLETE")
