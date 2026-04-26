"""Exit codes the daemon emits for the supervisor to interpret.

The contract is intentionally tiny: zero for clean shutdown, ``EXIT_RESTART``
for "respawn me," anything else is a crash and the supervisor exits with the
same code.
"""

from __future__ import annotations

EXIT_CLEAN: int = 0
EXIT_RESTART: int = 75
