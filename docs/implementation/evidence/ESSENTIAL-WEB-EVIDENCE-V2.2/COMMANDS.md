# Evidence v2.2 offline audit commands

Run in G:/Project/xlm-data-ultrax, starting HEAD
9e008987454fa8370438ec1f25a61a71196be5ec. No network or corpus text.
The embedded code is a one-off review calculation and bounded diagnostic,
not product implementation. Existing receipts are read-only. Output refuses
to overwrite prior review evidence.

## Independent audit

Exact command (result recorded in REVIEW):

```powershell
$reviewText = Get-Content -Raw -Encoding UTF8 docs/implementation/evidence/ESSENTIAL-WEB-EVIDENCE-V2.2/COMMANDS.md
$auditSource = [regex]::Match($reviewText, '(?s)```python\r?\n(.*?)```').Groups[1].Value
$auditSource | uv run --offline --locked --no-sync --extra cpu --extra eval python -B -
```

```python
import ctypes
import hashlib
import importlib.metadata
import json
import os
import platform
import subprocess
import sys
import time
from pathlib import Path

started = time.perf_counter()
root = Path.cwd()
out = root / "docs/implementation/evidence/ESSENTIAL-WEB-EVIDENCE-V2.2"
external = Path("G:/Project/xlm-evidence-v2/essential-web")
out.mkdir(parents=True, exist_ok=True)
bindings = {}
read_bytes = 0

def check(ok, message):
    if not ok:
        raise ValueError(message)

def canon(obj):
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
                      allow_nan=False).encode("utf-8")

def h(obj):
    return hashlib.sha256(canon(obj)).hexdigest()

def pairs(items):
    obj = {}
    for k, v in items:
        check(k not in obj, "duplicate JSON key")
        obj[k] = v
    return obj

def parse(raw):
    def invalid(value):
        raise ValueError("nonfinite JSON")
    return json.loads(raw.decode("utf-8"), object_pairs_hook=pairs, parse_constant=invalid)

def load(path, name):
    global read_bytes
    check(path.stat().st_size < 2 * 1048576, "bounded receipt read")
    raw = path.read_bytes()
    read_bytes += len(raw)
    obj = parse(raw)
    check(h({k:v for k,v in obj.items() if k != "digest"}) == obj["digest"], name + " digest")
    bindings[name] = {"path": str(path), "bytes": len(raw),
                      "sha256": hashlib.sha256(raw).hexdigest(), "digest": obj["digest"]}
    return obj

def publish(name, obj):
    path = out / name
    check(not path.exists(), "no overwrite: " + name)
    raw = canon(obj) + b"\n"
    tmp = path.with_suffix(path.suffix + ".tmp")
    with tmp.open("xb") as f:
        f.write(raw)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)

parent = load(root / "docs/implementation/evidence/ESSENTIAL-WEB-EVIDENCE-V2.1/freeze.json", "v21_freeze")
check(parent["digest"] == "bac82d6b9538f4005f7f0ffee6aa5c4f3a5394098c0fae8f63fc94c76832a7cd", "v21")
for descriptor in parent["artifacts"]:
    raw = (root / descriptor["relative_path"]).read_bytes()
    read_bytes += len(raw)
    check(len(raw) == descriptor["bytes"] and hashlib.sha256(raw).hexdigest() == descriptor["sha256"],
          "v21 artifact")
    check(h({k:v for k,v in descriptor.items() if k != "descriptor_digest"}) ==
          descriptor["descriptor_digest"], "v21 descriptor")
for name, descriptor in parent["parents"].items():
    obj = load(Path(descriptor["path"]), "v21_parent:" + name)
    check(bindings["v21_parent:" + name]["sha256"] == descriptor["sha256"], "v21 parent byte hash")
m = load(external / "footer_evidence.json", "M_complete")
old = load(external / "footer_evidence.incomplete.json", "M_old")
t = load(external / "text_cost_evidence_attempt2.incomplete.json", "T_costmap")
t_old = load(external / "text_cost_evidence.incomplete.json", "T_old")
selection = load(external / "text_selection_manifest.json", "selection")
child_dir = root / "docs/implementation/evidence/ESSENTIAL-WEB-EVIDENCE-V2.1-CHILD"
mc = load(child_dir / "arm_m_blocked_plan.json", "M_child")
tc = load(child_dir / "arm_t_child_plan_dry.json", "T_child")
for c in [mc, tc]:
    check(c["executable"] is False and c["authorization"] == "NONE", "child auth")
    check(c["freeze_digest"] == parent["digest"], "child parent")
check(selection["digest"] == tc["selection_digest"] == parent["selection_digest"], "selection identity")
check(bindings["selection"]["sha256"] ==
      "8424f9668fef6952a558ead0dae27a705545a7f4a4d302713707d3ec6bb1af27", "selection bytes")
wanted = {}
for cell in selection["cells"]:
    for group in ([cell] if "identities" in cell else cell["crawls"]):
        for repo, revision, file, row in group["identities"]:
            check(repo == m["repository"] and revision == m["revision"], "locator source")
            wanted.setdefault(file, []).append(row)
wanted = {f:sorted(rows) for f,rows in wanted.items()}
check(wanted == tc["wanted_by_file"] and sum(map(len,wanted.values())) == 118, "exact membership")

# Bind and examine both producer generations directly from local git objects.
historical_code = {}
for revision, path in [
    ("943b816", "src/xlm/data/evidence_v2/footer.py"),
    ("943b816", "src/xlm/data/sources/transport.py"),
    ("943b816", "scripts/evidence_v2.py"),
    ("6714160", "src/xlm/data/evidence_v2/footer.py")]:
    raw = subprocess.check_output(["git", "show", revision + ":" + path])
    historical_code[revision + ":" + path] = {
        "sha256": hashlib.sha256(raw).hexdigest(), "bytes": len(raw)}
    if revision == "943b816" and path.endswith("evidence_v2/footer.py"):
        old_code = raw.decode()
        check(old_code.index("if self.hops > frozen.MAX_REDIRECT_HOPS") <
              old_code.index("return super().redirect_request"), "fourth hop not followed")
    if revision == "943b816" and path.endswith("sources/transport.py"):
        transport_code = raw.decode()
        check("self.budget.read_body(fp, self.budget.max_bytes, retain=False)" in transport_code,
              "redirect bodies use transport-wide bound, not 4096")

check(old["budget"]["requests"] == 4 and old["budget"]["failed_requests"] == 1, "old attempts")
check("redirect hop 4" in old["reason"], "old refused transition")
check(len(old["completed_units"]) == 1, "old completed file count")
first = old["completed_units"][0]["file"]
failed = old["failed_file"]
check(first != failed and old["completed_units"][0]["footer_requests_used"] == 3, "old first ranges")
m_rows = []
for u in m["units"]:
    ranges = u["footer_ranges"]
    current_requests = sum(1 + r["hops"] for r in ranges)
    current_bytes = sum(r["bytes"] for r in ranges)
    check(current_requests == u["footer_requests_used"] == 6, "complete physical requests")
    check(current_bytes == u["footer_bytes_used"], "complete range bytes")
    old_requests = 6 if u["file"] == first else 1 if u["file"] == failed else 0
    old_bytes = old["budget"]["transfer_per_file"].get(u["file"], 0)
    requests = old_requests + current_requests
    recorded_bytes = old_bytes + current_bytes
    check(u["retained_rows"] == 512 and u["future_plan_feasible"] is True, "M windows")
    check(u["future_plan_feasibility"]["request_estimate"] == 85, "M data estimate")
    m_rows.append({"file": u["file"], "absolute_window": u["absolute_window"],
                   "etag": u["etag"], "remote_length": u["remote_length"],
                   "historical_requests": requests, "old_requests": old_requests,
                   "complete_requests": current_requests,
                   "recorded_range_body_bytes": recorded_bytes,
                   "all_body_bytes_exact": None,
                   "footer_requests_remaining_v22": 16 - requests,
                   "footer_requests_remaining_after_nominal_revalidation": 16 - requests - 2,
                   "footer_bytes_remaining_from_recorded_only": 4194304 - recorded_bytes})
check(sum(r["historical_requests"] for r in m_rows) == 55, "M history request sum")
check(sum(r["recorded_range_body_bytes"] for r in m_rows) == 2191448, "M history bytes")
check(mc["audit_evidence"]["cumulative_physical_requests"] == m_rows[0]["historical_requests"] == 12,
      "M first-file overrun")
check(mc["adopted_windows"] == [{"file":r["file"],"absolute_window":r["absolute_window"]}
                              for r in m_rows], "M windows identical")

# Existing implementation's audit receipts are captured, not adopted as correct.
commands = []
for arguments, filename in [
    (["verify-v21-freeze"], None),
    (["audit-m-history"], "v21_m_audit_capture.json"),
    (["reconcile-carry-in"], "v21_t_carry_capture.json")]:
    cmd = [sys.executable, "-B", "scripts/evidence_v2.py", *arguments]
    result = subprocess.run(cmd, capture_output=True, check=False)
    check(result.returncode == 0, "offline CLI failed")
    obj = parse(result.stdout)
    commands.append({"command": cmd, "exit_status": result.returncode})
    if filename:
        publish(filename, obj)
    if arguments == ["reconcile-carry-in"]:
        carry_receipt = obj
check(carry_receipt["digest"] == tc["carry_reconciliation_digest"], "carry digest reproduction")
check(carry_receipt["adopted_requests"] == 54 and carry_receipt["adopted_bytes"] == 2231492, "T carry")
check(carry_receipt["readiness"]["ready"] is True, "reproduce claimed carry readiness")

t_rows = []
for u in t["units"]:
    file = u["file"]
    check(u["wanted_rows"] == wanted[file], "T cost membership")
    chunks = [c for g in u["text_costs"]["groups"] for c in g["text_chunks"]]
    check(len(chunks) == 1, "one T chunk")
    c = chunks[0]
    dictionary = c["dictionary_page_offset"]
    start = min(c["offset"], dictionary)
    stop = start + c["compressed_bytes"]
    plan = tc["range_schedule"]["files"][file]
    ranges = plan["nominal_data_ranges"]
    check(ranges[0]["start"] == c["offset"], "child starts at data page")
    check(start < ranges[0]["start"], "dictionary excluded")
    check(ranges[-1]["end"] == c["offset"] + c["compressed_bytes"], "child end")
    check(all(r["bytes"] <= 4194304 for r in ranges), "range body maximum")
    check(sum(r["bytes"] for r in ranges) == c["compressed_bytes"], "nominal bytes")
    check(all(a["end"] == b["start"] for a,b in zip(ranges, ranges[1:])), "internal contiguity")
    check(u["transfer_upper_bytes"] == c["compressed_bytes"] + 4194304, "T transfer bound")
    carried = t["budget"]["transfer_per_file"][file] + t_old["budget"]["transfer_per_file"].get(file,0)
    t_rows.append({"file":file, "dictionary_chunk_start":start, "data_page_start":c["offset"],
                   "required_chunk_end_exclusive":stop,
                   "child_range_start":ranges[0]["start"], "child_range_end":ranges[-1]["end"],
                   "omitted_dictionary_prefix_bytes":c["offset"]-start,
                   "corrected_nominal_range_count":(c["compressed_bytes"]+4194303)//4194304,
                   "recorded_footer_bytes":carried,
                   "data_headroom_bound_only":31457280-u["transfer_upper_bytes"],
                   "total_headroom_recorded_only":33554432-u["transfer_upper_bytes"]-carried,
                   "reservation_bytes":tc["reservations"][file]["reservation_bytes"]})
check(sum(r["corrected_nominal_range_count"] for r in t_rows) == 47, "T count remains 47")
check(sum(u["transfer_upper_bytes"] for u in t["units"]) == 213517601, "T sum")
check(sum(u["decompressed_upper_bytes"] for u in t["units"]) == 311068163, "T decomp")
check(sum(u["scan_rows_upper"] for u in t["units"]) == 38400, "T scan")

# Bounded authored synthetic probes of the implementation itself; no network/text.
from xlm.data.evidence_v2 import budgets, carry, reserves
durable = carry.DurableLedger(budgets.new_arm_t_v21())
durable.ledger.charge_file_request("authored-probe.parquet", kind="data")
durable.ledger.charge_transfer("authored-probe.parquet", 123, kind="data")
durable.ledger.charge_time(7, kind="probe")
probe_path = out / "live_ledger_probe.json"
check(not probe_path.exists(), "probe must be fresh")
durable.save(probe_path)
reloaded = carry.DurableLedger.load(probe_path, budgets.new_arm_t_v21())
before = durable.effective()
after = reloaded.effective()
check((before["requests"], before["response_body_bytes"], before["elapsed_seconds"]) == (1,123,7), "probe before")
check((after["requests"], after["response_body_bytes"], after["elapsed_seconds"]) == (0,0,0), "loss reproduced")
check(probe_path.resolve().parent == out.resolve(), "probe removal confined")
probe_path.unlink()
scratch_probe = reserves.disk_schedule(adopted_artifact_bytes=0,
    per_file_stages=[{"scratch":11,"final":0}], review_package_bytes=2, log_bytes=0,
    scratch_cap=10, final_cap=20, combined_cap=100)
check(scratch_probe["fits"]["scratch"] is True, "scratch undercount reproduced")
final_probe = reserves.disk_schedule(adopted_artifact_bytes=0,
    per_file_stages=[{"scratch":0,"final":21},{"scratch":0,"final":1}],
    review_package_bytes=1, log_bytes=0, scratch_cap=100, final_cap=20, combined_cap=100)
check(final_probe["fits"]["final"] is True, "intermediate final overrun missed")
check("disk_schedule" not in tc and "deadlines" not in tc, "child omissions")

class PM(ctypes.Structure):
    _fields_ = [("cb",ctypes.c_ulong),("faults",ctypes.c_ulong)] + [
        (name,ctypes.c_size_t) for name in
        ["peak_rss","rss","peak_paged","paged","peak_nonpaged","nonpaged","pagefile","peak_pagefile"]]
pm = PM()
pm.cb = ctypes.sizeof(pm)
ctypes.windll.kernel32.GetCurrentProcess.restype = ctypes.c_void_p
fn = ctypes.windll.psapi.GetProcessMemoryInfo
fn.argtypes = [ctypes.c_void_p,ctypes.POINTER(PM),ctypes.c_ulong]
check(fn(ctypes.windll.kernel32.GetCurrentProcess(),ctypes.byref(pm),ctypes.sizeof(pm)) != 0, "RSS measure")
code_bindings = {}
for path in ["scripts/evidence_v2.py", "src/xlm/data/evidence_v2/carry.py",
             "src/xlm/data/evidence_v2/schedule.py", "src/xlm/data/evidence_v2/reserves.py",
             "src/xlm/data/evidence_v2/m_audit.py", "src/xlm/data/evidence_v2/footer.py",
             "src/xlm/data/evidence_v2/budgets.py", "src/xlm/data/evidence_v2/child_plans.py",
             "src/xlm/data/sources/transport.py", "src/xlm/data/acquisition/sampling.py"]:
    raw = (root/path).read_bytes()
    code_bindings[path] = {"bytes":len(raw),"sha256":hashlib.sha256(raw).hexdigest()}
report = {
    "kind":"essential_web_evidence_v22_protocol_readiness_audit",
    "status":"PROTOCOL_AMENDMENT_JUSTIFIED_EXECUTION_BLOCKED",
    "starting_head":subprocess.check_output(["git","rev-parse","HEAD"],text=True).strip(),
    "input_bindings":bindings, "historical_code_bindings":historical_code,
    "reviewed_code_bindings":code_bindings, "M_files":m_rows,
    "M_totals":{"historical_requests_reconstructed":55,"old_physical_requests":7,
                "complete_physical_requests":48,"recorded_range_body_bytes":2191448,
                "all_response_body_bytes_exact":None,
                "unmetered_history_is_blocking":True,
                "footer_requests_remaining_v22":25,
                "nominal_revalidation_physical_requests":16,
                "maximum_revalidation_physical_requests_arm":25,
                "footer_requests_after_nominal":71,
                "whole_arm_with_8x85_data_nominal":751,
                "whole_arm_with_max_footer_and_8x85_data":760,
                "footer_bytes_remaining_recorded_only":33554432-2191448},
    "T_files":t_rows,
    "T_totals":{"carry_requests_recorded":54,"carry_bytes_recorded":2231492,
                "data_ranges_after_dictionary_correction":47,"logical_controls_legacy":16,
                "nominal_logical_future_legacy":63,"legacy_nominal_plus_carry":117,
                "one_redirect_per_logical_plus_carry":180,
                "data_headroom_bound_only":251658240-213517601,
                "total_headroom_recorded_only":268435456-213517601-2231492,
                "omitted_dictionary_prefix_bytes_sum":sum(r["omitted_dictionary_prefix_bytes"] for r in t_rows),
                "memory_reservation_min_bytes":min(r["reservation_bytes"] for r in t_rows),
                "memory_reservation_max_bytes":max(r["reservation_bytes"] for r in t_rows)},
    "implementation_probes":{"live_ledger_before":before,"live_ledger_after_reload":after,
                             "scratch_false_pass":scratch_probe,"intermediate_final_false_pass":final_probe},
    "disk_verdict":"Stage-gated cumulative 16 MiB final limit; no numerical cap change. Acquisition needs a valid preflight; current model is not sufficient. Future labeling size alone is not an inherent acquisition block.",
    "findings":[
        "M known request event total 55; fourth redirect invocation was refused before follow.",
        "M recorded range bytes 2191448 are not all-body exact totals; redirect meter outputs missing.",
        "T all eight range schedules omit dictionary prefixes and extend beyond required chunk ends.",
        "T 4096-byte historical gap bounds are unsupported by producer transport, and gaps are not reserved.",
        "T durable save/load loses post-adoption live request, body and elapsed charges.",
        "T data subcap headroom must not subtract footer consumption; total category stays separate.",
        "T disk helper misses intermediate final occupancy and understates scratch in authored probes.",
        "Disk and deadline schedules absent from bound T child; reported realistic high water not independently reproducible from it.",
        "Memory formula is 89.28..100.18 MiB, not 93.6..105.0 MiB; no process-tree allocator proof.",
        "M 85 requests is leaf-range estimate plus 4 controls, not redirect-aware physical proof.",
        "Existing child parents maps empty; preserve as rejected dry evidence and bind complete lineage in new child plans."
    ],
    "commands":commands,
    "environment":{"platform":platform.platform(),"python":sys.version,
                   "packages_metadata_only":{p:importlib.metadata.version(p) for p in ["torch","pyarrow"]},
                   "uv_lock_sha256":hashlib.sha256((root/"uv.lock").read_bytes()).hexdigest()},
    "audit_measurements":{"receipt_bytes_read":read_bytes,
                          "elapsed_seconds_before_output":time.perf_counter()-started,
                          "current_process_endpoint_rss":pm.rss,"current_process_peak_rss":pm.peak_rss,
                          "network_requests":0,"corpus_text_bytes_read":0},
    "test_scope":"three authored synthetic implementation probes; no pytest/full/live/CUDA; real receipts arithmetic only",
    "exit_status":0
}
report["digest"] = h(report)
publish("verification.json",report)
print(json.dumps({"digest":report["digest"],"M":report["M_totals"],"T":report["T_totals"],
                  "resources":report["audit_measurements"]}))
```

Read-only discovery used Get-Content, rg, git status/log/rev-parse/show and
bounded JSON/source inspections. One piped git-show/Select-Object inspection
returned exit 1 after early pipeline closure; the complete historical sources
are read successfully by the audit subprocesses above. One rg call using the
literal Windows path glob src/xlm/data/acquisition/range* returned exit 1
(OS error 123); the subsequent named sampling.py/fetcher.py reads succeeded.
Neither affected files or was a failed acceptance test.

## Freeze construction

Exact command, exit 0:

```powershell
$reviewText = Get-Content -Raw -Encoding UTF8 docs/implementation/evidence/ESSENTIAL-WEB-EVIDENCE-V2.2/COMMANDS.md
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
folder = root / "docs/implementation/evidence/ESSENTIAL-WEB-EVIDENCE-V2.2"
def canon(obj):
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
                      allow_nan=False).encode("utf-8")
def h(obj):
    return hashlib.sha256(canon(obj)).hexdigest()
def check(ok, message):
    if not ok:
        raise ValueError(message)
def descriptor(path):
    raw = path.read_bytes()
    d = {"relative_path": path.relative_to(root).as_posix(), "bytes":len(raw),
         "sha256":hashlib.sha256(raw).hexdigest(),
         "media_type":"application/json" if path.suffix == ".json" else "text/markdown"}
    d["descriptor_digest"] = h(d)
    return d
audit = json.loads((folder/"verification.json").read_bytes())
check(h({k:v for k,v in audit.items() if k != "digest"}) == audit["digest"], "audit hash")
parent_path = Path(audit["input_bindings"]["v21_freeze"]["path"])
parent = json.loads(parent_path.read_bytes())
old = parent["arm_m_caps_unchanged"]
new = dict(old)
check(old["footer_requests_per_file"] == 10, "old cap")
new["footer_requests_per_file"] = 16
check({k for k in old if new[k] != old[k]} == {"footer_requests_per_file"}, "only one change")
roles = {
    "v21_freeze":"normative_parent",
    "M_complete":"adopted_footer_window_observations_not_old_compliance",
    "M_old":"historical_failed_planning_budget",
    "M_child":"historical_blocked_child_keep_blocked",
    "T_child":"historical_dry_child_reviewed_defective",
    "T_costmap":"historical_incomplete_cost_map",
    "T_old":"historical_planning_budget",
    "selection":"byte_identical_scientific_selection"
}
parents = {}
for name,role in roles.items():
    d = audit["input_bindings"][name]
    raw = Path(d["path"]).read_bytes()
    check(len(raw) == d["bytes"] and hashlib.sha256(raw).hexdigest() == d["sha256"], "parent drift")
    obj = json.loads(raw)
    check(h({k:v for k,v in obj.items() if k != "digest"}) == d["digest"], "parent digest")
    parents[name] = {**d,"role":role,"original_protocol_version":obj["protocol_version"],
                     "original_status":obj.get("status")}
artifact_names = [
    "docs/implementation/reports/ESSENTIAL-WEB-EVIDENCE-V2.2-PROTOCOL.md",
    "docs/implementation/reports/ESSENTIAL-WEB-EVIDENCE-V2.2-REVIEW.md",
    "docs/implementation/evidence/ESSENTIAL-WEB-EVIDENCE-V2.2/COMMANDS.md",
    "docs/implementation/evidence/ESSENTIAL-WEB-EVIDENCE-V2.2/verification.json",
    "docs/implementation/evidence/ESSENTIAL-WEB-EVIDENCE-V2.2/v21_m_audit_capture.json",
    "docs/implementation/evidence/ESSENTIAL-WEB-EVIDENCE-V2.2/v21_t_carry_capture.json"
]
m_capture = json.loads((folder/"v21_m_audit_capture.json").read_bytes())
t_capture = json.loads((folder/"v21_t_carry_capture.json").read_bytes())
freeze = {
    "kind":"essential_web_evidence_protocol_amendment_freeze","schema_version":1,
    "protocol_version":"essential-web-evidence-v2.2","frozen_date":"2026-09-28",
    "scope":"PROTOCOL_AND_READINESS_REVIEW_ONLY_NOT_ACQUISITION_AUTHORIZATION",
    "canonical_scheme":"UTF-8 json.dumps(sort_keys=True,separators=(',',':'),ensure_ascii=False,allow_nan=False); self-digest excludes only top-level digest",
    "source_checkout":audit["starting_head"],
    "parent_freeze_digest":parent["digest"],
    "repository":parent["repository"],"revision":parent["revision"],
    "policy_digest":parent["policy_digest"],
    "selection_digest":parent["selection_digest"],
    "selection_file_sha256":parent["selection_file_sha256"],"selection_total":118,
    "scientific_identity_namespace":"essential-web-evidence-v2.0",
    "metadata_seed":20260927,"text_selection_seed":20260927,"review_order_seed":20260928,
    "arm_m_caps_v2_1":old,"arm_m_caps_v2_2":new,
    "arm_t_caps_unchanged_from_v2_1":parent["arm_t_caps_v2_1"],
    "M_accounting":{
        "file_history":audit["M_files"],"known_event_requests":55,
        "recorded_range_body_bytes":2191448,"all_response_body_bytes_exact":None,
        "missing_history_blocks_execution":True,
        "historical_status":"v2.0/v2.1 first-file 12>10 request cap violation remains",
        "reconciliation_digest":audit["digest"],
        "v21_first_file_audit_capture_content_digest":h(m_capture)},
    "M_prospective_revalidation":{
        "file_order":"inherited frozen crawl order",
        "logical_request":"GET bytes=0-3 inclusive",
        "expected_status":206,"expected_magic":"PAR1",
        "identity":"exact adopted strong ETag and Content-Range total length; revision/path/plan checked locally",
        "logical_requests_per_file":1,
        "nominal_physical_requests_per_file":2,"nominal_physical_requests_arm":16,
        "max_prospective_physical_requests_per_file":4,
        "max_prospective_physical_requests_arm":25,
        "includes":"originals, followed redirects, retries, failed requests; no budget reset",
        "nominal_cumulative_footer_requests":71,
        "nominal_four_byte_payload_sum":32,
        "nominal_8x85_data_plus_history_revalidation":751,
        "max_footer_plus_8x85_data":760,
        "failure_policy":"STOP INCOMPLETE before any binding cap; do not guarantee all theoretical retries"},
    "T_disk_clarification":{
        "decision":"stage-gated cumulative acquisition and publication; no cap increase",
        "final_bytes_max":16777216,"combined_bytes_max":536870912,
        "retained_text_bytes_max":8388608,
        "acquisition_preflight_required":True,"later_publication_requires_remaining_shared_budget":True,
        "current_child_disk_readiness":"BLOCKED pending corrected inventory and tests"},
    "T_readiness":"BLOCKED beyond authorization: dictionary ranges, history, durable live state, disk, supervision and complete child bindings",
    "v21_t_carry_capture_digest":t_capture["digest"],
    "parents":parents,"artifacts":[descriptor(root/p) for p in artifact_names],
    "reviewed_code_bindings":audit["reviewed_code_bindings"],
    "historical_code_bindings":audit["historical_code_bindings"],
    "rationale":"Footer-only operational deviation does not change scientific membership. 16 permits 12 historical requests plus one original and three redirects; 55+16 nominal revalidation fits unchanged footer arm 80 and whole arm 880. Old violation and missing history remain explicit; no other numerical ceiling changes.",
    "environment":audit["environment"],
    "authorization":{"network":False,"arm_M_acquisition":False,"arm_T_acquisition":False,
                     "corpus_text_inspection":False},
    "required_sequence":["Muse offline implementation","new offline child-plan review",
                         "separate acquisition authorization review"],
    "verdict":"READY TO IMPLEMENT EVIDENCE V2.2"
}
freeze["digest"] = h(freeze)
target = folder/"freeze.json"
check(not target.exists(), "do not overwrite freeze")
temp = folder/"freeze.json.tmp"
with temp.open("xb") as f:
    f.write(json.dumps(freeze,sort_keys=True,indent=2,ensure_ascii=False,allow_nan=False).encode()+b"\n")
    f.flush()
    os.fsync(f.fileno())
os.replace(temp,target)
print(json.dumps({"freeze_digest":freeze["digest"],
                  "protocol_sha256":freeze["artifacts"][0]["sha256"],
                  "freeze_file_sha256":hashlib.sha256(target.read_bytes()).hexdigest()}))
```

## Final validation

Run after STATUS records the freeze digest. Exact commands, each exit 0:

```powershell
$reviewText = Get-Content -Raw -Encoding UTF8 docs/implementation/evidence/ESSENTIAL-WEB-EVIDENCE-V2.2/COMMANDS.md
$verifySection = $reviewText.Substring($reviewText.IndexOf('## Final validation'))
$verifySource = [regex]::Match($verifySection, '(?s)```python\r?\n(.*?)```').Groups[1].Value
$verifySource | uv run --offline --locked --no-sync --extra cpu --extra eval python -B -
git diff --check -- docs/implementation/STATUS.md docs/implementation/reports/ESSENTIAL-WEB-EVIDENCE-V2.2-PROTOCOL.md docs/implementation/reports/ESSENTIAL-WEB-EVIDENCE-V2.2-REVIEW.md docs/implementation/evidence/ESSENTIAL-WEB-EVIDENCE-V2.2
```

```python
import hashlib
import json
import subprocess
from pathlib import Path
root = Path.cwd()
folder = root/"docs/implementation/evidence/ESSENTIAL-WEB-EVIDENCE-V2.2"
def h(x):
    return hashlib.sha256(json.dumps(x,sort_keys=True,separators=(",",":"),
                                    ensure_ascii=False,allow_nan=False).encode()).hexdigest()
def check(ok,message):
    if not ok:
        raise ValueError(message)
freeze = json.loads((folder/"freeze.json").read_bytes())
check(h({k:v for k,v in freeze.items() if k != "digest"}) == freeze["digest"], "freeze digest")
for d in freeze["artifacts"]:
    raw=(root/d["relative_path"]).read_bytes()
    check(len(raw)==d["bytes"] and hashlib.sha256(raw).hexdigest()==d["sha256"],"artifact drift")
    check(h({k:v for k,v in d.items() if k!="descriptor_digest"})==d["descriptor_digest"],"descriptor")
for d in freeze["parents"].values():
    raw=Path(d["path"]).read_bytes()
    check(len(raw)==d["bytes"] and hashlib.sha256(raw).hexdigest()==d["sha256"],"parent drift")
    obj=json.loads(raw)
    check(h({k:v for k,v in obj.items() if k!="digest"})==d["digest"],"parent digest")
for p,d in freeze["reviewed_code_bindings"].items():
    check(hashlib.sha256((root/p).read_bytes()).hexdigest()==d["sha256"],"reviewed code drift")
old,new=freeze["arm_m_caps_v2_1"],freeze["arm_m_caps_v2_2"]
check({k for k in old if old[k]!=new[k]}=={"footer_requests_per_file"},"unexpected cap change")
check(new["footer_requests_per_file"]==16 and new["footer_requests_total"]==80
      and new["data_requests_per_plan"]==100 and new["requests_total"]==880,"M caps")
check(sum(f["historical_requests"] for f in freeze["M_accounting"]["file_history"])==55,"M requests")
check(sum(f["recorded_range_body_bytes"] for f in freeze["M_accounting"]["file_history"])==2191448,"M bytes")
check(freeze["digest"] in (root/"docs/implementation/STATUS.md").read_text(encoding="utf-8"),"status")
changed=subprocess.run(["git","diff","--name-only","--","src","scripts","tests","pyproject.toml",
                        "uv.lock",".python-version",
                        "docs/implementation/reports/ESSENTIAL-WEB-EVIDENCE-V2-PROTOCOL.md",
                        "docs/implementation/reports/ESSENTIAL-WEB-EVIDENCE-V2.1-PROTOCOL.md",
                        "docs/implementation/evidence/ESSENTIAL-WEB-EVIDENCE-V2",
                        "docs/implementation/evidence/ESSENTIAL-WEB-EVIDENCE-V2.1",
                        "docs/implementation/evidence/ESSENTIAL-WEB-EVIDENCE-V2.1-CHILD"],
                       capture_output=True,text=True,check=True).stdout.strip()
check(not changed,"source/dependency/old frozen artifacts changed")
print(json.dumps({"status":"VERIFIED_PROTOCOL_FREEZE_NOT_ACQUISITION_READY",
                  "freeze_digest":freeze["digest"],"protocol_sha256":freeze["artifacts"][0]["sha256"],
                  "new_artifact_bindings":len(freeze["artifacts"]),"parent_bindings":len(freeze["parents"]),
                  "selection_byte_identical":True,"implementation_changes":False}))
```

The independent audit exited 0. Its three synthetic diagnostic probes reproduced
implementation defects; this is not a product test-suite pass. No pytest suite,
full acceptance, live, CUDA or acquisition run occurred. Old inputs are unchanged.

