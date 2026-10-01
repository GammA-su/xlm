import json, sys, os
import pyarrow.parquet as pq
p = sys.argv[1]
md = pq.ParquetFile(p).metadata
rg = [md.row_group(i) for i in range(md.num_row_groups)]
print(json.dumps({
 "file": p, "file_size": os.path.getsize(p), "num_rows": md.num_rows,
 "row_groups": md.num_row_groups, "max_rg_rows": max(r.num_rows for r in rg),
 "min_rg_rows": min(r.num_rows for r in rg),
 "total_byte_size": sum(r.total_byte_size for r in rg),
 "max_rg_total_byte_size": max(r.total_byte_size for r in rg),
 "compressed": sum(r.column(c).total_compressed_size for r in rg for c in range(r.num_columns)),
 "first_rg_rows": rg[0].num_rows, "first_rg_total_byte_size": rg[0].total_byte_size,
 "footer_bytes": md.serialized_size, "columns": md.num_columns,
}, indent=1))
