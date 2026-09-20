# Authored evaluation-input fixtures

Everything under this directory is **authored synthetic content**. It is shaped
like the official benchmark record schemas so the pinned installed task
definitions (prompt templates, `process_docs`, `doc_to_choice`, metrics,
`acc_norm` normalization) apply unchanged, but no sentence, question, choice or
label here comes from ARC, HellaSwag, PIQA or BLiMP.

Two things keep that distinction visible rather than merely asserted:

* every record id begins with `syn_`, and every text is obviously invented;
* the manifests declare `scope_kind: authored_fixture` and
  `exposure_class: authored_fixture`, which makes every result carry the label
  "authored synthetic fixture scope" and makes it permanently ineligible as
  research or promotion evidence, however complete it is within its own scope.

`source_repository` and `source_revision` in these manifests name the *shape*
the records imitate. They are not a claim that the content was obtained from
those repositories — it was not; it was written for these tests.

## Layout

| Directory | Purpose |
|---|---|
| `dev_fixture_v1/` | Complete positive control: a four-task scope that is genuinely complete within itself. |
| `dev_fixture_subset/` | A correctly declared narrower scope, complete only within itself. |

Counterexamples - a runtime limit, a duplicate id, an unexpected id, a missing
BLiMP subdataset, a failed item, a wrong split, a corrupted artifact - are built
from these two by the tests, so each negative case states its own single
deviation instead of hiding it in a checked-in file.

Regenerate with:

```powershell
uv run --offline --locked --extra cpu --extra eval `
    python fixtures/eval/inputs/author_inputs.py
```

## Item identities

ARC-Easy carries an intrinsic `id`. HellaSwag, PIQA and BLiMP do not, so the
selected artifacts carry an explicit `xlm_item_locator` field instead, declared
through `item_id_field`. The locator records where the record came from —
`<source>/<split>/<row>` — and is fixed at selection time. It is never derived
from a result position, because sorting, filtering and a runtime limit all
change those.
