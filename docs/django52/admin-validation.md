# Contest bans applied after saving

Upstream's contest form removed banned users from the contest while the form was
still being validated. If the administrator's edit was then rejected (for
example, because another field had an error), those participants had already
been expelled. With Django 5.2 that query also fails for a contest that has not
been saved yet.

In this fork, `ContestAdmin.save_related` applies the ban only after the whole
form is valid and the contest is saved, using the saved contest's ID. Rescoring
works as before.
