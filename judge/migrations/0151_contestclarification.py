from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ('judge', '0150_announcement'),
    ]

    operations = [
        migrations.CreateModel(
            name='ContestClarification',
            fields=[
                ('id', models.AutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('question', models.TextField(verbose_name='question')),
                ('asked', models.DateTimeField(auto_now_add=True, db_index=True, verbose_name='asked at')),
                ('answer', models.TextField(blank=True, verbose_name='answer')),
                ('answered', models.DateTimeField(blank=True, db_index=True, null=True, verbose_name='answered at')),
                ('is_public', models.BooleanField(default=False,
                                                  help_text='A public answer is shown to every contestant. A private '
                                                            'one only goes back to whoever asked.',
                                                  verbose_name='answer is public')),
                ('answered_by', models.ForeignKey(blank=True, null=True,
                                                  on_delete=django.db.models.deletion.SET_NULL,
                                                  related_name='answered_clarifications', to='judge.profile',
                                                  verbose_name='answered by')),
                ('contest', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE,
                                              related_name='clarifications', to='judge.contest',
                                              verbose_name='contest')),
                ('problem', models.ForeignKey(blank=True,
                                              help_text='Leave empty for a question about the contest as a whole.',
                                              null=True, on_delete=django.db.models.deletion.CASCADE,
                                              related_name='clarifications', to='judge.contestproblem',
                                              verbose_name='problem')),
                ('user', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE,
                                           related_name='clarifications', to='judge.profile',
                                           verbose_name='asked by')),
            ],
            options={
                'verbose_name': 'contest clarification',
                'verbose_name_plural': 'contest clarifications',
                'ordering': ['-asked'],
            },
        ),
        migrations.AddIndex(
            model_name='contestclarification',
            index=models.Index(fields=['contest', '-asked'], name='clarification_contest_asked'),
        ),
    ]
