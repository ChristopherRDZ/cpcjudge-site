from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ('judge', '0151_contestclarification'),
    ]

    operations = [
        migrations.AddField(
            model_name='announcement',
            name='clarification',
            field=models.ForeignKey(blank=True, null=True,
                                    on_delete=django.db.models.deletion.SET_NULL,
                                    related_name='announcements', to='judge.contestclarification',
                                    verbose_name='clarification'),
        ),
        migrations.AlterField(
            model_name='announcement',
            name='body',
            field=models.TextField(blank=True,
                                   help_text='Shown as plain text: HTML and Markdown are not interpreted. Leave '
                                             'empty only on an announcement that carries a clarification.',
                                   verbose_name='announcement'),
        ),
    ]
