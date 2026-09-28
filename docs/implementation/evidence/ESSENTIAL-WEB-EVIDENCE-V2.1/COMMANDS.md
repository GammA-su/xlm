# Evidence v2.1 amendment: reproducible offline audit

Date: 2026-09-28. Repository: `G:\Project\xlm-data-ultrax`.
Starting HEAD: `aaa674d8b1d0da459b88450dc4eb6c68ca52e933`.
This is an embedded one-off receipt audit, not acquisition/product implementation.
Only explicitly named local receipts and repository contracts are read.
Existing external evidence is read-only; no text payload, network or dependency sync.

Parent verifier (exit 0):

```powershell
uv run --offline --locked --no-sync --extra cpu --extra eval python -B scripts/evidence_v2.py verify-freeze
```

Audit invocation (exit status recorded after execution in REVIEW):

```powershell
$reviewText = Get-Content -Raw -Encoding UTF8 docs/implementation/evidence/ESSENTIAL-WEB-EVIDENCE-V2.1/COMMANDS.md
$auditSource = [regex]::Match($reviewText, '(?s)```python\r?\n(.*?)```').Groups[1].Value
$auditSource | uv run --offline --locked --no-sync --extra cpu --extra eval python -B -
```

Output is an atomic, no-overwrite `verification.json`. To reproduce after delivery,
use a separate checkout/output copy: do not overwrite the frozen audit artifact.
Raw and canonical hashes have distinct meanings. Resource counters below measure
this audit process only, not acquisition.

```python
import ctypes
import hashlib
import importlib.metadata
import json
import os
import platform
import sys
import time
from collections import defaultdict
from pathlib import Path

START = time.perf_counter()
ROOT = Path.cwd()
OUT = ROOT / "docs/implementation/evidence/ESSENTIAL-WEB-EVIDENCE-V2.1"
EXTERNAL = Path("G:/Project/xlm-evidence-v2/essential-web")
MIB = 1048576
BINDINGS = {}
TOTAL_READ = 0

def require(ok, message):
    if not ok:
        raise ValueError(message)

def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=False, allow_nan=False).encode("utf-8")

def digest(value):
    return hashlib.sha256(canonical(value)).hexdigest()

def pairs(items):
    result = {}
    for key, value in items:
        require(key not in result, "duplicate JSON key")
        result[key] = value
    return result

def invalid(value):
    raise ValueError("nonfinite JSON")

def read(path):
    global TOTAL_READ
    require(path.stat().st_size <= 2 * MIB, "audit input exceeds 2 MiB")
    raw = path.read_bytes()
    TOTAL_READ += len(raw)
    value = json.loads(raw.decode("utf-8"), object_pairs_hook=pairs, parse_constant=invalid)
    require(value["digest"] == digest({k: v for k, v in value.items() if k != "digest"}),
            str(path) + ": digest mismatch")
    BINDINGS[path.name] = {"path": str(path), "bytes": len(raw),
                           "sha256": hashlib.sha256(raw).hexdigest(),
                           "digest": value["digest"]}
    return value

parent = read(ROOT / "docs/implementation/evidence/ESSENTIAL-WEB-EVIDENCE-V2/freeze.json")
for descriptor in parent["artifacts"]:
    raw = (ROOT / descriptor["relative_path"]).read_bytes()
    TOTAL_READ += len(raw)
    require(len(raw) == descriptor["bytes"], "parent length")
    require(hashlib.sha256(raw).hexdigest() == descriptor["sha256"], "parent byte hash")
    require(descriptor["descriptor_digest"] ==
            digest({k: v for k, v in descriptor.items() if k != "descriptor_digest"}),
            "parent descriptor")
inventory = read(ROOT / "docs/implementation/evidence/ESSENTIAL-WEB-EVIDENCE-V2/inventory-freeze.json")
m = read(EXTERNAL / "footer_evidence.json")
t = read(EXTERNAL / "text_cost_evidence_attempt2.incomplete.json")
selection = read(EXTERNAL / "text_selection_manifest.json")
prior_m = read(EXTERNAL / "footer_evidence.incomplete.json")
prior_t = read(EXTERNAL / "text_cost_evidence.incomplete.json")
for obj in [m, t, selection, prior_m, prior_t]:
    require(obj["freeze_digest"] == parent["digest"], "freeze parent drift")
    require(obj["protocol_version"] == parent["protocol_version"], "version drift")
for obj in [m, selection]:
    require(obj["repository"] == parent["repository"] and obj["revision"] == parent["revision"],
            "source drift")
    require(obj["policy_digest"] == parent["policy_digest"], "policy drift")
require(t["selection_digest"] == selection["digest"], "selection binding drift")
require(t["provenance"]["revision"] == parent["revision"], "T source revision")
require(t["status"] == "INCOMPLETE" and t["exit_status"] == 1 and not t["stopped_early"],
        "historical incomplete status")
require((t["files_planned"], t["files_feasible"], t["files_infeasible"]) == (8, 0, 8),
        "historical counts")
wanted = defaultdict(list)
strata_counts = {}
for cell in selection["cells"]:
    groups = [cell] if "identities" in cell else cell["crawls"]
    cell_count = 0
    for group in groups:
        for repo, revision, file, row in group["identities"]:
            require(repo == parent["repository"] and revision == parent["revision"],
                    "locator source drift")
            wanted[file].append(row)
            cell_count += 1
    strata_counts[cell.get("stratum", cell.get("name", str(len(strata_counts))))] = cell_count
for rows in wanted.values():
    require(len(rows) == len(set(rows)), "duplicate locator")
    rows.sort()
require(sum(map(len, wanted.values())) == selection["total_selected"] == 118, "selection count")
require(set(wanted) == {s["excluded_file"] for s in inventory["strata"]}, "T files")
require({u["file"] for u in m["units"]} ==
        {s["selected_file"] for s in inventory["strata"]}, "M files")
require(m["status"] == "COMPLETE" and len(m["units"]) == 8, "M completion")
m_rows = []
for u in m["units"]:
    require(u["retained_rows"] == 512 and u["future_plan_feasible"], "M future receipt")
    require(u["future_plan_feasibility"]["request_estimate"] == 85, "M request estimate")
    ranges = u["footer_ranges"]
    require(sum(r["bytes"] for r in ranges) == u["footer_bytes_used"], "M footer bytes")
    require(sum(1 + r["hops"] for r in ranges) == u["footer_requests_used"], "M requests")
    m_rows.append({"file": u["file"], "scan_rows": u["expected_scan_rows"],
                   "projected_transfer_estimate_bytes": u["projected_transfer_estimate_bytes"],
                   "footer_bytes": u["footer_bytes_used"],
                   "footer_requests": u["footer_requests_used"]})
for obj in [m, t, prior_m, prior_t]:
    budget = obj["budget"]
    require(sum(budget["transfer_per_file"].values()) == budget["response_body_bytes"]
            == budget["footer_body_bytes"], "footer counters")
require(sum(u["footer_requests"] for u in m_rows) == m["budget"]["requests"] == 48,
        "M request sum")
require(m["budget"]["response_body_bytes"] == 1922640, "M byte sum")
require(t["budget"]["requests"] == 48 and t["budget"]["response_body_bytes"] == 1994765,
        "T planning counters")
rows = []
for u in t["units"]:
    file = u["file"]
    require(u["wanted_rows"] == wanted[file], "wanted rows mismatch")
    require(u["selected_count"] == len(wanted[file]), "selected count mismatch")
    groups = u["text_costs"]["groups"]
    require(len(groups) == 1, "one wanted group")
    compressed = decompressed = chunks = scan = 0
    grouped = []
    for group in groups:
        gs = group["group_start_row"]
        gr = group["group_rows"]
        wr = group["wanted_rows"]
        grouped.extend(wr)
        require(all(gs <= x < gs + gr for x in wr), "group membership")
        sc = min(gr, ((max(wr) - gs) // 256 + 1) * 256)
        require(sc == group["scan_upper_rows"], "scan formula")
        c = sum(x["compressed_bytes"] for x in group["text_chunks"])
        d = sum(x["uncompressed_bytes"] for x in group["text_chunks"])
        n = len(group["text_chunks"])
        require(n == group["chunk_count"] == 1, "single text chunk")
        require(c == group["text_compressed_bytes"] and d == group["text_uncompressed_bytes"],
                "chunk sums")
        require(group["transfer_upper_bytes"] == c + n * 4 * MIB, "group transfer")
        require(group["decompressed_upper_bytes"] == d, "group decompression")
        require(group["selected_count"] == len(wr), "group count")
        require(group["selected_min"] == min(wr) and group["selected_max"] == max(wr),
                "group extrema")
        require(group["request_upper"] == n + 1, "legacy group request arithmetic")
        for chunk in group["text_chunks"]:
            require(chunk["path"] == "text", "text projection")
            start = min(chunk["offset"], chunk["dictionary_page_offset"])
            require(0 <= start < start + chunk["compressed_bytes"] <= u["remote_length"],
                    "chunk physical extent")
        compressed += c
        decompressed += d
        chunks += n
        scan += sc
    require(sorted(grouped) == wanted[file], "group locator equality")
    transfer = compressed + chunks * 4 * MIB
    workspace = decompressed + 32 * MIB
    legacy_requests = chunks + len(groups) + 4
    require((transfer, decompressed, scan, workspace, legacy_requests) ==
            (u["transfer_upper_bytes"], u["decompressed_upper_bytes"], u["scan_rows_upper"],
             u["workspace_upper_bytes"], u["requests_upper"]), "unit formulas")
    require(all(v["fits"] == (v["value"] <= v["cap"]) for v in u["fits"].values()),
            "unit fits")
    require(not u["fits"]["transfer"]["fits"] and
            all(v["fits"] for k, v in u["fits"].items() if k != "transfer"), "sole recorded failure")
    require(len(u["reasons"]) == 1 and "data/file cap" in u["reasons"][0], "refusal reasons")
    prior_bytes = prior_t["budget"]["transfer_per_file"].get(file, 0)
    footer_bytes = t["budget"]["transfer_per_file"][file] + prior_bytes
    require(transfer <= 30 * MIB and transfer + footer_bytes <= 32 * MIB,
            "amended transfer fit")
    rows.append({"file": file, "selected": len(wanted[file]), "compressed_bytes": compressed,
                 "transfer_upper_bytes": transfer, "decompressed_upper_bytes": decompressed,
                 "scan_rows": scan, "workspace_upper_bytes": workspace,
                 "legacy_requests_arithmetic_only": legacy_requests,
                 "minimum_chunk_ranges_at_4MiB": (compressed + 4 * MIB - 1) // (4 * MIB),
                 "illustrative_reserved_ranges": (transfer + 4 * MIB - 1) // (4 * MIB),
                 "known_footer_bytes_including_prior": footer_bytes,
                 "data_headroom_bytes": 30 * MIB - transfer,
                 "total_headroom_bytes": 32 * MIB - transfer - footer_bytes,
                 "compression_ratio": decompressed / compressed})
require(len(rows) == 8 and len({u["file"] for u in rows}) == 8, "complete unique cost map")
agg = t["aggregate"]
for field in ["transfer_upper", "decompressed_upper"]:
    values = [r[field + "_bytes"] for r in rows]
    require(sum(values) == agg[field + "_sum_bytes"], "aggregate sum")
    require(max(values) == agg[field + "_max_bytes"], "aggregate max")
require(sum(r["scan_rows"] for r in rows) == agg["scan_upper_total_rows"] == 38400, "scan sum")
require(sum(r["legacy_requests_arithmetic_only"] for r in rows) ==
        agg["requests_upper_future_total"] == 48, "legacy request sum")
require(agg["fits"]["transfer_arm"]["value"] == sum(r["transfer_upper_bytes"] for r in rows),
        "legacy transfer omission reproduced")
require(agg["fits"]["requests_arm"]["value"] == 96, "legacy request aggregate")
transfer = sum(r["transfer_upper_bytes"] for r in rows)
footer = t["budget"]["response_body_bytes"] + prior_t["budget"]["response_body_bytes"]
require(transfer == 213517601 and footer == 2231492, "known totals")
require(transfer <= 240 * MIB and transfer + footer <= 256 * MIB, "arm transfer fit")
require(selection["digest"] == "975ba3dee4af0598e665ea05c69bbe49336c3e3a35fca777a863073190b78474",
        "frozen selection identity")
require(BINDINGS["text_selection_manifest.json"]["sha256"] ==
        "8424f9668fef6952a558ead0dae27a705545a7f4a4d302713707d3ec6bb1af27", "selection bytes")

class Counters(ctypes.Structure):
    _fields_ = [("cb", ctypes.c_ulong), ("PageFaultCount", ctypes.c_ulong),
                ("PeakWorkingSetSize", ctypes.c_size_t), ("WorkingSetSize", ctypes.c_size_t),
                ("QuotaPeakPagedPoolUsage", ctypes.c_size_t), ("QuotaPagedPoolUsage", ctypes.c_size_t),
                ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
                ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
                ("PagefileUsage", ctypes.c_size_t), ("PeakPagefileUsage", ctypes.c_size_t)]
counter = Counters()
counter.cb = ctypes.sizeof(counter)
ctypes.windll.kernel32.GetCurrentProcess.restype = ctypes.c_void_p
get_info = ctypes.windll.psapi.GetProcessMemoryInfo
get_info.argtypes = [ctypes.c_void_p, ctypes.POINTER(Counters), ctypes.c_ulong]
require(get_info(ctypes.windll.kernel32.GetCurrentProcess(), ctypes.byref(counter),
                 ctypes.sizeof(counter)) != 0, "memory measurement")
packages = {}
for package in ["torch", "pyarrow"]:
    try:
        packages[package] = importlib.metadata.version(package)
    except importlib.metadata.PackageNotFoundError:
        packages[package] = None
report = {
    "kind": "essential_web_evidence_v2_1_offline_amendment_audit",
    "status": "VERIFIED_RECEIPT_ARITHMETIC_NOT_EXECUTION_READINESS",
    "environment": {"platform": platform.platform(), "python": sys.version,
                    "packages_metadata_only": packages,
                    "uv_lock_sha256": hashlib.sha256((ROOT / "uv.lock").read_bytes()).hexdigest()},
    "input_bindings": BINDINGS, "arm_t_files": rows, "arm_m_files": m_rows,
    "selection_total": 118, "selection_strata_counts": strata_counts,
    "arm_t_totals": {
        "transfer_upper_bytes": transfer, "decompressed_upper_bytes": 311068163,
        "scan_rows": 38400, "latest_footer_bytes": 1994765, "latest_footer_requests": 48,
        "known_footer_bytes_including_prior": footer, "known_footer_requests_including_prior": 54,
        "data_headroom_bytes": 240 * MIB - transfer,
        "total_headroom_latest_bytes": 256 * MIB - transfer - 1994765,
        "total_headroom_including_known_prior_bytes": 256 * MIB - transfer - footer,
        "total_with_full_footer_allocation_bytes": transfer + 16 * MIB,
        "headroom_with_full_footer_allocation_bytes": 256 * MIB - transfer - 16 * MIB,
        "minimum_chunk_ranges": sum(r["minimum_chunk_ranges_at_4MiB"] for r in rows),
        "illustrative_reserved_ranges": sum(r["illustrative_reserved_ranges"] for r in rows)},
    "limitations": [
        "Receipt hashes verify local integrity, not independent attestation of historic network traffic.",
        "No corpus text or raw footer re-fetch; source costs validated from numeric receipts only.",
        "Legacy request formula ignores range splitting, redirects and retries.",
        "Legacy transfer aggregate omits consumed footer bytes.",
        "Earlier failed planning attempts must carry forward; historical meter gaps remain.",
        "Prior M redirect counter bug prevents using recorded 4 requests as complete physical accounting.",
        "M projected estimates range 582200..1657177 bytes, not 0.56..0.58 MiB throughout.",
        "Zero elapsed/disk/decompressed counters do not establish zero real planning cost.",
        "Workspace reservations are not measured process-tree RSS or safe decoder-allocation proofs.",
        "No new execution, live, unit, fast, full or CUDA tests."],
    "measured_audit_only": {"input_bytes_read": TOTAL_READ,
                             "elapsed_seconds_before_output": time.perf_counter() - START,
                             "working_set_endpoint_bytes": counter.WorkingSetSize,
                             "peak_working_set_before_output_bytes": counter.PeakWorkingSetSize,
                             "network_requests": 0, "corpus_text_bytes_read": 0},
    "command_exit_status": 0
}
report["digest"] = digest(report)
OUT.mkdir(parents=True, exist_ok=True)
target = OUT / "verification.json"
require(not target.exists(), "audit output exists; do not overwrite historical evidence")
temporary = OUT / "verification.json.tmp"
with temporary.open("xb") as handle:
    handle.write(canonical(report) + b"\n")
    handle.flush()
    os.fsync(handle.fileno())
os.replace(temporary, target)
print(json.dumps({"digest": report["digest"], "resources": report["measured_audit_only"],
                  "totals": report["arm_t_totals"], "environment": report["environment"]}))
```

Exploratory inspections: PowerShell `Get-Content`, `rg`, `Get-Item`,
`Get-ChildItem`, `git status --short`, `git rev-parse HEAD`, and bounded
`uv run --offline --locked --no-sync --extra cpu --extra eval python -B -`
JSON key/counter inspections returned exit 0 except this initial old-schema
inspection (exit 1; no writes):

```python
import json
from pathlib import Path
p=Path('G:/Project/xlm-evidence-v2/essential-web/footer_evidence.incomplete.json')
d=json.loads(p.read_bytes()); u=d['completed_units'][0]
print({k:u[k] for k in ['footer_requests_used','footer_bytes_used','footer_ranges']})
```

It raised `KeyError: 'footer_ranges'`. The old receipt has no range list;
subsequent inspection used its available fields, preserving this limitation.
No failed test was retried or suppressed. Test suites were NOT RUN.

## Freeze construction

Exact invocation (exit 0):

```powershell
$reviewText = Get-Content -Raw -Encoding UTF8 docs/implementation/evidence/ESSENTIAL-WEB-EVIDENCE-V2.1/COMMANDS.md
$freezeSection = $reviewText.Substring($reviewText.IndexOf('## Freeze construction'))
$freezeSource = [regex]::Match($freezeSection, '(?s)```python\r?\n(.*?)```').Groups[1].Value
$freezeSource | uv run --offline --locked --no-sync --extra cpu --extra eval python -B -
```

```python
import hashlib
import json
import os
from pathlib import Path

root = Path.cwd()
out = root / "docs/implementation/evidence/ESSENTIAL-WEB-EVIDENCE-V2.1"
def canon(x):
    return json.dumps(x, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
                      allow_nan=False).encode("utf-8")
def h(x):
    return hashlib.sha256(canon(x)).hexdigest()
def check(condition, message):
    if not condition:
        raise ValueError(message)
def descriptor(relative_path):
    raw = (root / relative_path).read_bytes()
    d = {"relative_path": relative_path, "bytes": len(raw),
         "sha256": hashlib.sha256(raw).hexdigest(),
         "media_type": "application/json" if relative_path.endswith(".json") else "text/markdown"}
    d["descriptor_digest"] = h(d)
    return d
audit = json.loads((out / "verification.json").read_bytes())
check(h({k:v for k,v in audit.items() if k != "digest"}) == audit["digest"], "audit digest")
bindings = audit["input_bindings"]
roles = {
    "freeze.json": "normative_parent",
    "inventory-freeze.json": "unchanged_inventory",
    "footer_evidence.json": "adopted_observation_not_resource_compliance",
    "text_cost_evidence_attempt2.incomplete.json": "historical_incomplete_cost_map",
    "text_selection_manifest.json": "byte_identical_selection",
    "footer_evidence.incomplete.json": "budget_history_M_unreconciled",
    "text_cost_evidence.incomplete.json": "budget_history_T"
}
parents = {}
for name, role in roles.items():
    d = bindings[name]
    raw = Path(d["path"]).read_bytes()
    check(len(raw) == d["bytes"] and hashlib.sha256(raw).hexdigest() == d["sha256"],
          "parent changed since audit")
    parsed = json.loads(raw)
    check(h({k:v for k,v in parsed.items() if k != "digest"}) == d["digest"], "parent digest")
    parents[name] = {**d, "role": role, "original_protocol_version": parsed["protocol_version"]}
    if "status" in parsed:
        parents[name]["original_status"] = parsed["status"]
t = json.loads(Path(bindings["text_cost_evidence_attempt2.incomplete.json"]["path"]).read_bytes())
m = json.loads(Path(bindings["footer_evidence.json"]["path"]).read_bytes())
old = t["provenance"]["caps"]
new = dict(old)
changes = {"data_bytes_per_file_max": 31457280, "transfer_bytes_per_file_max": 33554432,
           "transfer_bytes_arm_max": 268435456}
new.update(changes)
new["data_bytes_arm_max"] = 251658240
check({k for k in old if old[k] != new[k]} == set(changes), "nontransfer cap changed")
artifacts = [
    "docs/implementation/reports/ESSENTIAL-WEB-EVIDENCE-V2.1-PROTOCOL.md",
    "docs/implementation/reports/ESSENTIAL-WEB-EVIDENCE-V2.1-REVIEW.md",
    "docs/implementation/evidence/ESSENTIAL-WEB-EVIDENCE-V2.1/COMMANDS.md",
    "docs/implementation/evidence/ESSENTIAL-WEB-EVIDENCE-V2.1/verification.json"
]
freeze = {
    "kind": "essential_web_evidence_protocol_amendment_freeze",
    "schema_version": 1, "protocol_version": "essential-web-evidence-v2.1",
    "frozen_date": "2026-09-28",
    "scope": "PROTOCOL_AMENDMENT_ONLY_NOT_ACQUISITION_AUTHORIZATION",
    "canonical_scheme": "UTF-8 json.dumps(sort_keys=True,separators=(',',':'),ensure_ascii=False,allow_nan=False), no BOM/newline; self-digest excludes only top-level digest",
    "source_checkout": "aaa674d8b1d0da459b88450dc4eb6c68ca52e933",
    "repository": m["repository"], "revision": m["revision"],
    "policy_digest": m["policy_digest"],
    "parent_freeze_digest": bindings["freeze.json"]["digest"],
    "arm_m_footer_evidence_digest": m["digest"],
    "arm_t_v2_0_cost_map_digest": t["digest"],
    "selection_digest": bindings["text_selection_manifest.json"]["digest"],
    "selection_file_sha256": bindings["text_selection_manifest.json"]["sha256"],
    "selection_total": 118,
    "scientific_identity_namespace": "essential-web-evidence-v2.0",
    "metadata_seed": 20260927, "text_selection_seed": 20260927, "review_order_seed": 20260928,
    "arm_t_caps_v2_0": old, "arm_t_caps_v2_1": new,
    "transfer_allocations_v2_1": {
        "per_file": {"footer": 2097152, "data": 31457280, "total": 33554432},
        "arm": {"footer": 16777216, "data": 251658240, "total": 268435456}},
    "arm_m_caps_unchanged": m["provenance"]["caps"],
    "parents": parents, "artifacts": [descriptor(p) for p in artifacts],
    "rationale": "Footer-only discovery revealed whole-column-chunk transport granularity. Next binary 32 MiB/file and 256 MiB/arm capacities with unchanged 2/16 MiB footer reserves fit the complete frozen map with material headroom. No semantic outcome, selection, reader, rubric, or non-transfer cap changes.",
    "adoption": {
        "M": "Reuse original footer/window observations by hash without network; resource compliance remains blocked pending prior-attempt reconciliation.",
        "T": "Preserve original v2.0 INCOMPLETE cost-map status as motivation and numeric planning parent.",
        "selection": "Reference original bytes and digest; no rewrite, relabel or reselection.",
        "accounting": "All prior attempts and planning carry forward once; version change never resets budgets. Unknown costs block execution.",
        "known_T_planning_carry_in": {"recorded_bytes": 2231492, "recorded_requests": 54,
                                    "complete_history_certified": False},
        "M_history_status": "BLOCKED: old redirect accounting implies first-file footer requests 12 > unchanged 10; must reconcile, never waive under this amendment."
    },
    "environment": audit["environment"],
    "authorization": {"arm_M_acquisition": False, "arm_T_text_acquisition": False,
                      "network": False, "corpus_text_inspection": False},
    "verdict": "READY TO IMPLEMENT EVIDENCE V2.1"
}
freeze["digest"] = h(freeze)
target = out / "freeze.json"
check(not target.exists(), "do not overwrite existing freeze")
temporary = out / "freeze.json.tmp"
with temporary.open("xb") as handle:
    handle.write(json.dumps(freeze, sort_keys=True, indent=2, ensure_ascii=False,
                            allow_nan=False).encode("utf-8") + b"\n")
    handle.flush()
    os.fsync(handle.fileno())
os.replace(temporary, target)
print(json.dumps({"freeze_digest": freeze["digest"],
                  "protocol_file_sha256": freeze["artifacts"][0]["sha256"],
                  "freeze_file_sha256": hashlib.sha256(target.read_bytes()).hexdigest(),
                  "freeze_bytes": target.stat().st_size}))
```

## Final validation

After STATUS.md records the new digest, exact invocation (exit 0):

```powershell
$reviewText = Get-Content -Raw -Encoding UTF8 docs/implementation/evidence/ESSENTIAL-WEB-EVIDENCE-V2.1/COMMANDS.md
$verifySection = $reviewText.Substring($reviewText.IndexOf('## Final validation'))
$verifySource = [regex]::Match($verifySection, '(?s)```python\r?\n(.*?)```').Groups[1].Value
$verifySource | uv run --offline --locked --no-sync --extra cpu --extra eval python -B -
git diff --check -- docs/implementation/STATUS.md docs/implementation/reports/ESSENTIAL-WEB-EVIDENCE-V2.1-PROTOCOL.md docs/implementation/reports/ESSENTIAL-WEB-EVIDENCE-V2.1-REVIEW.md docs/implementation/evidence/ESSENTIAL-WEB-EVIDENCE-V2.1
```

```python
import hashlib
import json
import subprocess
from pathlib import Path
root = Path.cwd()
folder = root / "docs/implementation/evidence/ESSENTIAL-WEB-EVIDENCE-V2.1"
def h(x):
    return hashlib.sha256(json.dumps(x, sort_keys=True, separators=(",", ":"),
                                    ensure_ascii=False, allow_nan=False).encode()).hexdigest()
def check(ok, message):
    if not ok:
        raise ValueError(message)
freeze = json.loads((folder / "freeze.json").read_bytes())
check(h({k:v for k,v in freeze.items() if k != "digest"}) == freeze["digest"], "freeze digest")
for descriptor in freeze["artifacts"]:
    raw = (root / descriptor["relative_path"]).read_bytes()
    check(len(raw) == descriptor["bytes"] and hashlib.sha256(raw).hexdigest() ==
          descriptor["sha256"], "new artifact changed")
    check(h({k:v for k,v in descriptor.items() if k != "descriptor_digest"}) ==
          descriptor["descriptor_digest"], "descriptor mismatch")
for descriptor in freeze["parents"].values():
    raw = Path(descriptor["path"]).read_bytes()
    check(len(raw) == descriptor["bytes"] and hashlib.sha256(raw).hexdigest() ==
          descriptor["sha256"], "parent changed")
    parsed = json.loads(raw)
    check(h({k:v for k,v in parsed.items() if k != "digest"}) == descriptor["digest"],
          "parent canonical mismatch")
old = freeze["arm_t_caps_v2_0"]
new = freeze["arm_t_caps_v2_1"]
check({k for k in old if old[k] != new[k]} ==
      {"data_bytes_per_file_max", "transfer_bytes_per_file_max", "transfer_bytes_arm_max"},
      "unexpected behavioral cap change")
check(set(new) - set(old) == {"data_bytes_arm_max"}, "unexpected cap addition")
for scope in freeze["transfer_allocations_v2_1"].values():
    check(scope["footer"] + scope["data"] == scope["total"], "allocation arithmetic")
check(freeze["digest"] in (root / "docs/implementation/STATUS.md").read_text(encoding="utf-8"),
      "status freeze digest absent")
changed = subprocess.run(["git", "diff", "--name-only", "--", "src", "scripts", "tests",
                          "pyproject.toml", "uv.lock", ".python-version",
                          "docs/implementation/reports/ESSENTIAL-WEB-EVIDENCE-V2-PROTOCOL.md",
                          "docs/implementation/evidence/ESSENTIAL-WEB-EVIDENCE-V2"],
                         check=True, capture_output=True, text=True).stdout.strip()
check(not changed, "unexpected product/dependency/parent changes")
print(json.dumps({"status": "VERIFIED", "freeze_digest": freeze["digest"],
                  "protocol_file_sha256": freeze["artifacts"][0]["sha256"],
                  "parent_bindings": len(freeze["parents"]),
                  "artifact_bindings": len(freeze["artifacts"]),
                  "selection_byte_identical": True, "implementation_changed": False,
                  "acquisition_authorized": False}))
```

All verification is offline, real-receipt arithmetic/integrity review. No pytest
suite, live compatibility, GPU, decode or acquisition execution is claimed.

