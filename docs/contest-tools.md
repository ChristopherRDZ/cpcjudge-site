# Contest tools: freeze, reveal, balloons and clarifications

Configure these tools on the contest's administration page. The contest tabs link
to the tools the signed-in user is allowed to use.

## Frozen scoreboard

Set **freeze minutes** to hide the last part of the contest. During the freeze,
everyone except the contest's editors sees the standings as they were when the
freeze began, and the results after that point are also hidden from:

- participation pages and the ranking of each division;
- submission lists, submission details and live updates;
- contest statistics, and the **AC rate** and **Users** columns of the contest's
  problem lists;
- the API, including the contest history in `/api/v2/user/<name>`.

Your own submissions are never hidden from you. On your own row, a **?** marks a
problem with a result the frozen board is not showing: a submission sent during
the freeze, one still being judged, or one sent just before the freeze whose
verdict arrived after it started. That last kind stays off the public board until
the reveal.

In contests with a per-participant time window, each participation freezes over
its own last minutes. The scoreboard stays frozen after the contest ends, until an
editor publishes it. Freezing does not change how a contest format calculates
points.

When you clone a contest, the freeze minutes are copied but the clone starts
unrevealed.

## Reveal ceremony

Editors can open **Reveal scoreboard** to present the final standings. The
ceremony uncovers hidden results from the bottom of the ranking up, animates
position changes, and supports manual control, reduced motion and a speed slider
from 0.25× to 4×.

It works for individual and team contests. In a mixed contest, choose the
individual or team division. Individual award cards show the contestant; team
cards show the registered members and their profile pictures.

Awards can use finishing places or medal ranges, respecting ties. First-to-solve
awards are independent of medals. Award cards pause the presentation. Settings are
stored per contest in the browser used for the ceremony.

**Publish** is a separate action that makes the real scoreboard public. Opening or
playing the ceremony does not publish anything. In a mixed contest, publishing
unfreezes both divisions. The admin also has **Reveal frozen scoreboards** and
**Freeze scoreboards again** actions.

## Balloon desk

Set each problem's balloon color and color name, and assign **balloon staff** to
the contest. The color field has a picker, an editable hex value and **No color**.

The **Balloons** tab lists pending and delivered balloons and an activity log.
Only editors and balloon staff can open it. Staff do not gain contest editing or
submission access. If two volunteers deliver the same balloon, the second gets a
notice instead of a duplicate. Undo is recorded in the log; nothing is erased.

A balloon is due for the first fully accepted, finally judged submission of an
official participation on a problem, within its contest window. Accepted
submissions made during the frozen part of a participation **never** earn a
balloon, not even after the reveal, so balloons cannot give away the frozen
results. A delivered balloon that later loses its AC stays on the list with a
warning. Select a problem to filter, or **All balloons** to see everything.

### Participant locations

Editors see a row for each official entrant or team, each preassigned private
contestant or team, and anyone with a saved location. Fill in the location next
to the name; empty is allowed. Search by name; long lists use pages of 100. Save
before changing pages.

Only edited fields are saved, so rows on other pages are never overwritten. If
another editor changed the same rows meanwhile, the form reports a conflict and
keeps your input. Forms expire after 24 hours.

## Announcements and clarifications

**Announcements** are created in the admin, for the whole site (this needs the
*announce to the whole site* permission) or for one contest. They are plain text,
appear as a pop-up until their expiry time, and stay listed on the contest's
**Clarifications** tab. A contest announcement stops popping up when the contest
ends unless you set another time. To withdraw one without deleting it, untick
*is visible*.

**Clarifications:** participants ask about the contest or a specific problem from
the **Clarifications** tab. Questions are plain text, limited in length, and
limited per participant (three waiting for an answer, one every 30 seconds).
Team members share their team's questions and limits.

The jury answers from the same tab or from the admin:

- A **private** answer goes back only to whoever asked (their whole team, for a
  team).
- A **public** answer is sent to every participant of that contest as an
  announcement. Correcting it updates the same announcement; making it private
  withdraws it.
- To move a question to another contest, change the contest **on the
  clarification**; its announcement moves with it. Jury members can only use
  contests they can edit.
- Deleting a clarification also withdraws its announcement.

Editors of the contest see a counter of unanswered questions in the navigation
bar while they are in the contest.

## Applying the migrations

The tools need migrations `0150` to `0155`. Back up the database before running
them, compile the translation catalogs, and rebuild the offline compression
manifest if you use it. See the [setup guide](setup-guide.md).

Regression tests for the freeze, reveal and balloon code are in
[tests/contest_features](../tests/contest_features/README.md).
