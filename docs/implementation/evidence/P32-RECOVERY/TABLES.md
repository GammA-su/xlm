# P32 observed results

## Six-leg offline gate

Skips are unavailable capabilities, not passes. RSS is sampled and sums processes.
Pass counts exclude any node with a later failure, including a teardown worker crash.

| Leg | Pass | Skip | Fail | Runner seconds | Peak tree GiB |
|---|---:|---:|---:|---:|---:|
| tier-a | 1632 | 2 | 0 | 104.250 | 6.474 |
| core | 68 | 0 | 0 | 355.547 | 2.425 |
| exclusive | 8 | 0 | 0 | 30.329 | 1.324 |
| heavy | 6 | 0 | 1 | 337.813 | 2.823 |
| scale | 8 | 0 | 0 | 104.797 | 0.763 |
| optional | 57 | 0 | 0 | 198.407 | 2.844 |

Total: 1779 passed / 2 skipped.
Aggregate accepted: False. Failed nodes: 1.

## Recovery matrices

| Matrix | Successful restarts | Cases |
|---|---:|---:|
| early | 44 | 44 |
| mature | 44 | 44 |
| selected | 20 | 20 |
| parallel | 4 | 4 |

## Fixed 1 GiB acquisition fixture

Decimal MB/s; binary MiB RSS; summed fsync/lock seconds can exceed wall time.

| Version | Workers | MB/s | Wall s | CPU s | RSS MiB | Tx / writes | fsync calls / s | Journal wait / hold s | Capacity wait s |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| baseline | 1 | 143.581 | 7.478 | 3.406 | 130.6 | 588 / 331 | 537 / 2.663 | 0.000 / 3.142 | 0.000 |
| baseline | 8 | 227.321 | 4.723 | 3.703 | 133.6 | 595 / 331 | 537 / 10.287 | 1.164 / 4.639 | 19.291 |
| baseline | 16 | 226.137 | 4.748 | 3.672 | 136.7 | 603 / 331 | 537 / 11.183 | 1.302 / 4.680 | 53.483 |
| current | 1 | 91.263 | 11.765 | 7.562 | 130.7 | 574 / 315 | 521 / 2.708 | 0.000 / 7.401 | 0.000 |
| current | 8 | 125.916 | 8.527 | 7.891 | 133.7 | 581 / 315 | 521 / 6.487 | 4.347 / 8.479 | 41.958 |
| current | 16 | 29.326 | 36.614 | 7.594 | 136.9 | 589 / 315 | 521 / 166.420 | 5.447 / 36.559 | 382.378 |
