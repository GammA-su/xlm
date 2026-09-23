# 100k resource comparison (final code)

Parent process counters; MiB = 1048576 bytes. No physical-disk or GPU claim.

| Stage | CPU seconds before / after | Read MiB before / after | Write MiB before / after | fsync before / after |
|---|---:|---:|---:|---:|
| gzip_decode_publication | 1.406 / 1.406 | 26.21 / 26.21 | 106.77 / 106.77 | 1 / 1 |
| adaptation_jsonl | 6.875 / 6.859 | 106.77 / 106.77 | 161.34 / 161.34 | 0 / 0 |
| canonical_sharding | 6.203 / 6.016 | 322.69 / 322.69 | 161.36 / 161.36 | 42 / 42 |
| cleaning | 76.609 / 58.516 | 500.62 / 500.62 | 353.99 / 353.99 | 87 / 87 |
| split_and_publish | 11.547 / 10.844 | 348.79 / 348.79 | 178.22 / 178.22 | 0 / 0 |
| fixture_tokenizer_fit | 0.047 / 0.094 | 0.47 / 0.47 | 0.02 / 0.02 | 0 / 0 |
| tokenization_shards | 103.219 / 68.312 | 178.22 / 178.22 | 427.74 / 427.74 | 0 / 0 |
| token_verification | 0.344 / 0.344 | 427.74 / 427.74 | 0.00 / 0.00 | 0 / 0 |
| mmap_packing | 4.797 / 4.719 | 0.00 / 0.00 | 0.00 / 0.00 | 0 / 0 |
| loader_dummy_consume | 0.109 / 0.094 | 2.12 / 2.12 | 0.00 / 0.00 | 0 / 0 |

before: total parent CPU 211.156 s; throughput 437.1 input docs/s, 0.398 canonical-input MiB/s; final artifact bytes 1,297,931,371.

after: total parent CPU 157.203 s; throughput 576.9 input docs/s, 0.526 canonical-input MiB/s; final artifact bytes 1,297,931,361.
