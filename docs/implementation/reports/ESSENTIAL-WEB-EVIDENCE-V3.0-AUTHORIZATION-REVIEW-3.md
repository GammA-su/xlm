# Essential-Web Evidence-v3.0 — independent Phase-P authorization review 3

**PHASE-P AUTHORIZATION BLOCKED** — Arm M and Arm T.

2026-09-29. Reviewed implementation A
`3dd5ebce0edb7d8e676966c9195b73fb5a1978c9` and children/evidence B
`12d85158206378c0d6833d95df9b9d8fa5789761`, starting HEAD B on
`data/mix01-ultrax-6b`. The redesigned path closes many earlier defects, but
independent probes demonstrate successful work/sealing outside enforced time
and memory limits. The passing implementation suite is insufficient for approval.

Evidence: [commands and environment](../evidence/ESSENTIAL-WEB-EVIDENCE-V3.0-AUTHORIZATION-REVIEW-3/COMMANDS.md),
[independent failures](../evidence/ESSENTIAL-WEB-EVIDENCE-V3.0-AUTHORIZATION-REVIEW-3/independent.txt),
[probe source](../evidence/ESSENTIAL-WEB-EVIDENCE-V3.0-AUTHORIZATION-REVIEW-3/test_independent.py),
[Git/hash audit](../evidence/ESSENTIAL-WEB-EVIDENCE-V3.0-AUTHORIZATION-REVIEW-3/identity.json),
[exact recovery](../evidence/ESSENTIAL-WEB-EVIDENCE-V3.0-AUTHORIZATION-REVIEW-3/exact-recovery.json).

No network, acquisition, corpus-text inspection, real epoch genesis, operator
approval, Phase D, implementation/scientific edits, commit or push. All execution
fixtures used authored Parquet data and disposable roots. Existing dirty STATUS
and selector documents and untracked reports were left untouched, as expressly
requested. This report supplies the review status instead of editing STATUS.md.

**Blocking findings and minimal repair requirements**

| ID | Independently reproduced result | Cause and required repair |
|---|---|---|
| R3-01 | A successful 206 returns its final EOF after 31 modeled seconds. Public `execute_phase_p(max_operations=1)` accepts and stages it; M remains P_RUNNING with one operation complete, 2 requests, 22 charged bytes, and 31 s charged. | `executor.py:835` checks before reads, but returns at EOF without checking again. `_stage` replaces the expired deadline. Check the completed read/identity/staging transition against the original deadline before declaring completion; retain elapsed and reservations on refusal. |
| R3-02 | During T's final result write, advance time by 1,801 s. Both arms return P_COMPLETE_SEALED; T durably records 1,801 s, beyond its 1,800 s arm ceiling. | `executor.py:954` has no guard after final write; `journal.py:323` allows close after exhaustion and `journal.py:285` seals without checking totals. Reject success after work/write/fsync overruns; enforce remaining arm/file/step limits at completion and replay. Keep consumed time, mark incomplete. |
| R3-03 | During T's final result write, the real Supervisor breach path latches RSS 268,435,457 >268,435,456. Both arms still seal successfully, with the breach message recorded by the monitor. | Same missing post-write/pre-seal checkpoint. This is a sampled and observed breach, not the admitted limitation of missed between-sample peaks. Supervision must gate every write/work completion and sealing transition. |
| R3-04 | Add 2 s of authored M footer parsing. Arm time rises by 2 s, but the corresponding file is charged **0**. | `_seal` holds `file=None` across all files. Parsing is protocol-counted per-file work; give each file its own clipped hold and durable elapsed attribution, including staging/sealing work attributable to that file. |
| R3-05 | Public validators return a minted authorization; ordinary nested mapping assignment changes Python to `invented-after-validation` and M request ceiling to 999. Public `genesis.publish` writes these into epoch_start under the **unchanged authorization digest and approval**. No `_mint`, sentinel, `object.__setattr__`, or private registry was used. | `trust.py:66` is shallow immutability; authorization/plan environment, caps and bindings remain mutable dicts. Deep-freeze or reconstruct/revalidate every binding at consumption, and restrict public genesis primitives to the actual path/artifact boundary. The four top-level functions reconstruct their own objects, so this probe does **not** establish that they accept a caller-supplied dict; it establishes a callable public genesis bypass and invalidates the claimed trusted-type invariant. |
| R3-06 | Socket/TLS simulation drives the actual `_LiveHttpsTransport.open`: connect uses 20 s, TLS uses 20 s, then GET is sent at **40 s** despite a 30 s absolute deadline. No real socket was opened. | `transport.py:219` reuses the initial relative timeout across connect/TLS/header work and does not recheck the deadline before send. DNS at `:184` is blocking with no bounded resolver cancellation. Recompute remaining time at every blocking step/send, enforce absolute header/body deadlines and bounded DNS; cancellation must interrupt work, not merely notice later. |
| R3-07 | Disable only the arms equality guard in memory. A coherently resealed synthetic `arms="MT"` request executes a physical fake operation. Full `derive_readiness` still returns READY_FOR_PHASE_P_AUTHORIZATION_REVIEW with `blocked=[]`. | `readiness.py:109` edits authorization but does not rebind its review, so an unrelated stale-review rejection masks a missing arms guard. Build coherent negative fixtures that isolate each invariant; include EOF, final-write, per-file-time and actual transport deadline scenarios. Static file hashes cannot detect in-memory disabled guards and are not a replacement for these sensitivity tests. |

An additional independently reproduced integrity defect, **R3-08**, affects the
public read-only inspector. Restore journal bytes to the initial GENESIS record
while leaving the later durable head intact: `inspect_epoch` reports zero requests,
but `execute_phase_p` correctly refuses the rollback. `executor.py:1082` bypasses
chain/head validation and replays only the reducer. Share a verified read-only
loader; do not report unauthenticated counters as epoch state. This does not
demonstrate execution accepting rollback, but blocks certifying the requested
inspection/accounting surface.

Additional source-reviewed limitations: recovery/reconciliation and initial
journal work happen before `_mark` and the supervisor start; they are real
post-genesis processing that needs bounded, durable time/memory accounting.
Final TIME/PHASE/SESSION_CLOSE flush work also has no following time charge.
A monitor thread cannot interrupt blocking DNS, native parsing or filesystem
writes. The parser's 4 MiB input bound and Thrift size limits are useful, but a
32-million-element container limit is not a mechanical 32 MiB allocator budget.
No claim of a hard 32 MiB parser-residency proof is justified here.

**Requested review record and requirement ledger**

Status vocabulary: IMPLEMENTED describes a mechanism; VERIFIED is limited to
the evidence stated; BLOCKED identifies a violated acceptance condition;
NOT RUN is not a pass; OUT OF SCOPE is deliberately excluded work.

1. **Starting HEAD / worktree — VERIFIED.** HEAD is B; initial dirty/untracked
   paths are preserved in COMMANDS.md. Normative source and committed artifacts
   were checked against Git blobs. Uncommitted STATUS/revalidation/selector
   narratives were not used as normative evidence.

2. **Frozen protocol/freeze — VERIFIED.** The exact frozen tree paths match
   commit `52569c525a6613faab096d17b60167b4aaa0f214`. Independently recomputed:
   protocol SHA-256 `c191aa49a7e35349e27eb077c7105ead1a15a7edbbb28ef0bc78f157b219fd79`;
   freeze self-digest `aa977972883af209afffebae221683658b2637281ae8e72741150750534fd834`;
   cap-map digest `e2485cd438524124c22074d59c48a5ee7dc699f9c9a9ea3f9c11deeef54e0f7b`.

3. **Commits A/B — VERIFIED.** B's direct parent is A. All 25 bound code files
   have the same committed bytes at A/B and match manifest SHA-256s. B changes
   children, reports/reproduction evidence and test typing; the normative
   implementation remains A. This distinction is intentional, not a wrong
   implementation-commit binding.

4. **Scientific identity — VERIFIED at committed adoption boundary.** Eight
   exact M files/windows, seed 20260927, projection `[eai_taxonomy,quality_signals]`,
   eight windows of 512 rows =4096; T descriptor binds the original 118 locators
   and eight files. Namespace `essential-web-evidence-v2.0`, revision
   `ce4eccc7e9604667b6d7f32cb6274b8b41f3113d`, selection
   `975ba3dee4af0598e665ea05c69bbe49336c3e3a35fca777a863073190b78474`, policy
   `f4357f61f434d5105d823266153dac6372e122f12767187631e8796b4899dd07` all match.
   Scientific v2/v2.1/v2.2 code and selector recipes/artifacts have no diff from
   the freeze to B; no strata/census/precedence/rubric/blinding/order reroll was
   introduced. Review-order seed remains 20260928. External original selection
   bytes were **NOT READ/NOT newly certified**; this review independently verifies
   immutable committed descriptors, including original SHA-256 and 23,807-byte
   length. No corpus material or blinding secret was inspected.

5. **v2 closure — VERIFIED.** Committed lineage closure remains
   CLOSED_NON_EXECUTABLE / HISTORICALLY_UNCERTIFIABLE; its self-digest and frozen
   bytes verify. Planning imports explicitly have `v3_budget_history=false`.
   Genesis initializes new v3 counters; old requests/time/body counts do not
   enter those budgets. Closure is procedural and at the supported v3 boundary;
   historical Python code still existing is not a reopened authorization.

6. **Architecture/public surface — IMPLEMENTED, BLOCKED.** CLI
   `check-auth`, `phase-p-genesis`, `phase-p-execute`, `inspect` map to the four
   intended functions. `_build_context` reconstructs authorization and approval;
   `_Session` loads genesis/journal, reconciles disk and supervises operations;
   `_fetch/_one_hop/_stream` reach only the private live transport in REAL mode.
   Plan operations come from validated files; caller URL/range/budget/transport
   fields are absent in REAL entry points. Nevertheless, public
   `authorization.derive_expectations/validate_authorization`,
   `plan.validate_plan_bytes`, `genesis.verify_root/publish/load_epoch_start`,
   `RootFS`, `Journal.create/append/load`, `EpochState.apply/new_state` are callable.
   `genesis.publish` is a physical publication route with the R3-05 bypass.
   `RootFS` primitives enforce containment but do not require authorization or
   ledger reservations; `Journal.append` signs a chain record but relies on the
   session for reducer validation/locking. These are not independent safe
   execution APIs. `EpochState` advances PHASE, DISK and accounting in memory;
   the session normally validates a cloned reducer state before append.
   `dry.publish/build-v3-children` write offline child artifacts outside the epoch
   and grant no execution authority. `synthetic.build_epoch/reload_epoch`, harness
   and fake transport serve authored fixtures only; REAL source/root substitution
   is refused. The callable inventory and imports are in identity.json. Private
   transport `_make_request/_mark_issued/open` were tested with mocks; arbitrary
   access to Python private state is not treated as an OS security boundary.

7. **Authorization — IMPLEMENTED, partly VERIFIED, BLOCKED.** Strict canonical
   JSON and exact key sets reject malformed arms, order, missing/extra arms,
   unknown/missing fields, wrong types, booleans/floats in integer caps, invented
   environment and all tested root/epoch/science/commit/manifest/plan/review/cap
   mismatches. Expectations derive from repo/runtime; wrong/stale request review
   refuses. Constructors, copies, pickles and fabricated dicts refuse in stock
   tests. **Nested mapping mutation does not refuse** (R3-05). Thus the claim
   that minted authorization/plan objects are immutable is false.

8. **Environment/code identity — VERIFIED with limits.** Actual runtime:
   CPython 3.12.13, project `.venv`, uv 0.12.19, PyArrow 25.0.1, psutil 7.2.2,
   torch 2.14.0+cpu, Windows 11 10.0.26200, AMD64. All 15 environment schema
   fields are compared exactly to the actual request/runtime values. Installed
   torch metadata/build is checked without importing torch; hardware GPU/driver
   availability and active CUDA state are **not** measured or schema-bound.
   `.python-version`, `pyproject.toml`, `uv.lock` are unchanged at A/B and checked
   as committed blobs. Their SHA-256s are respectively
   `aa0d6581054e6e4ff3f91839deca7a854ad37221b8784d060b42d0f847ff1a3b`,
   `75723d525207f7ff3d71fee35f9e69a258c4bc493fcc971189965c9d672fc54e`,
   `c44ecb0a0ed55e277d1bd24ee267e6ac8342f2e6e98efa2db11322ee458bac65`.
   Checkout representations are CRLF; distinct Git object IDs and worktree
   hashes are separately recorded. No 40/64-width mismatch. The environment
   check binds actual values approved in the request rather than independently
   enforcing a universal Python/CPU allowlist. CPU/CUDA installation policy and
   dependency graph were not changed. Child loading validates actual working
   bytes against manifest/recomputed payloads but does not itself fetch child
   blobs from Git; this audit separately proves those bytes committed at B.

9. **Reviewer authenticity — ACCEPTABLE threat-model interpretation.** For this
   operator-controlled local experiment, an exact committed review artifact
   bound to the authorization request plus separate typed operator approval is
   sufficient procedural authenticity. The protocol does not require a reviewer
   signature, and this review imposes none. This protects accidental/software
   mismatch, not a repository owner deliberately forging review and approval.
   The same distinction applies to rewriting both journal and its local head.
   This acceptance does not excuse software bypasses R3-01–07. Report hash is
   syntax-bound inside the review; reviewer identity itself is not authenticated.

10. **Operator approval — VERIFIED synthetic.** Exact `APPROVE_PHASE_P`, phase,
    epoch and authorization-core digest are required. `I do NOT approve phase-P
    execution`, absent/empty/wrong approval and fabricated trusted types refuse;
    notes cannot authorize. Real operator approval remains absent; none created.

11. **Exclusive genesis — IMPLEMENTED, partial verification, BLOCKED.** Eight
    simultaneous disposable claims produced exactly one winner/seven refusals.
    Existing/dirty roots, a copied claim in another physical directory,
    fabricated mapping, missing claim and incomplete/resealed start cases
    refused. Loader binds authorization (and transitively review), approval,
    implementation/config/env, plans/manifest, physical root, caps, zero ledgers
    and inventory. However, public publication trusts shallowly minted fields
    (R3-05). Clock anchors have shape checks, not comprehensive semantic timestamp
    verification. No real G: root was created.

12. **Journal/ledger — IMPLEMENTED, execution replay VERIFIED; inspector BLOCKED.**
    Normal reload/update/reload preserves counters, holds, footer category and
    plan operation identity; hash chain, sequence, epoch seed, head, torn tail,
    corruption and conflicting replay controls passed. Crash holds conserve
    request/body/time reservations; duplicate completed operations are not
    reissued. Additional exact recovery has 6 M +4 T requests. DATA reservation
    crash execution is OUT OF SCOPE: Phase P only permits footer category and
    rejects off-plan/data attempts; no Phase-D run was used to prove it.
    Inspector rollback failure R3-08 remains. No external rollback anchor exists.

13. **Plan-bound executor — VERIFIED for operation membership.** Arbitrary
    16–19 ranges, file/URL/operation substitutions, data labelled footer, wrong
    plan/root and synthetic/REAL swaps refuse. Journal reducer verifies next
    operation/range. Conditional T footer range derives from validated trailer.
    This does not certify time/memory safety of those planned operations.

14. **Transport/range/deadline — BLOCKED.** Requests carry exact URL, inclusive
    Range, absolute deadline, timeout, attempt ID and bounded read allowance;
    expected ETag/length/magic live in the plan/identity validator, not a mutable
    caller transport argument. Single-use issued-request check works. Actual
    live transport deadline enforcement fails the offline TLS/connect probe
    R3-06, and executor EOF enforcement fails R3-01. Live TLS/DNS NOT RUN.

15. **Redirects — VERIFIED synthetic.** Actual response Location drives follows;
    caller histories are absent. Only `huggingface.co` and
    `cas-bridge.xethub.hf.co`, HTTPS/443, exact canonical resource or signed host,
    at most three transitions. HTTP, localhost, IP literals, wildcard/sibling
    hosts, raw.githubusercontent.com, userinfo, wrong ports and fourth follow
    refused before the forbidden request. Received redirect bodies remain charged.

16. **Streaming bodies — VERIFIED synthetic, timing caveat.** Chunks are
    journalled before buffer exposure. Success, redirects, errors, partial/reset,
    timeout, short/long/oversized responses and overdelivery conserve returned
    bytes or conservative full reservations. Range/body cap is 4 MiB; current
    implementation is slightly stricter at exact-equal planned ranges because
    it needs an overrun sentinel. No complete-body materialization before
    metering on the real body reader. Late EOF completion remains R3-01.

17. **Remote identity — VERIFIED synthetic; live NOT RUN.** Strong expected and
    response ETag, exact 206 body length `end-start+1`, start/end/total,
    Content-Length/identity encoding and PAR1 checks refuse mismatches. URL path
    structure, not query substrings, binds repo/revision/file. Wrong/unplanned
    resources refuse. Strong ETag is server-asserted; no live identity claim made.

18. **M isolation/arithmetic — VERIFIED static/synthetic.** P logical 16,
    observed-style 24, cold 40; D identity reserve 32, combined controls 72 <=80.
    P request capacity 48, per-file 12; 48 succeeds and 49th refuses through the
    executor. Payload 1,398,416; footer margin 32,156,016; minimum file margin
    3,991,024. Only head and complete frozen footer/trailer ranges are planned;
    no selected data/metadata rows.

19. **T isolation/arithmetic — VERIFIED static/synthetic.** Exact head 0–3,
    trailer N−8..N−1, conditional footer N−8−L..N−9. Logical 24, observed-style
    32, cold 48; future D identity controls 32. No text-page fetch or caller
    text-to-footer relabelling route in the public executor.

20. **T cap decision — 721 IS CORRECT.** Protocol sections 4/6 explicitly give
    T no independent 80-control ceiling. The 80 is P+D cold no-retry control
    schedule. Reserve **79 future-D requests =32 identity +47 data** from the
    shared 800: P <=721. Per-file P <=90, except 2016-50 <=91, retaining 4+6 or
    4+5 future requests out of 100. This is baseline feasibility preservation,
    not a guarantee of every possible retry. No cap correction required.

21. **Future-D reservation decision — acceptable conservative policy.**
    `4*65,537=262,148` bytes/file, `2,097,184`/arm, follows four cold physical
    identity attempts and the chosen 64 KiB non-payload body bound plus sentinel.
    The protocol does not prescribe that exact number, but permits a tighter
    fail-closed implementation within its ceilings. It retains capacity for
    that bounded baseline, not unbounded redirect/error bodies or retries.
    Reserving no fixed active seconds for D is **not independently blocking**:
    the protocol defines shared cumulative 1800/600 and no numerical D minimum;
    later D must refuse if remaining time is inadequate. P must still enforce
    and preserve all actual charges. Current violations R3-01/02/04 are blocking.

22. **Raw T footer/statistics — acceptable narrow interpretation, with limits.**
    The protocol mandates receiving, parsing structural footer metadata and
    retaining a bounded cache; incidental opaque statistic bytes can therefore
    remain inside that cache. `footer._chunk/compare_t` never access `.statistics`,
    materialize min/max strings or log/render them, and no row decoding occurs.
    Interpreting the prohibition as banning every incidentally received statistic
    byte would conflict with the mandated full-footer cache. PyArrow does parse
    the footer container internally; this is not proof that its implementation
    never deserializes statistic fields internally. Extracting/using/rendering
    textual statistics remains forbidden. No real footer/statistics inspected.
    The frozen binding comparisons themselves cover selected counts/sums/spans;
    their schemas do not bind every original row-group/codec/schema attribute.
    Full inherited layout comparison is **NOT certified** by these synthetic
    fixtures and needs explicit coverage before a future passing review.

23. **Memory/process lifecycle — IMPLEMENTED, partly VERIFIED, BLOCKED.** No
    ExitProof/boolean release API; registry checks PID/create-time/death. Live
    child release refuses; external non-descendant PID plus descendants measured.
    During-body >256 MiB transient and measurement failures stop in stock tests.
    Final-write observed breach still seals (R3-03). Sampled RSS cannot bound
    shorter between-sample peaks; input/Thrift limits are not an exact 32 MiB
    parser-allocation proof. No fabricated GPU measurements.

24. **Windows disk — VERIFIED scoped synthetic/real-junction checks.** Actual
    child and ancestor NTFS junctions, links planted before/mid-run, traversal,
    absolute/alternate-volume path syntax, root replacement and unknown files
    refuse. Symlink creation was SKIPPED for privilege, not a pass. The extra
    exact 8-old +8-temp >10 cap test refuses both ordinary and replacement
    reservations; stock tests cover physical overdelivery (4 declared ->256),
    scratch/final/combined caps, shared control allowances, reload/mutation and
    reconciliation failure. There is no public accounted arbitrary import,
    `fits` flag, or release-before-delete API in this architecture; those exact
    old helper probes are inapplicable rather than passing tests. RootFS alone
    is containment-only, not accounting authority. Logical byte occupancy omits
    cluster overhead; concurrent hostile filesystem replacement remains a race
    limitation, not an authenticated local-owner boundary.

25. **Runtime — BLOCKED.** Durable request/work holds and open-crash reservation
    survive restart; exhausted-file and active-pause tests pass, and correct
    request file attribution is present. However, 31-second EOF success,
    1,801-second seal, uncharged per-file parsing, stale live transport timeout,
    and uncharged open/close work invalidate complete enforcement. Nominal
    30/600/1800 minimum selection alone is insufficient. Quiescent pauses remain
    non-resetting; the new exact crash test conserves 30 s plus actual/backoff.

26. **Readiness — BLOCKED.** Static identity and synthetic integration are
    distinct from UNVERIFIED_LIVE, and authorization NONE/executable false are
    verified. UNVERIFIED_LIVE alone can be acceptable under first-live fail-closed
    protocol semantics; it cannot excuse a reproducible unsafe real-transport
    algorithm or insensitive synthetic checks. R3-07 recomputes a false READY.

27. **Children — VERIFIED.** 14 files, 113,954 committed bytes. All manifest
    entries, file hashes, canonical self-digests and producer code bindings
    independently recomputed; relative/absolute builder outputs reproduce them.

    | Artifact | Canonical digest | File SHA-256 |
    |---|---|---|
    | artifact_manifest.json | `3a524225312641ad1c7f65247709ea66b576afadebe8c13e28d938096f0f3d0b` | `88a014e31d8cac0dbcc962d9298442068bd6048af16a9e797233a1f8a6159a37` |
    | arm_m_phase_p_dry.json | `b4af65eced13ac9e7cca0ef99830f3f9307f85e77da090dd6406c0e3c0766905` | `0ac4f51b09aa9bd2008237aeb824023d92c3d2a83cbc68a9b4b5f37b6286f580` |
    | arm_t_phase_p_dry.json | `9c1f17017d0d493ed5ebfa13a4e9db478ac5a0b781c0c96dfe9ddd0195e623a0` | `9f623e08edfb4e30b87d9d177b4c685ec9ab8628810b80fabbdcad6ff1ec912a` |

28. **End-to-end crash/restart — VERIFIED authored scope.** Existing integrated
    test and its eight failure variants passed. Additional independent path:
    authorization/review/fixture approval -> genesis -> head operation -> process
    death `os._exit(137)` after one journalled footer byte -> fresh process ->
    retry/backoff -> seal. Exactly **10 requests**, M **68,492** and T **1,283**
    charged body bytes, **40 modeled active seconds** (M36/T4), **97 contiguous
    journal records**, five unique ordered completions, one unchanged genesis,
    exact inventory file sizes/hashes and no request on sealed re-entry. The
    crashed response retains **65,537** bytes and **30 s**. Physical root files
    totaled **51,880 bytes** before sealed no-op; allowances remain separately
    reserved. This is one M/one T synthetic fixture, not real eight-file output
    or measured live performance.

29. **Acquisition-bounds failures — NON-BLOCKING FOR PHASE P.** Static imports of
    all bound modules, package initializers and CLI show no
    `xlm.data.acquisition.fetcher/progress` dependency. Those failures exercise
    the generic acquisition CLI and its Windows atomic progress-file path,
    not the v3 custom transport/journal. The reported 33-pass/2-fail run was
    **NOT rerun**: its HTTP-server fixture violates this task's NO NETWORK rule.
    The exact historical ENOENT cause is not newly established. Treat those
    failures as unresolved prerequisites before later generic acquisition,
    without claiming this review fixes or certifies it.

30. **G: root — VERIFIED metadata-only.** G: exists, volume/device
    `134349773812170000`; root `G:/Project/xlm-evidence-v3/essential-web` is absent.
    Only G:\ exists in that ancestor chain at inspection; no reparse tag there.
    Absence is valid before genesis. Future genesis must reobserve intended
    volume and all ancestors, pristine root, physical identity/inventory and
    exclusive claim. No G: paths were created.

31. **Tests/probes — VERIFIED results, acceptance BLOCKED.** Existing focused
    selection **224 passed /1 skipped**, exit0; independent safety selection
    **8 failed**, exit1; additional recovery/disk controls **2 passed**, exit0.
    Independent identity checker final exit0 after one checker-only newline
    correction, initial exit1 preserved. Exact commands, durations, environment
    and measured-resource limitations are in COMMANDS.md. Full repository gate,
    live tests, CUDA and acquisition-bounds rerun NOT RUN. No retries-until-green,
    weakened production assertions, blanket xfail, or fixture-as-live claims.

32. **Arm M decision — BLOCKED.** Shared runtime, memory/sealing, trusted-type
    and readiness defects prevent authorization despite correct operation plan.

33. **Arm T decision — BLOCKED.** Same defects; the T final-seal counterexamples
    are direct public-executor reproductions. Its 721-request interpretation is
    not the reason for refusal.

34. **Reviewer artifact — NOT ISSUED.** No positive typed Phase-P review decision
    or real operator approval was created. This report is a negative review,
    not an executable authorization artifact.

35. **Remaining preconditions — BLOCKED pending remediation.** Close R3-01–07,
    repair inspector R3-08, account open/recovery/flush work, establish the parser
    and inherited layout-check scope, regenerate/rebind affected children,
    then repeat independent review of exact commits. Only after a passing
    request-bound committed reviewer decision may the operator supply separate
    approval and run guarded check-auth/genesis. No signature prerequisite added.

36. **Exact next operator action.** Request a separate implementation task:
    “Remediate R3-01 through R3-08 in ESSENTIAL-WEB-EVIDENCE-V3.0-AUTHORIZATION-REVIEW-3.md,
    including its open/recovery time, parser-bound and layout-comparison gaps.
    Preserve the frozen protocol and scientific membership. Use offline synthetic
    fixtures only. Run the committed review probes and directly related regressions,
    regenerate children against the repaired implementation, and return for another
    independent Phase-P authorization review. No network, real genesis, approval,
    Phase D or push.” Do not run phase-p-genesis/phase-p-execute now.

37. **Files created.** This report and the new evidence directory containing
    COMMANDS.md, audit_identity.py, test_independent.py, test_positive.py,
    identity-initial.txt, identity.txt, identity.json, stock.txt/xml,
    independent.txt/xml, positive.txt/xml, exact-recovery.json and final-state.txt.
    Generated Python bytecode is ignored tooling cache, not implementation/evidence.
    No existing project file was edited.

38. **Commit — NONE.** User authorization to commit a positive reviewer artifact
    was conditional on both arms passing; neither passed. No staging/push.

39. **Verdict: PHASE-P AUTHORIZATION BLOCKED.**
