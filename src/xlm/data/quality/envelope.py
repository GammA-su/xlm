"""Strict typed operational envelope (recorded in units and the receipt, re-validated).

Every field is strictly typed (no bool-as-int, no int-as-float, no NaN/Infinity, no
unknown or missing field) and range-checked, and the cross-field relationships the
implementation relies on are enforced. Verification reconstructs this model from the
receipt; comparing a digest alone is never sufficient.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, StrictFloat, StrictInt, model_validator

WORKER_CHOICES = (1, 2, 4, 8, 16)
MAX_RSS_BYTES = 16 * 1024**3
MAX_DEADLINE_SECONDS = 7 * 86400.0
MAX_DOCUMENT_BYTES = 256 * 1024**2
MAX_OUTPUT_BYTES = 4 * 1024**4
MAX_RESERVE_BYTES = 2**62  # a huge reserve is valid; it refuses as "free space"
MIN_CHUNK_BYTES, MAX_CHUNK_BYTES = 1024, 256 * 1024**2
MAX_VERIFY_THREADS = 16
MAX_PENDING_COMMITS = 64
MAX_REVIEW_PER_STRATUM = 64
QUEUE_FACTOR = 2  # at most QUEUE_FACTOR x workers chunk tasks in flight
FLOAT_FIELDS = ("deadline_seconds", "supervisor_interval_seconds", "publication_margin_seconds")


class EnvelopeError(ValueError):
    """Content-free envelope refusal."""


class OperationalEnvelope(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True, allow_inf_nan=False)

    workers: StrictInt
    queue_tasks: StrictInt
    max_rss_bytes: StrictInt
    free_reserve_bytes: StrictInt
    max_output_bytes: StrictInt
    max_document_bytes: StrictInt
    deadline_seconds: StrictFloat
    chunk_bytes: StrictInt
    verify_threads: StrictInt
    max_pending_commits: StrictInt
    supervisor_interval_seconds: StrictFloat
    publication_margin_seconds: StrictFloat
    review_per_stratum: StrictInt

    @model_validator(mode="before")
    @classmethod
    def exact_float_types(cls, value: object) -> object:
        # StrictFloat still admits int; the envelope records exact float seconds.
        if isinstance(value, dict):
            for name in FLOAT_FIELDS:
                if name in value and type(value[name]) is not float:
                    raise ValueError(f"envelope {name} must be a float")
        return value

    @model_validator(mode="after")
    def bounded(self) -> OperationalEnvelope:
        def need(condition: bool, what: str) -> None:
            if not condition:
                raise ValueError(f"envelope {what}")

        need(self.workers in WORKER_CHOICES, "workers must be 1, 2, 4, 8 or 16")
        expected_queue = QUEUE_FACTOR * self.workers if self.workers > 1 else 1
        need(self.queue_tasks >= self.workers, "queue must hold at least one task per worker")
        need(self.queue_tasks == expected_queue, "queue must equal the implementation bound")
        need(0 < self.max_rss_bytes <= MAX_RSS_BYTES, "RSS ceiling outside (0, 16 GiB]")
        need(0 <= self.free_reserve_bytes <= MAX_RESERVE_BYTES, "free reserve outside its range")
        need(0 < self.max_output_bytes <= MAX_OUTPUT_BYTES, "output ceiling outside its range")
        need(0 < self.max_document_bytes <= MAX_DOCUMENT_BYTES, "document ceiling outside range")
        need(0 < self.deadline_seconds <= MAX_DEADLINE_SECONDS, "deadline outside (0, 7 days]")
        need(MIN_CHUNK_BYTES <= self.chunk_bytes <= MAX_CHUNK_BYTES, "chunk size outside range")
        need(1 <= self.verify_threads <= MAX_VERIFY_THREADS, "verify threads outside [1, 16]")
        need(1 <= self.max_pending_commits <= MAX_PENDING_COMMITS, "pending commits outside range")
        need(0 < self.supervisor_interval_seconds <= 5.0, "supervisor interval outside (0, 5]")
        need(
            0 <= self.publication_margin_seconds < self.deadline_seconds,
            "publication margin must be non-negative and below the deadline",
        )
        need(1 <= self.review_per_stratum <= MAX_REVIEW_PER_STRATUM, "review count outside range")
        return self


def validate_envelope(value: object) -> OperationalEnvelope:
    try:
        return OperationalEnvelope.model_validate(value)
    except ValueError:
        raise EnvelopeError("operational envelope is invalid") from None
