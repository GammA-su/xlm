"""Bounded footer-only fetch of one pinned FineWiki shard (metadata, no payload)."""
import io, json, struct, sys, urllib.parse, urllib.request
import pyarrow.parquet as pq
URL = ("https://huggingface.co/datasets/HuggingFaceFW/finewiki/resolve/"
       "8bd13e72e6a002407649b3e898535f42ceb1aeb9/data/enwiki/000_00014.parquet")
SIZE = 2538032319
HOSTS = {"huggingface.co", "cas-bridge.xethub.hf.co", "us.aws.cdn.hf.co"}
CAP = 4 * 1024 * 1024
log = {"requests": 0, "bytes": 0, "hosts": []}
class Guard(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        host = urllib.parse.urlparse(newurl).hostname
        log["hosts"].append(host)
        if host not in HOSTS: raise RuntimeError(f"host not allowlisted: {host}")
        return super().redirect_request(req, fp, code, msg, headers, newurl)
opener = urllib.request.build_opener(Guard)
def get(start, stop):
    if log["bytes"] + (stop - start) > CAP: raise RuntimeError("byte cap")
    req = urllib.request.Request(URL, headers={"Range": f"bytes={start}-{stop-1}"})
    with opener.open(req, timeout=30) as r:
        log["requests"] += 1
        if r.status != 206: raise RuntimeError(f"status {r.status}")
        total = int(r.headers["Content-Range"].rsplit("/", 1)[1])
        if total != SIZE: raise RuntimeError(f"size {total}")
        data = r.read(stop - start + 1)
    if len(data) != stop - start: raise RuntimeError("short read")
    log["bytes"] += len(data)
    return data
tail = get(SIZE - 8, SIZE)
flen, magic = struct.unpack("<I4s", tail)
if magic != b"PAR1" or flen + 8 > CAP: raise RuntimeError("bad trailer")
footer = get(SIZE - 8 - flen, SIZE - 8)
md = pq.read_metadata(io.BytesIO(footer + tail))
rg = [md.row_group(i) for i in range(md.num_row_groups)]
text = sum(r.column(c).total_uncompressed_size for r in rg for c in range(r.num_columns)
           if r.column(c).path_in_schema == "text")
print(json.dumps({"file": "data/enwiki/000_00014.parquet", "file_size": SIZE, "footer_length": flen,
  "num_rows": md.num_rows, "row_groups": md.num_row_groups,
  "max_rg_rows": max(r.num_rows for r in rg), "min_rg_rows": min(r.num_rows for r in rg),
  "total_byte_size": sum(r.total_byte_size for r in rg),
  "max_rg_total_byte_size": max(r.total_byte_size for r in rg),
  "text_uncompressed_bytes": text, "network": log}, indent=1))
