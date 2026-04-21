import textwrap

import pytest

from voice_commander.config import Config


def test_load_defaults_when_file_has_no_overrides(tmp_path):
    cfg_file = tmp_path / "config.toml"
    cfg_file.write_text("")
    cfg = Config.load(cfg_file)
    assert cfg.hotkey.key == "scroll_lock"
    assert cfg.audio.channels == 1
    assert cfg.feedback.sounds_dir == "assets/sounds"


def test_load_applies_overrides(tmp_path):
    cfg_file = tmp_path / "config.toml"
    cfg_file.write_text(
        textwrap.dedent("""
        [audio]
        channels = 2
    """)
    )
    cfg = Config.load(cfg_file)
    assert cfg.audio.channels == 2
    # untouched sections keep defaults
    assert cfg.hotkey.key == "scroll_lock"


def test_load_missing_file_returns_all_defaults(tmp_path):
    cfg = Config.load(tmp_path / "nonexistent.toml")
    assert cfg.audio.channels == 1
    assert cfg.audio.device == -1
    assert cfg.audio.output_dir == "outputs"


def test_invalid_type_raises(tmp_path):
    cfg_file = tmp_path / "config.toml"
    cfg_file.write_text('[audio]\nchannels = "not-an-int"\n')
    with pytest.raises((TypeError, ValueError)):
        Config.load(cfg_file)


def test_config_is_frozen(tmp_path):
    cfg = Config.load(tmp_path / "nope.toml")
    with pytest.raises((AttributeError, TypeError)):
        cfg.hotkey.key = "a"


def test_vad_defaults(tmp_path):
    cfg = Config.load(tmp_path / "nope.toml")
    assert cfg.vad.threshold == 0.4
    assert cfg.vad.min_speech_duration_ms == 100
    assert cfg.vad.min_silence_duration_ms == 250
    assert cfg.vad.speech_pad_ms == 30
    assert cfg.vad.pre_roll_ms == 300
    assert cfg.vad.max_utterance_ms == 8000
    assert cfg.vad.gates.min_word_count == 1
    assert cfg.vad.gates.max_no_speech_prob == 0.6


def test_local_overrides_base(tmp_path):
    base = tmp_path / "config.toml"
    base.write_text(
        textwrap.dedent("""
        [audio]
        channels = 1
        device = 13

        [vad]
        threshold = 0.4

        [vad.gates]
        min_word_count = 1
    """)
    )
    local = tmp_path / "config.local.toml"
    local.write_text(
        textwrap.dedent("""
        [audio]
        device = 8

        [vad.gates]
        min_word_count = 3
    """)
    )
    cfg = Config.load(base)
    # local overrides
    assert cfg.audio.device == 8
    assert cfg.vad.gates.min_word_count == 3
    # base values preserved where local silent
    assert cfg.audio.channels == 1
    assert cfg.vad.threshold == 0.4


def test_local_missing_uses_base(tmp_path):
    base = tmp_path / "config.toml"
    base.write_text("[audio]\ndevice = 5\n")
    cfg = Config.load(base)
    assert cfg.audio.device == 5


def test_explicit_local_path(tmp_path):
    base = tmp_path / "config.toml"
    base.write_text("[audio]\ndevice = 1\n")
    custom = tmp_path / "overrides.toml"
    custom.write_text("[audio]\ndevice = 42\n")
    cfg = Config.load(base, local_path=custom)
    assert cfg.audio.device == 42


def test_vad_overrides(tmp_path):
    cfg_file = tmp_path / "config.toml"
    cfg_file.write_text(
        textwrap.dedent("""
        [vad]
        threshold = 0.3
        min_silence_duration_ms = 200

        [vad.gates]
        min_word_count = 2
    """)
    )
    cfg = Config.load(cfg_file)
    assert cfg.vad.threshold == 0.3
    assert cfg.vad.min_silence_duration_ms == 200
    assert cfg.vad.gates.min_word_count == 2
    # untouched fields keep defaults
    assert cfg.vad.pre_roll_ms == 300


def test_mute_key_defaults_to_empty(tmp_path):
    cfg = Config.load(tmp_path / "nope.toml")
    assert cfg.hotkey.mute_key == ""


def test_mute_key_loads_from_toml(tmp_path):
    cfg_file = tmp_path / "config.toml"
    cfg_file.write_text('[hotkey]\nmute_key = "ctrl_r"\n')
    cfg = Config.load(cfg_file)
    assert cfg.hotkey.mute_key == "ctrl_r"


def test_mute_key_invalid_type_raises(tmp_path):
    cfg_file = tmp_path / "config.toml"
    cfg_file.write_text("[hotkey]\nmute_key = 123\n")
    with pytest.raises((TypeError, ValueError)):
        Config.load(cfg_file)


# ---------------------------------------------------------------------------
# LLMConfig parsing
# ---------------------------------------------------------------------------

def test_llm_section_absent_returns_all_defaults(tmp_path):
    """No [llm] section → all fields carry their documented defaults."""
    cfg = Config.load(tmp_path / "nope.toml")
    r = cfg.llm
    assert r.endpoint_url == "http://localhost:1234/v1"
    assert r.model_id == "google/gemma-4-e4b"
    assert r.timeout_ms == 600
    assert r.max_plan_steps == 8
    assert r.warmup_on_startup is True


def test_llm_all_fields_round_trip(tmp_path):
    """Every [llm] field explicitly set → values round-trip into dataclass."""
    cfg_file = tmp_path / "config.toml"
    cfg_file.write_text(
        textwrap.dedent("""
        [llm]
        endpoint_url = "http://192.168.1.50:1234/v1"
        model_id = "mistral/mistral-7b"
        timeout_ms = 1200
        max_plan_steps = 4
        warmup_on_startup = false
    """)
    )
    cfg = Config.load(cfg_file)
    r = cfg.llm
    assert r.endpoint_url == "http://192.168.1.50:1234/v1"
    assert r.model_id == "mistral/mistral-7b"
    assert r.timeout_ms == 1200
    assert r.max_plan_steps == 4
    assert r.warmup_on_startup is False


def test_llm_partial_section_respects_set_fields_and_defaults(tmp_path):
    """Partial [llm] section → set fields respected, missing fields get defaults."""
    cfg_file = tmp_path / "config.toml"
    cfg_file.write_text(
        textwrap.dedent("""
        [llm]
        max_plan_steps = 3
    """)
    )
    cfg = Config.load(cfg_file)
    r = cfg.llm
    assert r.max_plan_steps == 3
    # unset fields stay at defaults
    assert r.endpoint_url == "http://localhost:1234/v1"
    assert r.model_id == "google/gemma-4-e4b"
    assert r.timeout_ms == 600
    assert r.warmup_on_startup is True


def test_llm_timeout_ms_string_raises(tmp_path):
    """timeout_ms = 'fast' (string) → TypeError raised by _section type check."""
    cfg_file = tmp_path / "config.toml"
    cfg_file.write_text('[llm]\ntimeout_ms = "fast"\n')
    with pytest.raises((TypeError, ValueError)):
        Config.load(cfg_file)


def test_llm_max_plan_steps_string_raises(tmp_path):
    """max_plan_steps = 'many' (string) → TypeError raised by _section type check."""
    cfg_file = tmp_path / "config.toml"
    cfg_file.write_text('[llm]\nmax_plan_steps = "many"\n')
    with pytest.raises((TypeError, ValueError)):
        Config.load(cfg_file)


def test_llm_unknown_key_raises(tmp_path):
    """Unrecognised key in [llm] → ValueError from _section."""
    cfg_file = tmp_path / "config.toml"
    cfg_file.write_text("[llm]\nnot_a_real_field = true\n")
    with pytest.raises((TypeError, ValueError)):
        Config.load(cfg_file)


def test_llm_is_frozen(tmp_path):
    """LLMConfig is frozen — mutation raises."""
    cfg = Config.load(tmp_path / "nope.toml")
    with pytest.raises((AttributeError, TypeError)):
        cfg.llm.endpoint_url = "http://evil.example.com/v1"


def test_llm_warmup_timeout_ms_default(tmp_path):
    """warmup_timeout_ms defaults to 5000 when absent from config."""
    cfg = Config.load(tmp_path / "nope.toml")
    assert cfg.llm.warmup_timeout_ms == 5000


def test_llm_warmup_timeout_ms_round_trip(tmp_path):
    """warmup_timeout_ms can be set via TOML and round-trips into the dataclass."""
    cfg_file = tmp_path / "config.toml"
    cfg_file.write_text("[llm]\nwarmup_timeout_ms = 8000\n")
    cfg = Config.load(cfg_file)
    assert cfg.llm.warmup_timeout_ms == 8000


def test_llm_warmup_timeout_ms_string_raises(tmp_path):
    """warmup_timeout_ms = 'slow' (string) → TypeError from _section type check."""
    cfg_file = tmp_path / "config.toml"
    cfg_file.write_text('[llm]\nwarmup_timeout_ms = "slow"\n')
    with pytest.raises((TypeError, ValueError)):
        Config.load(cfg_file)


def test_llm_warmup_timeout_ms_independent_of_timeout_ms(tmp_path):
    """warmup_timeout_ms and timeout_ms are independent fields with separate defaults."""
    cfg_file = tmp_path / "config.toml"
    cfg_file.write_text("[llm]\ntimeout_ms = 300\n")
    cfg = Config.load(cfg_file)
    # per-call timeout changed, warmup timeout unchanged
    assert cfg.llm.timeout_ms == 300
    assert cfg.llm.warmup_timeout_ms == 5000
