r"""REVIEW-2026-10-03 **H3** — bound the observable uniqueness index.

Moves `Observable`'s unique key from `(data_type, normalized_data)` to
`(data_type, data_hash)`. `normalized_data` is an unbounded `TextField`; inside a btree
index it hits Postgres's ~2704-byte tuple cap (a long URL fails the INSERT on prod
only) and, on a non-`C` collation, the index comparison becomes locale-aware so
`C:\Temp\A.txt` and `c:\temp\a.txt` collide for a case-sensitive type — silently
contradicting AC5.4. Hashing bounds the indexed value at 64 hex characters and makes the
comparison collation-independent.

Operation order matters and is not the obvious one:

1. add `data_hash` **nullable** — a NOT NULL column cannot be added to a table that has
   rows, and the digest of an existing row has to be computed by this migration;
2. backfill every existing row with `sha256(canonical_value(row))`;
3. tighten to NOT NULL;
4. only then swap the constraint (removing the old unique index is what frees the
   unbounded `normalized_data` index, so it goes first, before the new one lands).

REVIEW-2026-10-04 round-2 **H-7** (TODO 1.17) — the backfill. It used to hash **raw**
`normalized_data`:

    hashlib.sha256(normalized_data)      # what this migration used to do
    data_hash(canonical_value(row))      # what the runtime does

Those disagree for any type that is not marked `is_case_sensitive`. An uppercase `hash` row
that migrated carried `b864a0…` where the runtime would have produced `b1c6d2…`, so the
migrated row and a later runtime row for the same artifact hashed differently and **both
were admitted** by `uniq_obs_dtype_hash` — the one constraint whose entire job is to stop
that. The two implementations also had no mechanism preventing further drift.

The fix is not a corrected copy of the formula but a call to the runtime's own
`canonical_value()`, so there is exactly one implementation of "the value the identity
digest is taken over" and a future change to it applies to migrated rows for free.
`select_related("data_type")` keeps it to one query per row rather than one per lookup.

Caveat for an already-migrated database: Django records applied migrations by name, so a
database that ran the *previous* version of this file is not re-run automatically. Nothing
in this repository holds one (the dev database has zero `observable` rows). To repair such
a database by hand, in the same spirit as the fix:

    UPDATE observable SET data_hash = <sha256 of casefolded normalized_data>
     WHERE data_type_id IN (SELECT id FROM observable_type WHERE NOT is_case_sensitive)

Resolve any UNIQUE violation this raises by hand rather than deleting rows: it means the
database genuinely holds two spellings of one artifact.
"""

import django.db.models.deletion
import observables.hashing
from django.db import migrations, models
from observables.hashing import canonical_value, data_hash


def backfill_data_hash(apps, schema_editor):
    """Give every pre-existing row the digest the *runtime* would give it (H-7)."""
    Observable = apps.get_model("observables", "Observable")
    rows = Observable.objects.select_related("data_type").only(
        "pk", "normalized_data", "data_type__is_case_sensitive"
    )
    for observable in rows.iterator():
        Observable.objects.filter(pk=observable.pk).update(
            data_hash=data_hash(canonical_value(observable))
        )


class Migration(migrations.Migration):

    dependencies = [
        ('observables', '0002_seed'),
    ]

    operations = [
        migrations.RemoveConstraint(
            model_name='observable',
            name='uniq_obs_dtype_norm',
        ),
        migrations.AddField(
            model_name='observable',
            name='data_hash',
            field=observables.hashing.DataHashField(editable=False, max_length=64, null=True),
        ),
        migrations.RunPython(backfill_data_hash, migrations.RunPython.noop),
        migrations.AlterField(
            model_name='observable',
            name='data_hash',
            field=observables.hashing.DataHashField(editable=False, max_length=64),
        ),
        migrations.AlterField(
            model_name='observable',
            name='data_type',
            field=models.ForeignKey(db_index=False, on_delete=django.db.models.deletion.PROTECT, related_name='observables', to='observables.observabletype'),
        ),
        migrations.AlterField(
            model_name='observable',
            name='pap',
            field=models.SmallIntegerField(choices=[(0, 'White'), (1, 'Green'), (2, 'Amber'), (3, 'Red')], default=2),
        ),
        migrations.AlterField(
            model_name='observable',
            name='tlp',
            field=models.SmallIntegerField(choices=[(0, 'White'), (1, 'Green'), (2, 'Amber'), (3, 'Red'), (4, 'Unknown')], default=2),
        ),
        migrations.AddConstraint(
            model_name='observable',
            constraint=models.UniqueConstraint(fields=('data_type', 'data_hash'), name='uniq_obs_dtype_hash'),
        ),
        migrations.AddConstraint(
            model_name='observable',
            constraint=models.CheckConstraint(condition=models.Q(('tlp__gte', 0), ('tlp__lte', 4)), name='observable_tlp_range'),
        ),
        migrations.AddConstraint(
            model_name='observable',
            constraint=models.CheckConstraint(condition=models.Q(('pap__gte', 0), ('pap__lte', 3)), name='observable_pap_range'),
        ),
    ]
