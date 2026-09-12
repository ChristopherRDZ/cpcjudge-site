# Contest administration candidate

This change was tested in the isolated Django upgrade laboratory on 2026-09-11.
It has not been deployed and is kept on `cpc-django52-candidate`.

The previous contest form changed participants' `current_contest` while cleaning
the form. An invalid form could therefore expel participants even though the
administrator's edit was rejected. A new unsaved contest also could not safely
be used by that query with the candidate Django version.

The candidate applies the ban-related update in `ContestAdmin.save_related`,
after the valid form and contest have been saved. It filters using the saved
contest ID and preserves the existing rescore behavior.

The laboratory reproduced the invalid-form side effect on the baseline and
checked that the candidate preserves participation after an invalid submission,
then applies the ban after a valid save. The committed file is byte-identical
to that tested candidate. This branch records preparation; it is not an
instruction to deploy or to change an active contest.
