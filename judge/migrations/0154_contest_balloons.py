import django.core.validators
import django.db.models.deletion
import django.utils.timezone
from django.db import migrations, models


class Migration(migrations.Migration):
    """Balloon control.

    Three new tables (the balloon staff of each contest, the delivery log and the contestant locations) and two
    text columns with an empty default at the end of the contest problem table. No existing row is rewritten and
    no existing column is altered; MariaDB adds both columns in place.
    """

    dependencies = [
        ('judge', '0153_scoreboard_freeze'),
    ]

    operations = [
        migrations.AddField(
            model_name='contest',
            name='balloon_staff',
            field=models.ManyToManyField(
                blank=True, related_name='balloon_contests', to='judge.profile', verbose_name='balloon staff',
                help_text='These users will be able to see which balloons are due and mark them as delivered, '
                          'without being able to edit the contest or see its submissions.'),
        ),
        migrations.AddField(
            model_name='contestproblem',
            name='balloon_color',
            field=models.CharField(
                blank=True, default='', max_length=7, verbose_name='balloon colour',
                help_text='As #rrggbb. Leave empty if this problem has no balloon colour.',
                validators=[django.core.validators.RegexValidator('^#(?:[A-Fa-f0-9]{3}){1,2}$', 'Invalid colour.')]),
        ),
        migrations.AddField(
            model_name='contestproblem',
            name='balloon_color_name',
            field=models.CharField(blank=True, default='', max_length=30, verbose_name='balloon colour name',
                                   help_text='What the balloon staff call it, e.g. "red".'),
        ),
        migrations.CreateModel(
            name='ContestBalloonAction',
            fields=[
                ('id', models.AutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('action', models.CharField(choices=[('D', 'Delivered'), ('U', 'Delivery undone')], max_length=1,
                                            verbose_name='action')),
                ('time', models.DateTimeField(default=django.utils.timezone.now, verbose_name='marked at')),
                ('participation', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE,
                                                    related_name='balloon_actions',
                                                    to='judge.contestparticipation', verbose_name='participation')),
                ('problem', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE,
                                              related_name='balloon_actions', to='judge.contestproblem',
                                              verbose_name='problem')),
                ('user', models.ForeignKey(null=True, on_delete=django.db.models.deletion.SET_NULL,
                                           related_name='+', to='judge.profile', verbose_name='marked by')),
            ],
            options={
                'verbose_name': 'balloon action',
                'verbose_name_plural': 'balloon actions',
                'ordering': ('time', 'id'),
            },
        ),
        migrations.CreateModel(
            name='ContestLocation',
            fields=[
                ('id', models.AutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('location', models.CharField(max_length=60, verbose_name='location')),
                ('contest', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='locations',
                                              to='judge.contest', verbose_name='contest')),
                ('user', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='+',
                                           to='judge.profile', verbose_name='user')),
            ],
            options={
                'verbose_name': 'contest location',
                'verbose_name_plural': 'contest locations',
                'unique_together': {('contest', 'user')},
            },
        ),
    ]
