# D02 / D04-D05 ownership and integration record

User-authorized parallel work, recorded 2026-09-20 before further Astra product edits.

- Astra owns D02 in `D:\Project\xlm`, branch `fix/d02`.
- Opus owns D04/D05 in `D:\Project\xlm-d0405`, branch `fix/d04-d05`.
- Original shared ancestor: `20673e3c6a2f80ebaaf4e89705d2951397fff3d0`.
- Opus's exact imported baseline: `0a8ff6592b3a893d0bd70f99649fdfcdf7fde310`;
  tree `f93a6ce4a5df89f71d08e873b4cd8df1f4dbacb8`.
- Opus before-evidence commit observed: `18cc02b3000603c3a7879d1d23bc4508a03bc235`.
- Astra now uses the same baseline commit. Local Git object fetch, index setup
  and branch reference setup did not check out or replace working files.
  `main` remains at its original commit. Neither checkout was reset.

The baseline contains all 278 paths in the preserved pre-D02 source/test hash
manifest, including previously untracked D01, D06 and D03-core implementation.
No baseline source is missing. Seven existing files differ from that older
manifest: acquisition disk/fetcher/plan/progress/verifier, source transport and
prepare config. They are explicitly unfinished D02 work captured by Opus's copy,
not D04/D05 changes. The baseline also contains newly added acquisition selection,
record and prepare-bounds modules. Subsequent Opus changes must be derived from
its own baseline commit, never from a folder comparison with Astra's newer tree.
The complete blob inventory and pre-setup working-byte inventory are preserved
in `data/audit/p23-remediation/integration/baseline-audit.json`.

## Shared interfaces

Astra changes acquisition plan/journal/capacity, transport, preparation bounds and
the data CLI implementation. Opus owns evaluation input binding, harness coverage
and its evaluation CLI implementation. Preserve D01 publication and D06 frozen
execution interfaces. No changes to frozen research contracts, dependency pins,
generic CLI registration or shared configuration schemas are planned by Astra.
Opus should record any necessary interface changes in its stage handoff. Global
status integration is Astra's responsibility; stage-specific reports can evolve
independently. No whole-folder copying or blanket conflict resolution is allowed.

## Integration gate

Both final commits and exact stage tests are still pending. Keep both branches,
all original evidence and the original working directories. Once both repairs
are ready, locally fetch Opus's scoped commits and perform a three-way merge in
a new clean integration worktree based on the completed D02 commit. Review shared
interfaces even without textual conflicts. Run both regression sets and a combined
authored-fixture public workflow with uv offline, locked dependencies and isolated
runtime paths. Report integrated commit, reviewed resolutions and combined tests
before changing the main working branch. No production/platform acceptance follows
from this integration; D07, D08 and deferred D03 work remain open.
