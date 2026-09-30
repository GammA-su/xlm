"""Freeze the Windows publication-fix code compatibility; never alter operator authorization."""

from pathlib import Path

import essential_web_fast as driver

from xlm.data.sources import essential_web_recovery as recovery


def main() -> None:
    repo = Path(__file__).resolve().parents[1]
    historical = driver.read_json(repo / recovery.MANIFEST)
    driver.check_digest(historical, "historical recovery")
    scope = driver.read_json(repo / recovery.SCOPE_FIX)
    driver.check_digest(scope, "scope-fix compatibility")
    relative, kind, allowed = recovery.COMPATIBILITY[1]
    current = recovery.code_identity(repo)
    changed = {name for name, digest in current.items() if scope["code"].get(name) != digest}
    if changed != allowed:
        raise RuntimeError(
            f"Windows fix must change exactly {sorted(allowed)}, not {sorted(changed)}"
        )
    record = driver.self_digest(
        {
            "kind": kind,
            "campaign": historical["campaign"],
            "recovery_digest": historical["digest"],
            "previous_digest": scope["digest"],
            "previous_code": scope["code"],
            "code": current,
            "authorization_basis": "No source, membership, acquisition plan, resource limit, "
            "selector, mixture, quota or scientific contract changes. Only live-progress "
            "coordination, failure reporting and restart classification change; document, "
            "ledger and receipt bytes do not. Existing batch authorizations apply.",
        }
    )
    driver.write_json(repo / relative, record)
    print(f"windows-fix code freeze: {record['digest']}; operator records unchanged")


if __name__ == "__main__":
    main()
