"""Repair `Observable.data_hash` rows left stale by the round-3 **H3-1** defect.

`is_case_sensitive` feeds `canonical_value()` and therefore `data_hash`, but nothing observed a
change to it. `observables/models.py` now intercepts the flip in `ObservableType.save()` and
re-hashes the affected rows; this migration repairs rows that were *already* written under the old
rule before that fix shipped.

It recomputes from the current rule rather than assuming a rule, so it is correct for any starting
state — including the seeded `file` type, which is case-sensitive by design (REVIEW H4).

Runs as a no-op on a healthy database: if every stored digest already equals
`sha256(canonical_value_under(row.normalized_data, case_sensitive=row.data_type.is_case_sensitive))`,
nothing is written. Only genuinely stale rows are touched, so on the common path this migration
costs one SELECT per type and no writes at all.

**Refuses rather than guesses.** If recomputing would collapse two existing rows into one digest,
this raises instead of picking a winner. Merging observables is a judgement call — they carry
case/alert links, IOC flags and TLP markings — and a migration has no business making it. See
`observables/rehash.py` for the full argument.
"""

from django.db import migrations

from observables.hashing import canonical_value_under, data_hash


def forwards(apps, schema_editor) -> None:
    """Rewrite only the digests that no longer match their type's current case rule."""
    Observable = apps.get_model("observables", "Observable")
    ObservableType = apps.get_model("observables", "ObservableType")

    case_by_type = dict(ObservableType.objects.values_list("pk", "is_case_sensitive"))

    # The collision check must consider the *whole* post-state, not just the rows being written.
    # A stale row can be made to collide with a row that was already correct: with `/Tmp/A.bin` and
    # `/tmp/a.bin` under a case-insensitive type, the second row's digest is already right and is
    # not rewritten, while the first is — checking only the rewritten rows would find one digest,
    # declare no collision, and then hit the UNIQUE constraint mid-`bulk_update`.
    stale: list[tuple[str, str]] = []
    digests: dict[str, list[str]] = {}
    # One pass to collect, then one write pass: interleaving reads and writes here would make a
    # mid-flight collision depend on row order, which is exactly the non-determinism to avoid.
    for pk, normalized, data_type_id, stored in Observable.objects.values_list(
        "pk", "normalized_data", "data_type_id", "data_hash"
    ):
        expected = data_hash(
            canonical_value_under(
                normalized, case_sensitive=bool(case_by_type.get(data_type_id, False))
            )
        )
        digests.setdefault(expected, []).append(str(pk))
        if expected != stored:
            stale.append((expected, str(pk)))

    # Refuse on collision rather than letting the UNIQUE constraint fail halfway through, which
    # would leave the migration partially applied on some backends.
    collided = [tuple(sorted(pks)) for pks in digests.values() if len(pks) > 1]
    if collided:
        raise RuntimeError(
            "observables.0005_rehash_observable_data_hash refuses to merge observables: "
            f"{len(collided)} group(s) of existing rows would collapse to the same digest under "
            f"their type's current case rule, e.g. {collided[:3]}. These rows are distinct today "
            "and carry case/alert links and TLP markings; resolve them deliberately (see "
            "observables/rehash.py) rather than having a migration pick a winner."
        )

    if stale:
        Observable.objects.bulk_update(
            [Observable(pk=pk, data_hash=digest) for digest, pk in stale],
            ["data_hash"],
            batch_size=500,
        )


def backwards(apps, schema_editor) -> None:
    """No-op.

    The digest is derived state with no pre-defect value to restore: what a row *should* hash to is
    a function of its type's rule, so "un-applying" this migration has no coherent meaning. Rows
    written by `forwards` were stale, and recomputing them is idempotent — re-running `forwards`
    after any later legitimate change converges on the same answer. Irreversible by construction,
    which `RunPython` accepts via the explicit `migrations.RunPython.noop` reverse below.
    """
    return None


class Migration(migrations.Migration):
    dependencies = [
        ("observables", "0004_observabletaglink_alter_observable_tags"),
    ]

    operations = [
        migrations.RunPython(forwards, migrations.RunPython.noop),
    ]