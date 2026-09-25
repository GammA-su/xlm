# P34 speculative update producer (opt-in)

`xlm.data.sampling.prefetch.PrefetchingBatcher` implements the trainer's
`BatcherProtocol` over one long-lived **spawned** child process that runs the
unchanged `MixtureBatcher`. The child prepares the next optimizer update while
the GPU trains the current one. Batch contents, order, masks, target counts,
trace chain and committed cursors are identical to the synchronous batcher;
this is enforced by tests and a bit-exact actual-50M CUDA gate.

It is **off by default**. The explicit run setting is:

```yaml
training:
  producer_prefetch: process_depth1
```

`off` keeps the synchronous `MixtureBatcher`; `process_depth1` wraps it in a
depth-1 `PrefetchingBatcher` with per-update content verification. Any other
value is rejected. The setting is validated by `TrainingConfig`, recorded in
the frozen execution envelope, and honored identically by direct training, the
queue worker, and checkpoint resume: resume reconstructs the batcher from the
frozen envelope, so a resumed run cannot silently change the mode. Non-mixture
inputs reject the option. Direct domain-API construction below remains
available.

## Use

```python
from xlm.data.sampling.prefetch import PrefetchingBatcher, ProducerSpec

source = build_training_batcher(...)          # an ordinary MixtureBatcher
state = source.get_state()                    # or the checkpoint's data_state.json
with PrefetchingBatcher(ProducerSpec.from_batcher(source), state) as batcher:
    trainer = Trainer(..., batcher=batcher, ...)
    trainer.train()
```

`depth` (default 1, maximum 2) bounds prepared-but-unconsumed updates.
`verify_content=True` re-hashes every received update. The final review measured
about 3 ms per B8 update; the actual configuration route and final production
throughput measurements include it. Library callers that disable it forgo
consumer-side array/provenance integrity verification.

The caller's script must use the standard `if __name__ == "__main__":` guard
(Windows `spawn` re-imports the main module in the child).

## State contract

| Concept | Owner | Changes when |
|---|---|---|
| Committed state (`get_state`, checkpoints) | consumer | `commit()` only |
| Speculative state | child batcher | every prepared update; replaced by `reset` |
| Generation | consumer | `rollback`, `load_state`, budget misprediction, error recovery |

Each prepared update carries `start_state_digest` and `end_state`
(`end_state_digest`) plus a content digest. The consumer uses an update only if
its start digest equals the committed digest; messages from older generations
are discarded unread. Checkpoints persist committed state only; a prepared,
unconsumed update is discarded across restart and regenerated identically.

## Failure behavior

* Producer exception (sampling, packing, encoding): surfaced on the next
  `next_step_microbatches`; known batcher errors keep their type
  (`SourceExhaustedError`, `RepeatBudgetExceededError`, `MixtureStreamError`),
  others become `PrefetchProducerError` carrying the type name only.
  Committed state is unchanged; the next request regenerates from it.
* Producer death or timeout: `PrefetchProducerError`; call `restart()`, which
  starts a new child from committed state.
* Consumer failure before `commit()`: call `rollback()` if model/optimizer
  state is still at the committed boundary (failures before `optimizer.step`),
  otherwise resume from the last checkpoint. Calling `next_step_microbatches`
  again without `commit`/`rollback` raises `PrefetchProtocolError`.
* An interrupted optimizer boundary leaves the trainer **in doubt**
  (`RecoveryRequiredError` on further updates, `train`, or checkpoint publication,
  including the trainer's bound checkpoint manager):
  the optimizer may have partially mutated, so the run must reload the last
  complete checkpoint in a fresh trainer. On CUDA, the trainer additionally
  synchronizes the current stream for every loader after the optimizer step and before
  promoting the cursor, so an asynchronous optimizer failure can never surface
  after the schedule/counters/cursor have advanced.
* The child exits when the consumer closes it, is garbage collected, or dies
  (pipe EOF); it is also a daemon process.
* Transport failures (`BrokenPipeError`, EOF, Windows poll errors, killed
  child) surface as `PrefetchProducerError` domain failures without cursor
  advancement. A consumed update still commits after producer death because
  commit performs no producer IPC. A lifetime inbound reader drains child writes
  during every control send, including small requests and reset handshakes.
  The inbox, frame size (128 MiB) and parent I/O waits are bounded; reset waits
  for its acknowledgment. Timeout/death discards speculation and requires restart.
  Shutdown finishes or cancels owned I/O before releasing Windows handles.

The frame cap accounts for the packer's maximum 1,048,576 positions, not just
the valid-target budget: compact arrays use 77 bytes per position. It is an
owned-child IPC guard, not a promise to safely unpickle external input or an
OS-enforced RSS limit. See [the final adversarial review](reports/P34.md) for
the calculation, negative tests, lifecycle evidence and current planning rates.

## Metadata representation

Tensors (`input_ids`, `labels`, `loss_mask`, `position_ids`) are CPU `int64`
tensors exactly as before. `segment_ids` and `metadata["input_attention_mask"]`
are tensors instead of nested lists. Per-target provenance
(`source_attribution`, `target_doc_ids`, `target_lineage_ids`,
`target_byte_spans`, `target_token_offsets`) is not placed in each batch; call
`batcher.pending_update.microbatch_provenance(i)` for the exact lists. The
trainer never reads them, and materializing them eagerly would cost about
24 ms of trainer-thread time per 65,536-target update (receiving the compact
update costs about 3.5 ms).
