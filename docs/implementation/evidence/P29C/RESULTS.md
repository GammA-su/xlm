# P29C measured results

Authored fixtures only. Seconds unless indicated. Raw JSON is adjacent.

## Cleaner worker sweep

| Code | Workers | Wall s | Docs/s | Text MiB/s | Tree RSS MiB | Worker-body CPU s | Parent + worker CPU s* |
| --- | --- | --- | --- | --- | --- | --- | --- |
| before | 1 | 70.399 | 1420.5 | 1.295 | 228.0 | 60.453 | 60.891 |
| before | 2 | 45.723 | 2187.1 | 1.994 | 665.1 | 68.047 | 69.828 |
| before | 4 | 30.398 | 3289.7 | 2.999 | 1106.6 | 73.062 | 74.516 |
| before | 6 | 25.326 | 3948.5 | 3.600 | 1540.8 | 76.547 | 77.891 |
| before | 8 | 24.536 | 4075.7 | 3.716 | 1980.0 | 81.391 | 82.641 |
| after | 1 | 53.241 | 1878.2 | 1.712 | 227.7 | 45.812 | 46.312 |
| after | 2 | 37.096 | 2695.7 | 2.458 | 666.6 | 51.359 | 52.922 |
| after | 4 | 25.505 | 3920.9 | 3.574 | 1105.3 | 54.422 | 55.672 |
| after | 6 | 21.271 | 4701.2 | 4.286 | 1542.5 | 54.641 | 55.750 |
| after | 8 | 20.256 | 4936.7 | 4.501 | 1982.1 | 58.594 | 59.844 |

*One-worker body CPU is already included in parent CPU. Child bootstrap CPU is unmeasured.

## Shards, dispatch and uneven work

| Run | Shard MiB | Wall s | RSS MiB | Worker CPU s | Per-process busy s range | Sum non-CPU residual s | Dispatch-to-start range s |
| --- | --- | --- | --- | --- | --- | --- | --- |
| after-w8 | 4 | 20.256 | 1982.1 | 58.594 | 8.97–10.41 | 19.93 | 6.79–6.90 |
| dynamic-w8 | 4 | 21.876 | 1980.8 | 59.562 | 9.02–11.04 | 23.08 | 8.07–16.91 |
| shard-1 | 1 | 25.437 | 1968.0 | 61.656 | 13.41–14.34 | 48.66 | 6.54–6.61 |
| shard-16 | 16 | 22.575 | 1977.6 | 56.781 | 7.60–13.12 | 14.76 | 6.53–6.62 |
| heavy-static | 1 | 33.508 | 1151.5 | 37.125 | 12.17–21.38 | 38.40 | 8.12–8.61 |
| heavy-dynamic | 1 | 29.210 | 1150.1 | 41.516 | 16.95–17.52 | 27.93 | 8.01–25.29 |
| heavy-repeat-dynamic | 1 | 21.540 | 1156.3 | 38.047 | 13.29–13.69 | 16.12 | 5.33–18.74 |
| heavy-repeat-static | 1 | 27.182 | 1145.4 | 36.078 | 10.83–17.55 | 27.11 | 5.40–5.45 |

Busy time sums task bodies. Residual is wall minus CPU, not OS wait. Dispatch time includes planning/imports/spawn and, for dynamic tasks, queueing since run start. Pure startup and OS idle were not isolated.

## Unprofiled stage telemetry, one worker / 100k

| Stage | Before | After |
| --- | --- | --- |
| canonical_normalization | 0.859 | 0.894 |
| html_extraction | 0.409 | 0.498 |
| boilerplate_removal | 1.867 | 1.901 |
| repetition_filter | 13.339 | 7.777 |
| language_filter | 8.433 | 8.176 |
| noise_filter | 1.373 | 1.201 |
| length_filter | 0.016 | 0.000 |
| pii_secret_filter | 9.402 | 1.307 |
| parse_seconds | 2.881 | 2.309 |
| serialization_seconds | 5.311 | 4.592 |
| accepted_write_seconds | 0.730 | 0.557 |
| quarantine_seconds | 0.127 | 0.062 |
| fsync_seconds | 6.110 | 4.356 |

Filter timers do not include all post-timer bookkeeping; small values are clock-quantized. This is attribution, not a complete additive budget.

## Long documents: median of three filter calls, milliseconds

| Kind | UTF-8 bytes | Language before → after ms | Repetition before → after ms | PII before → after ms |
| --- | --- | --- | --- | --- |
| clean | 1024 | 0.076 → 0.062 | 0.117 → 0.069 | 0.083 → 0.013 |
| clean | 10240 | 0.950 → 0.497 | 0.991 → 0.598 | 0.749 → 0.078 |
| clean | 102400 | 6.197 → 6.840 | 11.925 → 7.131 | 9.091 → 0.865 |
| clean | 1048576 | 77.227 → 67.028 | 64.396 → 48.970 | 93.475 → 10.037 |
| repetitive | 1024 | 0.152 → 0.052 | 0.090 → 0.043 | 0.123 → 0.011 |
| repetitive | 10240 | 0.496 → 0.439 | 0.537 → 0.314 | 1.288 → 0.079 |
| repetitive | 102400 | 5.795 → 4.720 | 8.052 → 2.656 | 10.930 → 0.821 |
| repetitive | 1048576 | 82.174 → 51.820 | 65.548 → 19.228 | 105.516 → 7.702 |
| noisy | 1024 | 0.168 → 0.141 | 0.242 → 0.080 | 0.094 → 0.042 |
| noisy | 10240 | 2.238 → 1.513 | 2.003 → 0.652 | 1.067 → 0.362 |
| noisy | 102400 | 19.881 → 16.209 | 14.508 → 5.401 | 11.075 → 4.707 |
| noisy | 1048576 | 172.118 → 179.951 | 159.024 → 41.638 | 125.972 → 45.775 |

## Pipeline (fixture generation excluded)

| Stage | Fresh P29B / clean w1 | P29C / clean w1 | P29C / clean w8 |
| --- | --- | --- | --- |
| gzip_decode_publication | 2.541 | 2.428 | 2.900 |
| adaptation_jsonl | 7.241 | 7.157 | 7.023 |
| canonical_sharding | 10.652 | 11.149 | 10.028 |
| cleaning | 68.614 | 54.017 | 24.156 |
| split_and_publish | 12.167 | 11.162 | 11.303 |
| fixture_tokenizer_fit | 0.090 | 0.096 | 0.073 |
| tokenization_shards | 55.276 | 54.680 | 52.375 |
| token_verification | 0.450 | 0.450 | 0.450 |
| mmap_packing | 3.427 | 3.211 | 3.275 |
| loader_dummy_consume | 0.101 | 0.367 | 0.105 |
| TOTAL | 160.558 | 144.719 | 111.688 |
| Peak tree RSS MiB | 2153.5 | 2119.9 | 2122.1 |

All token stages use eight workers. The pipeline loader is the same 16-step dummy consumer; the historical P29B 50-step mixture-loader probe (5.062 s) is a separate benchmark and is not included here.
