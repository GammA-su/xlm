"""Derived Phase-P readiness from static identity plus integrated scenarios.

A mechanism is never declared ready by a constant or a unit self-test of
an isolated helper. Each status is one of:

- ``VERIFIED_STATIC``: configuration/artifact identity recomputed and equal.
- ``VERIFIED_SYNTHETIC_INTEGRATION``: an adversarial scenario was driven
  through the PUBLIC executor API on a disposable synthetic epoch and the
  expected fail-closed outcome (with conserved accounting) was observed.
- ``UNVERIFIED_LIVE``: behaviour that only a live run can show (never run).
- ``NONE``: authorization.

If any scenario does not produce its expected outcome (for example because
a guard was disabled), that mechanism is ``BLOCKED`` and so is the verdict.
Recorded evidence is deterministic: outcome classes and synthetic
counters, never temporary paths or timings. Scenarios are authored
synthetic fixtures; they prove logic, not live source compatibility.
"""

from __future__ import annotations

import hashlib
import subprocess
import sys
import tempfile
import threading
from collections.abc import Callable
from pathlib import Path
from typing import Any

from xlm.data.evidence_v2 import canonical
from xlm.data.evidence_v3 import (
    envidentity,
    executor,
    frozen_v3,
    netpolicy,
    plan,
    synthetic,
    transport,
)

VERIFIED_STATIC = "VERIFIED_STATIC"
VERIFIED_SYNTHETIC = "VERIFIED_SYNTHETIC_INTEGRATION"
UNVERIFIED_LIVE = "UNVERIFIED_LIVE"
BLOCKED = "BLOCKED"

Scenario = Callable[[Path, Path], tuple[bool, dict[str, Any]]]


def _epoch(repo: Path, work: Path, **kwargs: Any) -> synthetic.SyntheticEpoch:
    ep = synthetic.build_epoch(repo, work, **kwargs)
    executor.perform_genesis(**ep.paths(), harness=ep.harness())
    return ep


def _run(ep: synthetic.SyntheticEpoch, **kwargs: Any) -> tuple[str, dict[str, Any]]:
    try:
        executor.execute_phase_p(**ep.paths(), harness=ep.harness(), **kwargs)
        outcome = "COMPLETED"
    except executor.PhasePStop:
        outcome = "STOPPED"
    except executor.PhasePError:
        outcome = "REFUSED"
    return outcome, executor.inspect_epoch(ep.root)


def _conserved(state: dict[str, Any]) -> bool:
    for arm in ("M", "T"):
        mine = [a for a in state["attempts"] if a["arm"] == arm]
        cell = state["arms"][arm]
        settled = [a for a in mine if a["charged"] is not None]
        if cell["requests"] != len(mine):
            return False
        if cell["body_charged"] != sum(a["charged"] for a in settled):
            return False
        if any(a["charged"] < a["body_total"] for a in settled):
            return False
    return True


def _counters(state: dict[str, Any]) -> dict[str, Any]:
    return {
        arm: {
            "phase": state["arms"][arm]["phase"],
            "requests": state["arms"][arm]["requests"],
            "body_charged": state["arms"][arm]["body_charged"],
            "ops_done": state["arms"][arm]["ops_done"],
        }
        for arm in ("M", "T")
    }


def scenario_baseline(repo: Path, work: Path) -> tuple[bool, dict[str, Any]]:
    """Full synthetic P run with a clean pause/resume and the REAL psutil sampler."""
    ep = _epoch(repo, work, use_real_sampler=True)
    first, _ = _run(ep, max_operations=3)
    second, state = _run(ep)
    ok = (
        first == "COMPLETED"
        and second == "COMPLETED"
        and state["arms"]["M"]["phase"] == state["arms"]["T"]["phase"] == "P_COMPLETE_SEALED"
        and state["arms"]["M"]["requests"] + state["arms"]["T"]["requests"]
        == len(ep.transport.calls)
        and _conserved(state)
    )
    return ok, {"outcome": second, "counters": _counters(state)}


def scenario_authorization(repo: Path, work: Path) -> tuple[bool, dict[str, Any]]:
    """arms='MT', resealed invented environment and negative approval refuse."""
    results: dict[str, str] = {}
    for name, edit in (
        ("arms_string", {"arms": "MT"}),
        ("invented_environment", {"environment_identity": "INVENTED"}),
    ):
        ep = synthetic.build_epoch(repo, work / name)
        body = canonical.loads_bytes_strict(ep.auth_path.read_bytes())
        if name == "invented_environment":
            env = dict(body["environment_identity"])
            env["torch_build"] = "cuda-99.9"
            body["environment_identity"] = env
        else:
            body.update(edit)
        ep.auth_path.write_bytes(synthetic.reseal(body))
        digest = canonical.loads_bytes_strict(ep.auth_path.read_bytes())["digest"]
        ep.approval_path.write_bytes(canonical.canonical_bytes(synthetic.approval_body(digest)))
        try:
            executor.check_authorization(**ep.paths(), harness=ep.harness())
            results[name] = "ACCEPTED"
        except executor.PhasePError:
            results[name] = "REFUSED"
    ep = synthetic.build_epoch(repo, work / "negative_approval")
    digest = canonical.loads_bytes_strict(ep.auth_path.read_bytes())["digest"]
    body = synthetic.approval_body(digest, decision="I do NOT approve phase-P execution")
    ep.approval_path.write_bytes(synthetic.reseal(body))
    try:
        executor.check_authorization(**ep.paths(), harness=ep.harness())
        results["negative_approval"] = "ACCEPTED"
    except executor.PhasePError:
        results["negative_approval"] = "REFUSED"
    return all(v == "REFUSED" for v in results.values()), {"results": results}


def scenario_genesis(repo: Path, work: Path) -> tuple[bool, dict[str, Any]]:
    ep = _epoch(repo, work)
    try:
        executor.perform_genesis(**ep.paths(), harness=ep.harness())
        second = "ACCEPTED"
    except executor.PhasePError:
        second = "REFUSED"
    return second == "REFUSED", {"second_genesis": second}


def scenario_journal(repo: Path, work: Path) -> tuple[bool, dict[str, Any]]:
    """Crash mid-body conserves the reservation; tail truncation refuses."""
    ep = _epoch(repo, work / "crash")

    def crash(
        _r: transport.TransportRequest, resp: synthetic.FakeResponse
    ) -> synthetic.FakeResponse:
        resp.chunk_limit = 2
        resp.crash_after_bytes = 2
        return resp

    ep.transport.overrides[1] = crash
    try:
        executor.execute_phase_p(**ep.paths(), harness=ep.harness())
        crashed = False
    except synthetic.SimulatedCrash:
        crashed = True
    _, state = _run(ep, max_operations=0)
    attempt = sorted(state["attempts"], key=lambda a: a["attempt_id"])[1]
    conserved = (
        attempt["outcome"] == "CRASH_RESERVED" and attempt["charged"] == attempt["reservation"]
    )
    tr = _epoch(repo, work / "truncate")
    _run(tr, max_operations=2)
    path = tr.root / "state/journal.jsonl"
    lines = path.read_bytes().splitlines(keepends=True)
    path.write_bytes(b"".join(lines[:-2]))
    truncated, _ = ("REFUSED", None) if _run(tr)[0] == "REFUSED" else ("ACCEPTED", None)
    ok = crashed and conserved and truncated == "REFUSED" and _conserved(state)
    return ok, {"crash_conserved": conserved, "truncation": truncated}


def _stop_scenario(
    repo: Path, work: Path, arrange: Callable[[synthetic.SyntheticEpoch], None], **kwargs: Any
) -> tuple[bool, dict[str, Any]]:
    ep = _epoch(repo, work, **kwargs)
    arrange(ep)
    outcome, state = _run(ep)
    ok = (
        outcome == "STOPPED" and state["arms"]["M"]["phase"] == "P_INCOMPLETE" and _conserved(state)
    )
    return ok, {"outcome": outcome, "counters": _counters(state)}


def scenario_redirect(repo: Path, work: Path) -> tuple[bool, dict[str, Any]]:
    def arrange(ep: synthetic.SyntheticEpoch) -> None:
        ep.transport.overrides[0] = lambda _r, _x: synthetic.FakeResponse(
            302, {"location": "https://evil.hf.co/x"}, b"moved"
        )

    return _stop_scenario(repo, work, arrange)


def scenario_body(repo: Path, work: Path) -> tuple[bool, dict[str, Any]]:
    def arrange(ep: synthetic.SyntheticEpoch) -> None:
        ep.transport.overrides[0] = lambda _r, resp: synthetic.FakeResponse(
            302, resp.headers, b"x" * 70_000
        )

    ok, evidence = _stop_scenario(repo, work, arrange)
    charged = evidence["counters"]["M"]["body_charged"]
    return ok and charged >= 65537, evidence


def scenario_identity(repo: Path, work: Path) -> tuple[bool, dict[str, Any]]:
    def arrange(ep: synthetic.SyntheticEpoch) -> None:
        ep.transport.overrides[1] = lambda _r, resp: synthetic.FakeResponse(
            206, {**resp.headers, "etag": 'W/"weak"'}, resp.body
        )

    return _stop_scenario(repo, work, arrange)


def scenario_memory(repo: Path, work: Path) -> tuple[bool, dict[str, Any]]:
    breach = threading.Event()
    seen = threading.Event()

    def reader() -> int:
        if breach.is_set():
            seen.set()
            return frozen_v3.MEMORY_RESIDENT_BYTES_MAX + 1
        return 8 * 1024 * 1024

    def arrange(ep: synthetic.SyntheticEpoch) -> None:
        def trip(
            _r: transport.TransportRequest, resp: synthetic.FakeResponse
        ) -> synthetic.FakeResponse:
            def on_chunk(pos: int) -> None:
                if pos >= 1:
                    breach.set()
                    seen.wait(10)

            resp.chunk_limit = 1
            resp.on_chunk = on_chunk
            return resp

        ep.transport.overrides[1] = trip

    return _stop_scenario(repo, work, arrange, memory_reader=reader)


def scenario_runtime(repo: Path, work: Path) -> tuple[bool, dict[str, Any]]:
    holder: dict[str, synthetic.SyntheticEpoch] = {}

    def arrange(ep: synthetic.SyntheticEpoch) -> None:
        holder["ep"] = ep

        def slow(
            _i: int, _r: transport.TransportRequest, resp: synthetic.FakeResponse
        ) -> synthetic.FakeResponse:
            if resp.status == 206:
                resp.on_chunk = lambda _pos: ep.clock.advance(31)
            return resp

        ep.transport.rule = slow

    ok, evidence = _stop_scenario(repo, work, arrange)
    state = executor.inspect_epoch(holder["ep"].root)
    evidence["time_s_M"] = state["arms"]["M"]["time_ns"] // 1_000_000_000
    return ok and state["arms"]["M"]["time_ns"] >= 3 * 31_000_000_000, evidence


def scenario_future_d_reserve(repo: Path, work: Path) -> tuple[bool, dict[str, Any]]:
    """M: exactly 48 P requests succeed; the 49th is refused (D keeps 32)."""
    results: dict[str, Any] = {}
    for extra_last in (1, 2):
        ep = _epoch(repo, work / f"extra{extra_last}", m_count=8, t_count=1)
        seen: dict[str, int] = {}

        def rule(
            _i: int,
            req: transport.TransportRequest,
            resp: synthetic.FakeResponse,
            ep: synthetic.SyntheticEpoch = ep,
            seen: dict[str, int] = seen,
            extra_last: int = extra_last,
        ) -> synthetic.FakeResponse:
            checked = netpolicy.check_url(req.url)
            for index, f in enumerate(ep.m_files):
                if req.range_header == "bytes=0-3" and netpolicy.is_canonical_resource(
                    checked, synthetic.SYNTHETIC_SOURCE, f.name
                ):
                    return synthetic.FakeResponse(
                        302, {"location": synthetic.signed_url(f.name, "hop1")}, b"r"
                    )
                for hop in (1, 2):
                    if req.url == synthetic.signed_url(f.name, f"hop{hop}"):
                        return synthetic.FakeResponse(
                            302, {"location": synthetic.signed_url(f.name, f"hop{hop + 1}")}, b"r"
                        )
                if req.range_header != "bytes=0-3" and req.url == synthetic.signed_url(
                    f.name, "hop3"
                ):
                    count = seen.get(f.name, 0)
                    seen[f.name] = count + 1
                    limit = extra_last if index == len(ep.m_files) - 1 else 1
                    if count < limit:
                        return synthetic.FakeResponse(503, {}, b"busy")
            return resp

        ep.transport.rule = rule
        outcome, state = _run(ep, max_operations=16)
        results[f"extra{extra_last}"] = {
            "outcome": outcome,
            "M_requests": state["arms"]["M"]["requests"],
            "M_phase": state["arms"]["M"]["phase"],
        }
    ok = results["extra1"] == {
        "outcome": "COMPLETED",
        "M_requests": 48,
        "M_phase": "P_COMPLETE_SEALED",
    } and results["extra2"] == {"outcome": "STOPPED", "M_requests": 48, "M_phase": "P_INCOMPLETE"}
    return ok, results


def scenario_junction(repo: Path, work: Path) -> tuple[bool, dict[str, Any]]:
    """A REAL NTFS junction planted in the root is refused before network."""
    if sys.platform != "win32":
        return False, {"junction": "NOT_TESTABLE_ON_THIS_PLATFORM"}
    ep = _epoch(repo, work)
    outside = work / "outside"
    outside.mkdir()
    made = subprocess.run(
        ["cmd", "/c", "mklink", "/J", str(ep.root / "phase_p"), str(outside)],
        capture_output=True,
        check=False,
    )
    if made.returncode != 0:
        return False, {"junction": "COULD_NOT_CREATE_REAL_JUNCTION"}
    outcome, _ = _run(ep)
    ok = outcome == "REFUSED" and ep.transport.calls == [] and not any(outside.iterdir())
    return ok, {"outcome": outcome, "network_calls": len(ep.transport.calls)}


SCENARIOS: dict[str, tuple[str, Scenario]] = {
    "integrated_phase_p_run": ("baseline", scenario_baseline),
    "authorization_and_operator_approval": ("authorization", scenario_authorization),
    "exclusive_genesis": ("genesis", scenario_genesis),
    "durable_journal": ("journal", scenario_journal),
    "redirect_policy": ("redirect", scenario_redirect),
    "streaming_body_accounting": ("body", scenario_body),
    "remote_identity": ("identity", scenario_identity),
    "memory_supervision_during_work": ("memory", scenario_memory),
    "runtime_deadlines": ("runtime", scenario_runtime),
    "future_d_reservation": ("reserve", scenario_future_d_reserve),
    "windows_junction_containment": ("junction", scenario_junction),
}


def static_checks(repo: Path, implementation_commit: str) -> dict[str, dict[str, Any]]:
    """Identity checks recomputed from committed bytes (no scenario runs)."""
    out: dict[str, dict[str, Any]] = {}
    freeze = plan._load_freeze(repo)
    protocol = (
        repo / "docs/implementation/reports/ESSENTIAL-WEB-EVIDENCE-V3.0-PROTOCOL.md"
    ).read_bytes()
    ok = (
        hashlib.sha256(protocol).hexdigest() == frozen_v3.PROTOCOL_SHA256
        and freeze["digest"] == frozen_v3.FREEZE_DIGEST
        and freeze["scientific_identity"]["selection_digest"] == frozen_v3.SELECTION_DIGEST
        and freeze["scientific_identity"]["policy_digest"] == frozen_v3.POLICY_DIGEST
        and canonical.digest({"M": freeze["arm_caps"]["M"], "T": freeze["arm_caps"]["T"]})
        == frozen_v3.RESOURCE_CAPS_DIGEST
    )
    out["frozen_protocol_science_caps"] = {
        "status": VERIFIED_STATIC if ok else BLOCKED,
        "evidence": (
            "protocol SHA-256, freeze digest, selection/policy digests, cap-map digest recomputed"
        ),
    }
    payloads = plan.recompute_real_payloads(repo)
    m_ok = payloads["M"]["ceilings"]["arm"]["requests"] == 48 and len(payloads["M"]["files"]) == 8
    t_ok = (
        payloads["T"]["ceilings"]["arm"]["requests"] == 800 - 79
        and len(payloads["T"]["files"]) == 8
    )
    out["phase_p_plans_from_freeze"] = {
        "status": VERIFIED_STATIC if m_ok and t_ok else BLOCKED,
        "evidence": "executable P plans recomputed from freeze.json; future-D reserve retained",
    }
    try:
        paths = envidentity.code_paths(repo)
        committed = envidentity.committed_identity(repo, implementation_commit, paths)
        envidentity.verify_worktree_against_commit(repo, implementation_commit, paths)
        envidentity.check_code_map_schema(committed)
        code_ok = True
    except envidentity.EnvIdentityError:
        code_ok = False
    out["code_identity_committed_blobs"] = {
        "status": VERIFIED_STATIC if code_ok else BLOCKED,
        "evidence": "SHA-256 of committed Git blobs at the implementation commit == working tree",
    }
    out["transport_host_policy"] = {
        "status": VERIFIED_STATIC
        if frozen_v3.ALLOWED_HOSTS == tuple(freeze["common_guards"]["hosts"])
        and frozen_v3.ALLOWED_PORT == freeze["common_guards"]["port"]
        else BLOCKED,
        "evidence": "exact host allowlist and port equal freeze.json common_guards",
    }
    return out


def derive_readiness(repo: Path, implementation_commit: str) -> dict[str, Any]:
    """Run static checks and every integrated scenario; derive the verdict."""
    mechanisms = static_checks(repo, implementation_commit)
    with tempfile.TemporaryDirectory(prefix="ev3-readiness-") as tmp:
        base = Path(tmp)
        for name, (folder, scenario) in SCENARIOS.items():
            try:
                ok, evidence = scenario(repo, base / folder)
            except Exception as exc:  # a crashing scenario is not evidence
                ok, evidence = False, {"error": type(exc).__name__}
            mechanisms[name] = {
                "status": VERIFIED_SYNTHETIC if ok else BLOCKED,
                "evidence": evidence,
            }
    mechanisms["live_https_transport"] = {
        "status": UNVERIFIED_LIVE,
        "evidence": "_LiveHttpsTransport never executed (no network in this task)",
    }
    mechanisms["live_source_identity_and_footers"] = {
        "status": UNVERIFIED_LIVE,
        "evidence": "real ETag/Content-Range/footer bindings are only observable in authorized P",
    }
    blocked = sorted(k for k, v in mechanisms.items() if v["status"] == BLOCKED)
    return {
        "mechanisms": mechanisms,
        "blocked": blocked,
        "authorization": "NONE",
        "executable": False,
        "verdict": "READY_FOR_PHASE_P_AUTHORIZATION_REVIEW" if not blocked else "BLOCKED",
        "note": "synthetic integration proves logic on authored fixtures, not live compatibility",
    }
