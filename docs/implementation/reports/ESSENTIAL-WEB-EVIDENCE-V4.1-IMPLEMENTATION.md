# Essential-Web evidence v4.1 — host-amendment implementation report

Date 2026-09-29, branch `data/mix01-ultrax-6b`. Starting HEAD
`21266bf7280194a5a548be6f8b58137a3c84c92e` (v4.0 final B01/B02
recertification PASS). Protocol/freeze commit
`cb3014d8da988cab8a84843c87d4eb0c9766571d`. The implementation commit carries
this report. Nothing was pushed.

Verdict: **READY FOR NARROW V4.1 HOST-AMENDMENT REVIEW.**

## 1. The stopped v4.0 live attempt

The first real v4.0 Phase-P invocation stopped on its first physical request.
It is recorded as **`STOPPED_POLICY_REFUSED`**
(`docs/implementation/evidence/ESSENTIAL-WEB-EVIDENCE-V4.1/v40_live_observation.json`,
digest `01799f6f6c0edf1cdc104fcb175251c4fd0a431700800215e41bc9e6831e5544`):
`INCOMPLETE` / `STOPPED`,
`M-00-head: host 'us.aws.cdn.hf.co' is not an exact allowlisted host`.
M had 1 attempt, 1098 body bytes, `POLICY_REFUSED` 1, 0/16 complete and 0
retained. T had 0 attempts and 0 retained. The operator reported these values.
The root `G:\Project\xlm-evidence-v4\essential-web` is on the operator's machine
and was not read, hashed or modified here. v4.0 is not relabelled as
successful.

No scientific output was exposed. From the v4.0 code, the only path that
yields one `POLICY_REFUSED` attempt carrying body bytes is an origin redirect
whose `Location` failed `check_url`. The 1098 bytes are that non-206 redirect
body. No request reached the CDN. No operation completed, no payload file and
no Parquet byte exist, and no T request was issued. The offline test
`test_v40_profile_reproduces_the_observed_live_stop_offline` reproduces this
exact stop shape with the unchanged v4.0 profile.

## 2. What changed (implementation)

The v4 engine is reused, not forked. The change adds an explicit execution
profile and host policy:

| File | Change |
|---|---|
| `src/xlm/data/evidence_v4/frozen.py` | `HostPolicy` (canonical host + exact signed-target tuple) and `Profile` (label, version, protocol SHA, freeze/plan digests, plan path, root, network object, host policy). `V40_HOSTS` / `V40` carry the unchanged v4.0 values. `validate_plan` / `load_committed_plan` take `profile` (default `V40`); `Plan.profile` records it. |
| `src/xlm/data/evidence_v4/transport.py` | `check_url`, `start_url`, `resolve_redirect`, `verify_identity` and `LiveHttpsTransport` take a `HostPolicy` (default v4.0). Only the host-membership tests changed: exact tuple membership instead of the single-host equality. Scheme, port, IP-literal, userinfo, spelling, canonical-path, 206/Content-Range/ETag/length/PAR1 checks are byte-identical. |
| `src/xlm/data/evidence_v4/phase_p.py` | The engine reads the version, digests and host policy from `plan.profile`. `_run_live` is shared. `run_live` / `live_root` behave as before for v4.0. New `run_live_v41` / `live_root_v41` use the v4.1 profile, its root and `LiveHttpsTransport(policy=v41.HOSTS)`. Offline runs also refuse the v4.1 root and the real v4.1 plan digest. |
| `src/xlm/data/evidence_v4/state.py` | The run row stores `plan.profile.protocol_version`. |
| `src/xlm/data/evidence_v4/v41.py` (new) | Frozen v4.1 constants (protocol SHA, freeze, plan, membership and observation digests; root; the 17 added hosts; `HOSTS`; `NETWORK`; `PROFILE`), the committed-plan loader and `verify_repository()`, which runs the full v4.0 parent verification first. |
| `scripts/evidence_v41.py` (new) | CLI with exactly `verify`, `show-plan`, `phase-p-status` and `phase-p --confirm-plan-digest`. There are no URL/host/file/range/ETag/root/plan/force options. |
| `tests/evidence_v4_support.py` | Synthetic plan builder and `WireTransport` accept a profile / host policy (default v4.0; existing fixtures unchanged). |
| `tests/test_evidence_v41.py` (new) | 157 offline test cases (section 4). |

Unchanged: limits, retries, timeout, B01 (`read1` partial accounting), B02
(absolute deadline on every blocking socket step), parsing, layout, restart
reconciliation, outputs, state schema, the v4.0 CLI and the v4.0 plan.

## 3. Frozen values

| Item | Value |
|---|---|
| Protocol | `docs/implementation/reports/ESSENTIAL-WEB-EVIDENCE-V4.1-PROTOCOL.md`, SHA-256 `3d667a263b92696a2d7a266f896e460b10a81ae38097f788c8b885ce768079cf` |
| Freeze digest | `285015604d30e1699b5f63fa4f25ec9c747c778be2f372bb87119adc0e455f80` (file SHA-256 `84d5e66ec64ac529ffb87f4ef879b1843e88ec0aa421eb0c9697e8dc16d8587d`) |
| v4.1 plan digest | `762cef78051011d518c4918a41aa87cf81fbb3224bf438af8cc1c499cfca7711` (new: `protocol_version`, `execution_root` and `network` are part of the canonical plan) |
| Membership-and-ranges digest (v4.0 = v4.1) | `304bd0761125b48720a4de9c82bb6b3bf47100b8a15fc0953d548a8a1dab38bd` |
| Scientific identity | namespace `essential-web-evidence-v2.0`; selection `975ba3de…78474`; revision `ce4eccc7…113d`; policy `f4357f61…dd07`; identity digest `080caebb…91c6`; adoption `158c3fc9…ffd3` (by reference) |
| Execution root | `G:\Project\xlm-evidence-v4.1\essential-web` (not created) |
| Hosts (19, exact) | `huggingface.co`, `cas-bridge.xethub.hf.co`, and added `cdn-lfs.hf.co`, `cdn-lfs-us-1.hf.co`, `cdn-lfs-eu-1.hf.co`, `transfer.xethub.hf.co`, `transfer.xethub-eu.hf.co`, `aws.cdn.hf.co`, `us.aws.cdn.hf.co`, `us-east-1.aws.cdn.hf.co`, `us-west-2.aws.cdn.hf.co`, `eu-west-3.aws.cdn.hf.co`, `ap-southeast-1.aws.cdn.hf.co`, `us.gcp.cdn.hf.co`, `us-east1.us.gcp.cdn.hf.co`, `us-central1.us.gcp.cdn.hf.co`, `us-west4.us.gcp.cdn.hf.co`, `europe-west4.us.gcp.cdn.hf.co`, `asia-southeast1.us.gcp.cdn.hf.co` |

The added-host list is the operator's transcription of the `lfsDomains` and
`cdnDomains` in `https://huggingface.co/.well-known/meta.json`. This task did
not re-fetch that file (no network).

## 4. Verification

Environment: Linux cloud container, CPython **3.12.3**
(`/usr/bin/python3.12`), uv 0.8.17, PyArrow 25.0.1, pytest 9.1.1, ruff 0.16.8,
mypy 2.3.1. The pinned CPython 3.12.13 cannot be installed with this uv, so this
follows the same policy as the v4.0 B01/B02 repair. The locked base + `dev`
dependencies were installed from PyPI with `UV_PYTHON=/usr/bin/python3.12 uv
sync --locked`. That was the only network use: package index only, no Hugging
Face host. The `cpu`/`eval` extras were not installed, and v4 imports neither.
Test settings were `OMP/MKL/OPENBLAS/NUMEXPR_NUM_THREADS=1`,
`TOKENIZERS_PARALLELISM=false` and `-p no:cacheprovider`. Every test is
offline: sockets are patched to refuse, fixtures are authored synthetic Parquet
files, and roots are under `tmp_path`. Logs are in
[`evidence/ESSENTIAL-WEB-EVIDENCE-V4.1-IMPLEMENTATION/`](../evidence/ESSENTIAL-WEB-EVIDENCE-V4.1-IMPLEMENTATION/).

| # | Command (prefix `uv run --offline --locked`) | Exit | Result |
|---|---|---|---|
| 1 | `python docs/implementation/evidence/ESSENTIAL-WEB-EVIDENCE-V4.1/build_v41_freeze.py freeze` (freeze commit) | 0 | digests of section 3 |
| 2 | `python -m pytest -n 0 -q tests/test_evidence_v4_{plan,transport,engine,e2e,wire}.py tests/test_evidence_v41.py` | 0 | **337 passed** (180 existing v4 + 157 new v4.1), 17.52 s; wall 18.28 s; peak child RSS 207,384,576 bytes |
| 3 | `python -m pytest -n 0 -q tests/test_evidence_v4_wire.py` (B01/B02 wire) | 0 | 23 passed |
| 4 | `python -m pytest -n 0 -q tests/test_evidence_v3.py tests/test_evidence_v2_core.py tests/test_evidence_v2_text.py` (science regressions) | 0 | 69 passed |
| 5 | `python docs/implementation/evidence/ESSENTIAL-WEB-EVIDENCE-V4.0-B01-B02-REPAIR/probe_b01_b02.py .` | 0 | output identical to the recorded `probe-repaired.json` (timing/path fields excluded) |
| 6 | `ruff check` on the 17 v4/v4.1 files (7 src + 2 CLI + support + 7 test modules) | 0 | all checks passed |
| 7 | `ruff format --check` on the same 17 files | 0 | 17 files already formatted |
| 8 | `mypy --strict` on the same 17 files | 0 | no issues |
| 9 | `python scripts/evidence_v41.py verify` | 0 | v4.0 parent + every v4.1 binding reproduce; root absent |

New v4.1 cases (`tests/test_evidence_v41.py`):

- Exact allowlist: the set equals the 19 listed hosts; each is accepted (also
  with `:443`). The v4.0 policy still refuses every added host. Refused under
  v4.1: `evil.hf.co`, `foo.us.aws.cdn.hf.co`, `us.aws.cdn.hf.co.evil.com`,
  `hf.co`, `cdn.hf.co`, `xethub.hf.co`, `eu.aws.cdn.hf.co`,
  `cdn-lfs-ap-1.hf.co`, trailing-dot and `huggingface.co.evil.com`; `http`,
  `ftp`, ports 444/80, IPv4/IPv6 literals, userinfo, fragment, uppercase.
- Redirect resolution to each of the 18 signed targets is accepted. Other
  redirect rules are unchanged (non-canonical HF path, query-revision,
  unlisted, missing Location refused).
- CDN identity is not weakened: a valid CDN 206 passes. Status 200, wrong
  total, wrong range, weak/other/missing ETag, missing Content-Range, wrong
  Content-Length, gzip, short body and non-PAR1 all fail. A CDN final URL is
  not identity under the v4.0 policy.
- Authored engine replay `huggingface.co → <each signed target>` with valid
  range/ETag/length/PAR1: **COMPLETE**. Receipt and run row bind v4.1. Signed
  queries are hashed only.
- The same responses redirected to each unlisted host: **STOP /
  `POLICY_REFUSED`**, one origin attempt, zero CDN calls, zero payload. Bad
  scheme/port/IP redirect targets also STOP.
- Production-parser wire replay `huggingface.co → us.aws.cdn.hf.co`:
  COMPLETE, with exact `GET`/`Host`/`Range` on the CDN socket. An unlisted host
  gives STOP after one socket. **B01** on the CDN hop: partial `PAR1` before
  `IncompleteRead` is persisted (4 bytes, SHA-256 of PAR1) and never promoted.
  **B02** on the CDN hop: 60+70 s is cut at exactly 120 s with timeouts
  `[120, 120, 120, 60]`.
- `LiveHttpsTransport(policy=v41.HOSTS)` connects only to listed hosts. The
  default (v4.0) transport still refuses `us.aws.cdn.hf.co`.
- Plan: v4.1 files, operations and source equal v4.0 (16 M + 24 T, 8 derived).
  Only `digest` and the three amended keys differ. The shared digest equals
  the frozen value. Edits (wildcard host, extra host, dropped host, port,
  scheme, v4.0 root/version, range, limits) are refused. v4.0 and v4.1 plans
  and profiles are not interchangeable.
- CLI/live: `run_live_v41` takes only the confirmation. The v4.0 digest,
  uppercase or zero digest refuse without creating the root. The CLI live path
  (recording stand-in) follows origin → CDN and still STOPs on a wrong ETag,
  after which the root refuses re-runs. There are no arbitrary options.
  `show-plan` prints 40 operations and the exact 19 hosts.

## 5. Requirement ledger

| Requirement | Status |
|---|---|
| v4.0 live result recorded as `STOPPED_POLICY_REFUSED`, not success | IMPLEMENTED, VERIFIED (record + test) |
| No scientific output exposed by v4.0 | VERIFIED from the reported totals and v4.0 code path; the operator root itself NOT RUN/not read here |
| Scientific identity, membership and ranges unchanged | VERIFIED (shared digest, dataclass equality, v4.0 parent verify, 69 science regressions) |
| Exact 19-host set; exact equality; no wildcard/suffix | IMPLEMENTED, VERIFIED |
| HTTPS/443/IP-literal refusals intact | VERIFIED |
| Redirect to an unlisted host → STOP `POLICY_REFUSED` | VERIFIED (engine + wire) |
| 206/Content-Range/length/strong ETag/PAR1 not weakened | VERIFIED |
| Limits, retries, timeout unchanged; B01 and B02 kept | VERIFIED (180 v4 tests incl. 23 wire, probe replay, CDN-hop B01/B02) |
| New v4.1 plan digest, explicitly distinguished from v4.0 | IMPLEMENTED, VERIFIED |
| Fresh v4.1 root; v4.0 root untouched | IMPLEMENTED; v4.1 root not created (VERIFIED absent here); v4.0 root not accessible here |
| Engine reused, not forked | IMPLEMENTED |
| Live v4.1 Phase P | NOT RUN (out of scope; operator action) |
| Pinned CPython 3.12.13 / Windows rerun | NOT RUN here (3.12.3 Linux) |
| Full offline acceptance suite | NOT RUN (focused selection only, per test policy) |
| Re-fetch of `meta.json` host list | NOT RUN (no network); operator transcription frozen |
| Phase D | OUT OF SCOPE |

Open limitations: real CDN TLS, redirect shape and ETag behaviour remain
unobserved live. Any mismatch still STOPs without broadening anything. The
freeze builder `build_v41_freeze.py` is frozen reproduction tooling. Like the
v4.0 builder, it is not held to the 100-column ruff limit and is outside the
lint selection.

## 6. Next command — DO NOT RUN in this task

After the narrow v4.1 host-amendment review passes, the operator runs from
the repository:

```powershell
uv run --offline --locked --extra cpu --extra eval python scripts/evidence_v41.py phase-p --confirm-plan-digest 762cef78051011d518c4918a41aa87cf81fbb3224bf438af8cc1c499cfca7711
```

`uv --offline` prevents dependency downloads. The `phase-p` program itself uses
the network, only to the 19 frozen hosts. It creates
`G:\Project\xlm-evidence-v4.1\essential-web` and never touches the v4.0 root.

Exact next operator action: request the narrow v4.1 host-amendment review of
commits `cb3014d` (protocol/freeze) and the implementation commit, for example
by running `uv run --offline --locked python scripts/evidence_v41.py verify` and
the focused test command above on the Windows checkout with CPython 3.12.13.
Run the command above only after that review passes.
