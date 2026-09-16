from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ('judge', '0149_add_organization_private_problems_permission'),
    ]

    operations = [
        migrations.CreateModel(
            name='Announcement',
            fields=[
                ('id', models.AutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('body', models.TextField(help_text='Shown as plain text: HTML and Markdown are not interpreted.',
                                          verbose_name='announcement')),
                ('created', models.DateTimeField(auto_now_add=True, db_index=True, verbose_name='creation time')),
                ('expires', models.DateTimeField(blank=True, db_index=True,
                                                 help_text='After this moment the announcement stops popping up. It '
                                                           'is still kept on the contest tab. Empty means it never '
                                                           'stops.',
                                                 null=True, verbose_name='stop showing at')),
                ('is_visible', models.BooleanField(default=True,
                                                   help_text='Uncheck to withdraw an announcement without deleting '
                                                             'it, so there is still a record that it was sent.',
                                                   verbose_name='is visible')),
                ('author', models.ForeignKey(blank=True, null=True,
                                             on_delete=django.db.models.deletion.SET_NULL,
                                             related_name='announcements', to='judge.profile',
                                             verbose_name='author')),
                ('contest', models.ForeignKey(blank=True,
                                              help_text='Leave empty to announce to the whole site. Otherwise the '
                                                        'announcement only reaches that contest, and is kept on its '
                                                        'announcements tab.',
                                              null=True, on_delete=django.db.models.deletion.CASCADE,
                                              related_name='announcements', to='judge.contest',
                                              verbose_name='contest')),
            ],
            options={
                'verbose_name': 'announcement',
                'verbose_name_plural': 'announcements',
                'ordering': ['-created'],
                'permissions': (('announce_site', 'Announce to the whole site'),),
            },
        ),
    ]
