"""Frozen evidence-v2.0 constants: data only, transcribed from the protocol.

Source: ``docs/implementation/reports/ESSENTIAL-WEB-EVIDENCE-V2-PROTOCOL.md``
as bound by ``docs/implementation/evidence/ESSENTIAL-WEB-EVIDENCE-V2/freeze.json``.
Changing any value here is a protocol change: new version, digest,
rationale, and authorization required. No logic, no I/O.
"""

from __future__ import annotations

PROTOCOL_VERSION = "essential-web-evidence-v2.0"
FREEZE_DIGEST = "fe2157799a86fe777e45c220716563f8a73245a12593c40909222555bf1b8248"
METADATA_SEED = 20260927
TEXT_SELECTION_SEED = 20260927
REVIEW_ORDER_SEED = 20260928
REPOSITORY = "EssentialAI/essential-web-v1.0"
REVISION = "ce4eccc7e9604667b6d7f32cb6274b8b41f3113d"
POLICY_DIGEST = "f4357f61f434d5105d823266153dac6372e122f12767187631e8796b4899dd07"
POLICY_SPEC_VERSION = "essential-web-selector-sweep-v1"
INVENTORY_DIGEST = "466ece976bfadfbd024c7fb66ff0076f1d86f36ce7d362331d85f1fee36c018f"
COMPLETE_INVENTORY_DIGEST = "d2b3eac5dca002336a97654564a13c93e8e4696e6a1624a6fdf3c012fd0927d5"
DEV_DISCOVERY_DIGEST = "c8d448fafb10b524496e6fd51bcde23f87c19c95b68dffe02f293d96be7530a7"
DEV_EXECUTION_DIGEST = "5065bcf6001ee38b63ca783f20a625af0256f2788ce9a0034edfba34c125bc23"
DEV_BUNDLE_DIGEST = "ac1c13b656d9dc3bc506726bfe3f1db925444efb899733052496be0425c58eb3"
DEV_COMBINED_SHA256 = "42e07c350d849c608ef202348971e3f7ea48aa80775d413a39dd3373628c58e1"
DEV_RECORDS = 4096
SWEEP_TOOL_VERSION = 2
SWEEP_REPORT_SCHEMA_VERSION = 2
SWEEP_MANIFEST_DIGEST = "e13c9c98efdf07fcc7c1375c4c8b62d918d77aa39c158171aa090c8695ac4087"
SWEEP_MANIFEST_FILE_SHA256 = "0d550a2d871f737c8bc46f82a8de38fd817b50bb1bffee46f2c3c07a129e26bc"

METADATA_FILE_RANK_PREFIX = "metadata-file"
TEXT_SELECT_RANK_PREFIX = "text-select"
REVIEW_ID_PREFIX = "review-id"
REVIEW_ORDER_PREFIX = "review-order"
REVIEW_ID_TAG = "ew2-"

WINDOW_SOURCE_ID = "essential_web"
WINDOW_VIEW_ID = "selector_recon"
WINDOW_VERSION_LABEL = "window-v2"
WINDOW_ROWS_PER_FILE = 512
WINDOW_ROWS_TOTAL = 4096
MAX_SCAN_ROWS_PER_FILE = 16384
MAX_SCAN_ROWS_ARM = 131072
WINDOW_BUFFER_BYTES = 4194304
WINDOW_BATCH_ROWS = 256
PILOT_REQUEST_CEILING = 100

PROJECTION_METADATA = ("eai_taxonomy", "quality_signals")
PROJECTION_TEXT = ("text",)

ALLOWED_HOSTS = ("huggingface.co", "cas-bridge.xethub.hf.co")
ALLOWED_PORT = 443
MAX_REDIRECT_HOPS = 3
MAX_RETRIES = 2
RETRY_DELAYS_SECONDS = (1, 2)
RETRYABLE_TRANSPORT = ("timeout", "reset")
RETRYABLE_HTTP = (429, 502, 503, 504)
PER_REQUEST_TIMEOUT_SECONDS = 30

# (crawl_suffix, file_count, excluded_basename, selected_basename), §3 order.
STRATA: tuple[tuple[str, int, str, str], ...] = (
    ("2014-15", 2772, "train-01787-of-02772.parquet", "train-01860-of-02772.parquet"),
    ("2015-32", 1920, "train-01486-of-01920.parquet", "train-01682-of-01920.parquet"),
    ("2016-50", 3132, "train-00454-of-03132.parquet", "train-02156-of-03132.parquet"),
    ("2018-05", 3429, "train-01826-of-03429.parquet", "train-03378-of-03429.parquet"),
    ("2019-09", 2577, "train-01825-of-02577.parquet", "train-00153-of-02577.parquet"),
    ("2021-04", 3315, "train-00297-of-03315.parquet", "train-00179-of-03315.parquet"),
    ("2021-49", 2895, "train-02221-of-02895.parquet", "train-00408-of-02895.parquet"),
    ("2024-26", 3168, "train-02037-of-03168.parquet", "train-01127-of-03168.parquet"),
)
CRAWL_ORDER: tuple[str, ...] = tuple(f"crawl=CC-MAIN-{s[0]}" for s in STRATA)


def crawl_path(crawl: str, basename: str) -> str:
    """Exact case-sensitive POSIX path; no normalization."""
    return f"data/{crawl}/{basename}"


ARM_M_LIMITS: dict[str, int] = {
    "plans": 8,
    "rows_per_file": 512,
    "rows_total": 4096,
    "max_scan_rows_per_file": 16384,
    "max_scan_rows_arm": 131072,
    "buffer_bytes": 4194304,
    "batch_rows": 256,
    "pilot_requests_per_plan": 100,
    "footer_requests_per_file": 10,
    "footer_requests_total": 80,
    "footer_bytes_per_file": 4194304,
    "footer_bytes_total": 33554432,
    "data_requests_per_plan": 100,
    "data_requests_total": 800,
    "requests_total": 880,
    "response_body_bytes_total": 268435456,
    "data_bytes_per_file": 29360128,
    "data_bytes_total": 234881024,
    "response_body_bytes_max": 4194304,
    "decompressed_bytes_per_file": 67108864,
    "decompressed_bytes_arm": 536870912,
    "record_bytes_max": 1048576,
    "parser_bytes_max": 33554432,
    "memory_resident_bytes": 268435456,
    "disk_scratch_bytes": 469762048,
    "disk_final_bytes": 67108864,
    "disk_combined_bytes": 536870912,
    "time_seconds_arm": 1800,
    "time_seconds_plan": 600,
    "time_seconds_request": 30,
    "max_retries": 2,
    "max_redirect_hops": 3,
}

ARM_T_LIMITS: dict[str, int] = {
    "selected_rows_max": 118,
    "document_bytes_max": 65536,
    "retained_text_bytes_max": 8388608,
    "final_artifact_bytes_max": 16777216,
    "requests_arm_max": 800,
    "requests_per_file_max": 100,
    "transfer_bytes_arm_max": 134217728,
    "transfer_bytes_per_file_max": 16777216,
    "footer_bytes_per_file_max": 2097152,
    "footer_bytes_total_max": 16777216,
    "data_bytes_per_file_max": 14680064,
    "response_body_bytes_max": 4194304,
    "range_buffer_bytes_max": 4194304,
    "decompressed_bytes_per_file_max": 67108864,
    "decompressed_bytes_arm_max": 536870912,
    "scan_rows_per_file_max": 16384,
    "scan_rows_arm_max": 131072,
    "batch_rows": 256,
    "ratio_max": 15,
    "ratio_exempt_bytes": 16777216,
    "parser_bytes_max": 33554432,
    "memory_resident_bytes": 268435456,
    "disk_scratch_bytes": 520093696,
    "disk_final_bytes": 16777216,
    "disk_combined_bytes": 536870912,
    "time_seconds_arm": 1800,
    "time_seconds_file": 600,
    "time_seconds_request": 30,
    "max_retries": 2,
    "max_redirect_hops": 3,
}

# (stratum_name, kind, per_crawl_request), §6 precedence order.
TEXT_STRATA: tuple[tuple[str, str, int | None], ...] = (
    ("B_science_census", "census", None),
    ("D_only_science_census", "census", None),
    ("B_practical_survivor", "ranked", 1),
    ("B_practical_loss", "ranked", 1),
    ("D_only_practical", "ranked", 2),
    ("B_prose_survivor", "ranked", 1),
    ("B_prose_loss", "ranked", 1),
    ("D_only_prose", "ranked", 2),
)
CENSUS_EXPECTED: dict[str, int] = {
    "B_science_census": 29,
    "D_only_science_census": 25,
}
TEXT_SELECTION_MAX = 118

ACQUISITION_STATUSES: tuple[str, ...] = (
    "full_text_available",
    "unreviewable_full_document_due_to_size",
    "unreviewable_missing_or_invalid_text",
    "acquisition_incomplete",
)

REVIEWERS: tuple[str, ...] = ("reviewer-1", "reviewer-2")

# --------------------------------------------------------------------------
# Evidence-v2.1 bounded transport amendment (additive; v2.0 above untouched).
# Scientific hash namespace intentionally stays v2.0 (see PROTOCOL_VERSION).
# --------------------------------------------------------------------------

V21_PROTOCOL_VERSION = "essential-web-evidence-v2.1"
V21_SCIENTIFIC_NAMESPACE = "essential-web-evidence-v2.0"
V21_FREEZE_DIGEST = "bac82d6b9538f4005f7f0ffee6aa5c4f3a5394098c0fae8f63fc94c76832a7cd"
V21_PROTOCOL_SHA256 = "1c437881148c3d6c1c42ca610e364055a0625732b07361f2e9271d0918fcdb4b"
V21_SELECTION_DIGEST = "975ba3dee4af0598e665ea05c69bbe49336c3e3a35fca777a863073190b78474"
V21_SELECTION_BYTES = 23807
V21_SELECTION_SHA256 = "8424f9668fef6952a558ead0dae27a705545a7f4a4d302713707d3ec6bb1af27"
V21_M_EVIDENCE_DIGEST = "2ecf3eb9df01a41f7f9defa20633841211396f654350df80e460f5c2b5740052"
V21_COSTMAP_DIGEST = "ed713a0a6fe22cdd396f758182b841b0208f0fe92795b3784f8d71bb6bbf665b"
V21_M_INCOMPLETE_DIGEST = "08b02d6a909f831039a7bfd9ddf01da7ca1dcd68d8f7cc2465d99291b26fa6c1"
V21_T_INCOMPLETE_DIGEST = "511d4c0771aec2c44066a04a1a7d07792902cdd0f1606bbd4249d80fbddee572"

# Only the transfer ceilings change; every other T limit is inherited.
V21_T_LIMITS: dict[str, int] = {
    **ARM_T_LIMITS,
    "data_bytes_per_file_max": 31457280,
    "data_bytes_arm_max": 251658240,
    "transfer_bytes_per_file_max": 33554432,
    "transfer_bytes_arm_max": 268435456,
}

# Adopted planning history: measured arm totals (attempt2 + prior failure).
V21_T_CARRY_BYTES = 2231492
V21_T_CARRY_REQUESTS = 54
V21_T_CARRY_ATTEMPT2_BYTES = 1994765
V21_T_CARRY_ATTEMPT2_REQUESTS = 48
V21_T_CARRY_PRIOR_BYTES = 236727
V21_T_CARRY_PRIOR_REQUESTS = 6

# --------------------------------------------------------------------------
# Evidence-v2.2 prospective M amendment (additive; v2.0/v2.1 above untouched).
# Only M's cumulative footer-request/file ceiling changes (10 -> 16).
# Scientific hash namespace stays v2.0.
# --------------------------------------------------------------------------

V22_PROTOCOL_VERSION = "essential-web-evidence-v2.2"
V22_SCIENTIFIC_NAMESPACE = "essential-web-evidence-v2.0"
V22_FREEZE_DIGEST = "b6602a445307d9638913c046b4cfebab3356e20559d89b3f2ab601aa924ebd0c"
V22_PROTOCOL_SHA256 = "fd698793068458b31563418fdca29c97fa1efe1099b8776c7fd1407887f4f49d"
V22_M_EVIDENCE_DIGEST = "2ecf3eb9df01a41f7f9defa20633841211396f654350df80e460f5c2b5740052"
V22_COSTMAP_DIGEST = "ed713a0a6fe22cdd396f758182b841b0208f0fe92795b3784f8d71bb6bbf665b"

# The ONLY v2.2 numeric cap change: M footer-request/file, cumulative.
V22_M_FOOTER_REQUESTS_PER_FILE = 16
# Prospective revalidation reservations nested inside cumulative caps.
V22_M_REVALIDATION_PER_FILE = 4
V22_M_REVALIDATION_ARM = 25

# Authoritative reconstructed M historical physical requests (distinct runs).
V22_M_HISTORY_REQUESTS: dict[str, int] = {
    "data/crawl=CC-MAIN-2014-15/train-01860-of-02772.parquet": 12,
    "data/crawl=CC-MAIN-2015-32/train-01682-of-01920.parquet": 7,
    "data/crawl=CC-MAIN-2016-50/train-02156-of-03132.parquet": 6,
    "data/crawl=CC-MAIN-2018-05/train-03378-of-03429.parquet": 6,
    "data/crawl=CC-MAIN-2019-09/train-00153-of-02577.parquet": 6,
    "data/crawl=CC-MAIN-2021-04/train-00179-of-03315.parquet": 6,
    "data/crawl=CC-MAIN-2021-49/train-00408-of-02895.parquet": 6,
    "data/crawl=CC-MAIN-2024-26/train-01127-of-03168.parquet": 6,
}
V22_M_HISTORY_TOTAL = 55

# Recorded returned range-body bytes (NOT all-body exact totals).
V22_M_HISTORY_BYTES: dict[str, int] = {
    "data/crawl=CC-MAIN-2014-15/train-01860-of-02772.parquet": 537616,
    "data/crawl=CC-MAIN-2015-32/train-01682-of-01920.parquet": 264322,
    "data/crawl=CC-MAIN-2016-50/train-02156-of-03132.parquet": 215631,
    "data/crawl=CC-MAIN-2018-05/train-03378-of-03429.parquet": 264763,
    "data/crawl=CC-MAIN-2019-09/train-00153-of-02577.parquet": 228714,
    "data/crawl=CC-MAIN-2021-04/train-00179-of-03315.parquet": 221283,
    "data/crawl=CC-MAIN-2021-49/train-00408-of-02895.parquet": 231779,
    "data/crawl=CC-MAIN-2024-26/train-01127-of-03168.parquet": 227340,
}
V22_M_HISTORY_BYTES_TOTAL = 2191448

# Reviewer-visible package MUST NOT contain these source attributes.
FORBIDDEN_PACKAGE_FIELDS: frozenset[str] = frozenset(
    {
        "policy",
        "crawl",
        "fdc",
        "fdc_code",
        "fdc_labels",
        "artifact",
        "correctness",
        "english",
        "english_score",
        "stratum",
        "source_stratum",
        "locator",
        "source_file",
        "source_row",
        "filename",
        "url",
        "acquisition_order",
        "stratum_counts",
    }
)
