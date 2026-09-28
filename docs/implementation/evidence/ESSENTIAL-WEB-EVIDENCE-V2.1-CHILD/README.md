# Evidence-v2.1 offline child artifacts (DRY, NOT AUTHORIZED)

These two JSON files are offline constructions for implementation review.
Neither authorizes acquisition or any network operation.

- `arm_m_blocked_plan.json` — Arm-M execution is BLOCKED: cumulative
  first-file planning usage (12 physical requests) exceeds the unchanged
  10-request/file cap. This receipt explains why; it is not a plan.
- `arm_t_child_plan_dry.json` — dry Arm-T execution child plan binding
  the v2.1 freeze, unchanged 118-locator selection, adopted cost map,
  carry-in reconciliation, exact range schedule, and reservations.
  `executable: false`, `authorization: NONE`, status DRY_NOT_AUTHORIZED.

Regenerating byte-identically (offline, from frozen inputs only):

```powershell
$V=@("run","--offline","--locked","--no-sync","--extra","cpu","--extra","eval")
uv @V python scripts/evidence_v2.py build-child-plans --out-dir <fresh-empty-dir>
```

Do not write these into any execution root and do not treat them as
authorization. See `../../reports/ESSENTIAL-WEB-EVIDENCE-V2.1-IMPLEMENTATION.md`.
