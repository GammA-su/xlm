# Evidence-v2 protocol freeze: command record

2026-09-27; Windows PowerShell, existing uv environment, Python 3.12.13.
Starting HEAD: `dabf8e7c6a2838062a4d427d35a489ea7e694482`.
All network and corpus-text operations were out of scope.
Read existing real metadata/evidence only; writes were limited to G: repository
documentation. No acquisition tool or synthetic acquisition fixture implemented.

## Primary bounded artifact/inventory verification

Exact command below, exit **0**. It writes only the two named protocol
evidence JSONs, refusing overwrite. The receipt preserves exact artifact
and inspected-code hashes. Expanded inventories are constructed in memory
from numbered filenames and validated against every original listing hash.
This is an offline freeze calculation, not a new discovery/acquisition tool.

```powershell
@'
import hashlib, json, platform, time
from pathlib import Path
import psutil, yaml

start = time.perf_counter()
root = Path.cwd()
out = root / "docs/implementation/evidence/ESSENTIAL-WEB-EVIDENCE-V2"
sweep = Path(r"G:\Project\xlm-selector-sweeps\essential-web-v2")
recon = Path(r"X:\XLM\recon\essential_web")
def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False).encode("utf-8")
def digest(value):
    return hashlib.sha256(canonical(value)).hexdigest()
def file_hash(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()
def read(name):
    return json.loads((recon / name).read_bytes())
m = json.loads((sweep / "sweep_manifest.json").read_bytes())
assert digest({k:v for k,v in m.items() if k != "digest"}) == "e13c9c98efdf07fcc7c1375c4c8b62d918d77aa39c158171aa090c8695ac4087"
assert file_hash(sweep / "sweep_manifest.json") == "0d550a2d871f737c8bc46f82a8de38fd817b50bb1bffee46f2c3c07a129e26bc"
objects = {}
for name, expected in m["artifacts"].items():
    raw = (sweep / name).read_bytes()
    assert len(raw) == expected["bytes"] and hashlib.sha256(raw).hexdigest() == expected["sha256"], name
    objects[name] = json.loads(raw) if name.endswith(".json") else raw.decode("utf-8")
policy = objects["policy_spec.json"]
assert digest(policy) == "f4357f61f434d5105d823266153dac6372e122f12767187631e8796b4899dd07"
assert policy == yaml.safe_load((root / "recipes/selectors/essential_web_selector_sweep_v1.yaml").read_text(encoding="utf-8"))
expected_counts = [[19,108,377,40,3552],[12,62,334,28,3660],[29,108,371,36,3552],[20,62,329,25,3660],[29,41,120,354,3552],[20,22,102,292,3660],[54,279,719,110,2934],[20,62,329,25,3660]]
for key, expected in zip(sorted(objects["summary.json"]["combos"]), expected_counts, strict=True):
    final = objects["summary.json"]["combos"][key]["final"]
    assert [final[k] for k in ["essential_science","essential_practical","essential_prose","unassigned","rejected"]] == expected
assert objects["crosstabs.json"]["report_schema_version"] == 2
assert objects["diagnostics.json"]["report_schema_version"] == 2
assert "word_count_distributions" in objects["crosstabs.json"]
assert "component_genre_share_spreads_pp" in objects["diagnostics.json"]
d, e, b = read("discovery.json"), read("execution.json"), read("bundle.json")
for receipt in [d,e,b]:
    assert digest({k:v for k,v in receipt.items() if k != "digest"}) == receipt["digest"]
assert e["digest"] == m["binding"]["execution_digest"]
assert b["digest"] == m["binding"]["bundle_digest"]
assert b["combined_sha256"] == file_hash(recon / "raw/selected_records.jsonl") == m["binding"]["combined_sha256"]
assert e["projection"] == ["eai_taxonomy","quality_signals"]
inventory = []
strata = []
for s in d["selections"]:
    n, crawl = s["parquet_files_in_crawl"], s["crawl"]
    paths = [f"data/{crawl}/train-{i:05d}-of-{n:05d}.parquet" for i in range(n)]
    assert digest(paths) == s["listing_sha256"]
    eligible = [p for p in paths if p not in d["files"]]
    rank = lambda p: digest(["essential-web-evidence-v2.0","metadata-file",20260927,d["repository"],d["revision"],crawl,p])
    chosen = min(eligible, key=lambda p:(rank(p),p.encode("utf-8")))
    inventory.append({"crawl":crawl,"files":eligible})
    strata.append({"stratum":s["stratum"],"crawl":crawl,"file_count":n,"path_template":f"data/{crawl}/train-{{i:05d}}-of-{n:05d}.parquet","index_start_inclusive":0,"index_stop_exclusive":n,"original_listing_sha256":s["listing_sha256"],"excluded_file":s["file"],"eligible_count":len(eligible),"eligible_paths_digest":digest(eligible),"selected_file":chosen,"selection_rank_sha256":rank(chosen)})
inventory_body = {"repository":d["repository"],"revision":d["revision"],"strata":inventory}
frozen = {"kind":"essential_web_evidence_v2_inventory_freeze","protocol_version":"essential-web-evidence-v2.0","metadata_seed":20260927,"repository":d["repository"],"revision":d["revision"],"parent_discovery_digest":d["digest"],"complete_eligible_inventory_digest":digest(inventory_body),"complete_eligible_inventory_canonical_shape":"{repository,revision,strata:[{crawl,files:[sorted eligible full paths]}]}","reconstruction":"All sequential filename lists reproduce their complete development listing hashes; no network enumeration or footer reads.","strata":strata}
frozen["digest"] = digest(frozen)
out.mkdir(parents=True, exist_ok=True)
dest = out / "inventory-freeze.json"
assert not dest.exists()
temp = dest.with_suffix(".json.partial")
temp.write_bytes(json.dumps(frozen,sort_keys=True,indent=2,ensure_ascii=False).encode()+b"\n")
temp.replace(dest)
result = {"status":"VERIFIED","python":platform.python_version(),"platform":platform.platform(),"starting_head":"dabf8e7c6a2838062a4d427d35a489ea7e694482","sweep_manifest_canonical_digest":m["digest"],"sweep_manifest_file_sha256":file_hash(sweep/"sweep_manifest.json"),"sweep_bytes_read":sum(p.stat().st_size for p in sweep.iterdir() if p.is_file()),"combined_metadata_bytes_hashed":b["combined_bytes"],"inventory_digest":frozen["digest"],"complete_eligible_inventory_digest":digest(inventory_body),"eligible_files":sum(len(s["files"]) for s in inventory),"artifact_hashes":m["artifacts"],"source_hashes":{p:file_hash(root/p) for p in ["scripts/essential_web_selector_sweep.py","src/xlm/data/acquisition/sampling.py","src/xlm/data/acquisition/selection.py","src/xlm/data/acquisition/plan.py","src/xlm/data/adapters/mix01_adapters.py","src/xlm/data/adapters/columns.py"]},"new_footer_inspection":False,"corpus_text_inspection":False,"network":False,"elapsed_seconds":round(time.perf_counter()-start,6),"endpoint_rss_bytes":psutil.Process().memory_info().rss}
dest = out / "verification.json"
assert not dest.exists()
temp = dest.with_suffix(".json.partial")
temp.write_bytes(json.dumps(result,sort_keys=True,indent=2).encode()+b"\n")
temp.replace(dest)
print(json.dumps({k:v for k,v in result.items() if k not in ["artifact_hashes","source_hashes"]},indent=2))
'@ | uv run --offline --locked --no-sync --extra cpu --extra eval python -B -
```

Result: all eight artifact hashes/lengths; canonical policy identity and
repository YAML equality; all eight assignment vectors; three recon receipt
self-digests; development payload hash; eight reconstructed inventory hashes
and selected-file ranks verified. See [verification.json](verification.json).
23,200 eligible paths, inventory digest
`d2b3eac5dca002336a97654564a13c93e8e4696e6a1624a6fdf3c012fd0927d5`.
Measured audit wall 0.243022 seconds; endpoint RSS 31,244,288 bytes, not peak.

## Initial failed identity assertion (preserved)

The first check used the request's literal manifest FILE SHA-256:

```python
assert hashlib.sha256((p/'sweep_manifest.json').read_bytes()).hexdigest() == '0d550a2d871f737c8bc46f82a8de38fd817b50bb1ffee46f2c3c07a129e26bc'
```

Executed in an inline Python stream using the same uv invocation as above;
exit **1**, AssertionError. The immediately preceding canonical-manifest
digest assertion succeeded. A read-only diagnostic printed actual byte
hashes (exit **0**) and established that the pasted literal has 63 hex
characters. The actual hash is
`0d550a2d871f737c8bc46f82a8de38fd817b50bb1bffee46f2c3c07a129e26bc`.
All eight subordinate artifact hashes matched. The primary check above
uses this directly measured identity and records the discrepancy; no input
file or scientific assertion was weakened or changed to make it pass.

Exploratory path searches also reported nonexistent src/xlm/acquisition,
src/xlm/recon, recipes/sources, src/xlm/data/network.py and
src/xlm/data/discovery.py paths; corrected inspection used the real
src/xlm/data/acquisition and src/xlm/data/sources paths. Literal PXX.md
does not exist; the task-specific protocol report serves that convention.
These are inspection/path failures, not test failures or unreported passes.

## Validation scope

Unit, fast, full acceptance, CUDA, live acquisition and human review:
**NOT RUN**. No test result is inferred from reading prior test source.
No v1/v2 assignment-byte replay performed here. The current v2 artifact
integrity and expected assignment totals were verified; earlier replay
evidence remains in the prior scientific review.

Final freeze/link/whitespace check command and outcome are appended below.

## Final documentation validation

The freeze-generation/link-check command below exited **0**. It verified
eight distinct new paths, no development-file overlap, all report link
targets, artifact byte bindings and canonical freeze digest. This command
is the initial freeze creation; the final freeze manifest additionally
binds this command record after completion (same canonical scheme).
The protocol is immutable at delivery; no acquisition occurred between
freeze creation and final documentation binding.

```powershell
@'
import hashlib, json, re
from pathlib import Path
root = Path.cwd()
base = root / "docs/implementation/evidence/ESSENTIAL-WEB-EVIDENCE-V2"
def canonical(x):
    return json.dumps(x,sort_keys=True,separators=(",",":"),ensure_ascii=False,allow_nan=False).encode()
def digest(x):
    return hashlib.sha256(canonical(x)).hexdigest()
paths = ["docs/implementation/reports/ESSENTIAL-WEB-EVIDENCE-V2-PROTOCOL.md", "docs/implementation/evidence/ESSENTIAL-WEB-EVIDENCE-V2/inventory-freeze.json", "docs/implementation/evidence/ESSENTIAL-WEB-EVIDENCE-V2/verification.json"]
artifacts = []
for name in paths:
    raw = (root/name).read_bytes()
    descriptor = {"relative_path":name,"bytes":len(raw),"sha256":hashlib.sha256(raw).hexdigest(),"media_type":"application/json" if name.endswith(".json") else "text/markdown"}
    artifacts.append({**descriptor,"descriptor_digest":digest(descriptor)})
body = {"kind":"essential_web_evidence_protocol_freeze","protocol_version":"essential-web-evidence-v2.0","scope":"PROTOCOL_ONLY_NOT_ACQUISITION_AUTHORIZATION","metadata_seed":20260927,"text_selection_seed":20260927,"review_order_seed":20260928,"repository":"EssentialAI/essential-web-v1.0","revision":"ce4eccc7e9604667b6d7f32cb6274b8b41f3113d","policy_digest":"f4357f61f434d5105d823266153dac6372e122f12767187631e8796b4899dd07","complete_eligible_inventory_digest":"d2b3eac5dca002336a97654564a13c93e8e4696e6a1624a6fdf3c012fd0927d5","artifacts":artifacts,"canonical_scheme":"UTF-8 json.dumps(sort_keys=True,separators=(',',':'),ensure_ascii=False,allow_nan=False), no BOM/newline; self-digest excludes only top-level digest"}
body["digest"] = digest(body)
dest = base/"freeze.json"
assert not dest.exists()
temp = dest.with_suffix(".json.partial")
temp.write_bytes(json.dumps(body,sort_keys=True,indent=2).encode()+b"\n")
temp.replace(dest)
loaded = json.loads(dest.read_bytes())
assert digest({k:v for k,v in loaded.items() if k!="digest"}) == loaded["digest"]
report = root/paths[0]
for link in re.findall(r"\]\(([^)]+)\)",report.read_text(encoding="utf-8")):
    assert (report.parent/link).resolve().is_file(), link
inventory = json.loads((base/"inventory-freeze.json").read_bytes())
assert len(inventory["strata"]) == 8
assert len({s["selected_file"] for s in inventory["strata"]}) == 8
assert not {s["selected_file"] for s in inventory["strata"]} & {s["excluded_file"] for s in inventory["strata"]}
assert inventory["complete_eligible_inventory_digest"] == body["complete_eligible_inventory_digest"]
for entry in artifacts:
    p = root/entry["relative_path"]
    assert p.stat().st_size == entry["bytes"] and hashlib.sha256(p.read_bytes()).hexdigest() == entry["sha256"]
print(json.dumps({"status":"VERIFIED","freeze_digest":body["digest"],"protocol_file_sha256":artifacts[0]["sha256"],"freeze_file_sha256":hashlib.sha256(dest.read_bytes()).hexdigest(),"protocol_bytes":artifacts[0]["bytes"],"new_files":8,"links":"all targets present"},indent=2))
'@ | uv run --offline --locked --no-sync --extra cpu --extra eval python -B -
```

`git diff --check` exited **0** (only Git LF-to-CRLF notices).
`git diff --stat` and `git status --short` exited **0**. The pre-existing
recon-report modification and prior review untracked files were preserved.
Our changes: STATUS.md, the new protocol report, and this evidence directory.
No commit was made. No tests were run.

Final audit command for the four-file evidence directory and
protocol binding (read-only, exit 0):

```powershell
@'
import hashlib, json
from pathlib import Path
root = Path.cwd()
base = root / "docs/implementation/evidence/ESSENTIAL-WEB-EVIDENCE-V2"
c = lambda x: json.dumps(x,sort_keys=True,separators=(",",":"),ensure_ascii=False,allow_nan=False).encode()
f = json.loads((base/"freeze.json").read_bytes())
assert hashlib.sha256(c({k:v for k,v in f.items() if k!="digest"})).hexdigest() == f["digest"]
for a in f["artifacts"]:
    raw = (root/a["relative_path"]).read_bytes()
    assert len(raw) == a["bytes"] and hashlib.sha256(raw).hexdigest() == a["sha256"]
    descriptor = {k:v for k,v in a.items() if k!="descriptor_digest"}
    assert hashlib.sha256(c(descriptor)).hexdigest() == a["descriptor_digest"]
print(f["digest"])
print("ALL FINAL BINDINGS VERIFIED")
'@ | uv run --offline --locked --no-sync --extra cpu --extra eval python -B -
```
