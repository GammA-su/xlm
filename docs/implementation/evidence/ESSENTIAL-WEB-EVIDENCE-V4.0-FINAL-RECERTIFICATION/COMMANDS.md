# Final B01/B02 recertification commands

Working directory: `F:\Project\xlm-data-ultrax`. Starting/reviewed HEAD:
`a9f89c0d13ef01e9ab387104c4acd4da948d2366`.

Initial inspection: `git status --short`, `git rev-parse HEAD`,
`git log --oneline -8`, `git branch --show-current`, `git show --stat HEAD`,
`git diff --stat`: exit 0. Read-only Get-Content/rg inspection of the attached
request, AGENTS, status, protocol, repair, transport, engine and focused tests.
One read-only batch failed at PowerShell parsing (exit 1) because an rg argument
used shell brace expansion; it was corrected to explicit filenames. No command
in that failed batch executed. This was not a product test failure.

The recorder writes exact child argument arrays, exits and wall times to
[commands.json](commands.json); outputs remain separate, including the initial
review-harness cleanup failure. The outer commands actually executed were:

```powershell
uv run --offline --locked --extra cpu --extra eval python docs/implementation/evidence/ESSENTIAL-WEB-EVIDENCE-V4.0-FINAL-RECERTIFICATION/run_review.py
# exit 1 after verifier (0), focused tests (0), repair probe (0), review harness cleanup (1)

# After explicitly closing the review-only SQLite connection:
uv run --offline --locked --extra cpu --extra eval python docs/implementation/evidence/ESSENTIAL-WEB-EVIDENCE-V4.0-FINAL-RECERTIFICATION/run_review.py --independent-only
# exit 0; reruns only the independent review probe

uv run --offline --locked --extra cpu --extra eval python docs/implementation/evidence/ESSENTIAL-WEB-EVIDENCE-V4.0-FINAL-RECERTIFICATION/run_review.py --injection-only
# exit 0; both injection-result assertions pass
```

The full focused child command was:

```powershell
uv run --offline --locked --extra cpu --extra eval python -m pytest -n 0 -q -p no:cacheprovider tests/test_evidence_v4_plan.py tests/test_evidence_v4_transport.py tests/test_evidence_v4_engine.py tests/test_evidence_v4_e2e.py tests/test_evidence_v4_wire.py --junitxml=F:\Project\xlm-data-ultrax\docs\implementation\evidence\ESSENTIAL-WEB-EVIDENCE-V4.0-FINAL-RECERTIFICATION\focused.xml
```

The recorder sets OMP/MKL/OPENBLAS/NUMEXPR thread counts to 1,
`TOKENIZERS_PARALLELISM=false`, `UV_OFFLINE=1`, HF/Transformers offline flags,
and `PYTHONDONTWRITEBYTECODE=1` for child commands. No workers were launched.
The tests block real network connections; the independent probe replaces socket
creation/resolution with explicit refusal. All HTTP wires are authored fixtures.
The supplied probe disables connection creation/resolution and uses its own
scripted socket. No real Phase-P command was executed.

`baseline.json` records environment, starting identity, prior dirty/untracked
file hashes and real-root absence. `focused.xml` contains all 180 passing cases.
`independent-corrected.txt` is JSON with exact byte counts, states, timings,
independent identities and measured fixture storage. Failed `independent.txt`
is retained as the honest initial harness result. No broad acceptance, additional
science selection, lint/typecheck, CUDA, network or production performance run.

Final preservation/publication commands (review artifacts only):

```powershell
uv run --offline --locked --extra cpu --extra eval python docs/implementation/evidence/ESSENTIAL-WEB-EVIDENCE-V4.0-FINAL-RECERTIFICATION/finalize_review.py
git add -- docs/implementation/evidence/ESSENTIAL-WEB-EVIDENCE-V4.0-FINAL-RECERTIFICATION docs/implementation/reports/ESSENTIAL-WEB-EVIDENCE-V4.0-FINAL-RECERTIFICATION.md
git diff --cached --check
git diff --cached --stat
git diff --cached --name-only
git commit -m "docs: pass final Essential-Web v4 B01/B02 recertification"
git status --short
git rev-parse HEAD
```

Preservation and artifact hashes are in `preservation.json` and
`artifact_manifest.json`. STATUS.md is intentionally excluded from staging;
its entire pre-review content remains intact below the new status notice.
Final commit identity is returned to the operator outside this self-bound report.

Documentation-write exception: the first two `finalize_review.py` invocations
exited 1 because Windows refused `os.replace` of STATUS.md (WinError 5).
The intended complete bytes remained in `STATUS.md.final-review.tmp`; the old
file was untouched. Applying the notice with the editor changed line endings
at the insertion boundary. The finalizer was adjusted to verify the preserved
prior SHA-256 and normalized current content, then write/fsync the exact intended
bytes in place, verify them, and remove only its own temporary. The final call
exited 0. This documentation write could not use atomic replacement; the prior
bytes were retained throughout and verified afterward. No product test was
rerun and no production code was changed for this Windows write issue.

Plain `git diff --cached --check` exits 2 on the retained CRLF Windows logs/JSON
(CR at EOL reported as trailing whitespace). Raw execution outputs were preserved.
`git -c core.whitespace=cr-at-eol diff --cached --check` exits 0. This setting is
command-local, not a repository configuration change. After these documentation
notes, the finalizer was rerun (exit 0) to regenerate the manifest, and only the
same review paths were restaged. Git add/stat/name inspection and commit exit 0.
