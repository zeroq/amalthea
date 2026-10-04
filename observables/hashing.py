"""Observable identity hashing (REVIEW-2026-10-03 **H3**).

The unique constraint on `Observable` used to be ``(data_type, normalized_data)`` where
``normalized_data`` is an unbounded ``TextField``. Inside a btree index that is two
Postgres-only failure modes, neither of which SQLite can reproduce:

1. **Tuple size cap (~2704 bytes).** A long URL, a UNC path or a base64 blob blows the
   index-entry limit and the INSERT fails — on production only, as an unexplained 500
   on an analyst's paste.
2. **Collation-aware comparison.** On any non-`C` collation the index comparison
   becomes locale-aware, so for a case-sensitive type (``file``)
   ``C:\\Temp\\A.txt`` and ``c:\\temp\\a.txt`` collide in the index while the
   normalization rule says they are distinct — silently contradicting AC5.4.

Hashing ``normalized_data`` fixes both: the indexed value becomes 64 hex characters
(the index tuple drops to ~80 bytes) and the comparison is over ASCII hex, so the
database collation is irrelevant.

The digest is taken over the value **after** case folding for any observable type that is
not marked ``is_case_sensitive``. Without that, AC5.4 ("case hashes normalize to
lowercase") would only be satisfied by whoever happens to write the extractor, and the
unique key would happily admit ``D41D8CD9…`` and ``d41d8cd9…`` as two different artifacts.
Folding here means the uniqueness the constraint provides *is* AC5.4, with or without the
mapping engine. A case-sensitive type (``file``) is hashed exactly as given.

Must land before any production data exists (plan §12 R12).
"""

from __future__ import annotations

import hashlib
from collections.abc import Sequence
from typing import Any

from django.apps import apps
from django.db import models

#: Length of the hex digest, i.e. the declared width of `Observable.data_hash`.
DATA_HASH_LENGTH = 64


def data_hash(normalized_data: str) -> str:
    """sha256 hex digest of an already-normalized observable value."""
    return hashlib.sha256(normalized_data.encode("utf-8")).hexdigest()


def canonical_value(instance: models.Model) -> str:
    """The value the identity digest is taken over.

    Case-folds anything whose `ObservableType.is_case_sensitive` is false — hex hashes,
    hostnames, e-mail addresses — because AC5.4 says those identify the same artifact
    regardless of case. A case-sensitive type is returned untouched, so two spellings of
    one file path stay distinct.
    """
    normalized = str(getattr(instance, "normalized_data", ""))
    data_type = getattr(instance, "data_type", None)
    data_type_id = getattr(instance, "data_type_id", None)
    if data_type is None and data_type_id is not None:
        observable_type = apps.get_model("observables", "ObservableType")
        data_type = observable_type.objects.filter(pk=data_type_id).first()
    if data_type is not None and not data_type.is_case_sensitive:
        return normalized.casefold()
    return normalized


class DataHashField(models.CharField):
    """`sha256(normalized_data)`, derived on INSERT and never accepted from a caller.

    The computation lives on the field (not in ``Observable.save()``) for the same
    reason `cases.numbering.AllocatedNumberField` does: `Field.pre_save` is the only
    hook every INSERT path executes, `bulk_create()` included.
    """

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        kwargs.setdefault("max_length", DATA_HASH_LENGTH)
        kwargs.setdefault("editable", False)
        super().__init__(*args, **kwargs)

    def deconstruct(self) -> tuple[str, str, Sequence[Any], dict[str, Any]]:
        name, _path, args, kwargs = super().deconstruct()
        return name, "observables.hashing.DataHashField", args, kwargs

    def pre_save(self, model_instance: models.Model, add: bool) -> Any:
        setattr(model_instance, self.attname, data_hash(canonical_value(model_instance)))
        return super().pre_save(model_instance, add)
