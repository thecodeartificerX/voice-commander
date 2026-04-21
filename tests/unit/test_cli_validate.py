"""Unit tests for the ``python -m voice_commander --validate`` CLI path.

We test :func:`voice_commander.__main__._run_validate` directly, mocking out:
  - ``discover`` so we never import tool modules or touch the global registry
  - ``ToolMetadataStore`` construction so we don't need a real tools directory

The ``*_or_die`` validators are exercised via real calls with controlled inputs
so that exit-code contracts are verified end-to-end without spawning subprocesses.
"""

# NOTE: Do NOT add `from __future__ import annotations` here.
# The validator calls typing.get_type_hints() which needs real runtime annotations.

import sys
from dataclasses import replace
from unittest.mock import MagicMock, patch

import pytest

from voice_commander.config import Config, LLMConfig
from voice_commander.registry import ToolEntry, ToolRegistry
from voice_commander.tool_metadata import ArgMetadata, ToolMetadata, ToolMetadataStore

# ---------------------------------------------------------------------------
# Helpers (mirrors test_validator.py style)
# ---------------------------------------------------------------------------

def _make_store(tools: dict[str, ToolMetadata]) -> MagicMock:
    """Return a mock ToolMetadataStore whose load_all() yields *tools*."""
    store = MagicMock(spec=ToolMetadataStore)
    store.load_all.return_value = tools
    return store


def _make_registry(*entries: ToolEntry) -> ToolRegistry:
    registry = ToolRegistry()
    for entry in entries:
        registry.register(entry)
    return registry


def _entry(
    name: str,
    func,
    *,
    module: str = "voice_commander.tools.fake",
    phrases: tuple[str, ...] = ("do thing",),
    llm_only: bool = False,
) -> ToolEntry:
    return ToolEntry(
        name=name,
        phrases=phrases,
        func=func,
        module=module,
        docstring=None,
        llm_only=llm_only,
    )


def _meta(
    name: str,
    *,
    phrases: tuple[str, ...] = ("do thing",),
    description: str = "A tool.",
    category: str = "test",
    enabled: bool = True,
    settle_ms: int = 0,
    llm_only: bool = False,
    args: dict[str, ArgMetadata] | None = None,
) -> ToolMetadata:
    return ToolMetadata(
        name=name,
        phrases=phrases,
        description=description,
        category=category,
        enabled=enabled,
        settle_ms=settle_ms,
        llm_only=llm_only,
        args=args or {},
    )


def _cfg_with_timeout(timeout_ms: int) -> Config:
    """Return a Config with llm_router.timeout_ms overridden."""
    return replace(Config(), llm=LLMConfig(timeout_ms=timeout_ms))


# ---------------------------------------------------------------------------
# Shared patch context
#
# _run_validate does:
#   1. ToolMetadataStore(tools_dir)   – we mock the class constructor
#   2. discover(pkg, store=store)     – we mock to return a controlled registry
#   3. validate_config_or_die(cfg)    – we let through (or mock to raise)
#   4. validate_or_die(registry, store) – we let through (or mock to raise)
# ---------------------------------------------------------------------------

_MAIN_MODULE = "voice_commander.__main__"


def _patch_infrastructure(registry: ToolRegistry, store: MagicMock):
    """Return a stack of patches that wire a controlled registry+store into _run_validate.

    Because _run_validate uses lazy imports (``from .registry import discover`` etc.
    inside the function body), we patch the names in their *source* modules, which is
    where the names are resolved at call time.
    """
    p_store_cls = patch(
        "voice_commander.tool_metadata.ToolMetadataStore",
        return_value=store,
    )
    p_discover = patch("voice_commander.registry.discover", return_value=registry)
    return p_store_cls, p_discover


# ---------------------------------------------------------------------------
# Happy path: clean config + passing registry → returns normally, prints "OK"
# ---------------------------------------------------------------------------

class TestHappyPath:
    def test_returns_without_raising(self, capsys):
        """Clean config + valid tool registry → _run_validate returns normally (exit 0)."""

        def my_tool() -> None:
            pass

        registry = _make_registry(_entry("my_tool", my_tool))
        store = _make_store({"my_tool": _meta("my_tool")})
        cfg = Config()  # default config has timeout_ms=600 ≥ 200 → valid

        p_store_cls, p_discover = _patch_infrastructure(registry, store)
        with p_store_cls, p_discover:
            from voice_commander.__main__ import _run_validate

            # Should not raise SystemExit
            _run_validate(cfg)

        captured = capsys.readouterr()
        assert "OK" in captured.out

    def test_main_with_validate_flag_exits_zero(self, capsys, monkeypatch):
        """``main()`` with --validate on a clean stack exits with code 0 (no SystemExit raised)."""

        def my_tool() -> None:
            pass

        registry = _make_registry(_entry("my_tool", my_tool))
        store = _make_store({"my_tool": _meta("my_tool")})

        monkeypatch.setattr(sys, "argv", ["voice_commander", "--validate"])

        # Config.load reads from disk; give it a minimal path that returns defaults.
        with patch("voice_commander.__main__.Config") as mock_cfg_cls:
            mock_cfg_cls.load.return_value = Config()
            p_store_cls, p_discover = _patch_infrastructure(registry, store)
            with p_store_cls, p_discover:
                from voice_commander.__main__ import main

                # main() should return cleanly (no SystemExit)
                main()

        captured = capsys.readouterr()
        assert "OK" in captured.out


# ---------------------------------------------------------------------------
# Config drift: timeout_ms below minimum → sys.exit(1)
# ---------------------------------------------------------------------------

class TestConfigDrift:
    def test_timeout_ms_too_low_causes_exit_1(self):
        """llm_router.timeout_ms=100 (< 200) → validate_config_or_die calls sys.exit(1)."""

        def my_tool() -> None:
            pass

        registry = _make_registry(_entry("my_tool", my_tool))
        store = _make_store({"my_tool": _meta("my_tool")})
        cfg = _cfg_with_timeout(100)  # below 200 ms minimum

        p_store_cls, p_discover = _patch_infrastructure(registry, store)
        with p_store_cls, p_discover:
            from voice_commander.__main__ import _run_validate

            with pytest.raises(SystemExit) as exc_info:
                _run_validate(cfg)

        assert exc_info.value.code == 1

    def test_timeout_ms_at_minimum_does_not_exit(self, capsys):
        """llm_router.timeout_ms=200 (exactly at minimum) → no SystemExit."""

        def my_tool() -> None:
            pass

        registry = _make_registry(_entry("my_tool", my_tool))
        store = _make_store({"my_tool": _meta("my_tool")})
        cfg = _cfg_with_timeout(200)

        p_store_cls, p_discover = _patch_infrastructure(registry, store)
        with p_store_cls, p_discover:
            from voice_commander.__main__ import _run_validate

            _run_validate(cfg)  # must not raise

        assert "OK" in capsys.readouterr().out


# ---------------------------------------------------------------------------
# Tool drift: validate_or_die raises when registry/TOML disagree → sys.exit(1)
# ---------------------------------------------------------------------------

class TestToolDrift:
    def test_rule2_missing_toml_arg_causes_exit_1(self):
        """Function param with no matching TOML arg → validate_or_die exits 1."""

        def my_tool(query: str) -> None:
            pass

        registry = _make_registry(_entry("my_tool", my_tool))
        # TOML has no args block → 'query' is missing → [rule2]
        store = _make_store({"my_tool": _meta("my_tool", args={})})
        cfg = Config()

        p_store_cls, p_discover = _patch_infrastructure(registry, store)
        with p_store_cls, p_discover:
            from voice_commander.__main__ import _run_validate

            with pytest.raises(SystemExit) as exc_info:
                _run_validate(cfg)

        assert exc_info.value.code == 1

    def test_rule4_unsupported_type_causes_exit_1(self):
        """Param annotated with unsupported type ``list`` → validate_or_die exits 1."""

        def my_tool(items: list) -> None:
            pass

        registry = _make_registry(_entry("my_tool", my_tool))
        store = _make_store({
            "my_tool": _meta(
                "my_tool",
                args={
                    "items": ArgMetadata(
                        name="items",
                        type_str="list",
                        description="Items.",
                        required=True,
                        default=None,
                    )
                },
            )
        })
        cfg = Config()

        p_store_cls, p_discover = _patch_infrastructure(registry, store)
        with p_store_cls, p_discover:
            from voice_commander.__main__ import _run_validate

            with pytest.raises(SystemExit) as exc_info:
                _run_validate(cfg)

        assert exc_info.value.code == 1

    def test_validate_or_die_mocked_raise_propagates_exit_1(self):
        """If validate_or_die is mocked to call sys.exit(1), the CLI exit code is 1."""

        def my_tool() -> None:
            pass

        registry = _make_registry(_entry("my_tool", my_tool))
        store = _make_store({"my_tool": _meta("my_tool")})
        cfg = Config()

        p_store_cls, p_discover = _patch_infrastructure(registry, store)
        with (
            p_store_cls,
            p_discover,
            # validate_or_die is imported from .validator inside _run_validate,
            # so we patch it in its source module.
            patch(
                "voice_commander.validator.validate_or_die",
                side_effect=SystemExit(1),
            ),
            pytest.raises(SystemExit) as exc_info,
        ):
            from voice_commander.__main__ import _run_validate

            _run_validate(cfg)

        assert exc_info.value.code == 1


# ---------------------------------------------------------------------------
# Stdout contract: "OK" only printed on success, not on failure
# ---------------------------------------------------------------------------

class TestOutputContract:
    def test_ok_not_printed_on_config_failure(self, capsys):
        """When config validation fails (exit 1), 'OK' is never printed to stdout."""

        def my_tool() -> None:
            pass

        registry = _make_registry(_entry("my_tool", my_tool))
        store = _make_store({"my_tool": _meta("my_tool")})
        cfg = _cfg_with_timeout(50)  # well below minimum

        p_store_cls, p_discover = _patch_infrastructure(registry, store)
        with p_store_cls, p_discover:
            from voice_commander.__main__ import _run_validate

            with pytest.raises(SystemExit):
                _run_validate(cfg)

        captured = capsys.readouterr()
        assert "OK" not in captured.out

    def test_ok_not_printed_on_tool_drift(self, capsys):
        """When tool validation fails (exit 1), 'OK' is never printed to stdout."""

        def my_tool(query: str) -> None:
            pass

        registry = _make_registry(_entry("my_tool", my_tool))
        store = _make_store({"my_tool": _meta("my_tool", args={})})  # missing 'query' arg
        cfg = Config()

        p_store_cls, p_discover = _patch_infrastructure(registry, store)
        with p_store_cls, p_discover:
            from voice_commander.__main__ import _run_validate

            with pytest.raises(SystemExit):
                _run_validate(cfg)

        captured = capsys.readouterr()
        assert "OK" not in captured.out
