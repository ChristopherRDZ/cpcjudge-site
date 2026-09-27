import judge.models.problem_data
import judge.utils.problem_data
from django.db import migrations, models


class Migration(migrations.Migration):
    """Interactive problems from the data page.

    Two new columns at the end of the problem data table: the interactor file, empty for every existing row, and
    whether contestants see its messages, with a database default of false. No existing row is rewritten and no
    existing column is altered; MariaDB adds both columns in place.
    """

    dependencies = [
        ('judge', '0155_teams'),
    ]

    operations = [
        migrations.AddField(
            model_name='problemdata',
            name='interactor',
            field=models.FileField(
                blank=True, null=True, storage=judge.utils.problem_data.ProblemDataStorage(),
                upload_to=judge.models.problem_data.problem_directory_file, verbose_name='interactor',
                help_text='Makes the problem interactive: the program that talks with each submission, in C++, C '
                          'or Python. Leave it empty for a normal problem.'),
        ),
        migrations.AddField(
            model_name='problemdata',
            name='interactor_feedback',
            field=models.BooleanField(
                db_default=False, default=False, verbose_name='show interactor messages',
                help_text='Show contestants what the interactor writes to standard error.'),
        ),
    ]
