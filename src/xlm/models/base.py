"""Abstract base class for models complying with XLM Contract C08."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

import torch
import torch.nn as nn

from xlm.core.contracts import InferenceInput, LMOutput, ModelCapabilities
from xlm.models.aliases import TensorAlias, resolve_tied_aliases
from xlm.models.parameter_counts import ParameterCounts


class BaseModel(nn.Module, ABC):
    """Abstract base model interface complying with Contract C08."""

    config: Any

    @abstractmethod
    def forward(
        self,
        input_ids: torch.Tensor | InferenceInput,
        attention_mask: torch.Tensor | None = None,
        position_ids: torch.Tensor | None = None,
        state: Any | None = None,
        requested_outputs: dict[str, bool] | None = None,
    ) -> LMOutput:
        """Standardized model forward pass.

        Args:
            input_ids: Token ID tensor of shape (batch, seq_len).
            attention_mask: Optional attention mask.
            position_ids: Optional explicit position IDs of shape (batch, seq_len).
            state: Optional recurrent or KV-cache state.
            requested_outputs: Optional requested auxiliary outputs (e.g. {'hidden_states': True}).

        Returns:
            LMOutput containing logits, auxiliary outputs, and optional state.
            Loss is strictly excluded.
        """
        ...

    @abstractmethod
    def get_capabilities(self) -> ModelCapabilities:
        """Return declared architectural capabilities."""
        ...

    @property
    def capabilities(self) -> ModelCapabilities:
        """Property alias for get_capabilities()."""
        return self.get_capabilities()

    @abstractmethod
    def count_parameters(self) -> ParameterCounts:
        """Return parameter count breakdown."""
        ...

    def tied_parameter_aliases(self) -> tuple[TensorAlias, ...]:
        """Declare which state names are aliases of one intended tensor (C08).

        Serialization stores each tied tensor once and rebuilds the alias from
        this declaration, so the answer must describe real object identity, not
        coincidental value equality. The default derives it from the live module
        graph and validates that every alias is a complete same-tensor alias,
        which is correct for any architecture that ties by assigning the same
        ``nn.Parameter`` to two attributes.

        Override only when an architecture establishes ties some other way; an
        override must still return complete same-tensor aliases.
        """
        return resolve_tied_aliases(self)
