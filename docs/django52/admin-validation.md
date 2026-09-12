# Contest administration validation

This change was tested in the isolated Django upgrade laboratory on 2026-09-11.
It is included in the deployed Django 5.2 update represented by `cpc-production`.

The previous contest form changed participants' `current_contest` while cleaning
the form. An invalid form could therefore expel participants even though the
administrator's edit was rejected. A new unsaved contest also could not safely
be used by that query with the candidate Django version.

The implementation applies the ban-related update in `ContestAdmin.save_related`,
after the valid form and contest have been saved. It filters using the saved
contest ID and preserves the existing rescore behavior.

The laboratory reproduced the invalid-form side effect on the baseline and
checked that the candidate preserves participation after an invalid submission,
then applies the ban after a valid save. The committed file is byte-identical
to both the tested candidate and the deployed implementation. The tests are
historical laboratory evidence; publishing the source does not change an
active contest or repeat the tests against production data.
