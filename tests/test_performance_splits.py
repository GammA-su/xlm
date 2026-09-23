"""P29 exact offline performance regression checks."""

from __future__ import annotations

import hashlib
import weakref
from collections.abc import Iterator

from xlm.core.contracts import CanonicalDocument
from xlm.data.normalization import canonical_normalize
from xlm.data.pools.splits import SplitConfig, assign_splits


def document(text: str, index: int = 0) -> CanonicalDocument:
    text = canonical_normalize(text)
    digest = hashlib.sha256(text.encode()).hexdigest()
    return CanonicalDocument(
        str(index),
        "fixture",
        "v1",
        "authored.jsonl",
        index,
        digest,
        digest,
        text,
        len(text.encode()),
        "en",
        1.0,
        "prose",
        {},
        [],
        "authored",
        [],
        [],
        {},
        "train",
    )


def test_split_stream_does_not_retain_document_text() -> None:
    references: list[weakref.ReferenceType[CanonicalDocument]] = []

    def documents() -> Iterator[CanonicalDocument]:
        for index in range(100):
            doc = document(f"Authored text {index}. " * 100, index)
            references.append(weakref.ref(doc))
            # The generator and grouping loop may hold their current records.
            assert sum(ref() is not None for ref in references) <= 2
            yield doc

    config = SplitConfig(
        diagnostic_val_target_bytes=4000, quick_val_target_bytes=1000, audit_target_bytes=2000
    )
    streamed = assign_splits(documents(), config=config)
    eager = assign_splits(
        [document(f"Authored text {i}. " * 100, i) for i in range(100)], config=config
    )
    assert streamed.to_dict() == eager.to_dict()
    assert streamed.membership_digest() == eager.membership_digest()
    assert all(ref() is None for ref in references)
