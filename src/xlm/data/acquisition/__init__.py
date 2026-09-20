"""Bounded, resumable data acquisition subsystem adhering to XLM Contracts C01 and C04."""

from __future__ import annotations

from xlm.data.acquisition.disk import (
    AtomicFileWriter,
    DiskCeilingExceededError,
    StorageCapacityManager,
)
from xlm.data.acquisition.fetcher import (
    AcquisitionLockHeldError,
    BoundedDecompressor,
    BoundedFetcher,
    DecompressionBombError,
)
from xlm.data.acquisition.plan import (
    AcquisitionLimits,
    AcquisitionMode,
    AcquisitionPlan,
    AuthorizationRequiredError,
    PlanAuthorization,
    SamplingFrame,
    SourceDriftDetectedError,
    load_acquisition_plan,
    save_acquisition_plan,
    validate_plan_authorization,
)
from xlm.data.acquisition.progress import (
    AcquisitionState,
    FileProgress,
    ProgressCorruptionError,
    ProgressJournal,
)
from xlm.data.acquisition.receipt import (
    AcquiredFileInfo,
    AcquisitionReceipt,
)
from xlm.data.acquisition.verifier import (
    AcquisitionVerifier,
    ChecksumMismatchError,
    VerificationError,
)

__all__ = [
    "AcquiredFileInfo",
    "AcquisitionLimits",
    "AcquisitionLockHeldError",
    "AcquisitionMode",
    "AcquisitionPlan",
    "AcquisitionReceipt",
    "AcquisitionState",
    "AcquisitionVerifier",
    "AtomicFileWriter",
    "AuthorizationRequiredError",
    "BoundedDecompressor",
    "BoundedFetcher",
    "ChecksumMismatchError",
    "DecompressionBombError",
    "DiskCeilingExceededError",
    "FileProgress",
    "PlanAuthorization",
    "ProgressCorruptionError",
    "ProgressJournal",
    "SamplingFrame",
    "SourceDriftDetectedError",
    "StorageCapacityManager",
    "VerificationError",
    "load_acquisition_plan",
    "save_acquisition_plan",
    "validate_plan_authorization",
]
