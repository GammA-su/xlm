"""XLM objectives package complying with Contract C09."""

from xlm.objectives.auxiliary_fixture import AuxiliaryLearningObjective
from xlm.objectives.base import (
    BaseObjective,
    InvalidBatchError,
    ObjectiveCapabilities,
    UnsupportedBatchingError,
    accumulate_microbatch_gradient,
)
from xlm.objectives.cross_entropy import (
    CrossEntropyObjective,
    compute_independent_diagnostic_ce,
    create_cross_entropy_objective,
)
from xlm.objectives.noop import (
    NoOpObjective,
    NoOpObjectiveConfig,
    create_noop_objective,
)

__all__ = [
    "AuxiliaryLearningObjective",
    "BaseObjective",
    "CrossEntropyObjective",
    "InvalidBatchError",
    "NoOpObjective",
    "NoOpObjectiveConfig",
    "ObjectiveCapabilities",
    "UnsupportedBatchingError",
    "accumulate_microbatch_gradient",
    "compute_independent_diagnostic_ce",
    "create_cross_entropy_objective",
    "create_noop_objective",
]
