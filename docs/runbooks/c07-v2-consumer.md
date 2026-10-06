# Mix-01 C07-v2 consumer operator runbook

Use the locally committed `fix/training-input-policy-v2` worktree, not the Opus source
or earlier conservative audit worktree. These are **operator commands; production was
not run by the agent**. Stop on any nonzero exit. Each code block is one independently
runnable PowerShell line. Do not place secret key values on a command line or in a log.
The existing C05 trust environment must be available; only freeze signs a new artifact.

Review [the verdict, exact policy bounds and evidence](../implementation/reports/C07-V2-CONSUMER.md).
Use v2, 16 tokenizer workers, 8 freeze workers, shard-native order and the default
`max_open_shards=0`. No training command is authorized or supplied here.

1. Print/review the exact release SHA against the final review response, branch and clean
   status. This also refuses the wrong branch, dirty files or a missing Opus ancestor.

```powershell
Set-Location F:/Project/xlm-training-input-v2; git rev-parse HEAD; git branch --show-current; git status --short; if ((git branch --show-current) -ne 'fix/training-input-policy-v2' -or (git status --porcelain)) { throw 'Wrong branch or dirty release' }; git merge-base --is-ancestor 602cd3ff83a6495589b8d9d7608c9ebc77118488 HEAD; if ($LASTEXITCODE) { throw 'Wrong source history' }
```

2. Focused consumer/C07 tests. Test-only thread settings; the production subprocess
   tokenizer workers set their own thread limits. A fresh short temporary directory
   avoids Windows' disabled long-path support. No full acceptance suite is implied.

```powershell
Set-Location F:/Project/xlm-training-input-v2; $env:OMP_NUM_THREADS='1'; $env:MKL_NUM_THREADS='1'; $env:OPENBLAS_NUM_THREADS='1'; $env:NUMEXPR_NUM_THREADS='1'; $env:TOKENIZERS_PARALLELISM='false'; uv run --offline --locked --extra cpu --extra eval python -m pytest tests/test_c07_consumer_v2.py tests/test_tokenize_freeze_fast.py tests/test_token_shards.py tests/test_mixture_stream.py tests/test_sampling_trace.py tests/test_configurable_training.py tests/test_prefetch.py tests/test_checkpoint.py tests/test_p35_readiness_planner.py tests/test_p35_m5_order.py -n 0 -q --basetemp (Join-Path C:/XLM-scratch ('c07-check-' + [guid]::NewGuid().ToString('N').Substring(0,8))); if ($LASTEXITCODE) { throw 'Focused checks failed' }
```

3. Confirm X: is detached and both output roots are fresh. Resume has different semantics
   and must not use this fresh-root command after components have been published.

```powershell
if (Get-PSDrive -Name X -ErrorAction SilentlyContinue) { throw 'X: must be detached' }; foreach ($p in @('G:/XLM/shards/mix01-policy-v2','G:/XLM/freeze/mix01-policy-v2')) { if (Test-Path -LiteralPath $p) { throw ('Output exists: ' + $p) } }; 'Detached and fresh'
```

4. Conservative capacity admission: G: at least 24 GiB complete shards plus 32 GiB
   reserve; C: at least 1 GiB temporary allowance plus 4 GiB reserve. The authenticated
   resource plan below computes the producer's own bound and can be stricter. Consumer
   verification itself has zero disk-payload scratch. Neither command creates real shards.

```powershell
if ((Get-PSDrive G).Free -lt 56GB) { throw 'G: requires at least 56 GiB free' }; if ((Get-PSDrive C).Free -lt 5GB) { throw 'C: requires at least 5 GiB free' }; Get-PSDrive G,C | Select-Object Name,Free
```

5. Verify pinned selection/C05/tokenizer and print deterministic resource plan. Inspect
   documents=5824661, valid_targets=6000000000, token_ids=6005824661. The plan verifies the
   signed upstream chain and selected SHA and refuses insufficient capacity before source
   tokenization. It may create/remove its small private tokenizer scratch snapshot.

```powershell
Set-Location F:/Project/xlm-training-input-v2; uv run --offline --locked --extra cpu --extra eval python -m xlm.data.exclusion.operator tokenize-selection --c05-proof G:/XLM/c05-policy-v2/policy-v2-p0001.proof.json --tokenizer G:/XLM/tokfit/mix01-fit-shares-v1-policy-v2/tokenizer --selection G:/XLM/selection/mix01-policy-v2 --output-root G:/XLM/shards/mix01-policy-v2 --scratch C:/XLM-scratch/tokenize-selection-policy-v2 --workers 16 --index-schema c07-offsets-v2 --expect-c05-plan-digest c15b54542238b0a4d6cce371c81ce0fe71caed5ed7c6bc23f937e4e85bbb8c59 --expect-c05-completion-digest df9834ab459bf00fac7ee4ffe69503432fc8e95b9af15009dcca7897a81c0a27 --expect-selection-digest 17b1cfce2ce643ecb72d53f367f95ade00f1a842d23a242c6f3a234c74c34662 --expect-selected-membership-sha256 a4539473bdfcbcf0d52efc621dcfb8efa3fff1cbba8e0e7cc943cf31cad54be9 --expect-tokenizer-fingerprint 50bf9d45f6f6fa88062483d0e83f3be419e610dee6b75d37809252504bac5b54 --progress-interval 5 --plan-only; if ($LASTEXITCODE) { throw 'Authenticated preflight failed' }
```

6. Launch tokenization only after the checks pass. This is the next production mutation.

```powershell
Set-Location F:/Project/xlm-training-input-v2; uv run --offline --locked --extra cpu --extra eval python -m xlm.data.exclusion.operator tokenize-selection --c05-proof G:/XLM/c05-policy-v2/policy-v2-p0001.proof.json --tokenizer G:/XLM/tokfit/mix01-fit-shares-v1-policy-v2/tokenizer --selection G:/XLM/selection/mix01-policy-v2 --output-root G:/XLM/shards/mix01-policy-v2 --scratch C:/XLM-scratch/tokenize-selection-policy-v2 --workers 16 --index-schema c07-offsets-v2 --expect-c05-plan-digest c15b54542238b0a4d6cce371c81ce0fe71caed5ed7c6bc23f937e4e85bbb8c59 --expect-c05-completion-digest df9834ab459bf00fac7ee4ffe69503432fc8e95b9af15009dcca7897a81c0a27 --expect-selection-digest 17b1cfce2ce643ecb72d53f367f95ade00f1a842d23a242c6f3a234c74c34662 --expect-selected-membership-sha256 a4539473bdfcbcf0d52efc621dcfb8efa3fff1cbba8e0e7cc943cf31cad54be9 --expect-tokenizer-fingerprint 50bf9d45f6f6fa88062483d0e83f3be419e610dee6b75d37809252504bac5b54 --progress-interval 5; if ($LASTEXITCODE) { throw 'Tokenization failed; inspect before resume' }
```

7. If interrupted: reverify completed immutable components and continue remaining ones.
   Partial components are never continued in place. Existing producer write-once and
   component resume behavior is preserved.

```powershell
Set-Location F:/Project/xlm-training-input-v2; uv run --offline --locked --extra cpu --extra eval python -m xlm.data.exclusion.operator tokenize-selection --c05-proof G:/XLM/c05-policy-v2/policy-v2-p0001.proof.json --tokenizer G:/XLM/tokfit/mix01-fit-shares-v1-policy-v2/tokenizer --selection G:/XLM/selection/mix01-policy-v2 --output-root G:/XLM/shards/mix01-policy-v2 --scratch C:/XLM-scratch/tokenize-selection-policy-v2 --workers 16 --index-schema c07-offsets-v2 --expect-c05-plan-digest c15b54542238b0a4d6cce371c81ce0fe71caed5ed7c6bc23f937e4e85bbb8c59 --expect-c05-completion-digest df9834ab459bf00fac7ee4ffe69503432fc8e95b9af15009dcca7897a81c0a27 --expect-selection-digest 17b1cfce2ce643ecb72d53f367f95ade00f1a842d23a242c6f3a234c74c34662 --expect-selected-membership-sha256 a4539473bdfcbcf0d52efc621dcfb8efa3fff1cbba8e0e7cc943cf31cad54be9 --expect-tokenizer-fingerprint 50bf9d45f6f6fa88062483d0e83f3be419e610dee6b75d37809252504bac5b54 --progress-interval 5 --resume; if ($LASTEXITCODE) { throw 'Resume refused or failed' }
```

8. Before freeze, independently reverify all published components with the same pins.
   Confirm every component has `resume_skip=true`; a plan may otherwise list missing
   components without producing them. Freeze also refuses an incomplete set.

```powershell
Set-Location F:/Project/xlm-training-input-v2; uv run --offline --locked --extra cpu --extra eval python -m xlm.data.exclusion.operator tokenize-selection --c05-proof G:/XLM/c05-policy-v2/policy-v2-p0001.proof.json --tokenizer G:/XLM/tokfit/mix01-fit-shares-v1-policy-v2/tokenizer --selection G:/XLM/selection/mix01-policy-v2 --output-root G:/XLM/shards/mix01-policy-v2 --scratch C:/XLM-scratch/tokenize-selection-policy-v2 --workers 16 --index-schema c07-offsets-v2 --expect-c05-plan-digest c15b54542238b0a4d6cce371c81ce0fe71caed5ed7c6bc23f937e4e85bbb8c59 --expect-c05-completion-digest df9834ab459bf00fac7ee4ffe69503432fc8e95b9af15009dcca7897a81c0a27 --expect-selection-digest 17b1cfce2ce643ecb72d53f367f95ade00f1a842d23a242c6f3a234c74c34662 --expect-selected-membership-sha256 a4539473bdfcbcf0d52efc621dcfb8efa3fff1cbba8e0e7cc943cf31cad54be9 --expect-tokenizer-fingerprint 50bf9d45f6f6fa88062483d0e83f3be419e610dee6b75d37809252504bac5b54 --progress-interval 5 --resume --plan-only; if ($LASTEXITCODE) { throw 'Shard verification failed' }
```

9. Freeze with the explicit **signed training-input-policy-v2**. This policy also checks
   the exact count digest `e0348ee8c07ab6c71ed6f5c95e2317fdca48407d020ce4b51b9f3aea22fab8e2`
   and the other protected upstream pins. Omitting the policy leaves the legacy 2 GiB
   consumer envelope and will not produce a production-admissible training input.

```powershell
Set-Location F:/Project/xlm-training-input-v2; uv run --offline --locked --extra cpu --extra eval python -m xlm.data.exclusion.operator freeze --c05-proof G:/XLM/c05-policy-v2/policy-v2-p0001.proof.json --tokenizer G:/XLM/tokfit/mix01-fit-shares-v1-policy-v2/tokenizer --selection G:/XLM/selection/mix01-policy-v2 --shards G:/XLM/shards/mix01-policy-v2 --output G:/XLM/freeze/mix01-policy-v2 --issuer GammA --key-env XLM_C05_OPERATOR_KEY --workers 8 --training-input-policy training-input-policy-v2 --progress-interval 5; if ($LASTEXITCODE) { throw 'Freeze failed' }
```

10. Reverify the freeze (reads payloads again; budget the verification time).

```powershell
Set-Location F:/Project/xlm-training-input-v2; uv run --offline --locked --extra cpu --extra eval python -m xlm.data.exclusion.operator verify-freeze --c05-proof G:/XLM/c05-policy-v2/policy-v2-p0001.proof.json --freeze G:/XLM/freeze/mix01-policy-v2/freeze.json --workers 8; if ($LASTEXITCODE) { throw 'Freeze verification failed' }
```

11. Run the **actual training resolver**, without constructing a model or starting
    training. This checks signatures, current shard bytes, reconstructed coverage,
    training binding and policy, then prints aggregate and every component's document,
    token-ID and valid-target totals. `--production` refuses authored fixtures and
    requires exactly 5824661 / 6005824661 / 6000000000. Allow an estimated 5–8 minutes;
    the signed policy caps verification at 1800 seconds and 16 GiB process-tree RSS.

```powershell
Set-Location F:/Project/xlm-training-input-v2; uv run --offline --locked --extra cpu --extra eval python -m xlm.training.input_preflight --training-data G:/XLM/freeze/mix01-policy-v2/training-data.json --production; if ($LASTEXITCODE) { throw 'Production training consumer refused' }
```

12. Inspect training data and print final freeze digest, binding and policy. This is
    inspection, not a substitute for step 11's cryptographic/payload verification.

```powershell
Get-Content -LiteralPath G:/XLM/freeze/mix01-policy-v2/training-data.json; $f = Get-Content -LiteralPath G:/XLM/freeze/mix01-policy-v2/freeze.json -Raw | ConvertFrom-Json; $f.digest; $f.payload | Select-Object mode,plan_digest,completion_digest,counts_digest,selection_digest,selected_membership_sha256,tokenizer,valid_targets,training_input_policy | ConvertTo-Json -Depth 6
```

Do not edit signed JSON to retrofit a policy. An existing freeze without this policy
needs a separately named fresh freeze publication, never an overwrite. This runbook's
canonical roots are intentionally fresh. Model training remains a subsequent task with
its own config, input identity, execution resource plan and authorization.
