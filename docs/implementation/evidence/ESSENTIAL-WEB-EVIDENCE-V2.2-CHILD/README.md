# Evidence-v2.2 offline child artifacts (DRY, NOT AUTHORIZED)

Nine JSON files built offline from frozen inputs only. Nothing here
authorizes acquisition or any network operation, and no artifact is
executable (`executable: false` everywhere it applies).

- `arm_m_child_plan.json` — Arm-M execution is BLOCKED (conclusion A:
  cumulative 12 > 10). Explains why; emits no executable plan.
- `arm_t_child_plan.json` — dry Arm-T execution child plan
  (DRY_NOT_AUTHORIZED, authorization NONE).
- `readiness_review.json` — per-arm readiness vectors (M and T BLOCKED,
  with exact failed items).
- `ledger_reconciliation.json` — T carry-in reconciliation
  (54 requests / 2,231,492 bytes adopted once each + bounded gaps).
- `disk_schedule.json`, `memory_schedule.json`, `deadline_schedule.json`
  — T reservations and guards (final-disk conditional; deadlines unknown).
- `range_schedule_m.json` — M data + revalidation schedules
  (positions resolve at revalidated execution; counts exact now).
- `range_schedule_t.json` — T dictionary-corrected 47-range schedule.

Regenerate byte-identically (offline, frozen inputs only):

```powershell
$V=@("run","--offline","--locked","--no-sync","--extra","cpu","--extra","eval")
uv @V python scripts/evidence_v2.py build-child-plans --out-dir <fresh-empty-dir>
```

Do not write these into any execution root and do not treat them as
authorization. See `../../reports/ESSENTIAL-WEB-EVIDENCE-V2.2-IMPLEMENTATION.md`.
