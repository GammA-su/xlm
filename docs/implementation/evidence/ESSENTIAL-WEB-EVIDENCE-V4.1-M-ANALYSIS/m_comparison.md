# Essential-Web M selector evidence: development versus M (generated)

Evidence reporting only. No policy is ranked and no selector decision is made.

Descriptive only. Both replicates are one contiguous 512-row window per crawl/file: clustered, not iid. Protocol section 5 forbids binomial iid confidence intervals, eight-window bootstrap confidence claims and significance claims, so none is reported; uncertainty is shown as per-crawl ranges, spreads and matched-crawl differences.

`dev` = frozen development sweep-v2; `M` = Phase-D confirmation replicate.
Counts use 4096 rows per replicate and 512 rows per crawl.

## 1. Final assignments (count, share of 4096)

| condition | component | dev | M | dev share | M share | M-dev | M-dev pp |
|---|---|---:|---:|---:|---:|---:|---:|
| A-normal | science | 19 | 17 | 0.46% | 0.41% | -2 | -0.049 pp |
| A-normal | practical | 108 | 117 | 2.64% | 2.86% | +9 | +0.220 pp |
| A-normal | prose | 377 | 374 | 9.20% | 9.13% | -3 | -0.073 pp |
| A-normal | unassigned | 40 | 50 | 0.98% | 1.22% | +10 | +0.244 pp |
| A-normal | rejected | 3552 | 3538 | 86.72% | 86.38% | -14 | -0.342 pp |
| A-strict | science | 12 | 7 | 0.29% | 0.17% | -5 | -0.122 pp |
| A-strict | practical | 62 | 68 | 1.51% | 1.66% | +6 | +0.146 pp |
| A-strict | prose | 334 | 344 | 8.15% | 8.40% | +10 | +0.244 pp |
| A-strict | unassigned | 28 | 38 | 0.68% | 0.93% | +10 | +0.244 pp |
| A-strict | rejected | 3660 | 3639 | 89.36% | 88.84% | -21 | -0.513 pp |
| B-normal | science | 29 | 24 | 0.71% | 0.59% | -5 | -0.122 pp |
| B-normal | practical | 108 | 117 | 2.64% | 2.86% | +9 | +0.220 pp |
| B-normal | prose | 371 | 372 | 9.06% | 9.08% | +1 | +0.024 pp |
| B-normal | unassigned | 36 | 45 | 0.88% | 1.10% | +9 | +0.220 pp |
| B-normal | rejected | 3552 | 3538 | 86.72% | 86.38% | -14 | -0.342 pp |
| B-strict | science | 20 | 11 | 0.49% | 0.27% | -9 | -0.220 pp |
| B-strict | practical | 62 | 68 | 1.51% | 1.66% | +6 | +0.146 pp |
| B-strict | prose | 329 | 342 | 8.03% | 8.35% | +13 | +0.317 pp |
| B-strict | unassigned | 25 | 36 | 0.61% | 0.88% | +11 | +0.268 pp |
| B-strict | rejected | 3660 | 3639 | 89.36% | 88.84% | -21 | -0.513 pp |
| C-normal | science | 29 | 24 | 0.71% | 0.59% | -5 | -0.122 pp |
| C-normal | practical | 41 | 46 | 1.00% | 1.12% | +5 | +0.122 pp |
| C-normal | prose | 120 | 127 | 2.93% | 3.10% | +7 | +0.171 pp |
| C-normal | unassigned | 354 | 361 | 8.64% | 8.81% | +7 | +0.171 pp |
| C-normal | rejected | 3552 | 3538 | 86.72% | 86.38% | -14 | -0.342 pp |
| C-strict | science | 20 | 11 | 0.49% | 0.27% | -9 | -0.220 pp |
| C-strict | practical | 22 | 30 | 0.54% | 0.73% | +8 | +0.195 pp |
| C-strict | prose | 102 | 111 | 2.49% | 2.71% | +9 | +0.220 pp |
| C-strict | unassigned | 292 | 305 | 7.13% | 7.45% | +13 | +0.317 pp |
| C-strict | rejected | 3660 | 3639 | 89.36% | 88.84% | -21 | -0.513 pp |
| D-normal | science | 54 | 56 | 1.32% | 1.37% | +2 | +0.049 pp |
| D-normal | practical | 279 | 257 | 6.81% | 6.27% | -22 | -0.537 pp |
| D-normal | prose | 719 | 717 | 17.55% | 17.50% | -2 | -0.049 pp |
| D-normal | unassigned | 110 | 118 | 2.69% | 2.88% | +8 | +0.195 pp |
| D-normal | rejected | 2934 | 2948 | 71.63% | 71.97% | +14 | +0.342 pp |
| D-strict | science | 20 | 11 | 0.49% | 0.27% | -9 | -0.220 pp |
| D-strict | practical | 62 | 68 | 1.51% | 1.66% | +6 | +0.146 pp |
| D-strict | prose | 329 | 342 | 8.03% | 8.35% | +13 | +0.317 pp |
| D-strict | unassigned | 25 | 36 | 0.61% | 0.88% | +11 | +0.268 pp |
| D-strict | rejected | 3660 | 3639 | 89.36% | 88.84% | -21 | -0.513 pp |

## 2. Per-crawl counts (dev / M, of 512)

### A-normal

| component | 2014-15 | 2015-32 | 2016-50 | 2018-05 | 2019-09 | 2021-04 | 2021-49 | 2024-26 | dev min-max | M min-max | max abs diff pp |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| science | 2 / 4 | 1 / 0 | 0 / 3 | 1 / 1 | 2 / 0 | 2 / 1 | 3 / 4 | 8 / 4 | 0-8 | 0-4 | 0.781 |
| practical | 10 / 4 | 8 / 10 | 6 / 10 | 11 / 16 | 17 / 18 | 20 / 19 | 20 / 20 | 16 / 20 | 6-20 | 4-20 | 1.172 |
| prose | 26 / 39 | 32 / 35 | 43 / 32 | 56 / 49 | 51 / 48 | 56 / 63 | 66 / 65 | 47 / 43 | 26-66 | 32-65 | 2.539 |
| unassigned | 6 / 5 | 5 / 5 | 5 / 4 | 3 / 1 | 4 / 7 | 9 / 14 | 5 / 6 | 3 / 8 | 3-9 | 1-14 | 0.977 |
| rejected | 468 / 460 | 466 / 462 | 458 / 463 | 441 / 445 | 438 / 439 | 425 / 415 | 418 / 417 | 438 / 437 | 418-468 | 415-463 | 1.953 |

### A-strict

| component | 2014-15 | 2015-32 | 2016-50 | 2018-05 | 2019-09 | 2021-04 | 2021-49 | 2024-26 | dev min-max | M min-max | max abs diff pp |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| science | 2 / 2 | 1 / 0 | 0 / 1 | 0 / 1 | 2 / 0 | 0 / 1 | 2 / 0 | 5 / 2 | 0-5 | 0-2 | 0.586 |
| practical | 6 / 3 | 2 / 7 | 4 / 3 | 7 / 9 | 14 / 11 | 7 / 9 | 15 / 12 | 7 / 14 | 2-15 | 3-14 | 1.367 |
| prose | 20 / 39 | 31 / 30 | 38 / 27 | 51 / 45 | 44 / 43 | 50 / 59 | 60 / 61 | 40 / 40 | 20-60 | 27-61 | 3.711 |
| unassigned | 4 / 1 | 4 / 3 | 2 / 4 | 2 / 1 | 4 / 5 | 5 / 13 | 4 / 5 | 3 / 6 | 2-5 | 1-13 | 1.562 |
| rejected | 480 / 467 | 474 / 472 | 468 / 477 | 452 / 456 | 448 / 453 | 450 / 430 | 431 / 434 | 457 / 450 | 431-480 | 430-477 | 3.906 |

### B-normal

| component | 2014-15 | 2015-32 | 2016-50 | 2018-05 | 2019-09 | 2021-04 | 2021-49 | 2024-26 | dev min-max | M min-max | max abs diff pp |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| science | 4 / 5 | 1 / 0 | 0 / 4 | 1 / 1 | 2 / 1 | 4 / 3 | 7 / 5 | 10 / 5 | 0-10 | 0-5 | 0.977 |
| practical | 10 / 4 | 8 / 10 | 6 / 10 | 11 / 16 | 17 / 18 | 20 / 19 | 20 / 20 | 16 / 20 | 6-20 | 4-20 | 1.172 |
| prose | 26 / 39 | 32 / 35 | 43 / 31 | 56 / 49 | 51 / 47 | 55 / 63 | 63 / 65 | 45 / 43 | 26-63 | 31-65 | 2.539 |
| unassigned | 4 / 4 | 5 / 5 | 5 / 4 | 3 / 1 | 4 / 7 | 8 / 12 | 4 / 5 | 3 / 7 | 3-8 | 1-12 | 0.781 |
| rejected | 468 / 460 | 466 / 462 | 458 / 463 | 441 / 445 | 438 / 439 | 425 / 415 | 418 / 417 | 438 / 437 | 418-468 | 415-463 | 1.953 |

### B-strict

| component | 2014-15 | 2015-32 | 2016-50 | 2018-05 | 2019-09 | 2021-04 | 2021-49 | 2024-26 | dev min-max | M min-max | max abs diff pp |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| science | 4 / 2 | 1 / 0 | 0 / 2 | 0 / 1 | 2 / 1 | 1 / 2 | 6 / 1 | 6 / 2 | 0-6 | 0-2 | 0.977 |
| practical | 6 / 3 | 2 / 7 | 4 / 3 | 7 / 9 | 14 / 11 | 7 / 9 | 15 / 12 | 7 / 14 | 2-15 | 3-14 | 1.367 |
| prose | 20 / 39 | 31 / 30 | 38 / 26 | 51 / 45 | 44 / 42 | 49 / 59 | 57 / 61 | 39 / 40 | 20-57 | 26-61 | 3.711 |
| unassigned | 2 / 1 | 4 / 3 | 2 / 4 | 2 / 1 | 4 / 5 | 5 / 12 | 3 / 4 | 3 / 6 | 2-5 | 1-12 | 1.367 |
| rejected | 480 / 467 | 474 / 472 | 468 / 477 | 452 / 456 | 448 / 453 | 450 / 430 | 431 / 434 | 457 / 450 | 431-480 | 430-477 | 3.906 |

### C-normal

| component | 2014-15 | 2015-32 | 2016-50 | 2018-05 | 2019-09 | 2021-04 | 2021-49 | 2024-26 | dev min-max | M min-max | max abs diff pp |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| science | 4 / 5 | 1 / 0 | 0 / 4 | 1 / 1 | 2 / 1 | 4 / 3 | 7 / 5 | 10 / 5 | 0-10 | 0-5 | 0.977 |
| practical | 7 / 1 | 2 / 4 | 2 / 4 | 4 / 6 | 8 / 10 | 8 / 7 | 5 / 7 | 5 / 7 | 2-8 | 1-10 | 1.172 |
| prose | 16 / 9 | 8 / 11 | 13 / 11 | 21 / 17 | 14 / 20 | 15 / 24 | 24 / 23 | 9 / 12 | 8-24 | 9-24 | 1.758 |
| unassigned | 17 / 37 | 35 / 35 | 39 / 30 | 45 / 43 | 50 / 42 | 60 / 63 | 58 / 60 | 50 / 51 | 17-60 | 30-63 | 3.906 |
| rejected | 468 / 460 | 466 / 462 | 458 / 463 | 441 / 445 | 438 / 439 | 425 / 415 | 418 / 417 | 438 / 437 | 418-468 | 415-463 | 1.953 |

### C-strict

| component | 2014-15 | 2015-32 | 2016-50 | 2018-05 | 2019-09 | 2021-04 | 2021-49 | 2024-26 | dev min-max | M min-max | max abs diff pp |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| science | 4 / 2 | 1 / 0 | 0 / 2 | 0 / 1 | 2 / 1 | 1 / 2 | 6 / 1 | 6 / 2 | 0-6 | 0-2 | 0.977 |
| practical | 4 / 1 | 0 / 2 | 1 / 1 | 2 / 4 | 6 / 7 | 3 / 5 | 3 / 5 | 3 / 5 | 0-6 | 1-7 | 0.586 |
| prose | 11 / 9 | 8 / 8 | 12 / 8 | 18 / 14 | 12 / 19 | 11 / 23 | 22 / 20 | 8 / 10 | 8-22 | 8-23 | 2.344 |
| unassigned | 13 / 33 | 29 / 30 | 31 / 24 | 40 / 37 | 44 / 32 | 47 / 52 | 50 / 52 | 38 / 45 | 13-50 | 24-52 | 3.906 |
| rejected | 480 / 467 | 474 / 472 | 468 / 477 | 452 / 456 | 448 / 453 | 450 / 430 | 431 / 434 | 457 / 450 | 431-480 | 430-477 | 3.906 |

### D-normal

| component | 2014-15 | 2015-32 | 2016-50 | 2018-05 | 2019-09 | 2021-04 | 2021-49 | 2024-26 | dev min-max | M min-max | max abs diff pp |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| science | 8 / 7 | 6 / 2 | 3 / 9 | 3 / 3 | 6 / 7 | 5 / 7 | 10 / 9 | 13 / 12 | 3-13 | 2-12 | 1.172 |
| practical | 39 / 16 | 28 / 40 | 40 / 48 | 33 / 26 | 34 / 33 | 37 / 30 | 36 / 33 | 32 / 31 | 28-40 | 16-48 | 4.492 |
| prose | 108 / 98 | 82 / 102 | 77 / 68 | 109 / 87 | 82 / 88 | 98 / 97 | 87 / 100 | 76 / 77 | 76-109 | 68-102 | 4.297 |
| unassigned | 13 / 25 | 29 / 22 | 21 / 18 | 7 / 6 | 10 / 10 | 12 / 17 | 11 / 9 | 7 / 11 | 7-29 | 6-25 | 2.344 |
| rejected | 344 / 366 | 367 / 346 | 371 / 369 | 360 / 390 | 380 / 374 | 360 / 361 | 368 / 361 | 384 / 381 | 344-384 | 346-390 | 5.859 |

### D-strict

| component | 2014-15 | 2015-32 | 2016-50 | 2018-05 | 2019-09 | 2021-04 | 2021-49 | 2024-26 | dev min-max | M min-max | max abs diff pp |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| science | 4 / 2 | 1 / 0 | 0 / 2 | 0 / 1 | 2 / 1 | 1 / 2 | 6 / 1 | 6 / 2 | 0-6 | 0-2 | 0.977 |
| practical | 6 / 3 | 2 / 7 | 4 / 3 | 7 / 9 | 14 / 11 | 7 / 9 | 15 / 12 | 7 / 14 | 2-15 | 3-14 | 1.367 |
| prose | 20 / 39 | 31 / 30 | 38 / 26 | 51 / 45 | 44 / 42 | 49 / 59 | 57 / 61 | 39 / 40 | 20-57 | 26-61 | 3.711 |
| unassigned | 2 / 1 | 4 / 3 | 2 / 4 | 2 / 1 | 4 / 5 | 5 / 12 | 3 / 4 | 3 / 6 | 2-5 | 1-12 | 1.367 |
| rejected | 480 / 467 | 474 / 472 | 468 / 477 | 452 / 456 | 448 / 453 | 450 / 430 | 431 / 434 | 457 / 450 | 431-480 | 430-477 | 3.906 |

## 3. Strict-versus-normal attrition

| policy | component | dev normal | dev strict | dev retained | M normal | M strict | M retained | retained diff pp |
|---|---|---:|---:|---:|---:|---:|---:|---:|
| A | science | 19 | 12 | 63.16% | 17 | 7 | 41.18% | -21.981 pp |
| A | practical | 108 | 62 | 57.41% | 117 | 68 | 58.12% | +0.712 pp |
| A | prose | 377 | 334 | 88.59% | 374 | 344 | 91.98% | +3.384 pp |
| A | selected total | 504 | 408 | 80.95% | 508 | 419 | 82.48% | +1.528 pp |
| B | science | 29 | 20 | 68.97% | 24 | 11 | 45.83% | -23.132 pp |
| B | practical | 108 | 62 | 57.41% | 117 | 68 | 58.12% | +0.712 pp |
| B | prose | 371 | 329 | 88.68% | 372 | 342 | 91.94% | +3.256 pp |
| B | selected total | 508 | 411 | 80.91% | 513 | 421 | 82.07% | +1.161 pp |
| C | science | 29 | 20 | 68.97% | 24 | 11 | 45.83% | -23.132 pp |
| C | practical | 41 | 22 | 53.66% | 46 | 30 | 65.22% | +11.559 pp |
| C | prose | 120 | 102 | 85.00% | 127 | 111 | 87.40% | +2.402 pp |
| C | selected total | 190 | 144 | 75.79% | 197 | 152 | 77.16% | +1.368 pp |
| D | science | 54 | 20 | 37.04% | 56 | 11 | 19.64% | -17.394 pp |
| D | practical | 279 | 62 | 22.22% | 257 | 68 | 26.46% | +4.237 pp |
| D | prose | 719 | 329 | 45.76% | 717 | 342 | 47.70% | +1.941 pp |
| D | selected total | 1052 | 411 | 39.07% | 1030 | 421 | 40.87% | +1.805 pp |

## 4. Policy-pair count changes (second minus first)

| pair | tier | replicate | science | practical | prose | unassigned | rejected | additions | removals | reassignments |
|---|---|---|---:|---:|---:|---:|---:|---:|---:|---:|
| A vs B | normal | dev | +10 | +0 | -6 | -4 | +0 | 4 | 0 | 6 |
| A vs B | normal | M | +7 | +0 | -2 | -5 | +0 | 5 | 0 | 2 |
| A vs B | strict | dev | +8 | +0 | -5 | -3 | +0 | 3 | 0 | 5 |
| A vs B | strict | M | +4 | +0 | -2 | -2 | +0 | 2 | 0 | 2 |
| B vs C | normal | dev | +0 | -67 | -251 | +318 | +0 | 0 | 318 | 0 |
| B vs C | normal | M | +0 | -71 | -245 | +316 | +0 | 0 | 316 | 0 |
| B vs C | strict | dev | +0 | -40 | -227 | +267 | +0 | 0 | 267 | 0 |
| B vs C | strict | M | +0 | -38 | -231 | +269 | +0 | 0 | 269 | 0 |
| B vs D | normal | dev | +25 | +171 | +348 | +74 | -618 | 544 | 0 | 0 |
| B vs D | normal | M | +32 | +140 | +345 | +73 | -590 | 517 | 0 | 0 |
| B vs D | strict | dev | +0 | +0 | +0 | +0 | +0 | 0 | 0 | 0 |
| B vs D | strict | M | +0 | +0 | +0 | +0 | +0 | 0 | 0 | 0 |

### M row transition matrices (rows moved between finals)

- A vs B / normal: essential_prose->essential_science: 2, unassigned->essential_science: 5
- A vs B / strict: essential_prose->essential_science: 2, unassigned->essential_science: 2
- B vs C / normal: essential_practical->unassigned: 71, essential_prose->unassigned: 245
- B vs C / strict: essential_practical->unassigned: 38, essential_prose->unassigned: 231
- B vs D / normal: rejected->essential_practical: 140, rejected->essential_prose: 345, rejected->essential_science: 32, rejected->unassigned: 73
- B vs D / strict: no row changes

## 5. Gate waterfall (sequential; rows surviving each stage)

| condition | replicate | input | valid | english | artifacts | missing | correctness | doctype | joint |
|---|---|---:|---:|---:|---:|---:|---:|---:|---:|
| A-normal | dev | 4096 | 4095 | 3125 | 1439 | 1351 | 1348 | 544 | 544 |
| A-normal | M | 4096 | 4096 | 3125 | 1474 | 1367 | 1366 | 558 | 558 |
| A-strict | dev | 4096 | 4095 | 1911 | 925 | 838 | 836 | 436 | 436 |
| A-strict | M | 4096 | 4096 | 1835 | 948 | 853 | 852 | 457 | 457 |
| B-normal | dev | 4096 | 4095 | 3125 | 1439 | 1351 | 1348 | 544 | 544 |
| B-normal | M | 4096 | 4096 | 3125 | 1474 | 1367 | 1366 | 558 | 558 |
| B-strict | dev | 4096 | 4095 | 1911 | 925 | 838 | 836 | 436 | 436 |
| B-strict | M | 4096 | 4096 | 1835 | 948 | 853 | 852 | 457 | 457 |
| C-normal | dev | 4096 | 4095 | 3125 | 1439 | 1351 | 1348 | 544 | 544 |
| C-normal | M | 4096 | 4096 | 3125 | 1474 | 1367 | 1366 | 558 | 558 |
| C-strict | dev | 4096 | 4095 | 1911 | 925 | 838 | 836 | 436 | 436 |
| C-strict | M | 4096 | 4096 | 1835 | 948 | 853 | 852 | 457 | 457 |
| D-normal | dev | 4096 | 4095 | 3125 | 3107 | 2600 | 2590 | 1162 | 1162 |
| D-normal | M | 4096 | 4096 | 3125 | 3108 | 2595 | 2587 | 1148 | 1148 |
| D-strict | dev | 4096 | 4095 | 1911 | 925 | 838 | 836 | 436 | 436 |
| D-strict | M | 4096 | 4096 | 1835 | 948 | 853 | 852 | 457 | 457 |

### M gate failures over valid rows (marginal / sole)

| gate | reason | marginal | sole |
|---|---|---:|---:|
| GN | gate_artifacts | 2258 | 600 |
| GN | gate_correctness | 15 | 0 |
| GN | gate_doctype | 2557 | 808 |
| GN | gate_english | 971 | 49 |
| GN | gate_missing_content | 659 | 51 |
| GS | gate_artifacts | 2258 | 303 |
| GS | gate_correctness | 15 | 0 |
| GS | gate_doctype | 2557 | 395 |
| GS | gate_english | 2261 | 119 |
| GS | gate_missing_content | 1641 | 54 |
| GD | gate_artifacts | 36 | 10 |
| GD | gate_correctness | 15 | 5 |
| GD | gate_doctype | 2557 | 1439 |
| GD | gate_english | 971 | 140 |
| GD | gate_missing_content | 659 | 203 |

B normal-to-strict losses by GS reason (M):

- practical: gate_english: 41, gate_english+gate_missing_content: 3, gate_missing_content: 5
- prose: gate_english: 17, gate_english+gate_missing_content: 1, gate_missing_content: 12
- science: gate_english: 10, gate_english+gate_missing_content: 2, gate_missing_content: 1
- unassigned: gate_english: 9

## 6. Input composition drift (share of rows with a validated value)

| classifier | label | dev | M | dev share | M share | M-dev pp |
|---|---|---:|---:|---:|---:|---:|
| doctype | About (Org.) | 76 | 95 | 1.86% | 2.32% | +0.464 pp |
| doctype | About (Personal) | 110 | 108 | 2.69% | 2.64% | -0.049 pp |
| doctype | Academic Writing | 76 | 80 | 1.86% | 1.95% | +0.098 pp |
| doctype | Audio Transcript | 20 | 25 | 0.49% | 0.61% | +0.122 pp |
| doctype | Comment Section | 100 | 92 | 2.44% | 2.25% | -0.195 pp |
| doctype | Content Listing | 333 | 325 | 8.13% | 7.93% | -0.195 pp |
| doctype | Creative Writing | 46 | 33 | 1.12% | 0.81% | -0.317 pp |
| doctype | Customer Support | 20 | 17 | 0.49% | 0.41% | -0.073 pp |
| doctype | Documentation | 112 | 111 | 2.73% | 2.71% | -0.024 pp |
| doctype | FAQ | 13 | 13 | 0.32% | 0.32% | +0.000 pp |
| doctype | Knowledge Article | 165 | 172 | 4.03% | 4.20% | +0.171 pp |
| doctype | Legal Notices | 19 | 23 | 0.46% | 0.56% | +0.098 pp |
| doctype | Listicle | 66 | 68 | 1.61% | 1.66% | +0.049 pp |
| doctype | News (Org.) | 328 | 333 | 8.01% | 8.13% | +0.122 pp |
| doctype | News Article | 491 | 481 | 11.99% | 11.74% | -0.244 pp |
| doctype | Nonfiction Writing | 10 | 12 | 0.24% | 0.29% | +0.049 pp |
| doctype | Other/Unclassified | 1 | 1 | 0.02% | 0.02% | +0.000 pp |
| doctype | Personal Blog | 269 | 299 | 6.57% | 7.30% | +0.732 pp |
| doctype | Product Page | 1291 | 1275 | 31.52% | 31.13% | -0.391 pp |
| doctype | Q&A Forum | 222 | 217 | 5.42% | 5.30% | -0.122 pp |
| doctype | Spam / Ads | 3 | 1 | 0.07% | 0.02% | -0.049 pp |
| doctype | Structured Data | 95 | 87 | 2.32% | 2.12% | -0.195 pp |
| doctype | Truncated | 16 | 19 | 0.39% | 0.46% | +0.073 pp |
| doctype | Tutorial | 116 | 104 | 2.83% | 2.54% | -0.293 pp |
| doctype | User Review | 98 | 105 | 2.39% | 2.56% | +0.171 pp |
| knowledge | Conceptual | 1223 | 1248 | 29.86% | 30.47% | +0.610 pp |
| knowledge | Factual | 2442 | 2454 | 59.62% | 59.91% | +0.293 pp |
| knowledge | Procedural | 431 | 394 | 10.52% | 9.62% | -0.903 pp |
| artifacts | Irrelevant Content | 2233 | 2222 | 54.52% | 54.25% | -0.269 pp |
| artifacts | Leftover HTML | 32 | 31 | 0.78% | 0.76% | -0.024 pp |
| artifacts | No Artifacts | 1830 | 1838 | 44.68% | 44.87% | +0.195 pp |
| artifacts | Text Extraction Errors | 1 | 5 | 0.02% | 0.12% | +0.098 pp |
| missing_content | Click Here References | 283 | 279 | 6.91% | 6.81% | -0.098 pp |
| missing_content | Incoherent Flow | 11 | 8 | 0.27% | 0.20% | -0.073 pp |
| missing_content | Indeterminate | 3 | 5 | 0.07% | 0.12% | +0.049 pp |
| missing_content | Missing Images or Figures | 933 | 982 | 22.78% | 23.97% | +1.196 pp |
| missing_content | Missing Referenced Data | 1 | 6 | 0.02% | 0.15% | +0.122 pp |
| missing_content | No missing content | 2516 | 2455 | 61.43% | 59.94% | -1.489 pp |
| missing_content | Truncated Snippets | 349 | 361 | 8.52% | 8.81% | +0.293 pp |
| correctness | Highly Correct | 676 | 622 | 16.50% | 15.19% | -1.318 pp |
| correctness | Mostly Correct | 235 | 220 | 5.74% | 5.37% | -0.366 pp |
| correctness | Not Applicable/Indeterminate | 3172 | 3239 | 77.44% | 79.08% | +1.636 pp |
| correctness | Partially Correct | 9 | 10 | 0.22% | 0.24% | +0.024 pp |
| correctness | Technically Flawed | 4 | 5 | 0.10% | 0.12% | +0.024 pp |
| fdc_digit1 | 0 | 384 | 375 | 9.38% | 9.16% | -0.222 pp |
| fdc_digit1 | 1 | 44 | 41 | 1.07% | 1.00% | -0.073 pp |
| fdc_digit1 | 2 | 85 | 69 | 2.08% | 1.68% | -0.391 pp |
| fdc_digit1 | 3 | 851 | 923 | 20.78% | 22.53% | +1.753 pp |
| fdc_digit1 | 4 | 28 | 26 | 0.68% | 0.63% | -0.049 pp |
| fdc_digit1 | 5 | 106 | 101 | 2.59% | 2.47% | -0.123 pp |
| fdc_digit1 | 6 | 1382 | 1354 | 33.75% | 33.06% | -0.692 pp |
| fdc_digit1 | 7 | 976 | 959 | 23.83% | 23.41% | -0.421 pp |
| fdc_digit1 | 8 | 82 | 66 | 2.00% | 1.61% | -0.391 pp |
| fdc_digit1 | 9 | 157 | 182 | 3.83% | 4.44% | +0.610 pp |

## 7. Component composition (selected-component denominators)

### Science: S5 / S61 final rows

| condition | dev S5 | dev S61 | dev S61 share | M S5 | M S61 | M S61 share |
|---|---:|---:|---:|---:|---:|---:|
| A-normal | 19 | 0 | 0.00% | 17 | 0 | 0.00% |
| A-strict | 12 | 0 | 0.00% | 7 | 0 | 0.00% |
| B-normal | 19 | 10 | 34.48% | 17 | 7 | 29.17% |
| B-strict | 12 | 8 | 40.00% | 7 | 4 | 36.36% |
| C-normal | 19 | 10 | 34.48% | 17 | 7 | 29.17% |
| C-strict | 12 | 8 | 40.00% | 7 | 4 | 36.36% |
| D-normal | 32 | 22 | 40.74% | 32 | 24 | 42.86% |
| D-strict | 12 | 8 | 40.00% | 7 | 4 | 36.36% |

### Practical: explicit / conditional branch and FDC domain

| condition | replicate | explicit | conditional | explicit share | 0xx | 6xx | other |
|---|---|---:|---:|---:|---:|---:|---:|
| A-normal | dev | 79 | 29 | 73.15% | 30 | 41 | 37 |
| A-normal | M | 82 | 35 | 70.09% | 32 | 46 | 39 |
| A-strict | dev | 38 | 24 | 61.29% | 16 | 22 | 24 |
| A-strict | M | 42 | 26 | 61.76% | 13 | 30 | 25 |
| B-normal | dev | 79 | 29 | 73.15% | 30 | 41 | 37 |
| B-normal | M | 82 | 35 | 70.09% | 32 | 46 | 39 |
| B-strict | dev | 38 | 24 | 61.29% | 16 | 22 | 24 |
| B-strict | M | 42 | 26 | 61.76% | 13 | 30 | 25 |
| C-normal | dev | 30 | 11 | 73.17% | 0 | 41 | 0 |
| C-normal | M | 32 | 14 | 69.57% | 0 | 46 | 0 |
| C-strict | dev | 13 | 9 | 59.09% | 0 | 22 | 0 |
| C-strict | M | 19 | 11 | 63.33% | 0 | 30 | 0 |
| D-normal | dev | 174 | 105 | 62.37% | 69 | 126 | 84 |
| D-normal | M | 143 | 114 | 55.64% | 64 | 103 | 90 |
| D-strict | dev | 38 | 24 | 61.29% | 16 | 22 | 24 |
| D-strict | M | 42 | 26 | 61.76% | 13 | 30 | 25 |

### Prose genres

| condition | genre | dev | M | dev share | M share | M-dev pp |
|---|---|---:|---:|---:|---:|---:|
| A-normal | Creative Writing | 8 | 9 | 2.12% | 2.41% | +0.284 pp |
| A-normal | Knowledge Article | 58 | 53 | 15.38% | 14.17% | -1.214 pp |
| A-normal | News Article | 205 | 174 | 54.38% | 46.52% | -7.853 pp |
| A-normal | Nonfiction Writing | 4 | 9 | 1.06% | 2.41% | +1.345 pp |
| A-normal | Personal Blog | 102 | 129 | 27.06% | 34.49% | +7.436 pp |
| A-strict | Creative Writing | 8 | 6 | 2.40% | 1.74% | -0.651 pp |
| A-strict | Knowledge Article | 47 | 45 | 14.07% | 13.08% | -0.990 pp |
| A-strict | News Article | 190 | 163 | 56.89% | 47.38% | -9.502 pp |
| A-strict | Nonfiction Writing | 4 | 7 | 1.20% | 2.03% | +0.837 pp |
| A-strict | Personal Blog | 85 | 123 | 25.45% | 35.76% | +10.307 pp |
| B-normal | Creative Writing | 8 | 9 | 2.16% | 2.42% | +0.263 pp |
| B-normal | Knowledge Article | 52 | 51 | 14.02% | 13.71% | -0.307 pp |
| B-normal | News Article | 205 | 174 | 55.26% | 46.77% | -8.482 pp |
| B-normal | Nonfiction Writing | 4 | 9 | 1.08% | 2.42% | +1.341 pp |
| B-normal | Personal Blog | 102 | 129 | 27.49% | 34.68% | +7.184 pp |
| B-strict | Creative Writing | 8 | 6 | 2.43% | 1.75% | -0.677 pp |
| B-strict | Knowledge Article | 42 | 43 | 12.77% | 12.57% | -0.193 pp |
| B-strict | News Article | 190 | 163 | 57.75% | 47.66% | -10.090 pp |
| B-strict | Nonfiction Writing | 4 | 7 | 1.22% | 2.05% | +0.831 pp |
| B-strict | Personal Blog | 85 | 123 | 25.84% | 35.96% | +10.129 pp |
| C-normal | Creative Writing | 7 | 8 | 5.83% | 6.30% | +0.466 pp |
| C-normal | Knowledge Article | 13 | 14 | 10.83% | 11.02% | +0.190 pp |
| C-normal | News Article | 70 | 62 | 58.33% | 48.82% | -9.514 pp |
| C-normal | Nonfiction Writing | 2 | 3 | 1.67% | 2.36% | +0.695 pp |
| C-normal | Personal Blog | 28 | 40 | 23.33% | 31.50% | +8.163 pp |
| C-strict | Creative Writing | 7 | 5 | 6.86% | 4.50% | -2.358 pp |
| C-strict | Knowledge Article | 11 | 12 | 10.78% | 10.81% | +0.027 pp |
| C-strict | News Article | 61 | 55 | 59.80% | 49.55% | -10.254 pp |
| C-strict | Nonfiction Writing | 2 | 1 | 1.96% | 0.90% | -1.060 pp |
| C-strict | Personal Blog | 21 | 38 | 20.59% | 34.23% | +13.646 pp |
| D-normal | Creative Writing | 32 | 19 | 4.45% | 2.65% | -1.801 pp |
| D-normal | Knowledge Article | 102 | 114 | 14.19% | 15.90% | +1.713 pp |
| D-normal | News Article | 382 | 364 | 53.13% | 50.77% | -2.362 pp |
| D-normal | Nonfiction Writing | 8 | 12 | 1.11% | 1.67% | +0.561 pp |
| D-normal | Personal Blog | 195 | 208 | 27.12% | 29.01% | +1.889 pp |
| D-strict | Creative Writing | 8 | 6 | 2.43% | 1.75% | -0.677 pp |
| D-strict | Knowledge Article | 42 | 43 | 12.77% | 12.57% | -0.193 pp |
| D-strict | News Article | 190 | 163 | 57.75% | 47.66% | -10.090 pp |
| D-strict | Nonfiction Writing | 4 | 7 | 1.22% | 2.05% | +0.831 pp |
| D-strict | Personal Blog | 85 | 123 | 25.84% | 35.96% | +10.129 pp |

## 8. Inherited diagnostic flags (not tests)

- dev zero-row crawl cells: A-normal/essential_science, A-strict/essential_science, B-normal/essential_science, B-strict/essential_science, C-normal/essential_science, C-strict/essential_practical, C-strict/essential_science, D-strict/essential_science
- dev twofold retention notes: A-normal/essential_prose, A-strict/essential_prose, B-normal/essential_prose, B-strict/essential_prose, D-strict/essential_prose
- dev S61 > 0.5 notes: none
- M zero-row crawl cells: A-normal/essential_science, A-strict/essential_science, B-normal/essential_science, B-strict/essential_science, C-normal/essential_science, C-strict/essential_science, D-strict/essential_science
- M twofold retention notes: A-normal/essential_prose, A-strict/essential_prose, B-normal/essential_prose, B-strict/essential_prose, D-strict/essential_prose
- M S61 > 0.5 notes: none
