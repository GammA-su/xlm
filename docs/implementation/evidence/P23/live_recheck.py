import json
from datetime import UTC, datetime
from pathlib import Path
from xlm.data.acquisition.plan import AcquisitionPlan, PlanAuthorization
from xlm.data.acquisition.fetcher import BoundedFetcher
from xlm.data.acquisition.verifier import AcquisitionVerifier
base = Path('data/audit/p23/live')
original = AcquisitionPlan.model_validate_json((base / 'plan.json').read_text())
limits = original.limits.model_copy(update={'max_requests': 3, 'max_retries': 1, 'max_workers': 1, 'overall_deadline_seconds': 60.0, 'max_decompressed_bytes': 1048576})
plan = original.model_copy(update={'plan_id': 'p23_finewiki_metadata_recheck', 'output_artifact_id': 'p23_finewiki_metadata_recheck', 'authorization': None, 'limits': limits})
plan = plan.with_computed_hash()
plan = plan.model_copy(update={'authorization': PlanAuthorization(authorization_hash=plan.compute_behavioral_hash(), authorized_by='user_P23_explicit_session_approval', authorized_at=datetime.now(UTC).isoformat(), scope='metadata-only pilot; no corpus records admitted', is_pilot_approved=True)})
(base / 'recheck_plan.json').write_text(plan.model_dump_json(indent=2), encoding='utf-8')
state = BoundedFetcher(plan, base / 'recheck_scratch', base / 'recheck_output').run()
receipt = AcquisitionVerifier(plan, base / 'recheck_output').verify()
result = {'scope': 'LIVE metadata-file acquisition only; corpus-row adapter pilot BLOCKED', 'fetch': state.model_dump(), 'receipt': receipt.model_dump(), 'request_metric_scope': 'primary attempts; redirects are not metered by the implementation'}
(base / 'recheck_result.json').write_text(json.dumps(result, indent=2), encoding='utf-8')
print(json.dumps(result, indent=2))
assert state.status == 'COMPLETED' and state.requests_made > 0
