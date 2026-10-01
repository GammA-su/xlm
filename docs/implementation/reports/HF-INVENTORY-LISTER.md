# Generic Hugging Face inventory lister (offline implementation)

Base: `818c663ba436fc91c8421fdddffa5cbaa783fd84` on `data/mix01-ultrax-6b`.
Worktree: `F:\Project\xlm-inventory`, branch `feat/generic-hf-inventory`.

## Current limitation

`scripts/mix01_inventory.py freeze` required an operator-assembled file list
(`--files` text plus optional `--sizes` JSON). There was no generic bounded
paginated Hub enumerator: the existing probe observed one file, and
`HuggingFaceTransport.list_files` fetched a single non-recursive tree page
with no cursor, filter, revision-consistency or completion proof. Production
planning for FinePDFs was blocked on a complete `eng_Latn` candidate universe.

## Architecture

New pure module `src/xlm/data/sources/hf_inventory.py`:

- `ListingFilter`: relative `path_prefix`, `extension_allowlist`,
  `include_globs`/`exclude_globs` (fnmatch). No source hard-coded.
- `ListingLimits`: `max_pages`, `max_items`, `max_requests`,
  `max_metadata_bytes`, `max_retries`, per-request timeout, total deadline.
- `PageItem`/`TreePage`/`PageFetcher` protocol: offline tests inject authored
  fixtures; live path uses `HfTreeFetcher`.
- `collect_listing`: validates repository (`owner/name`), 40-hex revisions,
  source/view ids, paths (no absolute, `//`, `.`/`..`, traversal, control
  chars), sizes (non-negative, bounded), identities; rejects duplicates
  (including conflicting metadata); enforces revision consistency per page;
  detects loops, missing continuation (`total_declared` vs delivered),
  and fails closed on any ceiling or deadline. Files sorted ascending by path
  for determinism.
- Receipt `hf_repository_file_listing` v1: provider, repository, requested +
  resolved revision, source/view, filters, sorted files
  (`path`/`size_bytes`/`oid`), counts, byte totals, page/request/metadata
  evidence, `pagination_complete: true`, `file_list_digest`, self `digest`.
  No timestamps participate in identity.
- `verify_listing`, `listing_to_freeze_inputs`, `write_listing` (write-once:
  identical reuse, divergent refuse), `read_listing` (bounded, verifying).
- Live `HfTreeFetcher` + `build_tree_url` + `assert_metadata_only`: only
  `/api/datasets/.../tree/...` endpoints; `/resolve/`, `/raw/`, Parquet
  URLs refused. Loopback base URL supported for isolated tests only.

`scripts/mix01_inventory.py` (minimal extension, legacy intact):

- `freeze` keeps `--files`/`--sizes`; adds `--listing` (verified receipt
  replaces manual lists, identity-bound to `--source`/`--repo`/`--revision`).
- New `list-hf` (NETWORK, future operator only; refuses while
  `HF_*_OFFLINE=1`): bounded metadata-only listing with explicit ceilings.
- New `verify-listing` (offline): verifies digest, ordering, counts, bytes,
  duplicates, path policy, frozen identity.

Ordering preserved: `freeze_inventory` untouched (v1,
`SHA-256(seed|repository|revision|file)`, seed default `20260918`, digest
semantics, benchmark tail reservation in planner). Listing is the complete
eligible universe; plan selection happens later.

## FinePDFs filter configuration (operator, not hard-coded)

- Repository `HuggingFaceFW/finepdfs-edu`, revision
  `9cfabe2127faca99b3d5c4dc6d1fcb397399ebde`, source `finepdfs_edu`, view
  `eng_Latn`.
- `--path-prefix data/eng_Latn/ --extension .parquet` (repeatable
  `--include-glob`/`--exclude-glob` available). Excludes README,
  metadata, scripts, other language trees by configuration, not filename
  guessing in generic code.

## Verification

Offline: `verify-listing` recomputes digests, ordering, counts, bytes,
duplicate absence, path policy, source/revision/filter identity. No network.

## Tests

`tests/test_hf_inventory.py` (40 cases, authored fixtures + loopback HTTP,
no live Hub): single/multi page, shuffled order determinism, duplicates,
conflicting metadata, loops, missing continuation, revision drift/symbolic,
request/page/item/metadata ceilings, retry ceiling/success, invalid/traversal
paths, size bounds, prefix/extension/glob filtering, FinePDFs non-Parquet
exclusion, digest determinism/revision/filter sensitivity, double-freeze
byte-identity, incomplete-listing refusal, legacy freeze, payload-endpoint
refusal, CLI verify, write-once, loopback metadata-only fetch.

Related: `test_mix01_inventory`, `test_source_plan`, `test_cli_inventory`
pass. `ruff check`, `ruff format --check`, `mypy --strict` (src module),
`git diff --check` pass. No live network/payload/download/production.
