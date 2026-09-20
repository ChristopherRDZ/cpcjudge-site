from datetime import timedelta

from django.core.cache import cache
from django.core.exceptions import ValidationError
from django.core.validators import MaxValueValidator, MinValueValidator, RegexValidator
from django.db import models, transaction
from django.db.models import CASCADE, Q
from django.urls import reverse
from django.utils import timezone
from django.utils.functional import cached_property
from django.utils.translation import gettext_lazy as _
from jsonfield import JSONField
from lupa import LuaRuntime
from moss import MOSS_LANG_C, MOSS_LANG_CC, MOSS_LANG_JAVA, MOSS_LANG_PYTHON

from judge import contest_format
from judge.models.problem import Problem
from judge.models.profile import Class, Organization, Profile
from judge.models.submission import Submission
from judge.ratings import rate_contest

__all__ = ['Contest', 'ContestTag', 'ContestParticipation', 'ContestProblem', 'ContestSubmission', 'Rating']


class MinValueOrNoneValidator(MinValueValidator):
    def compare(self, a, b):
        return a is not None and b is not None and super().compare(a, b)


class ContestTag(models.Model):
    color_validator = RegexValidator('^#(?:[A-Fa-f0-9]{3}){1,2}$', _('Invalid colour.'))

    name = models.CharField(max_length=20, verbose_name=_('tag name'), unique=True,
                            validators=[RegexValidator(r'^[a-z-]+$', message=_('Lowercase letters and hyphens only.'))])
    color = models.CharField(max_length=7, verbose_name=_('tag colour'), validators=[color_validator])
    description = models.TextField(verbose_name=_('tag description'), blank=True)

    def __str__(self):
        return self.name

    def get_absolute_url(self):
        return reverse('contest_tag', args=[self.name])

    @property
    def text_color(self, cache={}):
        if self.color not in cache:
            if len(self.color) == 4:
                r, g, b = [ord(bytes.fromhex(i * 2)) for i in self.color[1:]]
            else:
                r, g, b = [i for i in bytes.fromhex(self.color[1:])]
            cache[self.color] = '#000' if 299 * r + 587 * g + 144 * b > 140000 else '#fff'
        return cache[self.color]

    class Meta:
        verbose_name = _('contest tag')
        verbose_name_plural = _('contest tags')


class Contest(models.Model):
    INDIVIDUAL, TEAM, MIXED = 'individual', 'team', 'mixed'
    participation_mode = models.CharField(
        _('Modalidad de participación'), max_length=10, default=INDIVIDUAL,
        choices=((INDIVIDUAL, _('Individual')), (TEAM, _('Equipos')), (MIXED, _('Mixto'))))
    team_min_size = models.PositiveSmallIntegerField(_('Mínimo de integrantes'), default=1,
                                                     validators=[MinValueValidator(1), MaxValueValidator(100)])
    team_max_size = models.PositiveSmallIntegerField(_('Máximo de integrantes'), default=3,
                                                     validators=[MinValueValidator(1), MaxValueValidator(100)])
    FROZEN_WINDOWS_CACHE_KEY = 'frozen_contest_windows'
    SCOREBOARD_VISIBLE = 'V'
    SCOREBOARD_AFTER_CONTEST = 'C'
    SCOREBOARD_AFTER_PARTICIPATION = 'P'
    SCOREBOARD_HIDDEN = 'H'
    SCOREBOARD_VISIBILITY = (
        (SCOREBOARD_VISIBLE, _('Visible')),
        (SCOREBOARD_AFTER_CONTEST, _('Hidden for duration of contest')),
        (SCOREBOARD_AFTER_PARTICIPATION, _('Hidden for duration of participation')),
        (SCOREBOARD_HIDDEN, _('Hidden permanently')),
    )
    key = models.CharField(max_length=20, verbose_name=_('contest id'), unique=True,
                           validators=[RegexValidator('^[a-z0-9]+$', _('Contest id must be ^[a-z0-9]+$'))])
    name = models.CharField(max_length=100, verbose_name=_('contest name'), db_index=True)
    authors = models.ManyToManyField(Profile, verbose_name=_('authors'),
                                     help_text=_('These users will be able to edit the contest.'),
                                     related_name='authored_contests')
    curators = models.ManyToManyField(Profile, verbose_name=_('curators'),
                                      help_text=_('These users will be able to edit the contest, '
                                                  'but will not be listed as authors.'),
                                      related_name='curated_contests', blank=True)
    testers = models.ManyToManyField(Profile, verbose_name=_('testers'),
                                     help_text=_('These users will be able to view the contest, but not edit it.'),
                                     blank=True, related_name='tested_contests')
    tester_see_scoreboard = models.BooleanField(verbose_name=_('testers see scoreboard'), default=False,
                                                help_text=_('If testers can see the scoreboard.'))
    tester_see_submissions = models.BooleanField(verbose_name=_('testers see submissions'), default=False,
                                                 help_text=_('If testers can see in-contest submissions.'))
    spectators = models.ManyToManyField(Profile, verbose_name=_('spectators'),
                                        help_text=_('These users will be able to spectate the contest, '
                                                    'but not see the problems ahead of time.'),
                                        blank=True, related_name='spectated_contests')
    description = models.TextField(verbose_name=_('description'), blank=True)
    problems = models.ManyToManyField(Problem, verbose_name=_('problems'), through='ContestProblem')
    start_time = models.DateTimeField(verbose_name=_('start time'), db_index=True)
    end_time = models.DateTimeField(verbose_name=_('end time'), db_index=True)
    time_limit = models.DurationField(verbose_name=_('time limit'), blank=True, null=True)
    is_visible = models.BooleanField(verbose_name=_('publicly visible'), default=False,
                                     help_text=_('Should be set even for organization-private contests, where it '
                                                 'determines whether the contest is visible to members of the '
                                                 'specified organizations.'))
    is_rated = models.BooleanField(verbose_name=_('contest rated'), help_text=_('Whether this contest can be rated.'),
                                   default=False)
    view_contest_scoreboard = models.ManyToManyField(Profile, verbose_name=_('view contest scoreboard'), blank=True,
                                                     related_name='view_contest_scoreboard',
                                                     help_text=_('These users will be able to view the scoreboard.'))
    view_contest_submissions = models.ManyToManyField(Profile, verbose_name=_('can see contest submissions'),
                                                      blank=True, related_name='view_contest_submissions',
                                                      help_text=_('These users will be able '
                                                                  'to see in-contest submissions.'))
    scoreboard_visibility = models.CharField(verbose_name=_('scoreboard visibility'), default=SCOREBOARD_VISIBLE,
                                             help_text=_('Scoreboard visibility through the duration of the contest.'),
                                             max_length=1, choices=SCOREBOARD_VISIBILITY)
    use_clarifications = models.BooleanField(verbose_name=_('no comments'),
                                             help_text=_('Use clarification system instead of comments.'),
                                             default=True)
    rating_floor = models.IntegerField(verbose_name=_('rating floor'),
                                       help_text=_('Do not rate users who have a lower rating.'), null=True, blank=True)
    rating_ceiling = models.IntegerField(verbose_name=_('rating ceiling'),
                                         help_text=_('Do not rate users who have a higher rating.'),
                                         null=True, blank=True)
    rate_all = models.BooleanField(verbose_name=_('rate all'),
                                   help_text=_('Rate users even if they make no submissions.'),
                                   default=False)
    rate_exclude = models.ManyToManyField(Profile, verbose_name=_('exclude from ratings'), blank=True,
                                          related_name='rate_exclude+')
    is_private = models.BooleanField(verbose_name=_('private to specific users'), default=False)
    private_teams = models.ManyToManyField('Team', blank=True, related_name='private_contests',
                                          verbose_name=_('Equipos autorizados'),
                                          help_text=_('Equipos que pueden acceder e inscribirse en este concurso privado.'))
    private_contestants = models.ManyToManyField(Profile, blank=True, verbose_name=_('private contestants'),
                                                 help_text=_('If non-empty, only these users may see the contest.'),
                                                 related_name='private_contestants+')
    hide_problem_tags = models.BooleanField(verbose_name=_('hide problem tags'),
                                            help_text=_('Whether problem tags should be hidden by default.'),
                                            default=False)
    hide_problem_authors = models.BooleanField(verbose_name=_('hide problem authors'),
                                               help_text=_('Whether problem authors should be hidden by default.'),
                                               default=False)
    run_pretests_only = models.BooleanField(verbose_name=_('run pretests only'),
                                            help_text=_('Whether judges should grade pretests only, versus all '
                                                        'testcases. Commonly set during a contest, then unset '
                                                        'prior to rejudging user submissions when the contest ends.'),
                                            default=False)
    show_short_display = models.BooleanField(verbose_name=_('show short form settings display'),
                                             help_text=_('Whether to show a section containing contest settings '
                                                         'on the contest page or not.'),
                                             default=False)
    is_organization_private = models.BooleanField(verbose_name=_('private to organizations'), default=False)
    organizations = models.ManyToManyField(Organization, blank=True, verbose_name=_('organizations'),
                                           help_text=_('If non-empty, only these organizations may see the contest.'))
    limit_join_organizations = models.BooleanField(verbose_name=_('limit organizations that can join'), default=False)
    join_organizations = models.ManyToManyField(Organization, blank=True, verbose_name=_('join organizations'),
                                                help_text=_('If non-empty, only these organizations may join '
                                                            'the contest.'), related_name='join_only_contests')
    classes = models.ManyToManyField(Class, blank=True, verbose_name=_('classes'),
                                     help_text=_('If organization private, only these classes may see the contest.'))
    og_image = models.CharField(verbose_name=_('OpenGraph image'), default='', max_length=150, blank=True)
    logo_override_image = models.CharField(verbose_name=_('logo override image'), default='', max_length=150,
                                           blank=True,
                                           help_text=_('This image will replace the default site logo for users '
                                                       'inside the contest.'))
    tags = models.ManyToManyField(ContestTag, verbose_name=_('contest tags'), blank=True, related_name='contests')
    user_count = models.IntegerField(verbose_name=_('the amount of live participants'), default=0)
    summary = models.TextField(blank=True, verbose_name=_('contest summary'),
                               help_text=_('Plain-text, shown in meta description tag, e.g. for social media.'))
    access_code = models.CharField(verbose_name=_('access code'), blank=True, default='', max_length=255,
                                   help_text=_('An optional code to prompt contestants before they are allowed '
                                               'to join the contest. Leave it blank to disable.'))
    banned_users = models.ManyToManyField(Profile, verbose_name=_('personae non gratae'), blank=True,
                                          help_text=_('Bans the selected users from joining this contest.'))
    format_name = models.CharField(verbose_name=_('contest format'), default='default', max_length=32,
                                   choices=contest_format.choices(), help_text=_('The contest format module to use.'))
    format_config = JSONField(verbose_name=_('contest format configuration'), null=True, blank=True,
                              help_text=_('A JSON object to serve as the configuration for the chosen contest format '
                                          'module. Leave empty to use None. Exact format depends on the contest format '
                                          'selected.'))
    problem_label_script = models.TextField(verbose_name=_('contest problem label script'), blank=True,
                                            help_text=_('A custom Lua function to generate problem labels. Requires a '
                                                        'single function with an integer parameter, the zero-indexed '
                                                        'contest problem index, and returns a string, the label.'))
    locked_after = models.DateTimeField(verbose_name=_('contest lock'), null=True, blank=True,
                                        help_text=_('Prevent submissions from this contest '
                                                    'from being rejudged after this date.'))
    freeze_minutes = models.PositiveIntegerField(
        verbose_name=_('scoreboard freeze'), null=True, blank=True,
        help_text=_('Freeze the public scoreboard over the last this many minutes of each participation. Leave '
                    'empty not to freeze it. A virtual participation is frozen over its own last minutes, not '
                    'over the clock of the live contest.'))
    scoreboard_revealed = models.BooleanField(
        verbose_name=_('scoreboard revealed'), default=False,
        help_text=_('A frozen scoreboard stays frozen after the contest ends, so the result can be revealed at a '
                    'ceremony. Check this to lift the freeze and show everyone the real scoreboard and the '
                    'submissions it was hiding.'))
    balloon_staff = models.ManyToManyField(Profile, verbose_name=_('balloon staff'), blank=True,
                                           related_name='balloon_contests',
                                           help_text=_('These users will be able to see which balloons are due and '
                                                       'mark them as delivered, without being able to edit the '
                                                       'contest or see its submissions.'))
    points_precision = models.IntegerField(verbose_name=_('precision points'), default=3,
                                           validators=[MinValueValidator(0), MaxValueValidator(10)],
                                           help_text=_('Number of digits to round points to.'))

    @cached_property
    def format_class(self):
        return contest_format.formats[self.format_name]

    @cached_property
    def format(self):
        return self.format_class(self, self.format_config)

    @cached_property
    def get_label_for_problem(self):
        if not self.problem_label_script:
            return self.format.get_label_for_problem

        def DENY_ALL(obj, attr_name, is_setting):
            raise AttributeError()
        lua = LuaRuntime(attribute_filter=DENY_ALL, register_eval=False, register_builtins=False)
        return lua.eval(self.problem_label_script)

    def clean(self):
        if self.team_min_size and self.team_max_size and self.team_min_size > self.team_max_size:
            raise ValidationError({'team_max_size': _('El máximo debe ser mayor o igual al mínimo.')})
        if self.participation_mode == self.TEAM and self.is_rated:
            raise ValidationError({'is_rated': _('Los concursos exclusivos de equipos no pueden ser calificados.')})
        if self.pk:
            previous = type(self).objects.filter(pk=self.pk).values('participation_mode').first()
            if (previous and previous['participation_mode'] != self.participation_mode and
                    self.users.filter(virtual__gte=0).exists()):
                raise ValidationError({'participation_mode': _('No se puede cambiar la modalidad después de la primera participación.')})
        # Django will complain if you didn't fill in start_time or end_time, so we don't have to.
        if self.start_time and self.end_time and self.start_time >= self.end_time:
            raise ValidationError('What is this? A contest that ended before it starts?')
        self.format_class.validate(self.format_config)

        try:
            # a contest should have at least one problem, with contest problem index 0
            # so test it to see if the script returns a valid label.
            label = self.get_label_for_problem(0)
        except Exception as e:
            raise ValidationError('Contest problem label script: %s' % e)
        else:
            if not isinstance(label, str):
                raise ValidationError('Contest problem label script: script should return a string.')

    def is_in_contest(self, user):
        if user.is_authenticated:
            profile = user.profile
            return profile and profile.current_contest is not None and profile.current_contest.contest == self
        return False

    def can_see_own_scoreboard(self, user):
        if self.can_see_full_scoreboard(user):
            return True
        if not self.started:
            return False
        if not self.show_scoreboard and not self.is_in_contest(user) and not self.has_completed_contest(user):
            return False
        return True

    def can_see_full_scoreboard(self, user):
        if self.show_scoreboard:
            return True
        if not user.is_authenticated:
            return False
        if user.has_perm('judge.see_private_contest') or user.has_perm('judge.edit_all_contest'):
            return True
        if user.profile.id in self.editor_ids:
            return True
        if self.tester_see_scoreboard and user.profile.id in self.tester_ids:
            return True
        if self.started and user.profile.id in self.spectator_ids:
            return True
        if self.view_contest_scoreboard.filter(id=user.profile.id).exists():
            return True
        if self.scoreboard_visibility == self.SCOREBOARD_AFTER_PARTICIPATION and self.has_completed_contest(user):
            return True
        return False

    def has_completed_contest(self, user):
        if user.is_authenticated:
            participation = self.users.for_profile(user.profile).filter(virtual=ContestParticipation.LIVE).first()
            if participation and participation.ended:
                return True
        return False

    @cached_property
    def show_scoreboard(self):
        if not self.started:
            return False
        if (self.scoreboard_visibility in (self.SCOREBOARD_AFTER_CONTEST, self.SCOREBOARD_AFTER_PARTICIPATION) and
                not self.ended):
            return False
        return self.scoreboard_visibility != self.SCOREBOARD_HIDDEN

    @cached_property
    def freeze_delta(self):
        return timedelta(minutes=self.freeze_minutes) if self.freeze_minutes else None

    @cached_property
    def freeze_active(self):
        """Whether this contest freezes anything at all right now.

        A revealed scoreboard never freezes again, and an unrevealed one stays frozen after the contest ends:
        that is what leaves something to reveal.
        """
        return self.freeze_delta is not None and not self.scoreboard_revealed

    @cached_property
    def submission_freeze_cutoff(self):
        """Earliest moment at which any participation of this contest can have entered its freeze window.

        Used to hide submissions, where a per-participation cutoff is not available in one query. For a contest
        with a per-user time limit this hides a little more than strictly necessary for whoever started late.
        It never hides less.
        """
        if self.freeze_delta is None:
            return None
        end = min(self.start_time + self.time_limit, self.end_time) if self.time_limit else self.end_time
        return end - self.freeze_delta

    @cached_property
    def freeze_started(self):
        cutoff = self.submission_freeze_cutoff
        return cutoff is not None and self._now >= cutoff

    def is_frozen_for(self, user):
        """Whether this user has to be shown the frozen scoreboard and the submissions that go with it.

        Only those who can edit the contest see through the freeze. A staff account that can read every
        submission but cannot edit this contest is deliberately included in the freeze.
        """
        return self.freeze_active and self.freeze_started and not self.is_editable_by(user)

    @classmethod
    def frozen_submission_filter(cls, user):
        """Q matching the submissions the freeze hides from this user, or None if it hides nothing.

        This runs on every submission list, so the set of frozen contests — normally empty, at most a handful —
        is cached for half a minute. Revealing a scoreboard drops that cache, so the reveal is immediate.
        """
        windows = cache.get(cls.FROZEN_WINDOWS_CACHE_KEY)
        if windows is None:
            windows = []
            configurados = cls.objects.filter(freeze_minutes__isnull=False, scoreboard_revealed=False)
            for contest in configurados.only('id', 'start_time', 'end_time', 'time_limit',
                                             'freeze_minutes'):
                if contest.freeze_started:
                    windows.append((contest.id, contest.submission_freeze_cutoff))
            cache.set(cls.FROZEN_WINDOWS_CACHE_KEY, windows, 30)
        if not windows:
            return None

        # Whoever can edit a contest sees through its freeze, so those contests drop out of the filter. One
        # query for all of them, with the same authors-or-curators rule `editor_ids` uses.
        seen_through = set()
        if user.is_authenticated:
            if user.has_perm('judge.edit_all_contest'):
                return None
            if user.has_perm('judge.edit_own_contest'):
                profile_id = user.profile.id
                seen_through = set(
                    cls.objects.filter(id__in=[contest_id for contest_id, _cutoff in windows])
                       .filter(Q(authors=profile_id) | Q(curators=profile_id))
                       .values_list('id', flat=True),
                )

        query = Q()
        hides_something = False
        for contest_id, cutoff in windows:
            if contest_id in seen_through:
                continue
            query |= Q(contest_object_id=contest_id, date__gte=cutoff)
            hides_something = True
        return query if hides_something else None

    @property
    def contest_window_length(self):
        return self.end_time - self.start_time

    @cached_property
    def _now(self):
        # This ensures that all methods talk about the same now.
        return timezone.now()

    @cached_property
    def started(self):
        return self.start_time <= self._now

    @property
    def time_before_start(self):
        if self.start_time >= self._now:
            return self.start_time - self._now
        else:
            return None

    @property
    def time_before_end(self):
        if self.end_time >= self._now:
            return self.end_time - self._now
        else:
            return None

    @cached_property
    def ended(self):
        return self.end_time < self._now

    @cached_property
    def author_ids(self):
        return Contest.authors.through.objects.filter(contest=self).values_list('profile_id', flat=True)

    @cached_property
    def editor_ids(self):
        return self.author_ids.union(
            Contest.curators.through.objects.filter(contest=self).values_list('profile_id', flat=True))

    @cached_property
    def tester_ids(self):
        return Contest.testers.through.objects.filter(contest=self).values_list('profile_id', flat=True)

    @cached_property
    def spectator_ids(self):
        return Contest.spectators.through.objects.filter(contest=self).values_list('profile_id', flat=True)

    def __str__(self):
        return self.name

    def get_absolute_url(self):
        return reverse('contest_view', args=(self.key,))

    def update_user_count(self):
        self.user_count = self.users.filter(virtual=0).count()
        self.save()

    update_user_count.alters_data = True

    class Inaccessible(Exception):
        pass

    class PrivateContest(Exception):
        pass

    def access_check(self, user):
        # Do unauthenticated check here so we can skip authentication checks later on.
        if not user.is_authenticated:
            # Unauthenticated users can only see visible, non-private contests
            if not self.is_visible:
                raise self.Inaccessible()
            if self.is_private or self.is_organization_private:
                raise self.PrivateContest()
            return

        # If the user can view or edit all contests
        if user.has_perm('judge.see_private_contest') or user.has_perm('judge.edit_all_contest'):
            return

        # User is organizer or curator for contest
        if user.profile.id in self.editor_ids:
            return

        # User is tester for contest
        if user.profile.id in self.tester_ids:
            return

        # User is spectator for contest
        if user.profile.id in self.spectator_ids:
            return

        # Contest is not publicly visible
        if not self.is_visible:
            raise self.Inaccessible()

        # Contest is not private
        if not self.is_private and not self.is_organization_private:
            return

        if self.view_contest_scoreboard.filter(id=user.profile.id).exists():
            return

        in_org = (self.organizations.filter(id__in=user.profile.organizations.all()).exists() or
                  self.classes.filter(id__in=user.profile.classes.all()).exists())
        in_users = (self.private_contestants.filter(id=user.profile.id).exists() or
                    self.private_teams.filter(is_active=True, members=user.profile).exists() or
                    self.users.filter(team__isnull=False, team_roster__profile=user.profile).exists())

        if not self.is_private and self.is_organization_private:
            if in_org:
                return
            raise self.PrivateContest()

        if self.is_private and not self.is_organization_private:
            if in_users:
                return
            raise self.PrivateContest()

        if self.is_private and self.is_organization_private:
            if in_org and in_users:
                return
            raise self.PrivateContest()

    # Assumes the user can access, to avoid the cost again
    def is_live_joinable_by(self, user):
        if not self.started:
            return False

        if not user.is_authenticated:
            return False

        # Can be populated using annotate for performance in list views.
        editor_or_tester = getattr(self, 'editor_or_tester', None)
        if editor_or_tester is None:
            editor_or_tester = user.profile.id in self.editor_ids or user.profile.id in self.tester_ids

        if editor_or_tester:
            return False

        completed_contest = getattr(self, 'completed_contest', None)
        if completed_contest is None:
            completed_contest = self.has_completed_contest(user)

        if completed_contest:
            return False

        if self.limit_join_organizations:
            return self.join_organizations.filter(id__in=user.profile.organizations.all()).exists()
        return True

    # Also skips access check
    def is_spectatable_by(self, user):
        if not user.is_authenticated:
            return False

        if user.profile.id in self.editor_ids or user.profile.id in self.tester_ids:
            return True

        if self.limit_join_organizations:
            return self.join_organizations.filter(id__in=user.profile.organizations.all()).exists()
        return True

    def is_accessible_by(self, user):
        try:
            self.access_check(user)
        except (self.Inaccessible, self.PrivateContest):
            return False
        else:
            return True

    def is_editable_by(self, user):
        # If the user can edit all contests
        if user.has_perm('judge.edit_all_contest'):
            return True

        # If the user is a contest organizer or curator
        if user.has_perm('judge.edit_own_contest') and user.profile.id in self.editor_ids:
            return True

        return False

    def is_balloon_staff(self, user):
        return user.is_authenticated and self.balloon_staff.filter(id=user.profile.id).exists()

    def can_manage_balloons(self, user):
        """Whether this user may open the balloon sheet and mark balloons as delivered.

        Balloon staff get exactly that and nothing else: the sheet shows who solved which problem outside the
        freeze window, and never what the freeze is hiding.
        """
        return self.is_editable_by(user) or self.is_balloon_staff(user)

    @classmethod
    def get_visible_contests(cls, user):
        if not user.is_authenticated:
            return cls.objects.filter(is_visible=True, is_organization_private=False, is_private=False) \
                              .defer('description').distinct()

        queryset = cls.objects.defer('description')
        if not (user.has_perm('judge.see_private_contest') or user.has_perm('judge.edit_all_contest')):
            org_check = (Q(organizations__in=user.profile.organizations.all()) |
                         Q(classes__in=user.profile.classes.all()))
            q = Q(is_visible=True)
            q &= (
                Q(view_contest_scoreboard=user.profile) |
                Q(is_organization_private=False, is_private=False) |
                Q(is_organization_private=False, is_private=True, private_contestants=user.profile) |
                (Q(is_organization_private=True, is_private=False) & org_check) |
                (Q(is_organization_private=True, is_private=True, private_contestants=user.profile) & org_check)
            )

            q |= Q(authors=user.profile)
            q |= Q(curators=user.profile)
            q |= Q(testers=user.profile)
            q |= Q(spectators=user.profile)
            q |= (Q(is_visible=True, is_private=True, private_teams__is_active=True,
                    private_teams__members=user.profile) & (Q(is_organization_private=False) | org_check))
            q |= (Q(is_visible=True, is_private=True, users__team_roster__profile=user.profile) &
                  (Q(is_organization_private=False) | org_check))
            queryset = queryset.filter(q)
        return queryset.distinct()

    def rate(self):
        with transaction.atomic():
            Rating.objects.filter(contest__end_time__range=(self.end_time, self._now)).delete()
            for contest in Contest.objects.filter(
                is_rated=True, end_time__range=(self.end_time, self._now),
            ).order_by('end_time'):
                rate_contest(contest)

    class Meta:
        permissions = (
            ('see_private_contest', _('See private contests')),
            ('edit_own_contest', _('Edit own contests')),
            ('edit_all_contest', _('Edit all contests')),
            ('clone_contest', _('Clone contest')),
            ('moss_contest', _('MOSS contest')),
            ('contest_rating', _('Rate contests')),
            ('contest_access_code', _('Contest access codes')),
            ('create_private_contest', _('Create private contests')),
            ('change_contest_visibility', _('Change contest visibility')),
            ('contest_problem_label', _('Edit contest problem label script')),
            ('lock_contest', _('Change lock status of contest')),
        )
        verbose_name = _('contest')
        verbose_name_plural = _('contests')


class ContestParticipationQuerySet(models.QuerySet):
    def for_profile(self, profile):
        if profile is None:
            return self.none()
        return self.filter(Q(team__isnull=True, user=profile) |
                           Q(pk__in=profile.team_contest_entries.values('participation_id')))


class ContestParticipation(models.Model):
    LIVE = 0
    SPECTATE = -1

    contest = models.ForeignKey(Contest, verbose_name=_('associated contest'), related_name='users', on_delete=CASCADE)
    user = models.ForeignKey(Profile, verbose_name=_('user'), related_name='contest_history', on_delete=CASCADE)
    team = models.ForeignKey('Team', null=True, blank=True, on_delete=models.PROTECT, related_name='participations')
    team_name = models.CharField(max_length=60, blank=True, default='')
    objects = ContestParticipationQuerySet.as_manager()
    real_start = models.DateTimeField(verbose_name=_('start time'), default=timezone.now, db_column='start')
    score = models.FloatField(verbose_name=_('score'), default=0, db_index=True)
    cumtime = models.PositiveIntegerField(verbose_name=_('cumulative time'), default=0)
    is_disqualified = models.BooleanField(verbose_name=_('is disqualified'), default=False,
                                          help_text=_('Whether this participation is disqualified.'))
    tiebreaker = models.FloatField(verbose_name=_('tie-breaking field'), default=0.0)
    virtual = models.IntegerField(verbose_name=_('virtual participation id'), default=LIVE,
                                  help_text=_('0 means non-virtual, otherwise the n-th virtual participation.'))
    format_data = JSONField(verbose_name=_('contest format specific data'), null=True, blank=True)
    frozen_score = models.FloatField(verbose_name=_('score at freeze time'), null=True, blank=True)
    frozen_cumtime = models.PositiveIntegerField(verbose_name=_('cumulative time at freeze time'),
                                                 null=True, blank=True)
    frozen_tiebreaker = models.FloatField(verbose_name=_('tie-breaking field at freeze time'), null=True, blank=True)
    frozen_format_data = JSONField(verbose_name=_('contest format specific data at freeze time'),
                                   null=True, blank=True)
    frozen_at = models.DateTimeField(verbose_name=_('scoreboard frozen at'), null=True, blank=True,
                                     help_text=_('When the frozen copy above was taken. Empty means no copy was '
                                                 'needed, because nothing has changed since the freeze.'))

    @cached_property
    def freeze_starts_at(self):
        """When this participation enters its own freeze window.

        Measured from this participation's end, so a virtual participation freezes over its own last minutes
        instead of at a clock time that means nothing to it.
        """
        delta = self.contest.freeze_delta
        return None if delta is None else self.end_time - delta

    @property
    def is_frozen(self):
        return (self.contest.freeze_active and self.freeze_starts_at is not None and
                self._now >= self.freeze_starts_at)

    def freeze_scoreboard(self):
        """Keep the scoreboard as it stood when this participation entered its freeze window.

        Called right before recomputing, so what gets stored is the state before the first correction that
        lands after the freeze: exactly what the public has to keep seeing. A participation that is never
        recomputed again needs no copy, because its live fields still hold the frozen state — which is why
        every reader falls back to the live fields when `frozen_at` is empty.
        """
        if self.frozen_at is not None or not self.is_frozen:
            return
        self.frozen_score = self.score
        self.frozen_cumtime = self.cumtime
        self.frozen_tiebreaker = self.tiebreaker
        self.frozen_format_data = self.format_data
        self.frozen_at = self._now
        self.save(update_fields=['frozen_score', 'frozen_cumtime', 'frozen_tiebreaker',
                                 'frozen_format_data', 'frozen_at'])
    freeze_scoreboard.alters_data = True

    def recompute_results(self):
        with transaction.atomic():
            # All team members can finish judging at once. Freeze and scoring must see one serialized state.
            locked = type(self).objects.select_for_update().get(pk=self.pk)
            for field in ('frozen_at', 'frozen_score', 'frozen_cumtime', 'frozen_tiebreaker', 'frozen_format_data',
                          'score', 'cumtime', 'tiebreaker', 'format_data', 'is_disqualified'):
                setattr(self, field, getattr(locked, field))
            self.freeze_scoreboard()
            self.contest.format.update_participation(self)
            if self.is_disqualified:
                self.score = -9999
                self.cumtime = 0
                self.tiebreaker = 0
                self.save(update_fields=['score', 'cumtime', 'tiebreaker'])
    recompute_results.alters_data = True

    @transaction.atomic
    def set_disqualified(self, disqualified):
        type(self).objects.select_for_update().get(pk=self.pk)
        self.is_disqualified = disqualified
        self.save(update_fields=['is_disqualified'])
        self.recompute_results()
        if self.contest.is_rated and self.contest.ratings.exists():
            self.contest.rate()
        profiles = list(self.team_roster.values_list('profile_id', flat=True)) if self.team_id else [self.user_id]
        if self.is_disqualified:
            Profile.objects.filter(current_contest=self).update(current_contest=None)
            self.contest.banned_users.add(*profiles)
        else:
            self.contest.banned_users.remove(*profiles)
    set_disqualified.alters_data = True

    @property
    def live(self):
        return self.virtual == self.LIVE

    def clean(self):
        super().clean()
        if self.contest_id and self.user_id and self.virtual == self.LIVE and not self.team_id:
            if self.contest.participation_mode == Contest.TEAM:
                raise ValidationError(_('Este concurso sólo admite participaciones oficiales por equipo.'))
            if type(self).objects.for_profile(self.user).filter(contest_id=self.contest_id, virtual=0).exclude(pk=self.pk).exists():
                raise ValidationError(_('Este usuario ya tiene una inscripción oficial en el concurso.'))

    def contains_profile(self, profile_id):
        if not self.team_id:
            return self.user_id == profile_id
        return any(member.profile_id == profile_id for member in self.team_roster.all())

    @property
    def display_name(self):
        return self.team_name if self.team_id else self.user.display_name

    def submissions_url(self, problem=None):
        if self.team_id:
            args = [self.contest.key, self.pk]
            if problem:
                args.append(problem.code)
            return reverse('contest_team_problem_submissions' if problem else 'contest_team_submissions', args=args)
        args = [self.contest.key, self.user.user.username]
        if problem:
            args.append(problem.code)
        return reverse('contest_user_submissions' if problem else 'contest_all_user_submissions', args=args)

    @property
    def spectate(self):
        return self.virtual == self.SPECTATE

    @cached_property
    def start(self):
        contest = self.contest
        return contest.start_time if contest.time_limit is None and (self.live or self.spectate) else self.real_start

    @cached_property
    def end_time(self):
        contest = self.contest
        if self.spectate:
            return contest.end_time
        if self.virtual:
            if contest.time_limit:
                return self.real_start + contest.time_limit
            else:
                return self.real_start + (contest.end_time - contest.start_time)
        return contest.end_time if contest.time_limit is None else \
            min(self.real_start + contest.time_limit, contest.end_time)

    @cached_property
    def _now(self):
        # This ensures that all methods talk about the same now.
        return timezone.now()

    @property
    def ended(self):
        return self.end_time is not None and self.end_time < self._now

    @property
    def time_remaining(self):
        end = self.end_time
        if end is not None and end >= self._now:
            return end - self._now

    def __str__(self):
        if self.spectate:
            return _('%(user)s spectating in %(contest)s') % {'user': self.display_name, 'contest': self.contest.name}
        if self.virtual:
            return _('%(user)s in %(contest)s, v%(id)d') % {
                'user': self.display_name, 'contest': self.contest.name, 'id': self.virtual,
            }
        return _('%(user)s in %(contest)s') % {'user': self.display_name, 'contest': self.contest.name}

    class Meta:
        verbose_name = _('contest participation')
        verbose_name_plural = _('contest participations')

        unique_together = (('contest', 'user', 'virtual'), ('contest', 'team', 'virtual'))


class ContestProblem(models.Model):
    problem = models.ForeignKey(Problem, verbose_name=_('problem'), related_name='contests', on_delete=CASCADE)
    contest = models.ForeignKey(Contest, verbose_name=_('contest'), related_name='contest_problems', on_delete=CASCADE)
    points = models.IntegerField(verbose_name=_('points'))
    partial = models.BooleanField(default=True, verbose_name=_('partial'))
    is_pretested = models.BooleanField(default=False, verbose_name=_('is pretested'))
    order = models.PositiveIntegerField(db_index=True, verbose_name=_('order'))
    output_prefix_override = models.IntegerField(verbose_name=_('output prefix length override'),
                                                 default=0, null=True, blank=True)
    max_submissions = models.IntegerField(verbose_name=_('max submissions'),
                                          help_text=_('Maximum number of submissions for this problem, '
                                                      'or leave blank for no limit.'),
                                          default=None, null=True, blank=True,
                                          validators=[MinValueOrNoneValidator(1, _('Why include a problem you '
                                                                                   "can't submit to?"))])
    balloon_color = models.CharField(max_length=7, verbose_name=_('balloon colour'), blank=True, default='',
                                     validators=[ContestTag.color_validator],
                                     help_text=_('As #rrggbb. Leave empty if this problem has no balloon colour.'))
    balloon_color_name = models.CharField(max_length=30, verbose_name=_('balloon colour name'), blank=True,
                                          default='', help_text=_('What the balloon staff call it, e.g. "red".'))

    @property
    def balloon_text_color(self):
        if not self.balloon_color:
            return ''
        hex_digits = self.balloon_color[1:]
        if len(hex_digits) == 3:
            hex_digits = ''.join(digit * 2 for digit in hex_digits)
        r, g, b = bytes.fromhex(hex_digits)
        return '#000' if 299 * r + 587 * g + 144 * b > 140000 else '#fff'

    class Meta:
        unique_together = ('problem', 'contest')
        verbose_name = _('contest problem')
        verbose_name_plural = _('contest problems')
        ordering = ('order',)


class ContestSubmission(models.Model):
    submission = models.OneToOneField(Submission, verbose_name=_('submission'),
                                      related_name='contest', on_delete=CASCADE)
    problem = models.ForeignKey(ContestProblem, verbose_name=_('problem'), on_delete=CASCADE,
                                related_name='submissions', related_query_name='submission')
    participation = models.ForeignKey(ContestParticipation, verbose_name=_('participation'), on_delete=CASCADE,
                                      related_name='submissions', related_query_name='submission')
    points = models.FloatField(default=0.0, verbose_name=_('points'))
    is_pretest = models.BooleanField(verbose_name=_('is pretested'),
                                     help_text=_('Whether this submission was ran only on pretests.'),
                                     default=False)

    class Meta:
        verbose_name = _('contest submission')
        verbose_name_plural = _('contest submissions')


class Rating(models.Model):
    user = models.ForeignKey(Profile, verbose_name=_('user'), related_name='ratings', on_delete=CASCADE)
    contest = models.ForeignKey(Contest, verbose_name=_('contest'), related_name='ratings', on_delete=CASCADE)
    participation = models.OneToOneField(ContestParticipation, verbose_name=_('participation'),
                                         related_name='rating', on_delete=CASCADE)
    rank = models.IntegerField(verbose_name=_('rank'))
    rating = models.IntegerField(verbose_name=_('rating'))
    mean = models.FloatField(verbose_name=_('raw rating'))
    performance = models.FloatField(verbose_name=_('contest performance'))
    last_rated = models.DateTimeField(db_index=True, verbose_name=_('last rated'))

    class Meta:
        unique_together = ('user', 'contest')
        verbose_name = _('contest rating')
        verbose_name_plural = _('contest ratings')


class ContestMoss(models.Model):
    LANG_MAPPING = [
        ('C', MOSS_LANG_C),
        ('C++', MOSS_LANG_CC),
        ('Java', MOSS_LANG_JAVA),
        ('Python', MOSS_LANG_PYTHON),
    ]

    contest = models.ForeignKey(Contest, verbose_name=_('contest'), related_name='moss', on_delete=CASCADE)
    problem = models.ForeignKey(Problem, verbose_name=_('problem'), related_name='moss', on_delete=CASCADE)
    language = models.CharField(max_length=10)
    submission_count = models.PositiveIntegerField(default=0)
    url = models.URLField(null=True, blank=True)

    class Meta:
        unique_together = ('contest', 'problem', 'language')
        verbose_name = _('contest moss result')
        verbose_name_plural = _('contest moss results')
