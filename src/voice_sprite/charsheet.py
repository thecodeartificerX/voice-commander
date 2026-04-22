from __future__ import annotations

import logging
import tomllib
from dataclasses import dataclass
from pathlib import Path

from .state_machine import SpriteState

logger = logging.getLogger(__name__)

KNOWN_TOP_KEYS = {"frame_width", "frame_height", "fps", "states", "transitions"}


class CharSheetError(Exception):
    pass


@dataclass(frozen=True)
class AnimInfo:
    row: int
    frames: int
    once: bool = False


@dataclass
class CharSheet:
    frame_width: int
    frame_height: int
    fps: int
    states: dict[SpriteState, AnimInfo]
    transitions: dict[tuple[SpriteState, SpriteState], AnimInfo]

    def get_state_anim(self, state: SpriteState) -> AnimInfo:
        return self.states[state]

    def get_transition(self, from_state: SpriteState, to_state: SpriteState) -> AnimInfo | None:
        return self.transitions.get((from_state, to_state))


def load_charsheet(toml_path: Path, png_path: Path) -> CharSheet:
    """Load and validate a charsheet TOML + PNG pair."""
    if not png_path.exists():
        raise CharSheetError(f"Charsheet PNG not found: {png_path}")

    with toml_path.open("rb") as f:
        try:
            raw = tomllib.load(f)
        except tomllib.TOMLDecodeError as e:
            raise CharSheetError(f"Invalid TOML in {toml_path}: {e}") from e

    # Warn on unknown top-level keys
    for key in raw:
        if key not in KNOWN_TOP_KEYS:
            logger.warning("Unknown charsheet.toml key: %s (ignored)", key)

    frame_w = raw.get("frame_width", 128)
    frame_h = raw.get("frame_height", 128)
    fps = raw.get("fps", 12)

    # Parse state animations
    states_raw = raw.get("states", {})
    states: dict[SpriteState, AnimInfo] = {}
    for state in SpriteState:
        entry = states_raw.get(state.value)
        if entry is None:
            raise CharSheetError(
                f"State '{state.value}' missing from charsheet.toml [states] section"
            )
        if entry["frames"] < 1:
            raise CharSheetError(f"State '{state.value}' must have frames >= 1")
        if entry["row"] < 0:
            raise CharSheetError(f"State '{state.value}' must have row >= 0")
        states[state] = AnimInfo(
            row=entry["row"],
            frames=entry["frames"],
            once=entry.get("once", False),
        )

    # Parse transition animations
    transitions_raw = raw.get("transitions", {})
    transitions: dict[tuple[SpriteState, SpriteState], AnimInfo] = {}
    for key, entry in transitions_raw.items():
        parts = key.split("->")
        if len(parts) != 2:
            logger.warning("Malformed transition key: %s (ignored)", key)
            continue
        try:
            from_state = SpriteState(parts[0].strip())
            to_state = SpriteState(parts[1].strip())
        except ValueError:
            logger.warning("Unknown state in transition '%s' (ignored)", key)
            continue
        if entry["frames"] < 1:
            raise CharSheetError(f"Transition '{key}' must have frames >= 1")
        if entry["row"] < 0:
            raise CharSheetError(f"Transition '{key}' must have row >= 0")
        transitions[(from_state, to_state)] = AnimInfo(
            row=entry["row"],
            frames=entry["frames"],
            once=entry.get("once", True),
        )

    cs = CharSheet(
        frame_width=frame_w,
        frame_height=frame_h,
        fps=fps,
        states=states,
        transitions=transitions,
    )

    # Validate PNG bounds
    _validate_png_bounds(cs, png_path)

    return cs


def _validate_png_bounds(cs: CharSheet, png_path: Path) -> None:
    """Verify every declared row × frames fits inside the PNG."""
    try:
        from PIL import Image

        img = Image.open(png_path)
        png_w, png_h = img.size
        img.close()
    except ImportError:
        # Pillow not available — skip bounds check (pyglet will catch at render time)
        logger.warning("Pillow not installed — skipping charsheet PNG bounds validation")
        return

    all_anims: list[tuple[str, AnimInfo]] = []
    for state, anim in cs.states.items():
        all_anims.append((f"state:{state.value}", anim))
    for (from_s, to_s), anim in cs.transitions.items():
        all_anims.append((f"transition:{from_s.value}->{to_s.value}", anim))

    for label, anim in all_anims:
        max_x = anim.frames * cs.frame_width
        max_y = (anim.row + 1) * cs.frame_height
        if max_x > png_w or max_y > png_h:
            raise CharSheetError(
                f"{label} (row={anim.row}, frames={anim.frames}) exceeds PNG bounds "
                f"({png_w}x{png_h}). Need at least {max_x}x{max_y}."
            )
