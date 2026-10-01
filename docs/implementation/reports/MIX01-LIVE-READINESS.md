# Mix-01 live readiness from certified evidence — 2026-10-01

## Defect

After a valid UltraX admission, `xlm data mix01-status` reported
`[NOT_LIVE_VERIFIED] ultrax_ultrafineweb` ("no adapter has been tested against
live rows at the observed revision").

Cause: `mix01_status` (`src/xlm/data/sources/mix01.py`) checked the static
registry flag `live_verified` in `recipes/mixtures/mix01_views.yaml`, which is
`false` for UltraX. The admission gate was a separate check, and it passed. The
bridge (`certified_evidence.py`) already published immutable real-row
certification, and `verify_current` / `decision_binds_bridge` already existed.
However, nothing called them from readiness. Nothing was meant to update the
YAML flag after admission.

## Fix

Readiness now derives live verification from immutable store evidence
(`mix01_admission.live_verification`). It is evaluated only for admitted
components whose registry flag is false. For every view the component needs:

- the latest probe-evidence attempt is a bridge receipt whose artifact and digest verify;
- the receipt's `evidence_type` is `real_observed`, so authored or synthetic evidence is refused;
- the receipt's source equals the registry/catalog pin (source, view, component, exact
  revision, adapter), and the adapter code and stored record match (`verify_current`);
- the adapter accepted at least one real row;
- the admission decision binds the receipt digest, revision, adapter, repository and
  fingerprint.

Any gap fails closed and is reported as `NOT_LIVE_VERIFIED`, with the reason.
Admission state is still judged by the gate first. Admission alone never makes a
source ready. The Essential-Web registry flags are unchanged.

## Results (offline, Windows 11, Python 3.12.13, uv-locked cpu+eval)

The real operator store was read only and no operator artifact changed:

- `mix01-status`: essential_science/practical/prose **READY**, ultrax_ultrafineweb
  **READY** (bridge `b874f47e…c3ecf9`), **Ready: 4/12**.
- No re-probe, re-bridge, re-review or re-admission was needed.

| Check | Result |
|---|---|
| `pytest tests/test_mix01_admission.py -n 0` | 13 passed, exit 0 |
| Related selection (mix01 admission/views/source CLI/inventory/quotas, certified evidence, source admission, 3 UltraX, 4 Essential-Web readiness/selector/seal/freeze files), `-n 16 -m "not serial"` | 315 passed, exit 0 |
| ruff check / ruff format --check (changed files) | exit 0 / exit 0 |
| mypy --strict | NOT RUN: DLL blocked by Windows application control |
| Full acceptance, network, CUDA | NOT RUN |

The new tests use authored fixtures. They cover: certified bridge + admission → READY;
no certification; authored-only (synthetic) certification; wrong revision, adapter
or view; an admission that does not bind the bridge; a revoked admission; and
Essential-Web remaining READY.

## Next operator command

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -Command '. .\scripts\operator_storage.ps1; uv run --offline --locked --extra cpu --extra eval xlm data mix01-status'
```
