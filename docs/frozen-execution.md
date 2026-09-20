# Frozen execution and checkpoint provenance

D06 uses the existing plan, snapshot, authorization, queue, Trainer, checkpoint
manager, and artifact store. Its offline acceptance is tracked in
[the D06 report](implementation/reports/P23-D06.md). Full platform acceptance is
still blocked by the other remediation stages.

## Identity and execution

An executable version-2 plan contains a version-1 execution envelope. Registered
component and training defaults are resolved before hashing. The envelope binds
the configuration, source map, lockfile, actual installed runtime, selected extras,
input checksums, tokenizer bytes/fingerprint, component serializers, seeds, and
deterministic single-thread CPU policy. Plan identity additionally binds the
existing budgets, track, cadence, exposure, and lineage. Hash fields exclude their
own values. Plan IDs, creation times, cost observations, worker PID, temporary
paths, and environment location do not define computational identity.

Queue submission verifies the persisted plan and capture, then binds the copied
plan, job fields, selected runtime location, and effective authorization ticket
through the existing ledger authorization token. A textual authorization label
does not grant authority. Admission sidecars are mandatory. Revalidation at launch
rejects independent changes; it never repairs a submitted plan or snapshot.

Captures include the allowed `src/`, `recipes/`, `manifests/`, Python pin,
`pyproject.toml`, and `uv.lock` closure, including untracked implementation files.
Root corpus directories, environments, caches, and secret-like filenames are
excluded or refused. Existing conflicting or incomplete captures are not replaced.
An intact capture A continues to run after edits to the live checkout B.

The selected interpreter starts captured `experiments/bootstrap.py` with
`-I -S -B`. The bootstrap adds only captured XLM source and the explicitly selected
site-packages directory. It does not initialize site, execute `.pth` hooks, honor
inherited `PYTHONPATH`/user-site paths, or import the installed editable XLM package.
Python source loaders compile source directly: `-B` alone would still read existing
bytecode. Sourceless bytecode is refused. Observed XLM module origins and bytes,
the actual worker PID, and the Trainer method origin are recorded and checked.
Runtime caches and temporary files are outside the immutable capture.

## Offline runtime policy

Keep Python **3.12.13**, the existing lockfile, and an explicitly selected `cpu` or
`cuda` extra (mutually exclusive). Add `eval` for harness execution. For example:

```powershell
uv sync --offline --locked --extra cpu --extra eval
uv run --offline --locked --extra cpu --extra eval xlm train authored-plan.json --device cpu
uv run --offline --locked --extra cpu --extra eval xlm resume path/to/checkpoint --device cpu
```

An absent offline cache/dependency blocks execution. The launcher does not install,
download, substitute, or upgrade packages. Actual installed versions must be in
the frozen lock, and selected project/transitive requirements must be present.
The installed-file fingerprint covers all regular site-packages files, including
Python source, native binaries, package data, and distribution metadata, except:

- Generated `__pycache__` and `.pyc` files, which the worker cannot use as source substitutes.
- Installer bookkeeping: `RECORD`, `INSTALLER`, `REQUESTED`, and `direct_url.json`.
- XLM's editable installation metadata/hooks and installed XLM package, because
  XLM execution comes exclusively from the independently verified capture.

The interpreter launcher and base executable are separately hashed. Python patch,
implementation, machine/processor, OS version, distribution versions, selected
extras, and aggregate installed file identity are normalized runtime fields.
Environment directory location is an observation. An equivalent relocated
environment can execute the same identity; changed installed bytes cannot.

This is a local integrity/reproducibility boundary, **not an OS sandbox**. The
launcher/bootstrap, standard library, OS libraries/drivers, local account, and
filesystem are trusted. Dependency imports needed by the verifier are part of
that trusted startup; the inventory is checked before Trainer computation.
It does not defend against a hostile process with equivalent filesystem rights,
kernel compromise, or concurrent malicious replacement after verification.
CUDA and other operating systems require separate operator validation. CPU
numerical parity established on one machine is not a cross-platform guarantee.

## Limits and operational evidence

Capture is limited to 10,000 files, 20,000 traversed entries per include tree, and
256 MiB, with streamed 64 KiB copying/hashing and attempt-owned staging cleanup.
Execution JSON is at most 8 MiB; worker results are read with a 2 MiB limit.
Tokenizer/evaluation asset inspection is limited to 4,096 entries and 64 MiB.
Single-shard inputs are limited to 2 GiB, with an 8 MiB manifest limit.

Installed runtime inspection allows 45,000 files, 100,000 entries, 8 GiB, and
90 seconds. Hashing uses bounded batches and eight threads. A limit failure is a
refusal, not a partial inventory or permission to download a replacement.

Workers have a setup allowance of 120 seconds in addition to the recorded train
budget (600 seconds for evaluation). Combined stdout/stderr is capped at 16 MiB.
The parent tracks its process tree, cancellation, heartbeats, and sampled RSS.
It kills only that owned tree on time/output failure. Private scratch has a
sampled disk/entry guard; this is not a kernel disk quota. Checkpoint tensor
serialization has a write-time byte guard reserving publication-copy space and
counting this manager's prior publications. Default artifact/scratch allowance is
2 GiB; queue ticket limits are passed through. Existing immutable inputs and the
installed environment are separately inventoried, not billed as new artifacts.
Failure logs remain available; retries do not overwrite earlier attempt evidence.

Recovery checks the recorded runner/worker PID and process creation time before
treating a stale heartbeat as a crash. A live owner prevents a second launch. A
crashed attempt is counted once against its explicit retry allowance. Recovery
loads the latest integrity-valid checkpoint through the existing checkpoint
manager, restores committed counters/state, and continues without republishing
the old artifact. Corrupt or incompatible checkpoint evidence causes refusal.
An already completed final checkpoint is verified and reused without modification.
Abruptly lost, uncommitted computation is not promoted to measured research
exposure; crash evidence remains necessary when accounting for a real campaign.

## Continuation and evaluation

Frozen checkpoints carry checksummed `execution.json` and `runtime.json`, bound
to manifest code/dependency/plan fields. Run records use the same plan identity.
Ordinary continuation restores the original capture and compatible envelope in
a fresh process, including model, objective, optimizer, schedule, RNG, and data
state. Different PIDs or temporary paths do not require a fork. Changed budget,
device, environment, or policy requires a new compatible plan and explicit
authority; `--fork` does not grant production-scale authority. Direct forks
remain within the existing smoke caps.

Historical originals are preserved. Inspection and checksum verification do not
establish missing execution evidence. Low-level domain diagnostics remain usable
with `unresolved-domain-api` provenance, but cannot qualify for frozen CLI resume,
including through an automatic fork. No historical provenance is invented.

Diagnostic evaluation receipts retain the checkpoint's training envelope and
record a separate evaluator envelope and observations. Raw cache identity also
includes evaluator execution identity. Loose model directories remain diagnostic
inputs with explicitly unresolved training history; their actual weights, config,
and tokenizer are still bound to evaluation. This does not repair evaluation
coverage/statistics, authorize final-set access, or expand mixture/plugin execution.
