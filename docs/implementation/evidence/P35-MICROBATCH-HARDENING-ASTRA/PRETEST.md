# Independent review before test execution

Starting clean HEAD: e7644a3e667db2892ece23b4ca8d737c83b800c8.
Worktree: G:\Project\xlm-p35-microbatch-astra. No tests or product edits
preceded this record. Required reports, scientific contract, science guide,
AGENTS, actual STATUS and prior Astra review read; hardening source diffs traced.

Pre-test verdict: **BLOCKED pending independent verification**, not acceptance
of the implementing agent's certification.

1. Producer binding compares all six consumed fields and optional provenance,
   then rechecks the producer seal. This closes replacement before binding.
   It deliberately does not freeze tensors after binding. Stock Transformer,
   mask/RoPE and CE paths inspected do not write their inputs; extension code
   and hooks can. A1 must demonstrate this distinction, not claim a kill.
2. Checkpoint lineage checks occur before model/objective/optimizer/data/RNG
   restore. Full chain and LR contiguity plus data C/meta C/step are checked.
   Test wrong C in both directions, consistently rechained duplicates and
   intermediate gaps, partial endpoint, and all fork cases.
3. Joint receipt commit leaves the P34 in-doubt flag set through LR append and
   chain append. Exceptions become ScienceReceiptCommitError. This is fail-stop
   authority, not transactional rollback of already mutated optimizer/data.
4. Guard covers declaration, versions, rows, head and staging. Hypothesis:
   malformed state can make guard_state/identity_digest throw before the
   controller marks live state compromised. Test this as well as valid mutations.
5. Compact aliases are explicitly refused; stock _encode uses a shared dict
   setdefault table and validates strings, therefore emits unique aliases.
6. All load/fork paths use _check_receipt_lineage. Presence/version fixed;
   changed-policy nonzero history refused; empty C=0 re-origin allowed.
7. Evidence extraction uses a weaker LR validator than checkpoint load:
   schedule_counter is not checked. Challenge a valid chain with incorrect LR
   schedule metadata before trusting formal v2 eligibility.
8. Bounds require independent serialized-byte accounting, including LR rows,
   newline translation and non-row science metadata. The 400k internal ceiling
   is not the planned 91,554-update campaign maximum.

All runtime, CUDA, cost, static and adversarial checks are NOT RUN at this point.
No network/install/live data/pilot/formal study/push/merge authorized or performed.
