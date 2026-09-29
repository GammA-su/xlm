# Essential-Web evidence v4.1 — transport-host amendment protocol

Frozen 2026-09-29 on `data/mix01-ultrax-6b`, parent commit
`21266bf7280194a5a548be6f8b58137a3c84c92e`. Protocol version
`essential-web-evidence-v4.1`. This is a **minimal prospective amendment** of
the v4.0 Phase-P execution protocol
([`ESSENTIAL-WEB-EVIDENCE-V4.0-PROTOCOL.md`](ESSENTIAL-WEB-EVIDENCE-V4.0-PROTOCOL.md),
SHA-256 `4c7f11b15adb7d0601977fef61ab04d62fae0e9e64091fa22dbd99e1caf65727`,
freeze `747b82a2df7702db2b111ea62c2f27f3de3d22a37428472fdc0550af90767f57`).
It is not a redesign. Every v4.0 section applies unchanged except where this
document says otherwise, and it only says otherwise about three things: the
exact allowed host set, the execution version and the execution root.

Bound artifacts (directory
[`docs/implementation/evidence/ESSENTIAL-WEB-EVIDENCE-V4.1/`](../evidence/ESSENTIAL-WEB-EVIDENCE-V4.1/)):

| Artifact | Canonical self-digest |
|---|---|
| `phase_p_plan.json` (v4.1 plan; the only source of executable operations) | `762cef78051011d518c4918a41aa87cf81fbb3224bf438af8cc1c499cfca7711` |
| membership-and-ranges digest shared by the v4.0 and v4.1 plans (definition in section 5) | `304bd0761125b48720a4de9c82bb6b3bf47100b8a15fc0953d548a8a1dab38bd` |
| `v40_live_observation.json` (the stopped v4.0 live record) | `01799f6f6c0edf1cdc104fcb175251c4fd0a431700800215e41bc9e6831e5544` |
| parent v4.0 plan | `16b93183b485ced924466028b84a8b4cfd84a65f596dc95c80879e32464f35d3` |
| parent v4.0 `scientific_adoption.json` (adopted by reference) | `158c3fc9fed030d0290aa50eded70f7d94b162ab6186ae38aca2ce6b1ebdffd3` |
| `freeze.json` / `verification.json` | bind this document's SHA-256 (not stated here: circular) |

Canonical form `C(x)` and digest `H(x)` are identical to v2.0–v4.0. The builder
`build_v41_freeze.py` in the same directory reproduces every artifact offline
from the committed v4.0 plan.

## 1. Lineage and the stopped v4.0 live attempt

| Version | Status |
|---|---|
| v4.0 | Frozen `2ede38f`, implemented `791d3b8`, B01/B02 repaired `a9f89c0`, final narrow review PASS `21266bf`. First real Phase-P invocation: **`STOPPED_POLICY_REFUSED`** (below). Not successful. |
| v4.1 | This amendment: the v4.0 engine and plan with the exact currently documented Hugging Face storage/CDN host set and a fresh execution version/root. |

The operator ran exactly
`uv run --offline --locked --extra cpu --extra eval python scripts/evidence_v4.py phase-p --confirm-plan-digest 16b93183b485ced924466028b84a8b4cfd84a65f596dc95c80879e32464f35d3`
and reported:

- `status` `INCOMPLETE`, `run_status` `STOPPED`;
- `stop_reason` `M-00-head: host 'us.aws.cdn.hf.co' is not an exact allowlisted host`;
- M: logical complete 0 / 16, physical attempts 1, response body bytes 1098,
  `POLICY_REFUSED` 1, retained payload bytes 0;
- T: physical attempts 0, retained payload bytes 0.

What the v4.0 code implies about that record: the single attempt was
`M-00-head`, try 1, hop 0, to the canonical `huggingface.co` resource with
range 0–3. The only engine path that records `POLICY_REFUSED` *with* body bytes
on one attempt is a redirect response whose `Location` fails `check_url` inside
`resolve_redirect`. So the origin answered with a redirect to
`us.aws.cdn.hf.co`, the engine refused it before sending any request there, and
the run stopped. The 1098 bytes are the body of that non-206 origin redirect.
They went to `tmp/M-00-head.a1.part`, were never parsed and are not Parquet
bytes.

Scientific exposure: zero. No Phase-P operation completed, no payload file
exists, no Parquet byte (not even the 4-byte `PAR1` head) was received, no M
footer or selected M outcome was observed, and no T request was issued, so no
selected T text was observed. The v4.0 result is recorded as
`STOPPED_POLICY_REFUSED`. It is not relabelled as successful.

The v4.0 root `G:\Project\xlm-evidence-v4\essential-web` is historical. v4.1
never deletes, overwrites, reuses or reads it. This record was reported by the
operator. The root is on the operator's machine, and the freeze tooling did not
read or hash it.

## 2. Scientific identity (unchanged, adopted by reference)

v4.1 adopts the v4.0 scientific adoption (`158c3fc9…`, identity digest
`080caebb962c86562b530ddfa6a130118a49a6d207b044e83ce6a90a6d9691c6`) unchanged:

- scientific namespace `essential-web-evidence-v2.0`;
- selection digest `975ba3dee4af0598e665ea05c69bbe49336c3e3a35fca777a863073190b78474`;
- source `EssentialAI/essential-web-v1.0` at revision `ce4eccc7e9604667b6d7f32cb6274b8b41f3113d`;
- selector policy `f4357f61f434d5105d823266153dac6372e122f12767187631e8796b4899dd07`;
- **M**: the same 8 files, windows, seed 20260927, projection
  (`eai_taxonomy`, `quality_signals`) and 16 logical Phase-P operations;
- **T**: the same 118 locators, the same 8 development files and the same 24
  eventual structural Phase-P operations (8 of them derived after the trailer).

No reselection, no replacement source, no new ranges. Nothing about the live
stop informs membership: it happened before any byte of any selected file
arrived.

## 3. The only authorized transport change

Allowed hosts, compared by **exact string equality** of the
`urllib.parse.urlsplit` hostname (no wildcard such as `*.hf.co`, no suffix
match, no pattern, no future host):

- canonical host (unchanged): `huggingface.co`;
- prior signed target (unchanged): `cas-bridge.xethub.hf.co`;
- **added** — Hugging Face `lfsDomains`: `cdn-lfs.hf.co`,
  `cdn-lfs-us-1.hf.co`, `cdn-lfs-eu-1.hf.co`, `transfer.xethub.hf.co`,
  `transfer.xethub-eu.hf.co`;
- **added** — Hugging Face `cdnDomains`: `aws.cdn.hf.co`, `us.aws.cdn.hf.co`,
  `us-east-1.aws.cdn.hf.co`, `us-west-2.aws.cdn.hf.co`,
  `eu-west-3.aws.cdn.hf.co`, `ap-southeast-1.aws.cdn.hf.co`,
  `us.gcp.cdn.hf.co`, `us-east1.us.gcp.cdn.hf.co`,
  `us-central1.us.gcp.cdn.hf.co`, `us-west4.us.gcp.cdn.hf.co`,
  `europe-west4.us.gcp.cdn.hf.co`, `asia-southeast1.us.gcp.cdn.hf.co`.

That is 19 hosts in total: 1 canonical host and 18 signed targets, 17 of them
new. Provenance: the `lfsDomains` and `cdnDomains` of
`https://huggingface.co/.well-known/meta.json`, in the operator's transcription
of 2026-09-29. Hugging Face documents that downloads redirect to dedicated
CDN/storage hosts, and the live stop hit `us.aws.cdn.hf.co`, which is in that
list. The freeze tooling did not re-fetch the metadata (no network).

Still required, unchanged: scheme `https`; port 443; no userinfo, fragment,
localhost or IP literal; lowercase exact host spelling. A redirect to any host
outside the frozen list is `POLICY_REFUSED` → STOP, exactly as in v4.0.

## 4. Identity behaviour for a CDN redirect (unchanged semantics)

Every added host is a **signed target** with exactly the semantics v4.0 gave
`cas-bridge.xethub.hf.co`:

- Each logical request still starts at the frozen canonical resource
  `https://huggingface.co/datasets/EssentialAI/essential-web-v1.0/resolve/<revision>/<file>`.
  That origin URL remains the scientific resource identity.
- A signed target is reachable only as the validated destination of an actual
  redirect from that chain (≤ 3 transitions). Its path and query are opaque:
  there is no requirement that the CDN path resemble the repository path, and
  the query is stored only as a SHA-256.
- The response delivered from a signed target must still satisfy every v4.0
  identity check: HTTP 206; `Content-Range` exactly the requested start–end and
  the frozen total length; a strong ETag byte-identical to the frozen ETag;
  identity content coding; `Content-Length` (when present) and the body equal to
  the requested length; `PAR1` where required. None of these checks is weakened.
- Redirects back to `huggingface.co` are accepted only to the exact canonical
  path without a query, as in v4.0.

## 5. Plan digest: what changes and what does not

The v4.1 plan is the v4.0 plan with exactly three top-level fields replaced
and the result re-sealed:

- `protocol_version`: `essential-web-evidence-v4.0` → `essential-web-evidence-v4.1`;
- `execution_root`: `G:\Project\xlm-evidence-v4\essential-web` →
  `G:\Project\xlm-evidence-v4.1\essential-web`;
- `network`: `hosts` expanded to the 19 hosts above; the single
  `signed_target_host` replaced by the list `signed_target_hosts`; an explicit
  `host_matching` statement (exact equality). Method, scheme, port, redirect
  statuses, request headers and `credentials_sent: false` are unchanged.

The v4.1 plan digest is therefore **new**:
`762cef78051011d518c4918a41aa87cf81fbb3224bf438af8cc1c499cfca7711`, not the
v4.0 digest `16b93183b485ced924466028b84a8b4cfd84a65f596dc95c80879e32464f35d3`.
Its bytes are not the same, and this document does not claim they are.

Scientific membership and ranges are identical, and that is proven by the
**membership-and-ranges digest** `H(plan without digest, execution_root,
network, protocol_version)`. It covers kind, schema version, phase,
namespace, selection and policy digests, source, arms, all 16 files with
lengths, ETags and bindings, all 40 operations with their exact or derived
ranges, and all operational limits. It equals
`304bd0761125b48720a4de9c82bb6b3bf47100b8a15fc0953d548a8a1dab38bd` for
**both** the v4.0 and the v4.1 plan.

## 6. Unchanged operational limits and repairs

Unchanged, from v4.0 section 5 and the plan's `limits` object: ≤ 200 physical
HTTP attempts per arm; ≤ 64 MiB returned body bytes per arm; ≤ 4 MiB body per
response; ≤ 3 redirect transitions per logical request; ≤ 2 additional tries
(delays 1 s, 2 s); 120-second absolute physical-attempt deadline; ≤ 256 MiB
retained root; ≥ 1 GiB free before execution. The B01 partial-byte accounting
repair (`read1`, every received body byte persisted before a framing failure)
and the B02 absolute-deadline repair (one deadline arming every blocking socket
operation) stay in force unchanged. Parsing, accounting, timeout, retry
behaviour, restart reconciliation, isolation, outputs and all other Phase-P
logic are those of v4.0.

## 7. Execution root

v4.1 uses a fresh root `G:\Project\xlm-evidence-v4.1\essential-web`. It is
never created by the freeze or implementation tasks. The live invocation
creates it when absent and refuses a pre-existing non-empty root that has no
matching `state.sqlite`, as in v4.0. The run row binds protocol version
`essential-web-evidence-v4.1` and the v4.1 plan digest. v4.1 never uses
`G:\Project\xlm-evidence-v4\essential-web` (historical v4.0) or the v3 root.
Offline synthetic runs refuse all three frozen roots.

## 8. Implementation contract

Implement v4.1 as frozen constants plus the reused v4 engine. Do not copy the
engine. The execution version, root and host policy are explicit, compiled
values that travel with the loaded plan:

- one `HostPolicy` (canonical host plus exact signed-target tuple) consulted
  by URL checking, redirect resolution, response identity and the live
  transport; the v4.0 policy is unchanged;
- one execution `Profile` (version, protocol SHA-256, freeze and plan digests,
  plan path, root, network object, host policy); the v4.0 profile is unchanged;
- a CLI `scripts/evidence_v41.py` with exactly `verify`, `show-plan`,
  `phase-p-status`, `phase-p`. `phase-p` requires `--confirm-plan-digest
  762cef78051011d518c4918a41aa87cf81fbb3224bf438af8cc1c499cfca7711` and has no
  URL, file, range, ETag, plan, host, root or force option;
- offline tests only. The implementation task must not run `phase-p` against
  real remote data, must not create the v4.1 root, and must not touch the v4.0
  root.

## 9. Phase D

Nothing in v4.1 authorizes Phase D, training, tokenizer work or production
admission.

## 10. Narrow review questions

1. Is the scientific membership unchanged, and does the membership-and-ranges
digest match v4.0? 2. Is the host set exactly the 19 frozen hosts, matched by
exact equality, with no wildcard or suffix? 3. Are HTTPS/443/IP-literal
refusals intact? 4. Does a redirect to any unlisted host still STOP as
`POLICY_REFUSED`? 5. Are 206/Content-Range/length/strong-ETag/PAR1 checks
unweakened for CDN-delivered bytes? 6. Are the v4.0 limits, B01 and B02
unchanged? 7. Is the v4.1 root fresh and the v4.0 root untouched? 8. Is the
v4.0 live result recorded as `STOPPED_POLICY_REFUSED` and not as success?
