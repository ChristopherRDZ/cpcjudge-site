# Contest operations: freeze, reveal and balloons

This fork provides three tools for contest organisers. Configure them on the
contest administration page; the contest tabs link to tools the signed-in user
is allowed to use. The interface includes Spanish translations.

## Frozen scoreboard

Set **freeze minutes** to freeze the final part of a contest. The frozen view
also applies to participation results, submission lists, details, API responses,
statistics and public event broadcasts. Personal contest windows are respected.
Freezing does not change how a contest format calculates points. The scoreboard
stays frozen until an editor explicitly reveals it. Migration `0153` adds the
configuration and stored snapshots.

## Reveal ceremony

Editors can open **Reveal scoreboard** to present the final standings. The
ceremony steps through hidden results, animates position changes and supports
manual control, reduced motion and a speed slider from 0.25× to 4×.

The ceremony supports **individual and team contests**. For mixed contests,
choose the individual or team division to reveal that ranking. Individual
award cards show the contestant; team award cards include the registered
members and their profile pictures.

Awards can use finishing places or medal ranges, respecting tied ranks.
First-to-solve achievements are independent of medals. Participant award cards
pause the presentation for acknowledgement. Presentation preferences are stored
per contest in the browser. **Publish** is a separate explicit action that makes
the final scoreboard public; merely opening or playing the ceremony does not.
In a mixed contest, publishing the results unfreezes both divisions.

## Balloon desk

Set each problem's balloon color and color name, then assign **balloon staff**
to the contest. The color swatch in the administration form opens the browser's
color picker. The hexadecimal field remains editable, and **No color** clears
it. Existing values are preserved when the form is opened, including short hex
colors. New problem rows get their own picker.

The **Balloons** tab shows pending and delivered balloons and an activity log.
Only editors and the assigned staff can open it or mark deliveries. Staff do
not gain contest editing or submission access. A duplicate delivery by another
volunteer produces a notice rather than another delivery. Undo appends an event;
it does not erase the history.

A balloon corresponds to the first fully judged, full-score AC of an official,
eligible participation within its contest window. Accepted submissions made
during the frozen part of a participation **never generate balloons**, including
after the reveal. A delivered balloon that loses its AC remains visible with a
warning. The page updates on events and periodic polling. Select a problem to
filter it, or use **All balloons** to restore the complete list.

The page follows the site's light, dark or automatic theme. Participant names,
locations, notices and buttons use contrasting foreground/background colors.

## Participant locations

Editors see a row per official entrant, including disqualified entrants, plus
preassigned private contestants and everyone with a saved location. Private
contestants can be assigned seats before joining. For a public contest, entrants
appear after joining; the tool does not enumerate every account on the site.

Fill in the location beside the name. Empty locations are allowed for absent or
unassigned contestants. Search by username or display name; larger rosters use
pages of 100. Save before changing pages or searching. The browser warns about
unsaved edits, and live balloon updates leave the location form alone.

Only edited fields are saved. Rows outside the current page and later additions
are preserved. Clearing a previously filled field explicitly removes only that
location. Invalid or incomplete forms are rejected before any writes. Signed
form snapshots and a transaction detect overlapping edits from another editor;
review the retained input before saving a conflict again. Forms expire after
24 hours. Location text is escaped as text, not rendered as HTML.

## Deployment and validation

Migration `0154` adds the balloon/location models and problem color fields.
The later visual and location-form changes do not add another migration.
Back up before applying migrations, keep code/schema versions aligned and
verify file ownership and permissions according to your deployment environment.
Compile updated translation catalogs. Check offline compression whenever
changing templates or translations; this update's 5,472 tested block variants
produce the same keys as before the visual changes.

The location and balloon suite passed 24 tests on a disposable MariaDB instance,
including concurrent delivery. On SQLite the locking test is skipped. Browser
validation covered 26 checks across light/dark/automatic themes, phone layout,
filters, delivery/undo, location preservation and dynamic color pickers. Text
contrast on the tested balloon rows was at least 4.5:1. Real-data rendering was
checked with SQL READ ONLY and isolated caches; production forms were not
submitted by those checks. These are scoped checks, not a security guarantee.

See [synthetic tests](../tests/contest_features/README.md) for a reproducible,
isolated regression suite. The reveal's public **Publish** action has not been
exercised against production data as part of these checks.
