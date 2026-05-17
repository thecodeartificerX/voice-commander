import shutil
import threading
from pathlib import Path

import pytest
from starlette.testclient import TestClient

from voice_commander.registry import ToolEntry, ToolRegistry
from voice_commander.tool_metadata import ToolMetadataStore
from voice_commander.web.app import create_app

FIXTURES = Path(__file__).resolve().parent.parent / "fixtures" / "tools_sidecar"


@pytest.fixture
def app_env(tmp_path):
    """Set up registry + store + app for testing."""
    shutil.copy(FIXTURES / "sample.toml", tmp_path / "sample.toml")

    store = ToolMetadataStore(tmp_path)
    registry = ToolRegistry()
    for name in ("alpha", "beta"):
        registry.register(
            ToolEntry(
                name=name,
                phrases=(),
                func=lambda: None,
                module="test",
                docstring=None,
            )
        )
    registry.bind_metadata(store)

    reload_lock = threading.Lock()
    app = create_app(registry, store, reload_lock)
    client = TestClient(app)
    return client, registry, store


def test_index_returns_200(app_env):
    client, _, _ = app_env
    # GET / redirects to /page/commands; primitives live at /page/primitives.
    resp = client.get("/", follow_redirects=False)
    assert resp.status_code == 302
    assert resp.headers["location"] == "/page/commands"

    resp = client.get("/page/primitives")
    assert resp.status_code == 200
    assert "alpha" in resp.text
    assert "beta" in resp.text


def test_healthz(app_env):
    client, _, _ = app_env
    resp = client.get("/healthz")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}


def test_edit_returns_form_fragment(app_env):
    client, _, _ = app_env
    resp = client.get("/tool/alpha/edit")
    assert resp.status_code == 200
    assert "<form" in resp.text
    assert "alpha" in resp.text
    # Should be a fragment, not full page
    assert "<!DOCTYPE" not in resp.text


def test_cancel_returns_card(app_env):
    client, _, _ = app_env
    resp = client.get("/tool/alpha/cancel")
    assert resp.status_code == 200
    assert "alpha" in resp.text
    assert "<form" not in resp.text


def test_csrf_rejects_post_without_hx_header(app_env):
    client, _, _ = app_env
    resp = client.post("/tool/alpha/toggle")
    assert resp.status_code == 403


HX = {"HX-Request": "true"}


def test_save_valid(app_env):
    client, registry, _ = app_env
    resp = client.post(
        "/tool/alpha",
        data={
            "phrases": "new phrase one\nnew phrase two",
            "description": "Updated.",
            "category": "test",
        },
        headers=HX,
    )
    assert resp.status_code == 200
    # save() persists description; phrases are read-only from TOML and not
    # rewritten by save(), so we verify the description was updated.
    entry = registry.by_name("alpha")
    assert entry.description == "Updated."


def test_save_empty_phrases_returns_400(app_env):
    client, _, _ = app_env
    resp = client.post(
        "/tool/alpha",
        data={
            "phrases": "",
            "description": "x",
            "category": "test",
        },
        headers=HX,
    )
    assert resp.status_code == 400
    assert "at least one phrase" in resp.text.lower()


def test_save_duplicate_phrase_returns_400(app_env):
    client, _, _ = app_env
    # "beta phrase" belongs to beta — try to set it on alpha
    resp = client.post(
        "/tool/alpha",
        data={
            "phrases": "beta phrase",
            "description": "x",
            "category": "test",
        },
        headers=HX,
    )
    assert resp.status_code == 400
    assert "already used" in resp.text.lower()


def test_toggle_flips_enabled(app_env):
    client, registry, _ = app_env
    # alpha starts enabled=True
    assert registry.by_name("alpha").enabled is True

    resp = client.post("/tool/alpha/toggle", headers=HX)
    assert resp.status_code == 200
    assert registry.by_name("alpha").enabled is False

    # Toggle back
    resp = client.post("/tool/alpha/toggle", headers=HX)
    assert resp.status_code == 200
    assert registry.by_name("alpha").enabled is True


def test_edit_nonexistent_tool(app_env):
    client, _, _ = app_env
    resp = client.get("/tool/nonexistent/edit")
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# system = true filtering (ADR 0072)
# ---------------------------------------------------------------------------


@pytest.fixture
def app_env_with_system_tool(tmp_path):
    """Set up registry with a system=True tool and verify filtering."""
    # Write a TOML with alpha (normal) and speak (system=true).
    toml_content = """\
category = "test"

[tools.alpha]
phrases = ["alpha one"]
description = "Normal tool."
enabled = true

[tools.speak]
phrases = ["speak"]
description = "Toggle Windows voice dictation on/off."
enabled = true
system = true
"""
    toml_path = tmp_path / "tools.toml"
    toml_path.write_text(toml_content)

    store = ToolMetadataStore(tmp_path)
    registry = ToolRegistry()
    for name in ("alpha", "speak"):
        registry.register(
            ToolEntry(
                name=name,
                phrases=(),
                func=lambda: None,
                module="test",
                docstring=None,
            )
        )
    registry.bind_metadata(store)

    reload_lock = threading.Lock()
    app = create_app(registry, store, reload_lock)
    client = TestClient(app)
    return client, registry, store


def test_primitives_page_excludes_system_tool(app_env_with_system_tool):
    """GET /page/primitives must not contain system tools (speak)."""
    client, _, _ = app_env_with_system_tool
    resp = client.get("/page/primitives")
    assert resp.status_code == 200
    assert "alpha" in resp.text
    assert "speak" not in resp.text


def test_api_tools_excludes_system_tool(app_env_with_system_tool):
    """GET /api/tools must not include system tools."""
    client, _, _ = app_env_with_system_tool
    resp = client.get("/api/tools")
    assert resp.status_code == 200
    data = resp.json()
    names = [t["name"] for t in data]
    assert "alpha" in names
    assert "speak" not in names


def test_api_tools_returns_json_list(app_env_with_system_tool):
    """GET /api/tools returns a JSON array of tool objects."""
    client, _, _ = app_env_with_system_tool
    resp = client.get("/api/tools")
    assert resp.status_code == 200
    data = resp.json()
    assert isinstance(data, list)
    for tool in data:
        assert "name" in tool
        assert "description" in tool


# ---------------------------------------------------------------------------
# Dictation page + vocab routes
# ---------------------------------------------------------------------------

import json as _json_mod


def test_page_dictation_renders_three_editor_sections(app_env):
    """GET /page/dictation must render the Vocabulary, Corrections, and Commands sections."""
    client, _, _ = app_env
    resp = client.get("/page/dictation")
    assert resp.status_code == 200
    body = resp.text
    assert "Vocabulary" in body
    assert "Corrections" in body
    assert "Commands" in body


def test_post_vocab_returns_result_fragment_on_success(app_env, tmp_path, monkeypatch):
    """POST /dictation/vocab must write vocab.json and return the result fragment."""
    import voice_commander.dictation.vocab as _vocab_mod

    saved_vocabs: list = []
    original_save = _vocab_mod.VocabStore.save

    def _capturing_save(self, vocab):
        saved_vocabs.append(vocab)
        original_save(self, vocab)

    monkeypatch.setattr(_vocab_mod.VocabStore, "save", _capturing_save)
    # Point VocabStore to tmp_path
    monkeypatch.setattr(
        "voice_commander.web.app.Path",
        lambda *a, **kw: (
            (tmp_path / a[0]) if a and a[0] == "outputs/dictation" else __import__("pathlib").Path(*a, **kw)
        ),
    )

    client, _, _ = app_env
    resp = client.post(
        "/dictation/vocab",
        data={
            "vocab": "Supabase\nn8n",
            "corrections": _json_mod.dumps(
                [{"wrong": "supa base", "right": "Supabase"}]
            ),
            "commands": _json_mod.dumps(
                [{"phrase": "next line", "action": "newline"}]
            ),
        },
        headers={"HX-Request": "true"},
    )
    assert resp.status_code == 200
    # Fragment must confirm the save and the captured Vocabulary must reflect
    # the posted vocab words, the parsed correction, and the parsed command.
    body = resp.text
    assert "Vocabulary saved." in body
    assert len(saved_vocabs) == 1
    assert saved_vocabs[0].vocab == ("Supabase", "n8n")
    assert saved_vocabs[0].corrections[0].wrong == "supa base"
    assert saved_vocabs[0].commands[0].phrase == "next line"


def test_post_vocab_malformed_json_falls_back_gracefully(app_env, tmp_path, monkeypatch):
    """POST /dictation/vocab with malformed corrections/commands JSON still saves.

    The route's ``except`` clauses fall back to empty correction/command lists,
    so the save succeeds and the success fragment is returned.
    """
    monkeypatch.setattr(
        "voice_commander.web.app.Path",
        lambda *a, **kw: (
            (tmp_path / a[0]) if a and a[0] == "outputs/dictation" else __import__("pathlib").Path(*a, **kw)
        ),
    )

    client, _, _ = app_env
    resp = client.post(
        "/dictation/vocab",
        data={"vocab": "word", "corrections": "not-valid-json", "commands": "{bad"},
        headers={"HX-Request": "true"},
    )
    assert resp.status_code == 200
    assert "Vocabulary saved." in resp.text


def test_post_vocab_csrf_blocked_without_hx_header(app_env):
    """POST /dictation/vocab must be blocked without HX-Request header."""
    client, _, _ = app_env
    resp = client.post("/dictation/vocab", data={"vocab": "test"})
    assert resp.status_code == 403


def test_retranscribe_applies_vocab_postprocessing(app_env, tmp_path, monkeypatch):
    """POST /dictation/retranscribe must apply vocab corrections and commands.

    Writes a vocab.json with one correction (supa base → Supabase) and one
    command (next line → newline), monkeypatches post_audio to return a raw
    transcript that contains both, and asserts the clipboard text (and the
    response fragment) reflect the post-processed result.
    """
    import json as _json
    import numpy as np

    from voice_commander.dictation.store import DictationStore, encode_wav
    from voice_commander.dictation.vocab import VocabStore, Vocabulary, Correction, Command

    # ---- redirect Path("outputs/dictation") into tmp_path ----
    monkeypatch.setattr(
        "voice_commander.web.app.Path",
        lambda *a, **kw: (
            (tmp_path / a[0]) if a and a[0] == "outputs/dictation" else __import__("pathlib").Path(*a, **kw)
        ),
    )

    dictation_dir = tmp_path / "outputs/dictation"
    dictation_dir.mkdir(parents=True, exist_ok=True)

    # ---- write a vocab.json with a correction and a command ----
    vocab = Vocabulary(
        vocab=(),
        corrections=(Correction(wrong="supa base", right="Supabase"),),
        commands=(Command(phrase="next line", action="newline"),),
    )
    VocabStore(dictation_dir / "vocab.json").save(vocab)

    # ---- write a minimal last.wav so read_audio() returns bytes ----
    wav_bytes = encode_wav(np.zeros(1600, dtype=np.float32))
    DictationStore(dictation_dir).save_audio(wav_bytes)

    # ---- monkeypatch post_audio to return a raw transcript ----
    monkeypatch.setattr(
        "voice_commander.dictation.remote.post_audio",
        lambda *a, **kw: "I use supa base next line world",
    )

    # ---- capture the clipboard write ----
    captured: list[str] = []
    monkeypatch.setattr(
        "voice_commander.dictation.clipboard.set_clipboard_text",
        lambda text: captured.append(text),
    )

    client, _, _ = app_env
    resp = client.post(
        "/dictation/retranscribe",
        headers={"HX-Request": "true"},
    )
    assert resp.status_code == 200

    # The post-processed text must appear in either the captured clipboard
    # write or the response body (both paths read from the same processed text).
    assert len(captured) == 1, "set_clipboard_text must be called exactly once"
    result = captured[0]
    assert "Supabase" in result, f"correction not applied; got: {result!r}"
    assert "\n" in result, f"command not applied (expected newline); got: {result!r}"
    assert "supa base" not in result, f"raw wrong-word survived; got: {result!r}"
