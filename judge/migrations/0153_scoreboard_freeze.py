import jsonfield.fields
from django.db import migrations, models


class Migration(migrations.Migration):
    """Scoreboard freeze.

    Seven nullable columns appended to two tables, and nothing else: no data is rewritten, no index is built and
    no existing column is altered, so MariaDB adds them in place. A contest with no freeze configured keeps
    behaving exactly as before, because `freeze_minutes` defaults to empty.
    """

    dependencies = [
        ('judge', '0152_announcement_clarification'),
    ]

    operations = [
        migrations.AddField(
            model_name='contest',
            name='freeze_minutes',
            field=models.PositiveIntegerField(
                blank=True, null=True,
                help_text='Freeze the public scoreboard over the last this many minutes of each participation. '
                          'Leave empty not to freeze it. A virtual participation is frozen over its own last '
                          'minutes, not over the clock of the live contest.',
                verbose_name='scoreboard freeze'),
        ),
        migrations.AddField(
            model_name='contest',
            name='scoreboard_revealed',
            field=models.BooleanField(
                default=False,
                help_text='A frozen scoreboard stays frozen after the contest ends, so the result can be revealed '
                          'at a ceremony. Check this to lift the freeze and show everyone the real scoreboard and '
                          'the submissions it was hiding.',
                verbose_name='scoreboard revealed'),
        ),
        migrations.AddField(
            model_name='contestparticipation',
            name='frozen_score',
            field=models.FloatField(blank=True, null=True, verbose_name='score at freeze time'),
        ),
        migrations.AddField(
            model_name='contestparticipation',
            name='frozen_cumtime',
            field=models.PositiveIntegerField(blank=True, null=True, verbose_name='cumulative time at freeze time'),
        ),
        migrations.AddField(
            model_name='contestparticipation',
            name='frozen_tiebreaker',
            field=models.FloatField(blank=True, null=True, verbose_name='tie-breaking field at freeze time'),
        ),
        migrations.AddField(
            model_name='contestparticipation',
            name='frozen_format_data',
            field=jsonfield.fields.JSONField(blank=True, null=True,
                                             verbose_name='contest format specific data at freeze time'),
        ),
        migrations.AddField(
            model_name='contestparticipation',
            name='frozen_at',
            field=models.DateTimeField(
                blank=True, null=True,
                help_text='When the frozen copy above was taken. Empty means no copy was needed, because nothing '
                          'has changed since the freeze.',
                verbose_name='scoreboard frozen at'),
        ),
    ]
