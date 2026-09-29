"""Minted trusted objects for the Phase-P execution boundary.

A trusted object (validated authorization, validated operator approval,
verified root context, validated plan set, verified runtime identity) can
only be produced by the validator that owns it. Three independent
properties enforce that:

1. ``Cls(...)`` always raises: the class has no public constructor.
2. Minting requires the module-private ``_MINT`` sentinel.
3. Every minted instance is recorded in a weak registry; consumers call
   :func:`require_minted`, which rejects look-alikes built with
   ``object.__new__``, copies, pickles and subclasses.

Instances are immutable: attribute assignment, deletion, copying and
pickling raise. Python offers no memory-safe private state, so a caller
that deliberately imports underscore-prefixed internals is outside the
supported API; the supported API never accepts trusted objects from
callers at all (it parses bytes itself), and internal consumers re-check
the registry at every use.
"""

from __future__ import annotations

import weakref
from typing import Any, NoReturn


class TrustError(RuntimeError):
    """A trusted object was forged, mutated, copied or used out of context."""


_MINT = object()
_REGISTRY: weakref.WeakSet[TrustedObject] = weakref.WeakSet()


class TrustedObject:
    """Immutable base for minted objects; never constructible directly."""

    __slots__ = ("__weakref__",)

    def __new__(cls, *args: Any, **kwargs: Any) -> Any:
        raise TrustError(f"{cls.__name__} can only be produced by its validator")

    def __setattr__(self, name: str, value: Any) -> NoReturn:
        raise TrustError(f"{type(self).__name__} is immutable")

    def __delattr__(self, name: str) -> NoReturn:
        raise TrustError(f"{type(self).__name__} is immutable")

    def __reduce_ex__(self, protocol: Any) -> NoReturn:
        raise TrustError(f"{type(self).__name__} cannot be serialized or copied")

    def __copy__(self) -> NoReturn:
        raise TrustError(f"{type(self).__name__} cannot be copied")

    def __deepcopy__(self, memo: Any) -> NoReturn:
        raise TrustError(f"{type(self).__name__} cannot be copied")

    def __eq__(self, other: object) -> bool:
        return self is other

    def __hash__(self) -> int:
        return id(self)


def _mint[T: TrustedObject](cls: type[T], token: object, **fields: Any) -> T:
    """Create and register one trusted instance (validators only)."""
    if token is not _MINT:
        raise TrustError("minting requires the validator token")
    if cls is TrustedObject or not issubclass(cls, TrustedObject):
        raise TrustError("only concrete trusted classes can be minted")
    expected = set(cls.__slots__)
    if set(fields) != expected:
        raise TrustError(
            f"{cls.__name__} minted with fields {sorted(fields)} != {sorted(expected)}"
        )
    obj = object.__new__(cls)
    for name, value in fields.items():
        object.__setattr__(obj, name, value)
    _REGISTRY.add(obj)
    return obj


def require_minted[T: TrustedObject](obj: object, cls: type[T]) -> T:
    """Return ``obj`` only when it is a registered instance of exactly ``cls``."""
    if type(obj) is not cls:
        raise TrustError(f"expected a validated {cls.__name__}, got {type(obj).__name__}")
    if obj not in _REGISTRY:
        raise TrustError(f"{cls.__name__} was not produced by its validator")
    return obj
