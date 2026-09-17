"""Behavior regressions using only SQLite in memory and synthetic contest data."""
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
import django
django.setup()

from django.db import connection
from judge.models import Contest
from judge.views import contest_reveal as R

assert connection.vendor == 'sqlite' and connection.settings_dict['NAME'] == ':memory:'
start = datetime(2026, 1, 1, tzinfo=timezone.utc)
contest = Contest(id=1, start_time=start, end_time=start + timedelta(hours=5), time_limit=None)

# Minimal real SQL fixtures for the actual ORM query, without DMOJ migrations or production settings.
with connection.cursor() as cursor:
    cursor.execute('CREATE TABLE judge_contestproblem (id integer PRIMARY KEY, contest_id integer, points integer, "order" integer)')
    cursor.execute('CREATE TABLE judge_contestparticipation (id integer PRIMARY KEY, start datetime)')
    cursor.execute('CREATE TABLE judge_submission (id integer PRIMARY KEY, status text, result text, is_pretested bool, date datetime)')
    cursor.execute('CREATE TABLE judge_contestsubmission (id integer PRIMARY KEY, submission_id integer, problem_id integer, participation_id integer, points real, is_pretest bool)')
    for problem in range(1, 6):
        cursor.execute('INSERT INTO judge_contestproblem VALUES (%s, 1, 100, %s)', [problem, problem])
    for participant, offset in [(1, 600), (2, 0), (3, 0)]:
        cursor.execute('INSERT INTO judge_contestparticipation VALUES (%s, %s)',
                       [participant, start.replace(tzinfo=None) + timedelta(seconds=offset)])

serial = 0
def submission(problem, participant, seconds, points=100, result='AC', status='D', pretest=False, raw_pretest=False):
    global serial
    serial += 1
    with connection.cursor() as cursor:
        cursor.execute('INSERT INTO judge_submission VALUES (%s, %s, %s, %s, %s)',
                       [serial, status, result, raw_pretest, start.replace(tzinfo=None) + timedelta(seconds=seconds)])
        cursor.execute('INSERT INTO judge_contestsubmission VALUES (%s, %s, %s, %s, %s, %s)',
                       [serial, serial, problem, participant, points, pretest])

submission(1, 1, -1)                          # before contest
submission(1, 1, 20, result='WA')             # wrong answer despite points
submission(1, 1, 25, status='G')              # judging
submission(1, 1, 30, points=50)               # partial
submission(1, 1, 40, pretest=True)            # only pretests, contest flag
submission(1, 1, 50, raw_pretest=True)        # only pretests, submission flag
submission(1, 3, 60)                         # ineligible participation (virtual/DQ/spectator)
submission(1, 2, 120)                        # earliest eligible full AC
submission(1, 1, 180)
submission(1, 1, 5 * 3600)                    # boundary at contest end
submission(2, 2, 200)                        # identical timestamp, lower submission id wins
submission(2, 1, 200)
submission(3, 1, 300, points=20)              # no full AC
submission(4, 1, 18001)                      # after contest
submission(5, 1, 2500)                       # outside personal 30-minute window (600..2400)
submission(5, 2, 1700)                       # inside personal window
eligible = [SimpleNamespace(id=1), SimpleNamespace(id=2)]
assert R.first_to_solve(contest, eligible) == {1: 2, 2: 2, 5: 2}
assert R.first_to_solve(contest, []) == {}
contest.time_limit = timedelta(minutes=30)
assert R.first_to_solve(contest, eligible) == {1: 2, 2: 2, 5: 2}
# Remove the valid p5 result inside this disposable SQLite fixture: late AC must not become a winner.
with connection.cursor() as cursor:
    cursor.execute('UPDATE judge_submission SET result = %s WHERE id = %s', ['WA', serial])
assert 5 not in R.first_to_solve(contest, eligible)
print('PASS first-to-solve SQL: full AC, time windows, status, pretests, eligibility, tie, empty')

problems = [R.RevealProblem(1, 100, 'A', 'Alpha'), R.RevealProblem(2, 100, 'B', 'Beta')]
entries = [
    R.RevealEntry(1, 'Winner', 'winner', '', 200, 30, 0,
                  {'1': {'points': 100, 'time': 10}, '2': {'points': 100, 'time': 20}}),
    R.RevealEntry(2, 'FTS outside medals', 'fts', '', 100, 5, 0, {'2': {'points': 100, 'time': 5}},
                  frozen_at='x', frozen_score=0, frozen_cumtime=0, frozen_tiebreaker=0, frozen_format_data={}),
]
data, _ = R.assemble_reveal('default', {}, 0, problems, entries, {(2, 2): 1}, {1: 1, 2: 2})
assert data['rows'][1]['firstSolves'] == [1] and data['rows'][1]['finalRank'] == 2
assert data['rows'][1]['finalTime'] == '0:05' or data['rows'][1]['finalTime'] == '00:00:05'
for i, row in enumerate(data['rows']):
    assert row['awardStep'] >= next(n for n, step in enumerate(data['steps'], 1)
                                    if step['kind'] == 'finalize' and step['row'] == i)
print('PASS awards: independent of medals, exact final totals, not before row is complete')

# A score drop moves a previously finalized team upward; its card must wait for that final rank.
entries = [
    R.RevealEntry(1, 'Drops', 'a', '', 0, 0, 0, {}, frozen_at='x', frozen_score=100,
                  frozen_cumtime=10, frozen_tiebreaker=0, frozen_format_data={'1': {'points': 100, 'time': 10}}),
    R.RevealEntry(2, 'Rises later', 'b', '', 50, 20, 0, {'1': {'points': 50, 'time': 20}}),
]
data, _ = R.assemble_reveal('default', {}, 0, problems, entries, {})
finalized = next(n for n, step in enumerate(data['steps'], 1) if step['row'] == 1 and step['kind'] == 'finalize')
assert data['rows'][1]['finalRank'] == 1 and data['rows'][1]['awardStep'] > finalized
print('PASS awards: ranks that change after finalization do not get a premature card')
