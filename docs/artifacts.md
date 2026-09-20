# Artifact publication and legacy inspection

For execution envelopes, actual worker provenance, continuation, and the separate
training/evaluator identities, see [Frozen execution](frozen-execution.md).
Artifact request equivalence alone does not prove execution provenance.

New publications use schema v2 in the existing `ArtifactStore`. Explicit kind/ID
pairs remain immutable. A production key binds the declared producer and dependency
identities, configuration hash, serializer version, ordered input IDs, supplied
verified input-manifest fingerprints, and behavioral metadata. A separate content
hash binds sorted canonical payload paths, sizes and SHA-256 digests.

Publication streams the proposed bytes into private staging before reuse is
considered. Reuse requires both the existing artifact's integrity and exact
canonical request/content equivalence. Conflicting, corrupt, and incomplete
destinations raise an explicit conflict; they are never overwritten, repaired,
or automatically renamed. Identical reuse preserves the original bytes and mtime.
Only `created_at` and explicitly supplied `cosmetic_metadata` are excluded from
production identity. A timestamp or seed inside ordinary `metadata` is behavioral.
Cosmetic annotations cannot be used to hide a behavioral setting.

The container serializer defaults to `xlm.artifact-files/1`; callers with a
specific payload serialization contract can declare `serializer_version`.
`input_artifact_paths` optionally maps declared parent IDs to existing artifacts:
the store verifies them and records SHA-256 fingerprints of their manifest bytes.
IDs without supplied paths appear in `unresolved_input_artifact_ids`; no parent
evidence is invented. Fingerprints identify the exact supplied parent manifests.
Producer/dependency strings remain **declarations**, not authenticated execution
provenance. That separate guarantee is blocked by D06.

Schema-v1 artifacts retain their original files and schema. Inspection and checksum
verification remain available, labeled `legacy-checksum-only`. They cannot qualify
for automatic v2 request-equivalent reuse. Historical provenance remains unresolved;
an operator must explicitly select a new ID for a separately evidenced publication.
Inspection displays metadata; it does not itself perform checksum verification.

```powershell
uv run --offline --locked --extra cpu --extra eval xlm artifact inspect ARTIFACT_ID --json
uv run --offline --locked --extra cpu --extra eval xlm artifact verify ARTIFACT_ID
uv run --offline --locked --extra cpu --extra eval xlm artifact rebuild-ledger
```

ID lookup covers extensible artifact kinds and refuses ambiguity; use an explicit
directory path to inspect a particular artifact when IDs occur in multiple kinds.
The existing globally keyed ledger refuses replacing an ID with another kind/path.
Ledger reconstruction reports legacy and corrupt entries without rewriting them.

Publication and verification default to a 2-GiB payload cap per artifact; Python
callers can explicitly configure `max_publication_bytes`. This does not authorize
a larger job. Other bounds are 64-KiB streaming chunks, an 8-MiB manifest,
10,000 payload files, 20,000 payload tree entries, and a ten-second lock timeout.
The manifest/marker and concurrent attempts are additional to the payload cap;
aggregate acquisition/preparation accounting remains D02. Paths must be portable
canonical relative paths without reserved names, case aliases, traversal,
symlinks or junctions. Only the current attempt's private staging is cleaned up.

Concurrency evidence covers cooperative current-version publishers on Windows 11.
Mixed old/new publishers and hostile concurrent filesystem mutation are not a
verified isolation boundary. Linux execution has not been verified in D01. Do not
infer full platform acceptance from artifact consistency checks.
