"""Frozen v3.0 constants transcribed from the normative freeze.

Source: docs/implementation/evidence/ESSENTIAL-WEB-EVIDENCE-V3.0/freeze.json
(canonical digest aa977972883af209afffebae221683658b2637281ae8e72741150750534fd834)
and docs/implementation/reports/ESSENTIAL-WEB-EVIDENCE-V3.0-PROTOCOL.md
(SHA-256 c191aa49a7e35349e27eb077c7105ead1a15a7edbbb28ef0bc78f157b219fd79).

Changing any value here is a protocol change: new version, digest,
rationale, and authorization required. No logic, no I/O.
"""

from __future__ import annotations

PROTOCOL_VERSION = "essential-web-evidence-v3.0"
PROTOCOL_SHA256 = "c191aa49a7e35349e27eb077c7105ead1a15a7edbbb28ef0bc78f157b219fd79"
FREEZE_DIGEST = "aa977972883af209afffebae221683658b2637281ae8e72741150750534fd834"
FROZEN_DATE = "2026-09-28"

EPOCH_ID = "essential-web-evidence-v3.0:essential-web:epoch-0001"
EXECUTION_ROOT = "G:/Project/xlm-evidence-v3/essential-web"
EPOCH_INITIAL_STATE = "NOT_STARTED"

# Scientific identity (adopted byte-identically, namespace stays v2.0).
SCIENTIFIC_NAMESPACE = "essential-web-evidence-v2.0"
SELECTION_DIGEST = "975ba3dee4af0598e665ea05c69bbe49336c3e3a35fca777a863073190b78474"
SELECTION_SHA256 = "8424f9668fef6952a558ead0dae27a705545a7f4a4d302713707d3ec6bb1af27"
M_OBSERVATION_DIGEST = "2ecf3eb9df01a41f7f9defa20633841211396f654350df80e460f5c2b5740052"
T_COSTMAP_DIGEST = "ed713a0a6fe22cdd396f758182b841b0208f0fe92795b3784f8d71bb6bbf665b"
POLICY_DIGEST = "f4357f61f434d5105d823266153dac6372e122f12767187631e8796b4899dd07"
SOURCE_REVISION = "ce4eccc7e9604667b6d7f32cb6274b8b41f3113d"
SOURCE_REPOSITORY = "EssentialAI/essential-web-v1.0"
FROZEN_PROTOCOL_COMMIT = "52569c525a6613faab096d17b60167b4aaa0f214"
# Canonical digest of {"M": ARM_M_CAPS, "T": ARM_T_CAPS} with the standard
# canonical JSON form; independently recomputed from freeze.json arm_caps.
RESOURCE_CAPS_DIGEST = "e2485cd438524124c22074d59c48a5ee7dc699f9c9a9ea3f9c11deeef54e0f7b"
METADATA_SEED = 20260927
REVIEW_ORDER_SEED = 20260928
PROJECTION = ("eai_taxonomy", "quality_signals")

# Lineage closure.
V22_FREEZE_DIGEST = "b6602a445307d9638913c046b4cfebab3356e20559d89b3f2ab601aa924ebd0c"
TERMINAL_V22_READINESS_DIGEST = "3f1ae11da62ec9a96a23494021808d98635fa8b10b04608ca4af73a6d87ebcde"
LINEAGE_STATUS = "CLOSED_NON_EXECUTABLE"
LINEAGE_ACCOUNTING = "HISTORICALLY_UNCERTIFIABLE"

# Caps are transcribed verbatim from freeze.json arm_caps (unchanged v2.2/v2.1).
ARM_M_CAPS: dict[str, int] = {
    "batch_rows": 256,
    "buffer_bytes": 4194304,
    "data_bytes_per_file": 29360128,
    "data_bytes_total": 234881024,
    "data_requests_per_plan": 100,
    "data_requests_total": 800,
    "decompressed_bytes_arm": 536870912,
    "decompressed_bytes_per_file": 67108864,
    "disk_combined_bytes": 536870912,
    "disk_final_bytes": 67108864,
    "disk_scratch_bytes": 469762048,
    "footer_bytes_per_file": 4194304,
    "footer_bytes_total": 33554432,
    "footer_requests_per_file": 16,
    "footer_requests_total": 80,
    "max_redirect_hops": 3,
    "max_retries": 2,
    "max_scan_rows_arm": 131072,
    "max_scan_rows_per_file": 16384,
    "memory_resident_bytes": 268435456,
    "parser_bytes_max": 33554432,
    "pilot_requests_per_plan": 100,
    "plans": 8,
    "record_bytes_max": 1048576,
    "requests_total": 880,
    "response_body_bytes_max": 4194304,
    "response_body_bytes_total": 268435456,
    "rows_per_file": 512,
    "rows_total": 4096,
    "time_seconds_arm": 1800,
    "time_seconds_plan": 600,
    "time_seconds_request": 30,
}

ARM_T_CAPS: dict[str, int] = {
    "batch_rows": 256,
    "data_bytes_arm_max": 251658240,
    "data_bytes_per_file_max": 31457280,
    "decompressed_bytes_arm_max": 536870912,
    "decompressed_bytes_per_file_max": 67108864,
    "disk_combined_bytes": 536870912,
    "disk_final_bytes": 16777216,
    "disk_scratch_bytes": 520093696,
    "document_bytes_max": 65536,
    "final_artifact_bytes_max": 16777216,
    "footer_bytes_per_file_max": 2097152,
    "footer_bytes_total_max": 16777216,
    "max_redirect_hops": 3,
    "max_retries": 2,
    "memory_resident_bytes": 268435456,
    "parser_bytes_max": 33554432,
    "range_buffer_bytes_max": 4194304,
    "ratio_exempt_bytes": 16777216,
    "ratio_max": 15,
    "requests_arm_max": 800,
    "requests_per_file_max": 100,
    "response_body_bytes_max": 4194304,
    "retained_text_bytes_max": 8388608,
    "scan_rows_arm_max": 131072,
    "scan_rows_per_file_max": 16384,
    "selected_rows_max": 118,
    "time_seconds_arm": 1800,
    "time_seconds_file": 600,
    "time_seconds_request": 30,
    "transfer_bytes_arm_max": 268435456,
    "transfer_bytes_per_file_max": 33554432,
}

# Prospective totals (reproduced independently by schedules.py, never trusted
# blindly; these constants are the cross-check targets).
M_TOTAL_NOMINAL = 688
M_TOTAL_COLD_MAX_NO_RETRY = 720
M_FOOTER_NOMINAL_PHYSICAL = 40
M_FOOTER_COLD_MAX = 72
M_DATA_PHYSICAL = 648
M_DATA_PAYLOAD = 11692530
M_FOOTER_PAYLOAD_BOTH_PHASES = 1398448
M_NOMINAL_PAYLOAD_TOTAL = 13090978

T_TOTAL_NOMINAL = 95
T_TOTAL_COLD_MAX_NO_RETRY = 127
T_DATA_RANGES = 47
T_DATA_PAYLOAD = 179963169
T_DATA_BOUND = 213517601
T_FOOTER_RESERVE = 16777216

# Transport policy transcribed from freeze.json common_guards plus the
# inherited v2 retry/timeout values (protocol section 4). Exact hosts only.
ALLOWED_HOSTS: tuple[str, ...] = ("huggingface.co", "cas-bridge.xethub.hf.co")
CANONICAL_HOST = "huggingface.co"
SIGNED_TARGET_HOST = "cas-bridge.xethub.hf.co"
ALLOWED_PORT = 443
MAX_REDIRECT_TRANSITIONS = 3
MAX_RETRIES = 2
RETRY_DELAYS_SECONDS: tuple[int, ...] = (1, 2)
RETRYABLE_HTTP: tuple[int, ...] = (429, 502, 503, 504)
REQUEST_SECONDS_MAX = 30
FILE_SECONDS_MAX = 600
ARM_SECONDS_MAX = 1800
RESPONSE_BODY_BYTES_MAX = 4194304
MEMORY_RESIDENT_BYTES_MAX = 268435456
PARSER_BYTES_MAX = 33554432

PHASE_STATES: tuple[str, ...] = (
    "NOT_STARTED",
    "P_AUTHORIZED",
    "P_RUNNING",
    "P_COMPLETE_SEALED",
    "D_AUTHORIZED",
    "D_RUNNING",
    "D_COMPLETE",
    "INCOMPLETE",
    "REFUSED",
)
