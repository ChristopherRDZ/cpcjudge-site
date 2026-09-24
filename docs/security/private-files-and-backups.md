# Private configuration, permissions and backups

## Configuration and permissions

- Keep the effective `local_settings.py` outside the checkout, or link it from a
  protected location, and let only the services that need it read it.
- Keep judge keys and tunnel credentials away from the web, proxy and event
  services.
- Make the code, the virtualenv, deployment tools and Git metadata read-only for
  every service account.
- Editor backups, `.bak` copies and compiled bytecode of the settings contain the
  same secrets. Protect or delete them too.
- When you replace a file, check that its owner, mode and ACLs survived. Never fix
  an access error by making files world-writable.
- On Windows drives mounted under WSL, Linux modes do not tell the whole story:
  check the Windows ACLs as well. Without the `metadata` mount option, `chmod`
  and `chown` report success and change nothing.

## Backups

A useful backup contains the database dump, problem data, uploaded media, the
private settings and the service definitions, together with notes on how to
recreate accounts, directories and permissions. Code and the virtualenv can be
rebuilt from Git and `requirements.txt`.

- Verify each archive (checksum and contents) before rotating old ones out, and
  keep the last good copy if a backup fails.
- Test restores on a disposable database, never on the live one.
- Keep a copy off the machine.
- Backups contain private data: never add them to Git.

## Publishing your own changes

`.gitignore` prevents accidents; it is not a secret scanner, and it does not
remove files that are already tracked. Stage files explicitly, review the staged
diff and scan it before pushing.
