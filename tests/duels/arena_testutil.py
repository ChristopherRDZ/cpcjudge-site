"""Shared fixtures for the duel tests (judge/arena). Judging is replaced by a no-op."""
from datetime import timedelta
from unittest.mock import patch

from django.contrib.auth.models import Permission
from django.core.cache import cache
from django.utils import timezone

from judge.arena import engine
from judge.models import Contest, Judge, Language, Submission
from judge.models.arena import ArenaGame, ArenaMatch
from judge.models.tests.util import create_problem, create_user

LANGUAGES = ('PY3', 'CPP17')


def organizer(username='organizer', staff=True):
    user = create_user(username=username, is_staff=staff)
    user.user_permissions.add(Permission.objects.get(codename='manage_arena'),
                              Permission.objects.get(codename='see_private_problem'))
    return user


def player(username):
    return create_user(username=username)


def problem(code, public=False, points=1, name=None, languages=LANGUAGES, partial=False):
    return create_problem(code=code, name=name or code, is_public=public, points=points,
                          allowed_languages=list(languages), partial=partial)


def online_judge(*problems):
    judge, _ = Judge.objects.get_or_create(name='testjudge', defaults={'auth_key': 'x' * 32, 'online': True})
    judge.online = True
    judge.save()
    for item in problems:
        judge.problems.add(item)
    judge.runtimes.add(*Language.objects.filter(key__in=LANGUAGES))
    return judge


class NoJudging:
    """Replace sending submissions to the bridge with nothing, for the duration of a test."""

    def __enter__(self):
        self._patch = patch('judge.models.submission.judge_submission')
        self.mock = self._patch.start()
        return self.mock

    def __exit__(self, *exc):
        self._patch.stop()


def duel(actor, a, b, problems, best_of=1, rules=None, games=None, **kwargs):
    entries = games if games is not None else [[{'id': p.id, 'points': 100} for p in problems]]
    return engine.create_duel(actor.profile, a.profile, b.profile, entries, best_of=best_of, rules=rules or {},
                              **kwargs)


def start(actor, match, game=None):
    result = engine.start_game(actor.profile, match.pk, game.pk if game else None, force=True)
    assert result['started'], result
    return result['game']


def skip_countdown(game, seconds_ago=1):
    """Make a game that is in its countdown start right now, keeping its length."""
    game.refresh_from_db()
    length = game.ends_at - game.started_at
    started = timezone.now() - timedelta(seconds=seconds_ago)
    ArenaGame.objects.filter(pk=game.pk).update(started_at=started, ends_at=started + length)
    Contest.objects.filter(pk=game.contest_id).update(start_time=started, end_time=started + length)
    game.refresh_from_db()
    return game


def time_up(game, seconds_ago=1):
    game.refresh_from_db()
    ends = timezone.now() - timedelta(seconds=seconds_ago)
    ArenaGame.objects.filter(pk=game.pk).update(ends_at=ends)
    Contest.objects.filter(pk=game.contest_id).update(end_time=ends)
    game.refresh_from_db()
    return game


def submit(user, game, order=0, language='PY3', source='print(1)', nonce=''):
    with NoJudging():
        return engine.submit(user.profile, user, game.pk, order, language, source, nonce)


def grade(submission, result='AC', case_points=1, case_total=1, status='D'):
    """What the bridge does when a submission finishes: save the verdict, then update the contest."""
    submission.refresh_from_db()
    submission.status = status
    submission.result = result if status == 'D' else status
    submission.case_points = case_points
    submission.case_total = case_total
    submission.points = case_points
    submission.save()
    if status == 'D':
        submission.update_contest()
    return submission


def backdate(submission, seconds_after_start, game):
    game.refresh_from_db()
    Submission.objects.filter(pk=submission.pk).update(date=game.started_at + timedelta(seconds=seconds_after_start))


def tick(game):
    game = ArenaGame.objects.get(pk=game.pk)
    engine.tick(game)
    game.refresh_from_db()
    return game


def match_of(game):
    return ArenaMatch.objects.get(pk=game.match_id)


def clear_cache():
    cache.clear()
