# P29B measured results

All seconds are observed single-run fixture results; no confidence interval or production claim.

## Matched 10k

| Stage | Before s | After s | Before RSS MiB | After RSS MiB | Parent fsync before/after |
|---|---:|---:|---:|---:|---:|
| fixture_generation | 1.628 | 1.575 | 216.9 | 216.5 | 0/0 |
| gzip_decode_publication | 0.407 | 0.318 | 217.0 | 216.7 | 1/1 |
| adaptation_jsonl | 0.725 | 0.730 | 217.8 | 217.4 | 0/0 |
| canonical_sharding | 1.273 | 1.170 | 219.5 | 217.5 | 6/6 |
| cleaning | 7.585 | 7.575 | 222.7 | 220.9 | 15/15 |
| split_and_publish | 1.096 | 1.093 | 226.3 | 224.7 | 0/0 |
| fixture_tokenizer_fit | 0.094 | 0.093 | 226.3 | 224.7 | 0/0 |
| tokenization_shards | 8.316 | 8.847 | 233.7 | 233.5 | 0/4 |
| token_verification | 0.055 | 0.058 | 233.7 | 233.5 | 0/0 |
| mmap_packing | 0.592 | 0.338 | 239.7 | 238.6 | 0/0 |
| loader_dummy_consume | 0.106 | 0.106 | 235.2 | 235.0 | 0/0 |

Pipeline **20.250 -> 20.328 s**, 0.996x; 0.39% more wall time.
Input documents/s: 493.8 -> 491.9.

before: 3,667,412 framed tokens, 9,787 accepted docs; token stage 440,989 tokens/s, 1176.8 docs/s. Parent CPU total 18.328 s; final bytes 142,475,820; sampled disk peak 142,475,820 bytes.
after: 3,667,412 framed tokens, 9,787 accepted docs; token stage 414,551 tokens/s, 1106.3 docs/s. Parent CPU total 17.734 s; final bytes 142,475,791; sampled disk peak 142,475,791 bytes.

Parent counters exclude children; RSS sums process working sets and may count shared pages repeatedly.

## Matched 100k

| Stage | Before s | After s | Before RSS MiB | After RSS MiB | Parent fsync before/after |
|---|---:|---:|---:|---:|---:|
| fixture_generation | 15.763 | 15.911 | 216.1 | 217.2 | 0/0 |
| gzip_decode_publication | 2.291 | 2.340 | 216.2 | 217.3 | 1/1 |
| adaptation_jsonl | 6.993 | 7.038 | 225.2 | 226.6 | 0/0 |
| canonical_sharding | 10.329 | 9.886 | 227.3 | 228.1 | 42/42 |
| cleaning | 71.111 | 71.566 | 235.5 | 236.0 | 87/87 |
| split_and_publish | 11.327 | 11.655 | 298.9 | 297.8 | 0/0 |
| fixture_tokenizer_fit | 0.078 | 0.094 | 293.4 | 294.1 | 0/0 |
| tokenization_shards | 83.222 | 51.950 | 299.6 | 2137.1 | 0/51 |
| token_verification | 0.423 | 0.443 | 299.6 | 297.4 | 0/0 |
| mmap_packing | 6.262 | 3.369 | 368.8 | 367.4 | 0/0 |
| loader_dummy_consume | 0.107 | 0.090 | 300.0 | 297.4 | 0/0 |

Pipeline **192.142 -> 158.430 s**, 1.213x; 17.54% less wall time.
Input documents/s: 520.4 -> 631.2.

before: 36,678,213 framed tokens, 97,872 accepted docs; token stage 440,730 tokens/s, 1176.0 docs/s. Parent CPU total 179.875 s; final bytes 1,407,969,938; sampled disk peak 1,407,969,938 bytes.
after: 36,678,213 framed tokens, 97,872 accepted docs; token stage 706,024 tokens/s, 1884.0 docs/s. Parent CPU total 107.734 s; final bytes 1,407,969,933; sampled disk peak 2,150,931,249 bytes.

Parent counters exclude children; RSS sums process working sets and may count shared pages repeatedly.

## Shard-only worker/batch matrix

Batch experiments use 128 documents. Native threads default off unless labeled.

| Variant | Wall s | Worker CPU s | RSS MiB | Mean init s | Tokens/s | Docs/s | Output MiB/s |
|---|---:|---:|---:|---:|---:|---:|---:|
| scale-w1 | 94.833 | 72.172 | 229.1 | 0.057 | 386,765 | 1032.0 | 5.59 |
| scale-w2 | 67.181 | 88.812 | 672.3 | 0.080 | 545,960 | 1456.8 | 7.89 |
| scale-w4 | 43.146 | 93.562 | 1128.6 | 0.087 | 850,098 | 2268.4 | 12.29 |
| scale-w8 | 30.930 | 92.141 | 2074.7 | 0.108 | 1,185,854 | 3164.3 | 17.14 |
| batch-w1 | 94.269 | 72.344 | 231.1 | 0.057 | 389,079 | 1038.2 | 5.63 |
| batch-w8 | 30.409 | 93.438 | 2065.5 | 0.100 | 1,206,148 | 3218.5 | 17.44 |
| batch-w1-t8 | 67.638 | 88.797 | 242.4 | 0.060 | 542,269 | 1447.0 | 7.84 |
| batch-w4-t2 | 36.703 | 92.266 | 1141.3 | 0.080 | 999,327 | 2666.6 | 14.45 |

These timings exclude final single-shard assembly and input sharding; use the integrated table for end-to-end claims.

## Final mixture loader

| Mode | 50 steps s | Steps/s | Admission s | RSS MiB |
|---|---:|---:|---:|---:|
| before, cache=0 | 9.458 | 5.286 | 0.000 | 47.9 |
| before, cache=8 | 9.606 | 5.205 | 0.473 | 50.7 |
| final, cache=0 | 5.062 | 9.877 | 0.000 | 47.4 |
| final, cache=8 | 5.823 | 8.587 | 0.484 | 50.8 |

Batch and cursor hashing excluded from timed work; every digest matched.

## Retained disk

P29B retained bytes: 13,565,677,933; all artifacts: 19,636,465,098; disk free at collection: 136,785,317,888 bytes. This includes diagnostic repetitions; no corpus is committed.
