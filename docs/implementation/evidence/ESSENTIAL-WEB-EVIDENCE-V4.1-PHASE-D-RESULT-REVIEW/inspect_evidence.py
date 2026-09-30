"""Read-only inventories and content-free schema discovery for the result review."""

import hashlib
import json
import sqlite3
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
P = Path("G:/Project/xlm-evidence-v4.1/essential-web")
D = Path("G:/Project/xlm-evidence-v4.1/essential-web-phase-d")


def inventory(root: Path) -> dict[str, Any]:
    paths = [root, *sorted(root.rglob("*"))]
    assert len(paths) < 1000
    assert sum(p.stat().st_size for p in paths if p.is_file()) <= 1073741824
    result = {}
    for p in paths:
        assert not p.is_symlink()
        st = p.stat()
        row = dict(bytes=st.st_size, mtime_ns=st.st_mtime_ns)
        if p.is_file():
            with p.open("rb") as stream:
                row["sha256"] = hashlib.file_digest(stream, "sha256").hexdigest()
        result[p.relative_to(root).as_posix()] = row
    return result


def main() -> None:
    for name, root in [("parent", P), ("phase-d", D)]:
        dest = HERE / f"{name}-before.json"
        current = inventory(root)
        if dest.exists():
            assert json.loads(dest.read_bytes()) == current
        else:
            dest.write_text(json.dumps(current, indent=2) + "\n", encoding="utf-8")
    assert not list(D.glob("state.sqlite-*"))
    with sqlite3.connect((D / "state.sqlite").as_uri() + "?mode=ro&immutable=1", uri=True) as db:
        db.row_factory = sqlite3.Row
        for table in ["run", "operations", "attempts", "outputs", "decoded_outputs"]:
            rows = [dict(r) for r in db.execute("SELECT * FROM " + table)]
            print(table, len(rows), list(rows[0]))
            if table == "run":
                print(json.dumps(rows))
    for name in [
        "phase_d_receipt.json",
        "m_acquisition_manifest.json",
        "t_acquisition_manifest.json",
        "sealed/t_provenance.json",
    ]:
        obj = json.loads((D / name).read_bytes())
        print(name, list(obj))
        for key in [
            "totals",
            "arms",
            "summary",
            "status_counts",
            "retained_text_bytes",
            "retained_unique_text_bytes",
        ]:
            if key in obj:
                print(key, json.dumps(obj[key]))
        if "files" in obj:
            print("file schema", list(obj["files"][0]))
    for name in ["m_selected_metadata.jsonl", "sealed/t_selected_documents.jsonl"]:
        with (D / name).open("rb") as stream:
            row = json.loads(stream.readline())
        print(name, list(row))
    selection = json.loads(
        Path("F:/Project/xlm-evidence-v2/essential-web/text_selection_manifest.json").read_bytes()
    )
    print("selection keys", list(selection))
    for key in ["selected", "locators", "entries", "candidate_cells", "strata"]:
        if key in selection:
            value = selection[key]
            print(
                key,
                type(value).__name__,
                len(value),
                list(value[0]) if isinstance(value, list) and value else "",
            )


if __name__ == "__main__":
    main()
