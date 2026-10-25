"""Arena: one-on-one programming battles and single-elimination tournaments.

A battle between two people is an `ArenaMatch`, played as a series of `ArenaGame`s (best of one, three, ...).
Each game, once started, is backed by a hidden `Contest` created for it alone, so judging, priorities and the
submission pipeline are exactly those of any contest. Nothing else of the site sees those contests: they are
invisible, unrated and keep their scoreboard and submissions hidden.

Who won is never stored by the judging pipeline. The arena works it out from the submissions every time a page asks
for the state of a game (see judge/arena/scoring.py) and only then records the outcome, under a row lock on the
match, so two pages asking at once cannot record it twice.
"""
from django.core.exceptions import ValidationError
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from judge.models.contest import Contest, ContestParticipation, ContestProblem
from judge.models.problem import Problem
from judge.models.profile import Profile

__all__ = ['ArenaProblemBank', 'ArenaBankProblem', 'ArenaTournament', 'ArenaParticipant', 'ArenaRound',
           'ArenaMatch', 'ArenaPanel', 'ArenaGame', 'ArenaGameProblem', 'ArenaScreen', 'ArenaAction']

SLOT_A, SLOT_B = 'a', 'b'
SLOTS = ((SLOT_A, 'A'), (SLOT_B, 'B'))

MAX_DISPLAY_NAME = 40
MAX_PROBLEMS_PER_GAME = 5


class ArenaProblemBank(models.Model):
    """A list of problems an organizer keeps at hand to fill games quickly. Purely a helper: every game can always
    be given any problem by hand."""
    name = models.CharField(verbose_name=_('name'), max_length=100)
    description = models.TextField(verbose_name=_('description'), blank=True)
    created_by = models.ForeignKey(Profile, verbose_name=_('created by'), null=True, blank=True,
                                   on_delete=models.SET_NULL, related_name='+')
    created_at = models.DateTimeField(verbose_name=_('created at'), default=timezone.now)

    def __str__(self):
        return self.name

    class Meta:
        ordering = ('name', 'id')
        verbose_name = _('arena problem bank')
        verbose_name_plural = _('arena problem banks')


class ArenaBankProblem(models.Model):
    bank = models.ForeignKey(ArenaProblemBank, verbose_name=_('problem bank'), related_name='entries',
                             on_delete=models.CASCADE)
    problem = models.ForeignKey(Problem, verbose_name=_('problem'), related_name='arena_bank_entries',
                                on_delete=models.PROTECT)
    level = models.PositiveSmallIntegerField(verbose_name=_('level'), null=True, blank=True,
                                             validators=[MinValueValidator(1), MaxValueValidator(5)])
    note = models.CharField(verbose_name=_('note'), max_length=200, blank=True)
    order = models.PositiveIntegerField(verbose_name=_('order'), default=0)

    def __str__(self):
        return '%s: %s' % (self.bank_id, self.problem_id)

    class Meta:
        ordering = ('order', 'id')
        unique_together = ('bank', 'problem')
        verbose_name = _('arena bank problem')
        verbose_name_plural = _('arena bank problems')


class ArenaTournament(models.Model):
    DRAFT, RUNNING, FINISHED, ARCHIVED = 'draft', 'running', 'finished', 'archived'
    STATUS = (
        (DRAFT, _('Draft')),
        (RUNNING, _('In progress')),
        (FINISHED, _('Finished')),
        (ARCHIVED, _('Archived')),
    )
    SEED_MANUAL, SEED_RANDOM, SEED_RATING = 'manual', 'random', 'rating'
    SEEDING = (
        (SEED_MANUAL, _('Manual order')),
        (SEED_RANDOM, _('Random')),
        (SEED_RATING, _('By rating')),
    )

    name = models.CharField(verbose_name=_('name'), max_length=100)
    slug = models.SlugField(verbose_name=_('slug'), max_length=64, unique=True)
    description = models.TextField(verbose_name=_('description'), blank=True)
    status = models.CharField(verbose_name=_('status'), max_length=10, choices=STATUS, default=DRAFT,
                              db_index=True)
    bank = models.ForeignKey(ArenaProblemBank, verbose_name=_('problem bank'), null=True, blank=True,
                             on_delete=models.SET_NULL, related_name='tournaments')
    third_place_match = models.BooleanField(verbose_name=_('third place match'), default=False)
    seeding = models.CharField(verbose_name=_('seeding'), max_length=10, choices=SEEDING, default=SEED_MANUAL)
    rules = models.JSONField(verbose_name=_('rules'), default=dict, blank=True)
    created_by = models.ForeignKey(Profile, verbose_name=_('created by'), null=True, blank=True,
                                   on_delete=models.SET_NULL, related_name='+')
    # Besides its creator, who else runs it with the permission to organize only their own battles
    # (judge/arena/access.py). They also run the battles of its bracket.
    organizers = models.ManyToManyField(Profile, verbose_name=_('co-organizers'), blank=True, related_name='+')
    created_at = models.DateTimeField(verbose_name=_('created at'), default=timezone.now)
    started_at = models.DateTimeField(verbose_name=_('started at'), null=True, blank=True)
    finished_at = models.DateTimeField(verbose_name=_('finished at'), null=True, blank=True)
    version = models.PositiveIntegerField(default=0)

    def __str__(self):
        return self.name

    class Meta:
        ordering = ('-created_at', '-id')
        verbose_name = _('arena tournament')
        verbose_name_plural = _('arena tournaments')


class ArenaParticipant(models.Model):
    tournament = models.ForeignKey(ArenaTournament, verbose_name=_('tournament'), related_name='participants',
                                   on_delete=models.CASCADE)
    profile = models.ForeignKey(Profile, verbose_name=_('user'), related_name='+', on_delete=models.PROTECT)
    seed = models.PositiveIntegerField(verbose_name=_('seed'))

    def __str__(self):
        return '%s #%s' % (self.profile_id, self.seed)

    class Meta:
        ordering = ('seed', 'id')
        unique_together = ('tournament', 'profile')
        verbose_name = _('arena participant')
        verbose_name_plural = _('arena participants')


class ArenaRound(models.Model):
    tournament = models.ForeignKey(ArenaTournament, verbose_name=_('tournament'), related_name='rounds',
                                   on_delete=models.CASCADE)
    number = models.PositiveSmallIntegerField(verbose_name=_('number'))
    name = models.CharField(verbose_name=_('name'), max_length=60, blank=True)
    best_of = models.PositiveSmallIntegerField(verbose_name=_('best of'), default=1)
    rules = models.JSONField(verbose_name=_('rules'), default=dict, blank=True)

    def __str__(self):
        return '%s / %s' % (self.tournament_id, self.number)

    class Meta:
        ordering = ('number',)
        unique_together = ('tournament', 'number')
        verbose_name = _('arena round')
        verbose_name_plural = _('arena rounds')


class ArenaMatch(models.Model):
    """Two players and a series of games. A quick battle is a match without a tournament."""
    PENDING, READY, CALLED, PLAYING, DECIDED, CANCELLED = 'pending', 'ready', 'called', 'playing', 'decided', \
        'cancelled'
    STATUS = (
        (PENDING, _('Waiting for players')),
        (READY, _('Ready')),
        (CALLED, _('Called')),
        (PLAYING, _('Playing')),
        (DECIDED, _('Decided')),
        (CANCELLED, _('Cancelled')),
    )
    # From the moment the players are called to the waiting room until the series is decided, the rest of the
    # site sends them back to the arena.
    LOCKING_STATUSES = (CALLED, PLAYING)

    tournament = models.ForeignKey(ArenaTournament, verbose_name=_('tournament'), null=True, blank=True,
                                   related_name='matches', on_delete=models.CASCADE)
    round = models.ForeignKey(ArenaRound, verbose_name=_('round'), null=True, blank=True, related_name='matches',
                              on_delete=models.CASCADE)
    position = models.PositiveIntegerField(verbose_name=_('position'), default=0)
    is_third_place = models.BooleanField(verbose_name=_('third place match'), default=False)
    title = models.CharField(verbose_name=_('title'), max_length=100, blank=True)
    player_a = models.ForeignKey(Profile, verbose_name=_('player A'), null=True, blank=True, related_name='+',
                                 on_delete=models.PROTECT)
    player_b = models.ForeignKey(Profile, verbose_name=_('player B'), null=True, blank=True, related_name='+',
                                 on_delete=models.PROTECT)
    # Only for quick battles on the club's own laptops: who is actually sitting at a shared, trusted account.
    display_name_a = models.CharField(verbose_name=_('name shown for player A'), max_length=MAX_DISPLAY_NAME,
                                      blank=True)
    display_name_b = models.CharField(verbose_name=_('name shown for player B'), max_length=MAX_DISPLAY_NAME,
                                      blank=True)
    seed_a = models.PositiveIntegerField(verbose_name=_('seed of player A'), null=True, blank=True)
    seed_b = models.PositiveIntegerField(verbose_name=_('seed of player B'), null=True, blank=True)
    next_match = models.ForeignKey('self', verbose_name=_('winner goes to'), null=True, blank=True,
                                   related_name='+', on_delete=models.SET_NULL)
    next_slot = models.CharField(verbose_name=_('as player'), max_length=1, choices=SLOTS, blank=True)
    loser_next_match = models.ForeignKey('self', verbose_name=_('loser goes to'), null=True, blank=True,
                                         related_name='+', on_delete=models.SET_NULL)
    loser_next_slot = models.CharField(verbose_name=_('loser as player'), max_length=1, choices=SLOTS, blank=True)
    best_of = models.PositiveSmallIntegerField(verbose_name=_('best of'), default=1)
    rules = models.JSONField(verbose_name=_('rules'), default=dict, blank=True)
    status = models.CharField(verbose_name=_('status'), max_length=10, choices=STATUS, default=PENDING,
                              db_index=True)
    called_at = models.DateTimeField(verbose_name=_('called at'), null=True, blank=True)
    wins_a = models.PositiveSmallIntegerField(verbose_name=_('games won by A'), default=0)
    wins_b = models.PositiveSmallIntegerField(verbose_name=_('games won by B'), default=0)
    winner_slot = models.CharField(verbose_name=_('winner'), max_length=1, choices=SLOTS, blank=True)
    decided_reason = models.CharField(verbose_name=_('decided by'), max_length=20, blank=True)
    decided_at = models.DateTimeField(verbose_name=_('decided at'), null=True, blank=True)
    created_by = models.ForeignKey(Profile, verbose_name=_('created by'), null=True, blank=True,
                                   on_delete=models.SET_NULL, related_name='+')
    # Only for quick battles: the battles of a tournament are run by the organizers of the tournament.
    organizers = models.ManyToManyField(Profile, verbose_name=_('co-organizers'), blank=True, related_name='+')
    created_at = models.DateTimeField(verbose_name=_('created at'), default=timezone.now)
    version = models.PositiveIntegerField(default=0)

    @property
    def wins_needed(self):
        return self.best_of // 2 + 1

    def player(self, slot):
        return self.player_a if slot == SLOT_A else self.player_b

    def player_id(self, slot):
        return self.player_a_id if slot == SLOT_A else self.player_b_id

    def slot_of(self, profile_id):
        if profile_id is None:
            return None
        if self.player_a_id == profile_id:
            return SLOT_A
        if self.player_b_id == profile_id:
            return SLOT_B
        return None

    def shown_name(self, slot):
        display = self.display_name_a if slot == SLOT_A else self.display_name_b
        if display:
            return display
        player = self.player(slot)
        return player.display_name if player is not None else ''

    def clean(self):
        super().clean()
        if self.tournament_id and (self.display_name_a or self.display_name_b):
            raise ValidationError(_('Display names are only for quick battles; tournament players use their own '
                                    'accounts.'))
        if self.player_a_id and self.player_a_id == self.player_b_id:
            raise ValidationError(_('A player cannot face themselves.'))
        if self.best_of < 1 or self.best_of > 9 or self.best_of % 2 == 0:
            raise ValidationError(_('Best of must be an odd number between 1 and 9.'))

    def __str__(self):
        return '%s vs %s' % (self.shown_name(SLOT_A) or '?', self.shown_name(SLOT_B) or '?')

    class Meta:
        ordering = ('-created_at', '-id')
        indexes = [models.Index(fields=['tournament', 'round', 'position'])]
        permissions = (
            ('manage_arena', _('Organize own arena battles')),
            ('manage_all_arena', _('Organize all arena battles')),
        )
        verbose_name = _('arena match')
        verbose_name_plural = _('arena matches')


class ArenaPanel(ArenaMatch):
    """No table of its own: the entry of the admin side menu that takes organizers to the battle panel."""

    class Meta:
        proxy = True
        verbose_name = _('battle panel')
        verbose_name_plural = _('battles')


class ArenaGame(models.Model):
    PREPARED, COUNTDOWN, LIVE, VERIFYING, FINISHED, TIE, CANCELLED = \
        'prepared', 'countdown', 'live', 'verifying', 'finished', 'tie', 'cancelled'
    STATUS = (
        (PREPARED, _('Prepared')),
        (COUNTDOWN, _('Countdown')),
        (LIVE, _('Live')),
        (VERIFYING, _('Verifying')),
        (FINISHED, _('Finished')),
        (TIE, _('Tie')),
        (CANCELLED, _('Cancelled')),
    )
    ACTIVE_STATUSES = (COUNTDOWN, LIVE, VERIFYING)
    OPEN_STATUSES = (COUNTDOWN, LIVE)
    DONE_STATUSES = (FINISHED, TIE, CANCELLED)

    match = models.ForeignKey(ArenaMatch, verbose_name=_('match'), related_name='games', on_delete=models.PROTECT)
    number = models.PositiveSmallIntegerField(verbose_name=_('number'))
    status = models.CharField(verbose_name=_('status'), max_length=10, choices=STATUS, default=PREPARED,
                              db_index=True)
    # The rules in force, frozen when the game starts so editing the match afterwards cannot change a game in play.
    rules = models.JSONField(verbose_name=_('rules'), default=dict, blank=True)
    contest = models.OneToOneField(Contest, verbose_name=_('backing contest'), null=True, blank=True,
                                   related_name='arena_game', on_delete=models.PROTECT)
    participation_a = models.ForeignKey(ContestParticipation, verbose_name=_('participation of A'), null=True,
                                        blank=True, related_name='+', on_delete=models.PROTECT)
    participation_b = models.ForeignKey(ContestParticipation, verbose_name=_('participation of B'), null=True,
                                        blank=True, related_name='+', on_delete=models.PROTECT)
    started_at = models.DateTimeField(verbose_name=_('play starts at'), null=True, blank=True)
    ends_at = models.DateTimeField(verbose_name=_('play ends at'), null=True, blank=True)
    closed_at = models.DateTimeField(verbose_name=_('submissions closed at'), null=True, blank=True)
    finished_at = models.DateTimeField(verbose_name=_('finished at'), null=True, blank=True)
    winner_slot = models.CharField(verbose_name=_('winner'), max_length=1, choices=SLOTS, blank=True)
    reason = models.CharField(verbose_name=_('reason'), max_length=20, blank=True)
    result = models.JSONField(verbose_name=_('result'), default=dict, blank=True)
    decided_by = models.ForeignKey(Profile, verbose_name=_('decided by'), null=True, blank=True,
                                   on_delete=models.SET_NULL, related_name='+')
    created_at = models.DateTimeField(verbose_name=_('created at'), default=timezone.now)

    def participation(self, slot):
        return self.participation_a if slot == SLOT_A else self.participation_b

    def participation_id(self, slot):
        return self.participation_a_id if slot == SLOT_A else self.participation_b_id

    def __str__(self):
        return '%s #%s' % (self.match_id, self.number)

    class Meta:
        ordering = ('match', 'number')
        unique_together = ('match', 'number')
        verbose_name = _('arena game')
        verbose_name_plural = _('arena games')


class ArenaGameProblem(models.Model):
    game = models.ForeignKey(ArenaGame, verbose_name=_('game'), related_name='game_problems',
                             on_delete=models.CASCADE)
    problem = models.ForeignKey(Problem, verbose_name=_('problem'), related_name='+', on_delete=models.PROTECT)
    order = models.PositiveSmallIntegerField(verbose_name=_('order'))
    points = models.PositiveIntegerField(verbose_name=_('points'), default=100,
                                         validators=[MinValueValidator(1), MaxValueValidator(10000)])
    contest_problem = models.OneToOneField(ContestProblem, verbose_name=_('contest problem'), null=True,
                                           blank=True, related_name='+', on_delete=models.SET_NULL)

    @property
    def label(self):
        return chr(ord('A') + self.order) if self.order < 26 else str(self.order + 1)

    def __str__(self):
        return '%s %s' % (self.game_id, self.label)

    class Meta:
        ordering = ('order', 'id')
        unique_together = ('game', 'problem')
        verbose_name = _('arena game problem')
        verbose_name_plural = _('arena game problems')


class ArenaScreen(models.Model):
    """A projector, or a group of browser tabs, opened with a secret link and no login.

    Which match it shows is chosen from the organizer panel, so nobody has to touch the laptop next to the
    projector. Regenerating the token revokes every open tab at once.
    """
    IDLE, BRACKET, MATCH, MESSAGE = 'idle', 'bracket', 'match', 'message'
    MODES = (
        (IDLE, _('Waiting')),
        (BRACKET, _('Bracket')),
        (MATCH, _('Match')),
        (MESSAGE, _('Message')),
    )

    name = models.CharField(verbose_name=_('name'), max_length=60)
    token = models.CharField(verbose_name=_('token'), max_length=64, unique=True)
    mode = models.CharField(verbose_name=_('shows'), max_length=10, choices=MODES, default=IDLE)
    tournament = models.ForeignKey(ArenaTournament, verbose_name=_('tournament'), null=True, blank=True,
                                   on_delete=models.SET_NULL, related_name='+')
    match = models.ForeignKey(ArenaMatch, verbose_name=_('match'), null=True, blank=True,
                              on_delete=models.SET_NULL, related_name='+')
    follow_live = models.BooleanField(verbose_name=_('follow the match being played'), default=True)
    message = models.CharField(verbose_name=_('message'), max_length=200, blank=True)
    created_by = models.ForeignKey(Profile, verbose_name=_('created by'), null=True, blank=True,
                                   on_delete=models.SET_NULL, related_name='+')
    updated_at = models.DateTimeField(verbose_name=_('updated at'), auto_now=True)
    version = models.PositiveIntegerField(default=0)

    def __str__(self):
        return self.name

    class Meta:
        ordering = ('name', 'id')
        verbose_name = _('arena screen')
        verbose_name_plural = _('arena screens')


class ArenaAction(models.Model):
    """Who did what, append only. Nothing reads it to decide anything."""
    tournament = models.ForeignKey(ArenaTournament, verbose_name=_('tournament'), null=True, blank=True,
                                   related_name='actions', on_delete=models.CASCADE)
    match = models.ForeignKey(ArenaMatch, verbose_name=_('match'), null=True, blank=True, related_name='actions',
                              on_delete=models.CASCADE)
    game = models.ForeignKey(ArenaGame, verbose_name=_('game'), null=True, blank=True, related_name='actions',
                             on_delete=models.CASCADE)
    actor = models.ForeignKey(Profile, verbose_name=_('done by'), null=True, blank=True, related_name='+',
                              on_delete=models.SET_NULL)
    action = models.CharField(verbose_name=_('action'), max_length=30)
    data = models.JSONField(verbose_name=_('data'), default=dict, blank=True)
    time = models.DateTimeField(verbose_name=_('time'), default=timezone.now)

    def __str__(self):
        return '%s %s' % (self.action, self.time)

    class Meta:
        ordering = ('time', 'id')
        verbose_name = _('arena action')
        verbose_name_plural = _('arena actions')
