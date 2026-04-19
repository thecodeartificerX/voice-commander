# uv Reference

> Cited from: https://docs.astral.sh/uv/ (2026-04-19)
> Cited from: https://docs.astral.sh/uv/reference/cli/ (2026-04-19)
> Cited from: https://docs.astral.sh/uv/concepts/projects/dependencies/ (2026-04-19)
> Cited from: https://docs.astral.sh/uv/guides/projects/ (2026-04-19)

---

## Overview

uv is an extremely fast Python package and project manager written in Rust by Astral.
It replaces pip, pip-tools, pipenv, poetry, pyenv, virtualenv, and twine in a single
unified tool. Voice Commander uses uv for dependency management, virtual environments,
and running scripts in the isolated project environment.

Key properties:
- 10–100× faster than pip for installs
- Fully PEP 517/518/660/735 compliant
- Built-in lockfile (`uv.lock`) for reproducible environments
- Python version management via `uv python install`
- Dependency groups (dev, test, lint) via PEP 735

## Installation

```bash
# Windows (PowerShell)
powershell -c "irm https://astral.sh/uv/install.ps1 | iex"

# Or with pip
pip install uv

# Or with pipx
pipx install uv
```

Verify:
```bash
uv --version
```

---

## Minimal Working Example (Voice Commander)

```bash
# Initial project setup
uv init voice-commander
cd voice-commander

# Add runtime dependencies
uv add faster-whisper rapidfuzz sounddevice pynput windows-toasts pyautogui pystray

# Add dev dependencies
uv add --group dev pytest ruff mypy

# Sync environment (install all deps)
uv sync

# Run the application
uv run python main.py

# Run with dev group included
uv run --group dev pytest
```

---

## Command Reference

### `uv init`

Initialize a new project.

```bash
uv init [OPTIONS] [PATH]
```

| Flag | Description |
|------|-------------|
| `--app` / `--application` | Application project (default) |
| `--lib` / `--library` | Library project for distribution |
| `--script` | Standalone PEP 723 script |
| `--bare` | Only create pyproject.toml, skip README/main.py |
| `--name NAME` | Project name (defaults to directory name) |
| `--description TEXT` | Project description |
| `--python VERSION` | Python version for minimum version requirement |
| `--no-pin-python` | Skip creating `.python-version` file |
| `--build-backend BACKEND` | Choose backend: `uv`, `hatch`, `flit`, `pdm`, `poetry`, `setuptools`, `maturin`, `scikit` |
| `--vcs VCS` | Version control: `git` (default) or `none` |
| `--no-workspace` | Create standalone project, don't join parent workspace |
| `--no-readme` | Don't create README.md |
| `--package` / `--no-package` | Configure as distributable package |
| `--author-from MODE` | Fill authors: `auto`, `git`, or `none` |

**Examples:**
```bash
uv init                          # init in current directory
uv init my-project               # create new directory
uv init --lib my-library         # library template
uv init --bare --name vc         # minimal pyproject.toml only
uv init --python 3.11            # require Python >= 3.11
```

**Creates:**
```
my-project/
├── .gitignore
├── .python-version        # pinned Python version
├── README.md
├── main.py                # entry point
├── pyproject.toml
├── uv.lock                # created on first sync
└── .venv/                 # created on first sync
```

---

### `uv add`

Add dependencies to the project's `pyproject.toml` and update the lockfile.

```bash
uv add [OPTIONS] PACKAGE [PACKAGE ...]
```

| Flag | Description |
|------|-------------|
| `--dev` | Add to dev group (alias: `--group dev`) |
| `--group GROUP` | Add to named dependency group |
| `--optional EXTRA` | Add to optional dependencies (extras) |
| `--extra EXTRA` | Enable extras for the package |
| `--editable` | Add as editable install |
| `--requirements FILE` | Add packages from requirements.txt |
| `--frozen` | Add to pyproject.toml without re-locking |
| `--no-sync` | Add to pyproject.toml without syncing venv |
| `--branch BRANCH` | Git branch to use |
| `--bounds STYLE` | Version constraint: `lower`, `major`, `minor`, `exact` |
| `--marker MARKER` | Environment marker for the package |
| `--upgrade` | Allow upgrading packages |
| `--upgrade-package PKG` | Allow upgrading a specific package |
| `--index-url URL` | Custom PyPI index |
| `--extra-index-url URL` | Additional index |
| `--prerelease MODE` | Pre-release handling: `allow`, `disallow`, etc. |

**Examples:**
```bash
# Add runtime dependency
uv add requests

# Add with version constraint
uv add "requests>=2.28,<3"

# Add dev dependency
uv add --dev pytest ruff mypy

# Add to custom group
uv add --group lint ruff pylint

# Add from git
uv add git+https://github.com/org/repo.git

# Add from local path
uv add ./local-package

# Add with extras
uv add "uvicorn[standard]"

# Add optional dependency (extras in pyproject.toml)
uv add --optional docs sphinx

# Add without syncing
uv add --no-sync numpy
```

**Resulting pyproject.toml:**
```toml
[project]
dependencies = [
    "requests>=2.28",
]

[dependency-groups]
dev = [
    "pytest>=8.0",
    "ruff>=0.1",
]
lint = [
    "ruff>=0.1",
]
```

---

### `uv sync`

Synchronize the virtual environment with the lockfile and pyproject.toml.

```bash
uv sync [OPTIONS]
```

| Flag | Description |
|------|-------------|
| `--all-groups` | Include all dependency groups |
| `--group GROUP` | Include specific group |
| `--no-group GROUP` | Exclude specific group |
| `--no-dev` | Exclude dev group |
| `--only-dev` | Include only dev group |
| `--all-extras` | Include all optional extras |
| `--extra EXTRA` | Include specific extra |
| `--frozen` | Don't update lockfile; sync from existing lock |
| `--locked` | Assert lockfile is up to date (fail if not) |
| `--no-install-project` | Skip installing the project package itself |
| `--exact` | Remove packages not in lockfile (clean sync) |
| `--compile-bytecode` | Compile .py files to .pyc |
| `--python VERSION` | Python version for the environment |
| `--upgrade` | Allow upgrading dependencies |
| `--upgrade-package PKG` | Upgrade specific package |

**Examples:**
```bash
uv sync                     # sync runtime deps only (default group = dev)
uv sync --all-groups        # sync all groups including dev/lint/test
uv sync --group test        # sync with test group added
uv sync --frozen            # sync without re-locking (fast, CI)
uv sync --locked            # assert lockfile unchanged (strict CI check)
uv sync --no-dev            # production sync without dev deps
```

---

### `uv run`

Run a command or script in the project's virtual environment.

```bash
uv run [OPTIONS] COMMAND [ARGS...]
```

| Flag | Description |
|------|-------------|
| `--module` / `-m` | Run as Python module (`python -m MODULE`) |
| `--script` / `-s` | Treat as PEP 723 script |
| `--with PKG` | Install extra package just for this run |
| `--with-editable PATH` | Install path as editable for this run |
| `--all-groups` | Include all dependency groups |
| `--group GROUP` | Include specific group |
| `--no-group GROUP` | Exclude group |
| `--no-dev` | Exclude dev group |
| `--all-extras` | Include all extras |
| `--extra EXTRA` | Include specific extra |
| `--frozen` | Don't update lockfile |
| `--locked` | Assert lockfile unchanged |
| `--no-sync` | Skip syncing before run |
| `--isolated` | Fresh isolated environment |
| `--active` | Prefer active virtual environment |
| `--no-project` | Don't discover project |
| `--python VERSION` | Specific Python version |
| `--env-file FILE` | Load environment variables from file |
| `--all-packages` | Run with all workspace members |
| `--package PKG` | Run in specific workspace package |

**Examples:**
```bash
uv run python main.py               # run main.py
uv run python -m pytest             # run tests
uv run -m pytest tests/             # same with -m flag
uv run --group test pytest          # include test group
uv run -- flask run -p 8000         # pass args after --
uv run --with black black src/      # one-off with extra package
uv run --frozen python main.py      # don't re-lock (fast CI)
uv run --env-file .env python app.py # load .env file
```

---

### `uv lock`

Update or verify the project lockfile.

```bash
uv lock [OPTIONS]
```

| Flag | Description |
|------|-------------|
| `--upgrade` | Allow upgrading all packages |
| `--upgrade-package PKG` | Allow upgrading specific package |
| `--upgrade-group GROUP` | Allow upgrading packages in a group |
| `--frozen` | Don't re-lock; assert current lock is valid |
| `--check` | Exit with error if lockfile needs updating |
| `--all-groups` | Include all groups in resolution |
| `--group GROUP` | Include specific group |
| `--no-group GROUP` | Exclude group |
| `--extra EXTRA` | Include extras in resolution |
| `--python VERSION` | Target Python version |
| `--index-url URL` | Custom PyPI index |
| `--exclude-newer DATE` | Exclude packages published after date |

**Examples:**
```bash
uv lock                         # re-lock (update all to latest compatible)
uv lock --upgrade               # upgrade everything to latest
uv lock --upgrade-package numpy # upgrade only numpy
uv lock --check                 # CI: fail if lockfile is out of date
```

---

## Dependency Groups (PEP 735)

Dependency groups are local-only groupings of dependencies stored in `pyproject.toml`.
They are NOT included in the published package metadata.

### Defining groups in pyproject.toml

```toml
[dependency-groups]
dev = ["pytest>=8.1", "ruff>=0.4"]
lint = ["ruff>=0.4", "mypy>=1.0"]
test = ["pytest>=8.1", "pytest-cov>=4.0"]
docs = ["sphinx>=7.0", "myst-parser>=3.0"]
```

### The `dev` group

The `dev` group is special-cased:
- Created by default with `uv add --dev`
- Included by default in `uv run` and `uv sync` unless `--no-dev` is passed
- Shorthand: `--dev` = `--group dev`

### Group nesting (include-group)

Groups can include other groups:
```toml
[dependency-groups]
lint = ["ruff", "mypy"]
test = ["pytest", "pytest-cov"]
dev = [
    {include-group = "lint"},
    {include-group = "test"},
    "ipython",
]
```

### Default groups

Control which groups are included by default (in `uv run`, `uv sync`):
```toml
[tool.uv]
default-groups = ["dev", "test"]     # list
# or
default-groups = "all"               # all groups
```

Override per-command with `--no-default-groups`.

### Per-group Python version requirements

```toml
[tool.uv.dependency-groups]
dev = {requires-python = ">=3.12"}
```

### Using groups in commands

```bash
# Add to a group
uv add --group test pytest-mock

# Sync with specific group
uv sync --group lint

# Run with all groups
uv run --all-groups pytest

# Exclude a group
uv run --no-group docs python main.py

# Only run with dev group
uv run --only-group dev ruff check .
```

---

## pyproject.toml Structure

Full example for Voice Commander:

```toml
[project]
name = "voice-commander"
version = "0.1.0"
description = "Push-to-talk voice command dispatcher"
readme = "README.md"
requires-python = ">=3.11"
dependencies = [
    "faster-whisper>=1.0",
    "rapidfuzz>=3.0",
    "sounddevice>=0.4",
    "pynput>=1.7",
    "windows-toasts>=1.3",
    "pyautogui>=0.9",
    "pystray>=0.19",
    "pillow>=10.0",
    "tomli>=2.0; python_version < '3.11'",
    "numpy>=1.24",
]

[dependency-groups]
dev = [
    "pytest>=8.0",
    "ruff>=0.4",
    "mypy>=1.0",
    "pytest-cov>=4.0",
]

[tool.uv]
default-groups = ["dev"]

[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"
```

---

## uv.lock File

The lockfile is auto-generated by uv. Key properties:
- Cross-platform: works on Windows/Linux/macOS
- Should be committed to git
- Machine-readable TOML format (not for manual editing)
- Contains exact versions + hashes for all transitive deps
- Re-generated on `uv add`, `uv remove`, `uv lock --upgrade`

```bash
# Commit lockfile with project
git add pyproject.toml uv.lock
git commit -m "chore: add dependencies"
```

---

## Common Workflows

```bash
# Start a new project
uv init voice-commander
cd voice-commander
uv add faster-whisper rapidfuzz sounddevice pynput

# Install everything
uv sync --all-groups

# Run tests
uv run pytest

# Lint
uv run --group lint ruff check .

# Update a dependency
uv lock --upgrade-package faster-whisper
uv sync

# Production install (no dev deps)
uv sync --no-dev --frozen

# CI: assert lockfile is current, then test
uv sync --locked
uv run pytest

# Run a one-off command without touching project deps
uv run --isolated --with httpx python -c "import httpx; print(httpx.get('https://example.com').status_code)"
```

---

## Known Gotchas

1. **`uv.lock` must be committed** — without it, `uv sync --frozen` fails in CI.

2. **Groups not published** — dependency groups are local-only. If you publish the
   package, only `[project].dependencies` is included; groups are stripped.

3. **`default-groups` defaults to `["dev"]`** — on a fresh `uv sync`, dev deps ARE
   installed. Use `uv sync --no-dev` for production.

4. **Resolution includes ALL groups** — even when syncing only runtime deps, uv resolves
   all groups in the lockfile. Conflicting versions across groups will fail at lock time.

5. **`uv add` vs direct pyproject.toml edit** — always use `uv add` to modify deps;
   direct edits to pyproject.toml require a manual `uv lock` to update the lockfile.

6. **Python version pinning** — `.python-version` is read by uv to select Python.
   Delete or change it to use a different Python version.

7. **`--frozen` in CI** — always use `uv sync --frozen` or `uv run --frozen` in CI to
   prevent accidental lockfile updates.

8. **Virtual environment location** — uv creates `.venv/` in the project root by default.
   This is gitignored by the generated `.gitignore`.

---

## Cited from

- https://docs.astral.sh/uv/ — fetched 2026-04-19
- https://docs.astral.sh/uv/reference/cli/ — fetched 2026-04-19
- https://docs.astral.sh/uv/concepts/projects/dependencies/ — fetched 2026-04-19
- https://docs.astral.sh/uv/guides/projects/ — fetched 2026-04-19
