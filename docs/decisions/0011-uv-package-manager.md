# ADR 0011: uv as Package Manager

**Status:** Accepted
**Date:** 2026-04-19

## Context

Voice Commander requires reliable, reproducible Python environment management on Windows. The project has several constraints that make environment management non-trivial:

- **CUDA-specific wheels** — `faster-whisper` depends on `cuBLAS` and `cuDNN` DLLs that ship inside PyPI wheels. The exact wheel must be resolved consistently across developer machines and CI to avoid runtime DLL-not-found errors.
- **Windows path oddities** — Several older Python toolchains have known issues with Windows long paths, spaces in `%APPDATA%`, and the `Scripts\` vs `bin\` layout difference between platforms.
- **Lockfile requirement** — The project must pin all transitive dependencies to ensure that a fresh `git clone` produces an environment identical to the one that passed the Phase 5 soak test. `pip install -r requirements.txt` without a full lock graph is insufficient because it does not pin transitive dependencies deterministically.
- **Developer ergonomics** — New contributors should be able to go from `git clone` to a working dev environment in a single command without installing anything beyond `uv` itself.
- **pyproject.toml as source of truth** — The project uses a single `pyproject.toml` for both package metadata (name, version, entry points) and dependency declarations, following PEP 517/518/621. A tool that treats `pyproject.toml` as first-class avoids the duplication of maintaining a separate `requirements.txt` alongside it.

## Decision

Use **`uv`** (Astral's Rust-based Python package manager) as the sole tool for environment creation, dependency resolution, and script execution. Commit **`uv.lock`** to version control. Treat **`pyproject.toml`** as the authoritative declaration of all dependencies (production, development, and optional extras).

Key workflow commands:

```bash
uv sync                          # create venv + install all deps from uv.lock
uv sync --extra cuda             # include CUDA optional deps
uv add <package>                 # add dep, update pyproject.toml + uv.lock atomically
uv run pytest                    # run command inside the managed venv
uv lock --upgrade-package <pkg>  # upgrade a single package and update lock
```

`uv.lock` is committed and reviewed in pull requests. A CI step verifies that `uv.lock` is consistent with `pyproject.toml` via `uv lock --check`. Developers must not bypass `uv` to install packages directly into the venv with `pip`, as this would produce an environment that diverges from the lockfile.

## Consequences

### Positive
- Dependency resolution is 10–100x faster than pip or poetry on cold installs, making CI setup and new-developer onboarding fast.
- `uv.lock` pins every transitive dependency to an exact version and hash, guaranteeing byte-for-byte reproducibility across machines and over time.
- A single `uv sync` is the complete setup command; no `python -m venv`, no `pip install`, no separate `pip-compile` step.
- `uv` manages the Python interpreter version alongside packages (via `.python-version` or `pyproject.toml [tool.uv] python`), eliminating the separate `pyenv` / `py` launcher dependency.
- `pyproject.toml` remains the single source of truth; no `requirements.txt`, `requirements-dev.txt`, or `setup.cfg` files to keep in sync.
- `uv` is actively maintained by Astral with fast Windows support; it handles the `Scripts\` layout and Windows long-path issues correctly out of the box.

### Negative
- `uv` is not part of the Python standard library and must be installed separately (via the official installer script or `winget`). Developers who only have bare Python installed need one additional bootstrap step.
- The `uv.lock` format is specific to `uv`; migrating to another tool in the future would require regenerating the lockfile. The lockfile is not human-readable in the same way as `requirements.txt`.
- Some legacy CI images (e.g. GitHub-hosted Windows runners with only Python 3.8 available) may require an explicit `uv` installation step. This is a one-line addition to any CI workflow.
- Dependency groups (`[dependency-groups]` in PEP 735) are a relatively new PEP; IDEs and static analysis tools that inspect `pyproject.toml` may not yet fully understand the `[dependency-groups]` section.

### Neutral
- The `uv.lock` file will appear in `git diff` whenever any dependency is added or upgraded. This is intentional and expected; reviewers should check lockfile diffs as part of normal PR review.
- `uv run` can be used to invoke any script or tool (e.g. `uv run pytest`, `uv run python -m voice_commander`) without activating the venv manually. This is the recommended invocation pattern in documentation and Makefile targets.
- The decision to commit `uv.lock` applies equally to the production lockfile and the development extras. There is no separate lock for production-only dependencies; the single lockfile covers the full dependency graph.

## Alternatives considered

### Poetry
A mature Python dependency manager with a lockfile (`poetry.lock`) and `pyproject.toml` integration. Rejected because: (1) Poetry's dependency resolver is significantly slower than `uv`, particularly on Windows; (2) Poetry has known issues with CUDA/PyTorch wheels that ship as platform-specific extras via direct URLs; (3) Poetry's virtual environment management uses a global cache that can conflict between projects on Windows when paths contain spaces; (4) `uv` is a strict superset of Poetry's capabilities for this use case with better Windows support.

### pip-tools (`pip-compile` + `pip-sync`)
Compiles `requirements.in` to a pinned `requirements.txt` using `pip-compile`, then installs with `pip-sync`. Workable and well-understood, but: (1) requires maintaining a separate `requirements.in` (or `pyproject.toml` extras) alongside the compiled `requirements.txt`, introducing duplication; (2) `pip-compile` does not understand `pyproject.toml` dependency groups natively; (3) `pip-sync` is slower than `uv sync`; (4) no built-in Python version management.

### Plain pip with requirements.txt
Use `pip install -r requirements.txt` with a manually maintained pinned requirements file. Rejected because a manually maintained file will drift from the true transitive dependency graph; `pip` does not verify hashes by default; and there is no `pip freeze`-based workflow that integrates cleanly with `pyproject.toml` as the authoritative source. This approach is error-prone for a project with CUDA-specific wheel constraints.

### conda / mamba
Conda environments can manage both Python packages and system-level CUDA toolkit dependencies in a unified way. Rejected because: (1) conda is heavyweight and slow on Windows; (2) the project's CUDA dependencies are shipped as PyPI wheels (via `faster-whisper`'s bundled binaries), not conda packages, so conda's primary advantage does not apply; (3) conda environments are not lockfile-based in the same reproducibility sense as `uv.lock`; (4) conda is not the norm in the Python packaging ecosystem this project targets.

## References

- uv documentation: https://docs.astral.sh/uv/
- uv lockfile reference: https://docs.astral.sh/uv/concepts/resolution/
- PEP 517 (build system interface): https://peps.python.org/pep-0517/
- PEP 621 (pyproject.toml metadata): https://peps.python.org/pep-0621/
- PEP 735 (dependency groups): https://peps.python.org/pep-0735/
