"""The ranking of the duels from the command line (judge/arena/rating.py, 2026-10-26).

    manage.py rate_duels              # whether the stored ranking is what the battles give; only reads
    manage.py rate_duels --recompute  # work it out again and save what changed

The ranking is worked out again by itself after anything that can move it; this is for after a failure that was
logged, or after changing something by hand (an account made unlisted, a game deleted from the admin). The page to
decide does the same with its button.
"""
from django.core.management.base import BaseCommand, CommandError

from judge.arena import rating


class Command(BaseCommand):
    help = 'checks the ranking of the duels, or works it out again with --recompute'

    def add_arguments(self, parser):
        parser.add_argument('--recompute', action='store_true', help='work the ranking out again and save it')

    def handle(self, *args, **options):
        if options['recompute']:
            try:
                written = rating.recompute()
            except rating.RankingBusy:
                raise CommandError('The ranking is being worked out by another process; try again.')
            self.stdout.write('Ranking of the duels worked out again: %d rows written or deleted.' % written)
            return
        behind = rating.check()
        if behind:
            raise CommandError('The ranking of the duels is not up to date: %d rows differ. '
                               'Run rate_duels --recompute.' % behind)
        self.stdout.write('The ranking of the duels is up to date.')
