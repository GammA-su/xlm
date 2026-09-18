# Prompt 00 — Repository foundation and uv environment

Read the shared agent instructions, architecture, contracts, evaluation policy and current status before editing. Implement this milestone in the existing repository; run its tests and update the ledger.


Inspect the repository, existing `AGENTS.md`, source code, tests, package metadata, hardware assumptions and user modifications. Preserve useful existing implementation. Create `docs/implementation/STATUS.md` from the provided template and a requirement ledger mapped to the acceptance matrix. Record discrepancies with old XLM documents; the previous FineWeb-first catalog is superseded.

Implement a clean `src/xlm` package, a console entrypoint named `xlm`, a typed CLI skeleton with `--help`, `--version` and `doctor`, and a minimal configuration-independent artifact-root resolver. `doctor` must report Python/uv/package versions, OS, available CPU/RAM/disk, optional CUDA device/driver information, selected PyTorch build and disabled capabilities without downloading anything. Absence of CUDA is not an error for CPU-only use. Lazy-load optional GPU/evaluation/dashboard dependencies.

Use uv to resolve a tested Python 3.12 environment and commit a real `uv.lock`; do not write a fictional lockfile. Configure mutually exclusive CPU and CUDA extras with appropriate explicit package indexes using current official uv/PyTorch documentation. Select the CUDA build based on the actual machine and supported wheels, not a remembered CUDA number. If this environment lacks a GPU, make CPU installation verified and CUDA installation NOT RUN. Document PowerShell and Linux commands; use pathlib rather than shell-specific paths.

Add Ruff, pytest and a type checker, reasonable project settings, author-written offline test fixtures, `.gitignore` entries for raw corpora/runs/secrets/virtual environments, and CI for dependency lock consistency, formatting, linting, type checking and offline tests. CI must not fetch multi-gigabyte models or official sealed datasets. Include markers for `network`, `cuda`, `slow` and `operator` tests.

Make failures and success statuses honest. Do not add empty modules or tests that always pass simply to mirror every folder in the architecture diagram. Future commands may be documented but must not be registered as fake successful operations.

Acceptance: install with `uv sync --locked --extra cpu`; run CLI help/version/doctor; run actual offline tests with `uv run --locked --extra cpu pytest -q`; verify imports do not require credentials, network or a GPU. Verify the entrypoint does not shadow an unrelated installed `xlm` package and that the recorded package version is its own. Report CPU/CUDA verification separately. Produce environment and repository-inventory reports and identify prompt 01 as next.


Finish with actual commands/results, artifact paths, blockers and the next prompt. Never label mocks, skipped tests or unrun research campaigns as verified.
