"""Independent mechanical leakage scan of the reviewer directories (counts only)."""

import base64
import json
import re
import sys
from pathlib import Path

root = Path("F:/XLM-Review/essential-web-v4.1-t")
D = Path("G:/Project/xlm-evidence-v4.1/essential-web-phase-d")
key = (root / "custodian/sealed/custodian_key.bin").read_bytes()
docs = [
    json.loads(line) for line in (D / "sealed/t_selected_documents.jsonl").read_bytes().splitlines()
]
ledger = json.loads((root / "custodian/master_ledger.json").read_bytes())
sel = json.loads(
    Path("F:/Project/xlm-evidence-v2/essential-web/text_selection_manifest.json").read_bytes()
)
fixed = {
    "key_raw": [key],
    "key_hex": [key.hex().encode()],
    "key_b64": [base64.b64encode(key)],
    "repository": [b"EssentialAI", b"essential-web-v1.0"],
    "revision": [docs[0]["locator"][1].encode()],
    "crawl_id": [b"CC-MAIN"],
    "parquet": [b".parquet", b"train-0"],
    "condition": [f"{p}-{t}".encode() for p in "ABCD" for t in ("normal", "strict")],
    "ownership": [c["stratum"].encode() for c in sel["cells"]],
    "source_file": sorted({d["locator"][2].encode() for d in docs}),
    "text_sha256": [d["sha256"].encode() for d in docs if d["sha256"]],
    "selection_digest": [sel["digest"].encode()],
    "unreviewable_id": [r["review_id"].encode() for r in ledger["entries"] if not r["reviewable"]],
    "custodian_path": [b"G:\\", b"G:/", b"xlm-evidence", b"xlm-data-ultrax", b"XLM-Review"],
}
words = [
    b"selector",
    b"locator",
    b"stratum",
    b"provenance",
    b"crawl",
    b"census",
    b"hypothesis",
    b"sha256",
    b"hmac",
    b"custodian",
    b"policy",
]
res = {}
for rv in ("reviewer-1", "reviewer-2"):
    files = sorted(p for p in (root / rv).rglob("*") if p.is_file())
    hits = {k: 0 for k in fixed}
    word_struct = {w.decode(): 0 for w in words}
    word_text = {w.decode(): 0 for w in words}
    other = "reviewer-2" if rv == "reviewer-1" else "reviewer-1"
    cross = 0
    for p in files:
        raw = p.read_bytes()
        for k, vals in fixed.items():
            hits[k] += sum(1 for v in vals if v in raw)
        cross += other.encode() in raw
        is_text = p.parent.name in ("texts", "view") or p.name == "package.json"
        for w in words:
            n = len(re.findall(rb"\b" + w + rb"\b", raw, re.I))
            (word_text if is_text else word_struct)[w.decode()] += n
    pk = json.loads((root / rv / "package.json").read_bytes())
    ids = [f["review_id"] for f in pk["forms"]]
    res[rv] = {
        "files": len(files),
        "bytes": sum(p.stat().st_size for p in files),
        "exact_value_hits": hits,
        "names_other_reviewer": cross,
        "generic_words_outside_document_text": word_struct,
        "generic_words_in_files_holding_document_text": word_text,
        "items": len(ids),
        "unique_ids": len(set(ids)),
        "package_top_keys": sorted(pk),
        "form_keys": sorted(pk["forms"][0]),
        "subdirs": sorted({p.parent.name for p in files}),
    }
r1 = {f["review_id"] for f in json.loads((root / "reviewer-1/package.json").read_bytes())["forms"]}
r2 = {f["review_id"] for f in json.loads((root / "reviewer-2/package.json").read_bytes())["forms"]}
res["same_membership"] = r1 == r2
res["ledger"] = {
    "entries": len(ledger["entries"]),
    "reviewable": sum(r["reviewable"] for r in ledger["entries"]),
    "unreviewable": [
        [r["status"], r["utf8_bytes"], r["text_sha256"], r["order_position"]]
        for r in ledger["entries"]
        if not r["reviewable"]
    ],
}
ok = all(
    sum(res[r]["exact_value_hits"].values()) == 0
    and res[r]["names_other_reviewer"] == 0
    and sum(res[r]["generic_words_outside_document_text"].values()) == 0
    for r in ("reviewer-1", "reviewer-2")
)
res["structural_clean"] = ok
print(json.dumps(res, indent=1))
sys.exit(0 if ok else 1)
