# P34 speculative update producer (opt-in)

`xlm.data.sampling.prefetch.PrefetchingBatcher` implements the trainer's
`BatcherProtocol` over one long-lived **spawned** child process that runs the
unchanged `MixtureBatcher`. The child prepares the next optimizer update while
the GPU trains the current one. Batch contents, order, masks, target counts,
trace chain and committed cursors are identical to the synchronous batcher;
this is enforced by tests and a bit-exact actual-50M CUDA gate.

It is **not** wired into `xlm train` or recipes. Enabling it in a research
recipe is a separate contract decision (see the P34 report).

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
`verify_content=True` re-hashes every received update (about 2.7 ms per B8
update); tests enable it, throughput runs do not.

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
* The child exits when the consumer closes it, is garbage collected, or dies
  (pipe EOF); it is also a daemon process.

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
