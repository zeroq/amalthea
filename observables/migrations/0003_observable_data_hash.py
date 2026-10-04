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
2. backfill every existing row with `sha256(normalized_data)`;
3. tighten to NOT NULL;
4. only then swap the constraint (removing the old unique index is what frees the
   unbounded `normalized_data` index, so it goes first, before the new one lands).
"""

import hashlib

import django.db.models.deletion
import observables.hashing
from django.db import migrations, models


def backfill_data_hash(apps, schema_editor):
    Observable = apps.get_model('observables', 'Observable')
    for pk, normalized_data in Observable.objects.values_list('pk', 'normalized_data').iterator():
        digest = hashlib.sha256(normalized_data.encode('utf-8')).hexdigest()
        Observable.objects.filter(pk=pk).update(data_hash=digest)


class Migration(migrations.Migration):

    dependencies = [
        ('cases', '0004_case_hardening'),
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
