"""Tests for log_llm_sources — per-field source logging (ADR 0040 / spec §5).

At daemon startup, every [llm].* field must emit an INFO log line showing the
resolved value and where it came from (env / config.toml / default). Silent
fallback is forbidden.
"""

from __future__ import annotations

import logging
import textwrap
from pathlib import Path

import pytest

from voice_commander.config import Config, LLMConfig, log_llm_sources


def _emit(cfg: Config, caplog: pytest.LogCaptureFixture) -> str:
    caplog.set_level(logging.INFO, logger="voice_commander.config")
    caplog.clear()
    log_llm_sources(cfg)
    return "\n".join(rec.getMessage() for rec in caplog.records)


def test_all_fields_have_source_line(
    tmp_path: Path, caplog: pytest.LogCaptureFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Each [llm].* field emits an INFO line containing '(source: ...)'."""
    # Strip any VC_LLM_* env vars that could bleed in from the test environment.
    for name in list(os_env_with_prefix("VC_LLM_")):
        monkeypatch.delenv(name, raising=False)

    cfg = Config.load(tmp_path / "nonexistent.toml")
    text = _emit(cfg, caplog)
    from dataclasses import fields

    for f in fields(LLMConfig):
        assert f"llm.{f.name}" in text, f"Missing log line for field {f.name}"
    assert "(source: " in text


def test_env_var_shows_as_source(
    tmp_path: Path, caplog: pytest.LogCaptureFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("VC_LLM_ENDPOINT_URL", "http://foo:1/v1")
    cfg = Config.load(tmp_path / "nonexistent.toml")
    text = _emit(cfg, caplog)
    assert "VC_LLM_ENDPOINT_URL" in text
    assert "http://foo:1/v1" in text


def test_config_toml_shows_as_source(
    tmp_path: Path, caplog: pytest.LogCaptureFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    for name in list(os_env_with_prefix("VC_LLM_")):
        monkeypatch.delenv(name, raising=False)
    base = tmp_path / "config.toml"
    base.write_text(
        textwrap.dedent("""
        [llm]
        max_plan_steps = 5
    """)
    )
    cfg = Config.load(base)
    text = _emit(cfg, caplog)
    # Find the max_plan_steps line and confirm config.toml is its source.
    lines = [line for line in text.splitlines() if "max_plan_steps" in line]
    assert lines, f"No log line for max_plan_steps; got:\n{text}"
    assert any("config.toml" in line for line in lines), (
        f"max_plan_steps should be sourced from config.toml; got:\n{text}"
    )


def test_default_shows_as_source(
    tmp_path: Path, caplog: pytest.LogCaptureFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    for name in list(os_env_with_prefix("VC_LLM_")):
        monkeypatch.delenv(name, raising=False)
    cfg = Config.load(tmp_path / "nonexistent.toml")
    text = _emit(cfg, caplog)
    assert "(source: default)" in text


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def os_env_with_prefix(prefix: str) -> list[str]:
    import os

    return [k for k in os.environ if k.startswith(prefix)]
