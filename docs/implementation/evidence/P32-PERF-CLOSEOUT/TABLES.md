# P32 performance closeout observations

All repetitions retained. MB/s uses decimal megabytes. Fsync seconds sum threads.

| State | Rep | Workers | MB/s | Wall s | CPU s | Fsync s | Peak RSS MiB |
|---|---:|---:|---:|---:|---:|---:|---:|
| baseline | 1 | 1 | 88.703 | 12.105 | 7.766 | 2.710 | 130.8 |
| baseline | 1 | 8 | 124.393 | 8.632 | 8.141 | 5.671 | 134.3 |
| baseline | 1 | 16 | 123.767 | 8.675 | 8.078 | 6.478 | 137.5 |
| baseline | 2 | 1 | 90.054 | 11.923 | 7.578 | 2.743 | 134.9 |
| baseline | 2 | 8 | 124.678 | 8.612 | 7.750 | 5.960 | 136.8 |
| baseline | 2 | 16 | 119.465 | 8.988 | 7.719 | 6.984 | 139.8 |
| baseline | 3 | 1 | 89.515 | 11.995 | 7.516 | 2.747 | 136.4 |
| baseline | 3 | 8 | 123.116 | 8.721 | 8.625 | 5.360 | 138.0 |
| baseline | 3 | 16 | 120.338 | 8.923 | 8.328 | 6.680 | 140.2 |
| current | 1 | 1 | 98.859 | 10.861 | 6.625 | 2.695 | 131.2 |
| current | 1 | 8 | 141.769 | 7.574 | 7.562 | 4.612 | 134.2 |
| current | 1 | 16 | 141.497 | 7.588 | 7.469 | 5.488 | 137.4 |
| current | 2 | 1 | 97.461 | 11.017 | 6.688 | 2.774 | 134.9 |
| current | 2 | 8 | 136.938 | 7.841 | 7.953 | 4.475 | 136.8 |
| current | 2 | 16 | 135.678 | 7.914 | 8.203 | 5.003 | 139.5 |
| current | 3 | 1 | 24.972 | 42.997 | 6.484 | 34.553 | 136.5 |
| current | 3 | 8 | 29.268 | 36.687 | 7.734 | 167.426 | 138.2 |
| current | 3 | 16 | 28.436 | 37.760 | 7.859 | 181.023 | 140.3 |

Profile timings are inclusive sums across threads, cover fetcher construction/run,
and overlap. Profiling overhead is substantial; use the unprofiled table for throughput.

| State | Workers | Component | Calls | Seconds |
|---|---:|---|---:|---:|
| baseline | 1 | filelock_wait | 575 | 0.263085 |
| baseline | 1 | journal_read | 574 | 2.093568 |
| baseline | 1 | orphan_inventory | 574 | 1.630286 |
| baseline | 1 | control_inventory | 540 | 2.609962 |
| baseline | 1 | fsync_journal | 315 | 0.220914 |
| baseline | 1 | journal_write | 315 | 2.017951 |
| baseline | 1 | final_status | 2 | 0.036847 |
| baseline | 1 | download_read | 16384 | 0.908049 |
| baseline | 1 | payload_write | 16384 | 0.521626 |
| baseline | 1 | incremental_hash | 16384 | 0.598506 |
| baseline | 1 | fsync_data | 208 | 2.491950 |
| baseline | 1 | publication_intent | 16 | 0.290894 |
| baseline | 1 | payload_verification | 32 | 1.853625 |
| baseline | 1 | hardlink_publication | 16 | 0.032921 |
| baseline | 1 | settlement_completion | 16 | 2.202310 |
| baseline | 1 | fsync_control | 1 | 0.000511 |
| baseline | 8 | filelock_wait | 582 | 0.339886 |
| baseline | 8 | journal_read | 581 | 2.187182 |
| baseline | 8 | orphan_inventory | 581 | 2.126983 |
| baseline | 8 | control_inventory | 540 | 3.353413 |
| baseline | 8 | fsync_journal | 315 | 0.514830 |
| baseline | 8 | journal_write | 315 | 2.709952 |
| baseline | 8 | final_status | 2 | 0.038016 |
| baseline | 8 | download_read | 16384 | 2.461490 |
| baseline | 8 | payload_write | 16384 | 1.472335 |
| baseline | 8 | incremental_hash | 16384 | 1.373276 |
| baseline | 8 | fsync_data | 208 | 3.103158 |
| baseline | 8 | publication_intent | 16 | 6.019018 |
| baseline | 8 | payload_verification | 32 | 1.913995 |
| baseline | 8 | hardlink_publication | 16 | 0.034953 |
| baseline | 8 | settlement_completion | 16 | 2.405059 |
| baseline | 8 | fsync_control | 1 | 0.000634 |
| baseline | 16 | filelock_wait | 590 | 0.334962 |
| baseline | 16 | journal_read | 589 | 2.187495 |
| baseline | 16 | orphan_inventory | 589 | 2.132167 |
| baseline | 16 | control_inventory | 540 | 3.360931 |
| baseline | 16 | fsync_journal | 315 | 0.453376 |
| baseline | 16 | journal_write | 315 | 2.750148 |
| baseline | 16 | final_status | 2 | 0.034004 |
| baseline | 16 | download_read | 16384 | 2.748210 |
| baseline | 16 | payload_write | 16384 | 1.650958 |
| baseline | 16 | incremental_hash | 16384 | 1.594224 |
| baseline | 16 | fsync_data | 208 | 2.707990 |
| baseline | 16 | publication_intent | 16 | 16.914682 |
| baseline | 16 | payload_verification | 32 | 1.873932 |
| baseline | 16 | hardlink_publication | 16 | 0.033320 |
| baseline | 16 | settlement_completion | 16 | 2.230918 |
| baseline | 16 | fsync_control | 1 | 0.000528 |
| current | 1 | filelock_wait | 575 | 0.297534 |
| current | 1 | journal_read | 574 | 2.158782 |
| current | 1 | orphan_inventory | 574 | 1.895851 |
| current | 1 | control_inventory | 540 | 2.992062 |
| current | 1 | fsync_journal | 315 | 5.362280 |
| current | 1 | journal_write | 315 | 7.412883 |
| current | 1 | final_status | 2 | 0.066628 |
| current | 1 | download_read | 16384 | 1.128904 |
| current | 1 | payload_write | 16384 | 0.607836 |
| current | 1 | incremental_hash | 16384 | 0.618504 |
| current | 1 | fsync_data | 208 | 22.387448 |
| current | 1 | publication_intent | 16 | 0.579996 |
| current | 1 | hardlink_publication | 16 | 0.371703 |
| current | 1 | settlement_completion | 16 | 1.014156 |
| current | 1 | fsync_control | 1 | 0.000680 |
| current | 8 | filelock_wait | 582 | 0.411190 |
| current | 8 | journal_read | 581 | 2.177503 |
| current | 8 | orphan_inventory | 581 | 2.590543 |
| current | 8 | control_inventory | 540 | 4.089967 |
| current | 8 | fsync_journal | 315 | 0.363841 |
| current | 8 | journal_write | 315 | 2.987594 |
| current | 8 | final_status | 2 | 0.036388 |
| current | 8 | download_read | 16384 | 3.058331 |
| current | 8 | payload_write | 16384 | 1.782398 |
| current | 8 | incremental_hash | 16384 | 1.578915 |
| current | 8 | fsync_data | 208 | 2.658226 |
| current | 8 | publication_intent | 16 | 3.797298 |
| current | 8 | hardlink_publication | 16 | 0.123975 |
| current | 8 | settlement_completion | 16 | 0.582445 |
| current | 8 | fsync_control | 1 | 0.000716 |
| current | 16 | filelock_wait | 590 | 0.400718 |
| current | 16 | journal_read | 589 | 2.190630 |
| current | 16 | orphan_inventory | 589 | 2.553654 |
| current | 16 | control_inventory | 540 | 3.966484 |
| current | 16 | fsync_journal | 315 | 0.464858 |
| current | 16 | journal_write | 315 | 3.297411 |
| current | 16 | final_status | 2 | 0.036479 |
| current | 16 | download_read | 16384 | 3.819416 |
| current | 16 | payload_write | 16384 | 2.285821 |
| current | 16 | incremental_hash | 16384 | 2.036491 |
| current | 16 | fsync_data | 208 | 2.739843 |
| current | 16 | publication_intent | 16 | 8.575989 |
| current | 16 | hardlink_publication | 16 | 0.133635 |
| current | 16 | settlement_completion | 16 | 0.523824 |
| current | 16 | fsync_control | 1 | 0.000575 |

| Gate | Passed | Skipped | Failed | Exit | Seconds |
|---|---:|---:|---:|---:|---:|
| focused | 177 | 1 | 0 | 0 | 124.128 |
| related | 52 | 0 | 0 | 0 | 35.603 |
| gate-tier-a | 1652 | 2 | 0 | 0 | 108.317 |
| gate-core | 68 | 0 | 0 | 0 | 429.965 |
| gate-exclusive | 8 | 0 | 0 | 0 | 30.952 |
| gate-heavy | 7 | 0 | 0 | 0 | 384.399 |
| gate-scale | 8 | 0 | 0 | 0 | 105.394 |
| gate-optional | 57 | 0 | 0 | 0 | 204.256 |
