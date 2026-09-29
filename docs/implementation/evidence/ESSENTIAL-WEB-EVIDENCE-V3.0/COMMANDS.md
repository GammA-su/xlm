# Evidence v3.0 protocol-lineage audit commands

Repository: G:/Project/xlm-data-ultrax. HEAD:
4051e5c4052e5e04da85f14b791a8b406bb9e8b3. No network, acquisition,
corpus text, new selection, product implementation, tests, install or push.
This embedded one-off audit hashes local evidence and calculates prospective
schedules. It does not create or activate the execution root/epoch.

## Independent audit

Exact invocation (exit status recorded in REVIEW):

```powershell
$reviewText = Get-Content -Raw -Encoding UTF8 docs/implementation/evidence/ESSENTIAL-WEB-EVIDENCE-V3.0/COMMANDS.md
$auditSource = [regex]::Match($reviewText, '(?s)```python\r?\n(.*?)```').Groups[1].Value
$auditSource | uv run --offline --locked --no-sync --extra cpu --extra eval python -B -
```

```python
import hashlib
import importlib.metadata
import json
import os
import platform
import subprocess
import sys
import time
from pathlib import Path

root=Path.cwd()
out=root/"docs/implementation/evidence/ESSENTIAL-WEB-EVIDENCE-V3.0"
out.mkdir(parents=True,exist_ok=True)
started=time.perf_counter()
read_count=0
def check(ok,message):
    if not ok:
        raise ValueError(message)
def canon(x):
    return json.dumps(x,sort_keys=True,separators=(",",":"),ensure_ascii=False,allow_nan=False).encode("utf-8")
def h(x):
    return hashlib.sha256(canon(x)).hexdigest()
def pairs(items):
    result={}
    for k,v in items:
        check(k not in result,"duplicate key")
        result[k]=v
    return result
def parsed(raw):
    def invalid(x):
        raise ValueError("nonfinite JSON")
    return json.loads(raw.decode("utf-8"),object_pairs_hook=pairs,parse_constant=invalid)
def read(path):
    global read_count
    check(path.stat().st_size<=2*1048576,"bounded local read")
    raw=path.read_bytes()
    read_count+=len(raw)
    return raw
def bind(path):
    raw=read(path)
    result={"path":str(path),"bytes":len(raw),"sha256":hashlib.sha256(raw).hexdigest()}
    if path.suffix==".json":
        obj=parsed(raw)
        result["canonical_content_digest"]=h(obj)
        if isinstance(obj,dict) and "digest" in obj:
            check(h({k:v for k,v in obj.items() if k!="digest"})==obj["digest"],str(path)+" self-digest")
            result["digest"]=obj["digest"]
        if isinstance(obj,dict):
            result["original_version"]=obj.get("protocol_version")
            result["original_status"]=obj.get("status")
    return result
history={}
for name in ["ESSENTIAL-WEB-EVIDENCE-V2","ESSENTIAL-WEB-EVIDENCE-V2.1",
             "ESSENTIAL-WEB-EVIDENCE-V2.1-CHILD","ESSENTIAL-WEB-EVIDENCE-V2.2",
             "ESSENTIAL-WEB-EVIDENCE-V2.2-CHILD"]:
    for path in sorted((root/"docs/implementation/evidence"/name).iterdir()):
        if path.is_file() and path.suffix in (".json",".md"):
            history[path.relative_to(root).as_posix()]=bind(path)
for name in ["ESSENTIAL-WEB-EVIDENCE-V2-PROTOCOL.md","ESSENTIAL-WEB-EVIDENCE-V2-IMPLEMENTATION.md",
             "ESSENTIAL-WEB-EVIDENCE-V2.1-PROTOCOL.md","ESSENTIAL-WEB-EVIDENCE-V2.1-REVIEW.md",
             "ESSENTIAL-WEB-EVIDENCE-V2.1-IMPLEMENTATION.md","ESSENTIAL-WEB-EVIDENCE-V2.2-PROTOCOL.md",
             "ESSENTIAL-WEB-EVIDENCE-V2.2-REVIEW.md","ESSENTIAL-WEB-EVIDENCE-V2.2-IMPLEMENTATION.md"]:
    path=root/"docs/implementation/reports"/name
    history[path.relative_to(root).as_posix()]=bind(path)
external=Path("G:/Project/xlm-evidence-v2/essential-web")
for name in ["footer_evidence.json","footer_evidence.incomplete.json","text_cost_evidence_attempt2.incomplete.json",
             "text_cost_evidence.incomplete.json","text_selection_manifest.json","dry_arm_m_plan.json"]:
    history["external/"+name]=bind(external/name)
for version in ["V2","V2.1","V2.2"]:
    fp=root/f"docs/implementation/evidence/ESSENTIAL-WEB-EVIDENCE-{version}/freeze.json"
    f=parsed(read(fp))
    for d in f["artifacts"]:
        raw=read(root/d["relative_path"])
        check(len(raw)==d["bytes"] and hashlib.sha256(raw).hexdigest()==d["sha256"],"old frozen artifact")
        check(h({k:v for k,v in d.items() if k!="descriptor_digest"})==d["descriptor_digest"],"descriptor")
    for d in f.get("parents",{}).values():
        raw=read(Path(d["path"]))
        check(len(raw)==d["bytes"] and hashlib.sha256(raw).hexdigest()==d["sha256"],"old parent")
v22=parsed(read(root/"docs/implementation/evidence/ESSENTIAL-WEB-EVIDENCE-V2.2/freeze.json"))
m=parsed(read(external/"footer_evidence.json"))
t=parsed(read(external/"text_cost_evidence_attempt2.incomplete.json"))
selection=parsed(read(external/"text_selection_manifest.json"))
child=root/"docs/implementation/evidence/ESSENTIAL-WEB-EVIDENCE-V2.2-CHILD"
terminal=parsed(read(child/"readiness_review.json"))
ms=parsed(read(child/"range_schedule_m.json"))
ts=parsed(read(child/"range_schedule_t.json"))
check(all(x["status"]=="BLOCKED" and x["executable"] is False for x in terminal["arms"].values()),"terminal blocks")
check(selection["digest"]=="975ba3dee4af0598e665ea05c69bbe49336c3e3a35fca777a863073190b78474","selection")
check(history["external/text_selection_manifest.json"]["sha256"]==
      "8424f9668fef6952a558ead0dae27a705545a7f4a4d302713707d3ec6bb1af27","selection bytes")
wanted={}
for cell in selection["cells"]:
    for group in ([cell] if "identities" in cell else cell["crawls"]):
        for repository,revision,file,row in group["identities"]:
            check(repository==m["repository"] and revision==m["revision"],"source")
            wanted.setdefault(file,[]).append(row)
check(sum(map(len,wanted.values()))==118 and all(len(r)==len(set(r)) for r in wanted.values()),"unique locators")
m_files=[]
for u in m["units"]:
    leaves=u["window_report"]["projected_physical_leaves"]
    check(len(leaves)==81 and all(0<x["compressed_bytes"]<=4194304 for x in leaves),"81 bounded leaf chunks")
    check(all(not any(k in x for k in ("offset","data_page_offset","dictionary_page_offset")) for x in leaves),"M offsets absent")
    check(u["retained_rows"]==512 and u["absolute_window"][1]-u["absolute_window"][0]==512,"M window")
    footer_range=u["footer_ranges"][-1]
    length=u["remote_length"]
    check(footer_range["end"]==length-9,"recorded complete footer ends before trailer")
    start=footer_range["start"]
    footer_and_trailer=length-start
    check(footer_and_trailer<=4194304,"M footer bound")
    data=sum(x["compressed_bytes"] for x in leaves)
    unc=sum(x["uncompressed_bytes"] for x in leaves)
    current=ms["plans"][u["file"]]
    check(current["positions"]=="UNRESOLVED_until_revalidated_execution" and current["max_physical_requests"]==88,"M old model")
    m_files.append({"file":u["file"],"window":u["absolute_window"],"etag":u["etag"],"remote_length":length,
                    "phase_P_ranges_inclusive":[[0,3],[start,length-1]],
                    "phase_P_payload_bytes":4+footer_and_trailer,
                    "phase_D_identity_range_inclusive":[0,3],"data_chunk_count":81,
                    "data_chunk_offsets_known":False,"data_payload_bytes":data,"data_uncompressed_bytes":unc,
                    "P_logical":2,"P_nominal_physical":3,"P_cold_chain_max_no_retry":5,
                    "D_footer_nominal":2,"D_footer_cold_chain_max":4,
                    "D_data_physical_no_retry":81,
                    "file_footer_payload_both_phases":8+footer_and_trailer,
                    "requests_nominal_all":86,"requests_cold_chain_max_all_no_retry":90})
t_files=[]
for u in t["units"]:
    file=u["file"]
    check(sorted(wanted[file])==u["wanted_rows"],"T membership")
    chunks=[c for g in u["text_costs"]["groups"] for c in g["text_chunks"]]
    check(len(chunks)==1,"single chunk")
    c=chunks[0]
    start=min(c["offset"],c["dictionary_page_offset"])
    stop=start+c["compressed_bytes"]
    rs=ts["files"][file]["nominal_data_ranges"]
    check(rs[0]["start"]==start and rs[-1]["end"]==stop,"corrected T boundaries")
    check(all(r["end"]-r["start"]==r["bytes"] and 0<r["bytes"]<=4194304 for r in rs),"T sizes")
    check(all(a["end"]==b["start"] for a,b in zip(rs,rs[1:])),"T continuity")
    n=len(rs)
    t_files.append({"file":file,"etag":u["etag"],"remote_length":u["remote_length"],
                    "data_range_count":n,"data_ranges_half_open":rs,
                    "data_payload_bytes":c["compressed_bytes"],"data_transfer_upper":u["transfer_upper_bytes"],
                    "phase_P_range_pattern":["0-3","N-8..N-1","N-8-L..N-9 after trailer establishes L"],
                    "P_logical":3,"P_nominal_physical":4,"P_cold_chain_max_no_retry":6,
                    "D_footer_nominal":2,"D_footer_cold_chain_max":4,
                    "D_data_physical_no_retry":n,"requests_nominal_all":6+n,
                    "requests_cold_chain_max_all_no_retry":10+n,
                    "footer_byte_reservation":2097152,"retained_rows":u["selected_count"]})
check(sum(x["data_range_count"] for x in t_files)==47,"T ranges")
check(sum(x["data_payload_bytes"] for x in t_files)==179963169,"T bytes")
m_footer=sum(x["file_footer_payload_both_phases"] for x in m_files)
m_data=sum(x["data_payload_bytes"] for x in m_files)
check(m_footer==1398448 and m_data==11692530,"prospective M byte arithmetic")
t_bound=sum(x["data_transfer_upper"] for x in t_files)
check(t_bound==213517601,"T bound")
# All artifacts are read-only; bind current implementation and locked environment.
code={}
paths=list((root/"src/xlm/data/evidence_v2").glob("*.py"))
paths += [root/p for p in ["scripts/evidence_v2.py","src/xlm/data/sources/transport.py",
         "src/xlm/data/acquisition/sampling.py","src/xlm/data/acquisition/selection.py",
         "src/xlm/data/acquisition/fetcher.py","src/xlm/data/acquisition/plan.py",
         "pyproject.toml","uv.lock",".python-version","recipes/selectors/essential_web_selector_sweep_v1.yaml"]]
for path in sorted(paths):
    code[path.relative_to(root).as_posix()]=bind(path)
new_root=Path("G:/Project/xlm-evidence-v3/essential-web")
check(not new_root.exists(),"proposed fresh root already exists; refuse assumed freshness")
report={
    "kind":"essential_web_evidence_v3_lineage_decision_audit",
    "starting_head":subprocess.check_output(["git","rev-parse","HEAD"],text=True).strip(),
    "history_bindings":history,"implementation_bindings":code,
    "parent_v22_freeze_digest":v22["digest"],"terminal_v22_readiness_digest":terminal["digest"],
    "scientific_identity":{"selection_digest":selection["digest"],
        "selection_sha256":history["external/text_selection_manifest.json"]["sha256"],
        "M_observation_digest":m["digest"],"T_costmap_digest":t["digest"],
        "selection_total":118,"M_windows":[{"file":x["file"],"window":x["window"]} for x in m_files]},
    "M_prospective_files":m_files,"T_prospective_files":t_files,
    "prospective_totals":{
        "M_footer_logical":24,"M_footer_nominal_physical":40,"M_footer_cold_max_no_retry":72,
        "M_data_physical_no_retry":648,"M_total_nominal":688,"M_total_cold_max_no_retry":720,
        "M_footer_payload_both_phases":m_footer,"M_data_payload":m_data,
        "M_nominal_payload_total_excludes_redirect_error":m_footer+m_data,
        "M_data_byte_headroom":234881024-m_data,"M_footer_payload_headroom":33554432-m_footer,
        "M_total_payload_headroom":268435456-m_footer-m_data,
        "T_footer_logical":32,"T_footer_nominal_physical":48,"T_footer_cold_max_no_retry":80,
        "T_data_physical_no_retry":47,"T_total_nominal":95,"T_total_cold_max_no_retry":127,
        "T_footer_bytes_reserved":16777216,"T_data_transfer_upper":t_bound,
        "T_upper_with_full_footer_reserve":t_bound+16777216,
        "T_total_headroom_with_full_footer_reserve":268435456-t_bound-16777216,
        "T_data_bound_headroom":251658240-t_bound,
        "T_nominal_data_payload_headroom":251658240-179963169,
        "M_decompressed_chunk_sum":sum(x["data_uncompressed_bytes"] for x in m_files),
        "T_decompressed_chunk_sum":sum(x["decompressed_upper_bytes"] for x in t["units"])},
    "fresh_root":str(new_root),"fresh_root_absent_verified":True,"epoch_started":False,
    "resource_review_findings":[
        "M 88/plan is conditional 85+3 accounting, not a resolved executable byte-range plan; M offsets absent.",
        "Both existing reader and decode path require Parquet schema/codec footer metadata; no raw old footer cache is adopted.",
        "Current ProcessTreeSupervisor inserts zero for missing owned PIDs and tree reader skips unreadable children; fail-closed coverage not certified.",
        "DeadlineTracker persists arm elapsed only, not file elapsed or monotonic anchors; runtime integration still required.",
        "begin_range uses end-start while footer transport ranges are inclusive and does not durably write before issuing.",
        "DiskInventory is a logical map; physical writes/deletions and crash reconciliation require binding. Published disk_schedule uses older modeled helper.",
        "Current mechanisms must be integrated and focused-tested for v3; no new live or synthetic test was run in this lineage review."
    ],
    "environment":{"platform":platform.platform(),"python":sys.version,
        "packages_metadata_only":{p:importlib.metadata.version(p) for p in ["torch","pyarrow","psutil"]}},
    "measurements":{"bounded_file_bytes_read":read_count,
        "elapsed_seconds_before_output":time.perf_counter()-started,"network_requests":0,"corpus_text_bytes_read":0},
    "exit_status":0
}
report["digest"]=h(report)
target=out/"verification.json"
check(not target.exists(),"no overwrite")
temporary=out/"verification.json.tmp"
with temporary.open("xb") as f:
    f.write(canon(report)+b"\n")
    f.flush()
    os.fsync(f.fileno())
os.replace(temporary,target)
print(json.dumps({"digest":report["digest"],"totals":report["prospective_totals"],
                  "measurements":report["measurements"],"history_files":len(history),
                  "code_files":len(code)}))
```

Read-only discovery used Get-Content, rg, git status/rev-parse and bounded
JSON inspections. An rg search included the nonexistent window_reader.py;
it reported OS error 2, then rg located the actual reader in selection.py.
No files were affected and no acceptance test was run or retried.

## Freeze construction and final validation

Executed once after protocol/report/command-log authoring was complete.
This creates data-only decision artifacts, prepends STATUS, then reads back
all bindings and checks preservation/whitespace. final_validation.json sits
outside its own freeze graph to avoid circular hashes. The one-off script
is not a v3 implementation or executable acquisition plan.

An initial tool-call draft of this command-log appendix had a JavaScript
quoting SyntaxError before execution; no command or write ran from that call.
The corrected authoring call below is the only freeze construction run.

```powershell
$reviewText = Get-Content -Raw -Encoding UTF8 docs/implementation/evidence/ESSENTIAL-WEB-EVIDENCE-V3.0/COMMANDS.md
$freezeSource = [regex]::Match($reviewText, '(?s)## Freeze construction and final validation.*?```python\r?\n(.*?)```').Groups[1].Value
$freezeSource | uv run --offline --locked --no-sync --extra cpu --extra eval python -B -
git status --short
```

```python
import hashlib
import json
import os
import subprocess
import time
from pathlib import Path

started=time.perf_counter()
root=Path.cwd()
out=root/"docs/implementation/evidence/ESSENTIAL-WEB-EVIDENCE-V3.0"
read_bytes=0
def check(ok,message):
    if not ok:
        raise ValueError(message)
def canonical(obj):
    return json.dumps(obj,sort_keys=True,separators=(",",":"),ensure_ascii=False,allow_nan=False).encode("utf-8")
def digest(obj):
    return hashlib.sha256(canonical(obj)).hexdigest()
def read(path):
    global read_bytes
    check(path.stat().st_size<=2*1048576,"bounded read")
    raw=path.read_bytes()
    read_bytes+=len(raw)
    return raw
def load(path):
    obj=json.loads(read(path))
    if isinstance(obj,dict) and "digest" in obj:
        check(digest({k:v for k,v in obj.items() if k!="digest"})==obj["digest"],"self-digest "+str(path))
    return obj
def atomic(path,raw,replace=False):
    check(replace or not path.exists(),"refuse overwrite")
    temporary=path.with_name(path.name+".tmp")
    with temporary.open("xb") as stream:
        stream.write(raw)
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary,path)
def save(name,obj):
    result={**obj,"digest":digest(obj)}
    atomic(out/name,canonical(result)+b"\n")
    return result
def descriptor(path,role):
    raw=read(path)
    result={"relative_path":path.relative_to(root).as_posix(),"bytes":len(raw),
            "sha256":hashlib.sha256(raw).hexdigest(),"role":role}
    if path.suffix==".json":
        obj=json.loads(raw)
        result["canonical_content_digest"]=digest(obj)
        if "digest" in obj:
            result["digest"]=obj["digest"]
    result["descriptor_digest"]=digest(result)
    return result
def verify(binding,path):
    raw=read(path)
    check(len(raw)==binding["bytes"] and hashlib.sha256(raw).hexdigest()==binding["sha256"],"binding "+str(path))
    if path.suffix==".json":
        obj=json.loads(raw)
        if "digest" in obj:
            check(digest({k:v for k,v in obj.items() if k!="digest"})==obj["digest"],"parent self-digest")
        if "canonical_content_digest" in binding:
            check(digest(obj)==binding["canonical_content_digest"],"canonical binding")
        if "digest" in binding:
            check(obj["digest"]==binding["digest"],"bound object digest")

audit=load(out/"verification.json")
head=subprocess.check_output(["git","rev-parse","HEAD"],text=True).strip()
check(head==audit["starting_head"],"reviewed HEAD")
for group in ("history_bindings","implementation_bindings"):
    for binding in audit[group].values():
        verify(binding,Path(binding["path"]))
old=load(root/"docs/implementation/evidence/ESSENTIAL-WEB-EVIDENCE-V2.2/freeze.json")
execution_root=Path("G:/Project/xlm-evidence-v3/essential-web")
check(not execution_root.exists(),"fresh root must remain absent")
epoch_id="essential-web-evidence-v3.0:essential-web:epoch-0001"
revision="ce4eccc7e9604667b6d7f32cb6274b8b41f3113d"
first=audit["M_prospective_files"][0]
first_operation={
    "authorization_now":"NONE","eligible_only_after":"independent phase-P authorization and durable epoch_start",
    "arm":"M","file":first["file"],"method":"GET","range_header":"bytes=0-3",
    "canonical_url":"https://huggingface.co/datasets/EssentialAI/essential-web-v1.0/resolve/"+revision+"/"+first["file"],
    "expected_status":206,"expected_content_range":"bytes 0-3/"+str(first["remote_length"]),
    "expected_magic":"PAR1","expected_strong_etag":first["etag"],
    "charge_category":"footer","data_pages_authorized":False}
epoch=save("epoch_definition.json",{
    "kind":"prospective_epoch_definition_not_live_genesis","protocol_version":"essential-web-evidence-v3.0",
    "execution_epoch_id":epoch_id,"state":"NOT_STARTED","execution_root":execution_root.as_posix(),
    "root_absent_at_decision":True,"authorization":"NONE","network_events_before_genesis":0,
    "live_ledgers_created":False,"v3_historical_network_carry_in":False,
    "budget_interval":"all v3 machine execution from guarded genesis; excludes disclosed pre-epoch v2 history",
    "epoch_start_artifact":"epoch_start.json in execution_root; NOT CREATED by this decision",
    "epoch_start_required_bindings":["protocol freeze digest","approved implementation commit and code/dependency hashes",
        "phase authorization and plan hashes","epoch ID","absolute resolved root","UTC start and clock anchors",
        "zero prior v3 network events","initial inventory/reservations","exclusive owner"],
    "start_rule":"durable atomic genesis and initial ledgers before any socket; failure keeps gate closed",
    "once_only":True,"automatic_second_epoch":False,
    "restart_rule":"same ledgers, totals, reservations and remaining budgets; no reset or fresh root",
    "crash_rule":"uncertain issuance/body/work stays conservatively reserved or BLOCKED, never refunded",
    "imports_allowed":"hash-allowlisted planning metadata and scientific identity only; actual copies consume v3 resources",
    "imports_forbidden":["v2 raw-response caches","partials","corpus text","live ledgers","signed URLs"],
    "root_rules":["refuse preexisting or dirty root","refuse link/reparse escape","all caches/temps/output within counted root",
        "no ledger loss/rollback/repeated genesis","no alternate root under epoch ID","no X: or shared cache spill"],
    "meters":{"network":"actual physical attempts/redirects/retries and all returned bodies",
        "time":"cumulative active machine seconds per arm/file, absolute per-request timeout; also report wall time",
        "pause":"only sealed quiescent administrative review with workers stopped; no active work or counter reset",
        "disk":"actual current and peak footprint including controls/partials/caches/temp/duplicates, persistent during pause",
        "memory":"live process-tree RSS plus conservative allocator/base/output reservations; unknown live usage fails closed",
        "work":"all parsing/decompression/scan/output/retry/cleanup, across both stages"},
    "resource_categories_shared_across_stages":True,"cross_arm_borrowing":False,
    "shared_control_files":"conservatively charged to both arms","first_network_boundary":first_operation,
    "stages":["offline implementation and DRY plans","independent phase-P authorization review",
        "operator phase-P footer preparation","quiescent exact-plan freeze/review",
        "separate phase-D authorization","operator data execution",
        "M reporting before T unblinding; later publication spends remaining resources"]})
closure=save("lineage_closure.json",{
    "kind":"permanent_prior_execution_lineage_closure","issued_under":"essential-web-evidence-v3.0",
    "versions":["essential-web-evidence-v2.0","essential-web-evidence-v2.1","essential-web-evidence-v2.2"],
    "status":"CLOSED_NON_EXECUTABLE","historical_accounting":"HISTORICALLY_UNCERTIFIABLE",
    "terminal_v22_readiness_digest":audit["terminal_v22_readiness_digest"],
    "parent_v22_freeze_digest":audit["parent_v22_freeze_digest"],
    "history_bindings":audit["history_bindings"],"original_bytes_and_statuses_preserved":True,
    "historical_footer_network_did_occur":True,"successful_authorized_data_acquisition_established":False,
    "known_history":{"M":{"physical_requests":55,"recorded_range_body_bytes":2191448,
        "first_file_historical_request_violation":"12 > 10 under original cap"},
        "T":{"recorded_requests":54,"recorded_range_body_bytes":2231492}},
    "unknown_history":["redirect/error-body byte totals","elapsed time"],"old_body_ceiling_bytes":268435456,
    "reason":"missing measurements cannot establish cumulative compliance; code patches cannot recover them",
    "future_discovered_facts":"append provenance only; no silent reopening",
    "v3_claim":"prospective interval only, never cumulative lifetime project-cost compliance",
    "v3_network_charge_from_history":False,"old_costs_are_zero":False})
planning_keys=["external/footer_evidence.json","external/text_cost_evidence_attempt2.incomplete.json",
    "docs/implementation/evidence/ESSENTIAL-WEB-EVIDENCE-V2.2-CHILD/range_schedule_t.json"]
planning={key:{**audit["history_bindings"][key],"v3_role":"adopted_planning_observation",
    "v3_budget_history":False,"identity_revalidation_required":True} for key in planning_keys}
protocol=root/"docs/implementation/reports/ESSENTIAL-WEB-EVIDENCE-V3.0-PROTOCOL.md"
review=root/"docs/implementation/reports/ESSENTIAL-WEB-EVIDENCE-V3.0-REVIEW.md"
artifacts=[descriptor(path,role) for path,role in [
    (protocol,"normative_protocol"),(review,"decision_and_exact_handoff"),
    (out/"COMMANDS.md","reproducible_offline_audit_and_freeze_source"),
    (out/"verification.json","independent_local_evidence_audit"),
    (out/"epoch_definition.json","not_started_epoch_definition"),
    (out/"lineage_closure.json","permanent_prior_lineage_closure")]]
freeze=save("freeze.json",{
    "kind":"essential_web_evidence_protocol_lineage_freeze","protocol_version":"essential-web-evidence-v3.0",
    "frozen_date":"2026-09-28","status":"READY_TO_IMPLEMENT","verdict":"READY TO IMPLEMENT EVIDENCE V3.0 CLEAN EPOCH",
    "canonicalization":"UTF-8 JSON, sorted keys, compact separators, ensure_ascii=False, no NaN; digest excludes top-level digest",
    "epoch_id":epoch_id,"epoch_state":"NOT_STARTED","authorization":"NONE",
    "protocol_sha256":artifacts[0]["sha256"],"artifacts":artifacts,
    "parent_v22_freeze_digest":audit["parent_v22_freeze_digest"],
    "terminal_v22_readiness_digest":audit["terminal_v22_readiness_digest"],
    "historical_closed_lineage":audit["history_bindings"],
    "epoch_definition_digest":epoch["digest"],"lineage_closure_digest":closure["digest"],
    "scientific_identity":{**audit["scientific_identity"],"repository":"EssentialAI/essential-web-v1.0",
        "revision":revision,"scientific_namespace":"essential-web-evidence-v2.0",
        "metadata_and_text_seed":20260927,"review_order_seed":20260928,
        "projection":["eai_taxonomy","quality_signals"],
        "policy_digest":"f4357f61f434d5105d823266153dac6372e122f12767187631e8796b4899dd07",
        "selection_adoption":"byte-identical original artifact by reference; no regenerated identity",
        "rubric_blinding_strata_censuses":"unchanged original protocol and manifest; two reviewers and adjudication",
        "secret_lifecycle":"unchanged; no secret generated in this task",
        "inventory_binding":audit["history_bindings"]["docs/implementation/evidence/ESSENTIAL-WEB-EVIDENCE-V2/inventory-freeze.json"],
        "selection_binding":audit["history_bindings"]["external/text_selection_manifest.json"]},
    "adopted_planning_observations":planning,
    "audited_baseline":{"commit":head,"code_and_dependency_bindings":audit["implementation_bindings"],
        "qualified_for_v3_execution":False,"future_authorization_must_bind_actual_final_implementation":True},
    "arm_caps":{"M":old["arm_m_caps_v2_2"],"T":old["arm_t_caps_unchanged_from_v2_1"]},
    "numerical_cap_changes":[],
    "explicit_contract_changes":["new prospective execution interval and permanent v2 closure",
        "two-stage footer preparation/data schedules with independent authorization",
        "cumulative active-machine clock and explicit quiescent pause semantics; numerical runtime ceilings unchanged"],
    "common_guards":{"hosts":["huggingface.co","cas-bridge.xethub.hf.co"],"scheme":"https","port":443,
        "concurrency":1,"threads_enabled":False,"retry_delays_seconds":[1,2],"ratio_max":15,"ratio_exempt_bytes":16777216},
    "first_network_boundary":first_operation,
    "M_prospective_files":audit["M_prospective_files"],"T_prospective_files":audit["T_prospective_files"],
    "prospective_totals":audit["prospective_totals"],
    "schedules_status":"phase P rules frozen; M data offsets unresolved until authorized P and separate D freeze/review",
    "implementation_requirements":audit["resource_review_findings"],
    "current_network_requests":0,"current_corpus_text_bytes_read":0,
    "tests_this_task":"NOT RUN; local metadata/hash/arithmetic audits only"})
construction_exit_status=0

# STATUS is an index outside the immutable freeze graph.
q=chr(96)
banner=(
    "> **ESSENTIAL-WEB EVIDENCE V3.0 PROTOCOL / LINEAGE DECISION (2026-09-28, offline):\n"
    "> READY TO IMPLEMENT EVIDENCE V3.0 CLEAN EPOCH.** Option A preserves the exact\n"
    "> scientific sample; v2.x is permanently CLOSED_NON_EXECUTABLE and\n"
    "> HISTORICALLY_UNCERTIFIABLE. Old costs remain disclosed provenance, outside\n"
    "> the new prospective execution interval. Epoch-0001 is NOT_STARTED; fresh\n"
    "> G:/Project/xlm-evidence-v3/essential-web remains absent; no authorization.\n"
    "> All numerical caps unchanged. Explicit P footer preparation is necessary\n"
    "> for M offsets and both readers' metadata; separate D authorization follows\n"
    "> exact-plan review. M nominal/cold-no-retry physical totals 688/720;\n"
    "> T 95/127. Runtime guards still need integration/repairs and focused tests.\n"
    "> Protocol SHA-256: "+q+freeze["protocol_sha256"]+q+".\n"
    "> Freeze canonical digest: "+q+freeze["digest"]+q+".\n"
    "> [Protocol](reports/ESSENTIAL-WEB-EVIDENCE-V3.0-PROTOCOL.md),\n"
    "> [decision and exact Muse prompt](reports/ESSENTIAL-WEB-EVIDENCE-V3.0-REVIEW.md#5-exact-muse-handoff),\n"
    "> [freeze](evidence/ESSENTIAL-WEB-EVIDENCE-V3.0/freeze.json),\n"
    "> [exact commands](evidence/ESSENTIAL-WEB-EVIDENCE-V3.0/COMMANDS.md),\n"
    "> [final validation](evidence/ESSENTIAL-WEB-EVIDENCE-V3.0/final_validation.json).\n"
    "> Local audit exit 0; no pytest/live tests or implementation this turn.\n"
    "> Next: exact Muse offline implementation prompt -> DRY plans -> independent\n"
    "> phase-P authorization review. No network, acquisition, text, selection\n"
    "> change, live genesis, commit or push. Previous status entries below are\n"
    "> historical narrative; their execution eligibility is superseded.\n\n")
status=root/"docs/implementation/STATUS.md"
previous=read(status).decode("utf-8")
header="# Implementation status"
check(previous.startswith(header) and "EVIDENCE V3.0 PROTOCOL / LINEAGE DECISION" not in previous,"status prepend guard")
newline="\r\n" if previous.startswith(header+"\r\n") else "\n"
offset=len(header)+len(newline)
updated=previous[:offset]+newline+banner.replace("\n",newline)+previous[offset:]
atomic(status,updated.encode("utf-8"),replace=True)

# Independent readback of the frozen dependency graph and preserved inputs.
freeze=load(out/"freeze.json")
epoch=load(out/"epoch_definition.json")
closure=load(out/"lineage_closure.json")
for binding in freeze["artifacts"]:
    check(digest({k:v for k,v in binding.items() if k!="descriptor_digest"})==binding["descriptor_digest"],"descriptor")
    verify(binding,root/binding["relative_path"])
for binding in freeze["historical_closed_lineage"].values():
    verify(binding,Path(binding["path"]))
for binding in freeze["audited_baseline"]["code_and_dependency_bindings"].values():
    verify(binding,Path(binding["path"]))
for binding in freeze["adopted_planning_observations"].values():
    verify(binding,Path(binding["path"]))
check(freeze["historical_closed_lineage"]==audit["history_bindings"],"full history")
check(freeze["audited_baseline"]["code_and_dependency_bindings"]==audit["implementation_bindings"],"all code bindings")
check(freeze["epoch_definition_digest"]==epoch["digest"] and freeze["lineage_closure_digest"]==closure["digest"],"epoch/closure")
check(closure["status"]=="CLOSED_NON_EXECUTABLE" and closure["historical_accounting"]=="HISTORICALLY_UNCERTIFIABLE","closed")
check(epoch["authorization"]==freeze["authorization"]=="NONE" and epoch["state"]=="NOT_STARTED","no authority")
check(not Path(epoch["execution_root"]).exists(),"no real execution root")
check(epoch["first_network_boundary"]==freeze["first_network_boundary"],"first operation")
check(freeze["arm_caps"]=={"M":old["arm_m_caps_v2_2"],"T":old["arm_t_caps_unchanged_from_v2_1"]},"unchanged caps")
check(freeze["numerical_cap_changes"]==[],"no cap increases")
check(freeze["scientific_identity"]["selection_digest"]=="975ba3dee4af0598e665ea05c69bbe49336c3e3a35fca777a863073190b78474","membership")
check(freeze["scientific_identity"]["selection_sha256"]=="8424f9668fef6952a558ead0dae27a705545a7f4a4d302713707d3ec6bb1af27","selection bytes")
check(freeze["scientific_identity"]["scientific_namespace"]=="essential-web-evidence-v2.0","namespace")
check(freeze["prospective_totals"]==audit["prospective_totals"],"totals")
for arm in ("M","T"):
    check(freeze[arm+"_prospective_files"]==audit[arm+"_prospective_files"],"file schedule")
check(hashlib.sha256(read(protocol)).hexdigest()==freeze["protocol_sha256"],"protocol")
status_text=read(status).decode("utf-8")
check(freeze["digest"] in status_text and freeze["protocol_sha256"] in status_text,"status hashes")
check(subprocess.check_output(["git","rev-parse","HEAD"],text=True).strip()==head,"unchanged HEAD")
changed=subprocess.check_output(["git","diff","--name-only"],text=True).splitlines()
allowed={"docs/implementation/STATUS.md","docs/implementation/reports/ESSENTIAL-WEB-SELECTOR-RECON.md"}
check(set(changed)<=allowed,"unexpected tracked changes")
diff=subprocess.run(["git","diff","--check"],capture_output=True,text=True,check=False)
check(diff.returncode==0,"git diff --check: "+diff.stderr+diff.stdout)
new_files=[protocol,review]+sorted(out.glob("*"))
for path in new_files:
    if path.is_file():
        check(path.name!="epoch_start.json" and path.suffix!=".tmp","no live genesis/partial artifact")
        if path.suffix==".md":
            check(all(line==line.rstrip(" \t") for line in read(path).decode("utf-8").splitlines()),"new markdown whitespace")
inventory={p.relative_to(root).as_posix():p.stat().st_size for p in new_files if p.is_file()}
result=save("final_validation.json",{
    "kind":"essential_web_v3_protocol_freeze_final_validation","status":"VERIFIED","exit_status":0,
    "protocol_sha256":freeze["protocol_sha256"],"freeze_digest":freeze["digest"],
    "epoch_definition_digest":epoch["digest"],"lineage_closure_digest":closure["digest"],
    "audit_digest":audit["digest"],"baseline_head":head,
    "history_bindings_verified":len(freeze["historical_closed_lineage"]),
    "code_dependency_bindings_verified":len(freeze["audited_baseline"]["code_and_dependency_bindings"]),
    "artifact_descriptors_verified":len(freeze["artifacts"]),
    "selection_bytes_unchanged":True,"old_artifacts_unchanged":True,"code_unchanged":True,
    "numerical_caps_unchanged":True,"genesis_created":False,"root_absent":True,"authorization":"NONE",
    "commands":{"independent_audit_exit_status":0,"freeze_construction_exit_status":construction_exit_status,
        "git_diff_check_exit_status":diff.returncode,"final_validation_exit_status":0},
    "test_execution":"NOT RUN; no pytest, synthetic product test, live test, CUDA or benchmark",
    "network_requests":0,"corpus_text_bytes_read":0,"environment":audit["environment"],
    "measurements":{"local_bytes_read":read_bytes,"elapsed_seconds_before_output":time.perf_counter()-started,
        "new_protocol_artifacts_before_this_result_bytes":sum(inventory.values()),
        "artifact_inventory_before_this_result":inventory,"peak_RSS":"NOT MEASURED","peak_disk":"NOT MEASURED",
        "limitations":"local audit only; no live runtime or performance qualification"}})
print(json.dumps({"status":result["status"],"exit_status":0,"protocol_sha256":freeze["protocol_sha256"],
    "freeze_digest":freeze["digest"],"result_digest":result["digest"],"measurements":result["measurements"]}))
```

