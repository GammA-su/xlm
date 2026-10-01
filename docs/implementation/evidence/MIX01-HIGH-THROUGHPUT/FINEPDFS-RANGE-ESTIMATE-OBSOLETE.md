# Status: FinePDFs range estimate in transport-policy-modeled.json is OBSOLETE

The `finepdfs_edu` `range_selected` entry in
[transport-policy-modeled.json](transport-policy-modeled.json) is about
2,529,955,258 B, 1,337 requests and 442,437 rows. It is **invalid** for
FinePDFs range-v1 comparison and must not select or size a production mode.

- Its per-row transfer came from one light calibration row group (174), about
  5.7 KB/row against a whole-file mean of 12.35 KB/row.
- It assumes every row group is range-reachable. A footer audit of
  `data/eng_Latn/train/000_00083.parquet` (sha256 `4eeb58bc…a38d`) shows that
  64 of 221 groups (29% of rows, 54% of projected bytes) are refused by
  `records.check_row_group`.

The file is kept unchanged as history. The formal disposition is the
`range-v1-reach-v1` record (digest `27ceafe9…5780`) bound by the v2 FinePDFs
transport policy as `range_selected: non_comparable`. See
[FINEPDFS-RANGE-ROWGROUP-AUDIT](../../reports/FINEPDFS-RANGE-ROWGROUP-AUDIT.md)
and [FINEPDFS-WHOLE-FILE-POLICY](../../reports/FINEPDFS-WHOLE-FILE-POLICY.md).
