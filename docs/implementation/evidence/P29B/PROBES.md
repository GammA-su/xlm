# Separate encode probes

Unframed tokens; authored accepted documents. These are sequential diagnostics, not matched whole-pipeline runs.

## 10,000 documents / 3,726,824 tokens

| Operation | Seconds | Tokens/s | Docs/s | Sampled RSS MiB |
|---|---:|---:|---:|---:|
| native_encode | 3.717 | 1,002,774 | 2690.7 | 269.1 |
| ids_only | 3.913 | 952,480 | 2555.7 | 269.2 |
| with_offsets | 5.003 | 744,866 | 1998.7 | 269.8 |
| native_batch_16 | 3.643 | 1,022,897 | 2744.7 | 270.7 |
| native_batch_64 | 3.829 | 973,280 | 2611.6 | 274.6 |
| native_batch_128 | 3.092 | 1,205,160 | 3233.7 | 280.4 |
| native_batch_512 | 3.238 | 1,151,024 | 3088.5 | 309.2 |

## 97,872 documents / 36,482,469 tokens

| Operation | Seconds | Tokens/s | Docs/s | Sampled RSS MiB |
|---|---:|---:|---:|---:|
| native_encode | 40.863 | 892,789 | 2395.1 | 680.0 |
| ids_only | 36.396 | 1,002,365 | 2689.1 | 680.0 |
| with_offsets | 48.360 | 754,394 | 2023.8 | 680.1 |
| native_batch_16 | 35.980 | 1,013,967 | 2720.2 | 681.7 |
| native_batch_64 | 36.651 | 995,413 | 2670.4 | 685.9 |
| native_batch_128 | 36.041 | 1,012,247 | 2715.6 | 693.1 |
| native_batch_512 | 44.305 | 823,438 | 2209.0 | 724.9 |

Probe RSS includes the intentionally materialized authored document list. Product shard workers stream documents. Binary-block probes operate on a repeated bounded 128-document prefix; do not treat their duration as writing a full 100k shard.
