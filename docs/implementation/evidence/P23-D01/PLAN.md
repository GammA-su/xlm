# Stage 1 proposal — D01 artifact request equivalence

Status: AWAITING USER APPROVAL. Implementation files and the acceptance ledger
have not been changed. Only isolated authored regression fixtures/evidence and
this proposed plan have been added.

The remediation brief and the user's current instructions supersede the earlier
P23 external authorizations. Remediation execution is offline, with tiny authored
fixtures only. P23 audit completion remains distinct from platform acceptance.

## Reproduced behavior

`src/xlm/artifacts/store.py:publish_artifact` currently reuses an existing
completed artifact when its ID/configuration hash match, after verifying the
old payload. It does not compare the newly proposed payload, producer,
dependencies or input lineage. The existing conflict test changes configuration
and content together, leaving this behavior undetected. An incomplete existing
directory is also removed and republished rather than rejected.

Run from D:\Project\xlm with PYTHONPATH=D:\Project\xlm\src and UV_OFFLINE,
HF_HUB_OFFLINE, HF_DATASETS_OFFLINE set to 1:

```text
uv run --offline --locked --no-sync --project data/audit/p23/repo --extra cpu --extra eval pytest data/audit/p23-remediation/stage01/test_d01_before.py -q --basetemp=data/audit/p23-remediation/stage01/tmp --junitxml=data/audit/p23-remediation/stage01/before.xml
```

Actual result: exit 1, **5 failed, 1 passed**, 0.44 s. The positive identical
request case passes. Same-length changed bytes, changed producer, changed
dependency identity, changed input lineage, and incomplete-directory rejection
all fail their expected rejection assertions. Four-byte payloads only; no
training, network, real source data, or user artifacts were used. Full failures
are retained in `before.log` and `before.xml`. Use a new basetemp for later runs.

## Proposed repair

1. Extend the existing artifact manifest with versioned deterministic publication
   identities. Keep the production/recipe key separate from the output-content
   digest. Cover kind, schema/serializer identity, declared producer/dependency
   identity, behavioral configuration, input lineage and relevant metadata;
   content identity covers sorted canonical paths, sizes and SHA-256 digests.
   Include verified input-manifest fingerprints where applicable. Missing legacy
   provenance stays explicitly unresolved; never invent a digest for unavailable
   evidence. Exclude declared cosmetic annotations/creation timestamps only,
   without heuristically stripping fields named `time` or `seed`.
2. Reuse the existing lock and private staging path. Stage/copy and hash the exact
   requested bytes, then compare the complete request and payload manifest with
   the verified existing artifact while holding the lock. Identical requests
   reuse without touching the original. A changed payload or relevant provenance
   raises an explicit conflict. Keep the existing explicit artifact IDs; do not
   silently rename conflicting outputs or overwrite a completed/incomplete artifact.
   Clean up only private staging allocated by the failed attempt.
3. Validate kind/ID before creating directories or lock files. Keep extensible
   artifact kinds but require safe path components. Validate manifest paths using
   Windows and POSIX forms; reject escapes, drive/UNC paths, aliases/collisions,
   reserved publication filenames and symlink/junction escapes. CLI ID lookup
   must not turn an invalid/ambiguous identifier into a successful lookup.
4. Preserve schema-v1 originals. Continue explicit legacy inspection/checksum
   verification, distinguish it from current request-equivalence verification,
   and require complete evidence before reuse under the new identity policy.
   Do not stamp historical artifacts as reproducible. D06 separately supplies
   authentic executed-code/environment provenance; D01 cannot prove the truth of
   a producer identity merely because a caller supplies a string.
5. Update existing manifest/store/ledger/CLI consumers only where this repair
   requires it. Add regressions to the maintained tests, preserve the pre-fix
   failures, and update the defect ledger with exact commands and outcomes.

Expected implementation files: `src/xlm/artifacts/manifest.py`, `store.py`,
`ledger.py` where required for legacy handling, `src/xlm/cli/artifact_cmd.py`,
and existing publication callers needing explicit serializer/input identities.
Expected tests: `tests/test_artifacts.py`, `tests/test_ledger.py`, and focused
public publication-path tests. No new artifact store, trainer or evaluator.

## Stage acceptance

- Same ID/config with identical bytes/provenance reuses unchanged output.
- Same-length byte changes or relevant provenance changes fail and preserve it.
- Timestamp-only cosmetic changes do not alter deterministic identity.
- Corrupt and incomplete existing artifacts fail without replacement.
- Two bounded fresh-process publishers: identical requests converge; conflicting
  requests produce one valid artifact and explicit losing-attempt evidence.
- Cross-platform path attacks, duplicate normalized paths and publication marker
  injection fail before writes outside the permitted staging/destination.
- Public `xlm data import-local --publish`, `xlm artifact inspect --json`, and
  `xlm artifact verify` exercise current and legacy success/refusal behavior.
  The exact import command spelling/options will be checked against registration
  before creating the CLI regression.
- Relevant artifact/ledger/public-CLI tests, lint and types pass using uv offline.

## Subsequent approval gates

After Stage 1, present D06's own plan and adversarial reproduction for approval.
The inspected queue still verifies a live tree and invokes already-imported code;
its existing snapshot machinery will be repaired to execute captured code in a
controlled fresh process. Subsequent separately approved stages follow the brief:
D03 mixture/plugins, D02 acquisition, D04/D05 evaluation, D08 statistics, D07 export,
then one complete final audit against one frozen final tree in separate base-only
and CPU+evaluation environments. The final orchestrator must exit zero; earlier
nonzero evidence remains preserved. External validation will be labeled
`NOT RUN — OPERATOR ACTION REQUIRED`, with concrete commands and input artifacts.

Approval requested for Stage 1 only.
