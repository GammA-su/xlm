"""Freeze only the scope-fix code compatibility; never alter operator authorization."""

from pathlib import Path

import essential_web_fast as driver

from xlm.data.sources import essential_web_recovery as recovery


def main() -> None:
    repo = Path(__file__).resolve().parents[1]
    historical = driver.read_json(repo / recovery.MANIFEST)
    driver.check_digest(historical, "historical recovery")
    current = recovery.code_identity(repo)
    changed = {name for name, digest in current.items() if historical["code"].get(name) != digest}
    if changed != {
        "scripts/essential_web_fast.py",
        "src/xlm/data/sources/essential_web_recovery.py",
    }:
        raise RuntimeError("scope-only fix may change only the driver and recovery dispatcher")
    record = driver.self_digest(
        {
            "kind": "essential_web_recovery_scope_fix_v1",
            "campaign": historical["campaign"],
            "recovery_digest": historical["digest"],
            "previous_code": historical["code"],
            "code": current,
            "authorization_basis": "No source, membership, acquisition plan, resource limit or "
            "scientific contract changes. Existing batch authorization applies; recovery override "
            "and its authorization are consulted only for the exact historical unit/content.",
        }
    )
    driver.write_json(repo / recovery.SCOPE_FIX, record)
    print(f"scope-fix code freeze: {record['digest']}; operator records unchanged")


if __name__ == "__main__":
    main()
