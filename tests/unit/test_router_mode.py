"""Unit tests for the ``--router-mode`` CLI flag and ``apply_router_mode()``.

Contract source: the task description for the router-mode-flags feature branch.

Coverage:
  A. apply_router_mode happy paths
  B. apply_router_mode error paths
  C. CLI flag parsing via _build_parser()
  D. Integration with _run_validate (via main() with monkeypatched sys.argv)
"""

# NOTE: Do NOT add `from __future__ import annotations` here.
# The validator calls typing.get_type_hints() which needs real runtime annotations.

import dataclasses
import sys
from unittest.mock import MagicMock, patch

import pytest

from voice_commander.config import Config, LLMRouterConfig, MatchingConfig
from voice_commander.registry import ToolEntry, ToolRegistry
from voice_commander.tool_metadata import ArgMetadata, ToolMetadata, ToolMetadataStore

# ---------------------------------------------------------------------------
# Helpers — mirrors test_cli_validate.py style
# ---------------------------------------------------------------------------


def _make_store(tools: dict[str, ToolMetadata]) -> MagicMock:
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


def _patch_infrastructure(registry: ToolRegistry, store: MagicMock):
    """Return patches that wire a controlled registry+store into _run_validate."""
    p_store_cls = patch(
        "voice_commander.tool_metadata.ToolMetadataStore",
        return_value=store,
    )
    p_discover = patch("voice_commander.registry.discover", return_value=registry)
    return p_store_cls, p_discover


def _cfg_with_llm_router(**kwargs) -> Config:
    """Return a Config with a custom LLMRouterConfig built from kwargs."""
    return dataclasses.replace(Config(), llm_router=LLMRouterConfig(**kwargs))


def _clean_registry_and_store():
    """Return a minimal valid (tool, store) pair for a passing validate run."""

    def my_tool() -> None:
        pass

    registry = _make_registry(_entry("my_tool", my_tool))
    store = _make_store({"my_tool": _meta("my_tool")})
    return registry, store


# ---------------------------------------------------------------------------
# A. apply_router_mode happy paths
# ---------------------------------------------------------------------------


class TestApplyRouterModeHappyPaths:
    def test_apply_router_mode_hybrid_is_noop(self):
        """hybrid mode → apply_router_mode returns the identical Config object."""
        from voice_commander.__main__ import apply_router_mode

        cfg = Config()
        result = apply_router_mode(cfg, "hybrid")

        # Must be the exact same object (no copy), and equal by value
        assert result is cfg

    def test_apply_router_mode_fuzzy_disables_llm(self):
        """fuzzy mode → llm_router.enabled goes True→False; threshold and endpoint untouched."""
        from voice_commander.__main__ import apply_router_mode

        # Start with llm enabled and a known threshold
        original_threshold = 75.0
        cfg = dataclasses.replace(
            Config(),
            matching=MatchingConfig(threshold=original_threshold),
            llm_router=LLMRouterConfig(enabled=True, endpoint_url="http://myhost/v1"),
        )

        result = apply_router_mode(cfg, "fuzzy")

        assert result.llm_router.enabled is False
        # threshold must be untouched
        assert result.matching.threshold == original_threshold
        # endpoint_url must be untouched
        assert result.llm_router.endpoint_url == "http://myhost/v1"
        # result is a new object (not same reference), since a replace was done
        assert result is not cfg

    def test_apply_router_mode_llm_enables_llm_and_sets_threshold_101(self):
        """llm mode → threshold becomes 101.0 and llm_router.enabled becomes True."""
        from voice_commander.__main__ import apply_router_mode

        # Fully configured llm_router section (enabled=False initially)
        cfg = dataclasses.replace(
            Config(),
            llm_router=LLMRouterConfig(
                enabled=False,
                endpoint_url="http://myhost:1234/v1",
                model_id="mistral/mistral-7b",
            ),
        )

        result = apply_router_mode(cfg, "llm")

        assert result.matching.threshold == 101.0
        assert result.llm_router.enabled is True
        # other fields on llm_router must be preserved
        assert result.llm_router.endpoint_url == "http://myhost:1234/v1"
        assert result.llm_router.model_id == "mistral/mistral-7b"

    def test_apply_router_mode_llm_with_already_enabled_router(self):
        """llm mode works equally when llm_router.enabled is already True."""
        from voice_commander.__main__ import apply_router_mode

        cfg = dataclasses.replace(
            Config(),
            llm_router=LLMRouterConfig(
                enabled=True,
                endpoint_url="http://myhost:1234/v1",
                model_id="mistral/mistral-7b",
            ),
        )

        result = apply_router_mode(cfg, "llm")

        assert result.matching.threshold == 101.0
        assert result.llm_router.enabled is True


# ---------------------------------------------------------------------------
# B. apply_router_mode error paths
# ---------------------------------------------------------------------------


class TestApplyRouterModeErrorPaths:
    def test_apply_router_mode_llm_missing_endpoint_raises(self):
        """llm mode with empty endpoint_url → RuntimeError mentioning [llm_router] and llm."""
        from voice_commander.__main__ import apply_router_mode

        cfg = dataclasses.replace(
            Config(),
            llm_router=LLMRouterConfig(
                enabled=False,
                endpoint_url="",  # empty — triggers the guard
                model_id="mistral/mistral-7b",
            ),
        )

        with pytest.raises(RuntimeError) as exc_info:
            apply_router_mode(cfg, "llm")

        msg = str(exc_info.value)
        assert "[llm_router]" in msg
        assert "llm" in msg

    def test_apply_router_mode_llm_missing_model_raises(self):
        """llm mode with empty model_id → RuntimeError mentioning [llm_router] and llm."""
        from voice_commander.__main__ import apply_router_mode

        cfg = dataclasses.replace(
            Config(),
            llm_router=LLMRouterConfig(
                enabled=False,
                endpoint_url="http://myhost:1234/v1",
                model_id="",  # empty — triggers the guard
            ),
        )

        with pytest.raises(RuntimeError) as exc_info:
            apply_router_mode(cfg, "llm")

        msg = str(exc_info.value)
        assert "[llm_router]" in msg
        assert "llm" in msg

    def test_apply_router_mode_unknown_mode_raises_valueerror(self):
        """Unrecognised mode string → ValueError containing the offending mode name."""
        from voice_commander.__main__ import apply_router_mode

        cfg = Config()

        with pytest.raises(ValueError) as exc_info:
            apply_router_mode(cfg, "bogus")

        assert "bogus" in str(exc_info.value)

    def test_apply_router_mode_another_unknown_mode_raises_valueerror(self):
        """A second unknown mode string to confirm ValueError is general, not hard-coded."""
        from voice_commander.__main__ import apply_router_mode

        cfg = Config()

        with pytest.raises(ValueError) as exc_info:
            apply_router_mode(cfg, "turbo")

        assert "turbo" in str(exc_info.value)


# ---------------------------------------------------------------------------
# C. CLI flag parsing via _build_parser()
# ---------------------------------------------------------------------------


class TestCliParsing:
    def test_cli_parses_router_mode_flag(self):
        """--router-mode llm sets args.router_mode == 'llm'."""
        from voice_commander.__main__ import _build_parser

        parser = _build_parser()
        args = parser.parse_args(["--router-mode", "llm"])

        assert args.router_mode == "llm"

    def test_cli_parses_router_mode_fuzzy(self):
        """--router-mode fuzzy sets args.router_mode == 'fuzzy'."""
        from voice_commander.__main__ import _build_parser

        parser = _build_parser()
        args = parser.parse_args(["--router-mode", "fuzzy"])

        assert args.router_mode == "fuzzy"

    def test_cli_parses_router_mode_hybrid_explicitly(self):
        """--router-mode hybrid (explicit) sets args.router_mode == 'hybrid'."""
        from voice_commander.__main__ import _build_parser

        parser = _build_parser()
        args = parser.parse_args(["--router-mode", "hybrid"])

        assert args.router_mode == "hybrid"

    def test_cli_default_router_mode_is_hybrid(self):
        """No --router-mode flag → default is 'hybrid'."""
        from voice_commander.__main__ import _build_parser

        parser = _build_parser()
        args = parser.parse_args([])

        assert args.router_mode == "hybrid"

    def test_cli_router_mode_rejects_invalid_value(self):
        """--router-mode bogus → argparse raises SystemExit (invalid choice)."""
        from voice_commander.__main__ import _build_parser

        parser = _build_parser()

        with pytest.raises(SystemExit):
            parser.parse_args(["--router-mode", "bogus"])

    def test_cli_router_mode_works_with_validate_flag(self):
        """--validate --router-mode fuzzy coexist on the same parsed args namespace."""
        from voice_commander.__main__ import _build_parser

        parser = _build_parser()
        args = parser.parse_args(["--validate", "--router-mode", "fuzzy"])

        assert args.validate is True
        assert args.router_mode == "fuzzy"

    def test_cli_validate_alone_has_hybrid_default(self):
        """--validate without --router-mode still defaults router_mode to 'hybrid'."""
        from voice_commander.__main__ import _build_parser

        parser = _build_parser()
        args = parser.parse_args(["--validate"])

        assert args.validate is True
        assert args.router_mode == "hybrid"


# ---------------------------------------------------------------------------
# D. Integration with _run_validate / main()
# ---------------------------------------------------------------------------


class TestValidateIntegration:
    def test_validate_with_router_mode_fuzzy_exits_zero(self, capsys, monkeypatch):
        """--validate --router-mode fuzzy on a clean stack → exits 0 (no SystemExit)."""

        def my_tool() -> None:
            pass

        registry, store = _clean_registry_and_store()

        monkeypatch.setattr(
            sys, "argv", ["voice_commander", "--validate", "--router-mode", "fuzzy"]
        )

        with patch("voice_commander.__main__.Config") as mock_cfg_cls:
            mock_cfg_cls.load.return_value = Config()
            p_store_cls, p_discover = _patch_infrastructure(registry, store)
            with p_store_cls, p_discover:
                from voice_commander.__main__ import main

                # Should not raise SystemExit
                main()

        captured = capsys.readouterr()
        assert "OK" in captured.out

    def test_validate_with_router_mode_hybrid_exits_zero(self, capsys, monkeypatch):
        """--validate --router-mode hybrid on a clean stack → exits 0."""

        def my_tool() -> None:
            pass

        registry, store = _clean_registry_and_store()

        monkeypatch.setattr(
            sys, "argv", ["voice_commander", "--validate", "--router-mode", "hybrid"]
        )

        with patch("voice_commander.__main__.Config") as mock_cfg_cls:
            mock_cfg_cls.load.return_value = Config()
            p_store_cls, p_discover = _patch_infrastructure(registry, store)
            with p_store_cls, p_discover:
                from voice_commander.__main__ import main

                main()

        captured = capsys.readouterr()
        assert "OK" in captured.out

    def test_validate_with_router_mode_llm_missing_config_exits_nonzero(self, monkeypatch):
        """--validate --router-mode llm with no llm_router endpoint configured → non-zero exit.

        The default Config has endpoint_url='http://localhost:1234/v1' and
        model_id='google/gemma-4-e4b', which are both non-empty — so the guard
        inside apply_router_mode will NOT fire for the default config.
        We supply a config where endpoint_url is empty to trigger the RuntimeError.
        """

        def my_tool() -> None:
            pass

        registry, store = _clean_registry_and_store()

        # Config with empty endpoint_url to trigger apply_router_mode's guard
        bad_cfg = dataclasses.replace(
            Config(),
            llm_router=LLMRouterConfig(endpoint_url="", model_id=""),
        )

        monkeypatch.setattr(sys, "argv", ["voice_commander", "--validate", "--router-mode", "llm"])

        with patch("voice_commander.__main__.Config") as mock_cfg_cls:
            mock_cfg_cls.load.return_value = bad_cfg
            p_store_cls, p_discover = _patch_infrastructure(registry, store)
            with p_store_cls, p_discover:
                from voice_commander.__main__ import main

                # apply_router_mode raises RuntimeError; main() should propagate it
                # (it is not caught → bubbles out as an unhandled exception, not SystemExit)
                with pytest.raises((SystemExit, RuntimeError)):
                    main()

    def test_validate_with_router_mode_llm_full_config_exits_zero(self, capsys, monkeypatch):
        """--validate --router-mode llm with complete [llm_router] section → exits 0."""

        def my_tool() -> None:
            pass

        registry, store = _clean_registry_and_store()

        good_cfg = dataclasses.replace(
            Config(),
            llm_router=LLMRouterConfig(
                enabled=True,
                endpoint_url="http://myhost:1234/v1",
                model_id="mistral/mistral-7b",
            ),
        )

        monkeypatch.setattr(sys, "argv", ["voice_commander", "--validate", "--router-mode", "llm"])

        with patch("voice_commander.__main__.Config") as mock_cfg_cls:
            mock_cfg_cls.load.return_value = good_cfg
            p_store_cls, p_discover = _patch_infrastructure(registry, store)
            with p_store_cls, p_discover:
                from voice_commander.__main__ import main

                main()

        captured = capsys.readouterr()
        assert "OK" in captured.out
