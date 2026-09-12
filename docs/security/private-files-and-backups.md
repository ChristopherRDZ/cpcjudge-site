# Private configuration, permissions and backups

File protection was applied on 2026-09-10. Remaining secret-bearing settings
copies on a Windows-mounted volume were removed to protected backup storage on
2026-09-11. Production configuration and its values are not source artifacts.

## Configuration and access

- Keep effective Django settings outside the checkout, linked or loaded from a
  protected private location. Grant read access only to the roles that need it.
- Keep judge credentials and tunnel credentials separate from web, proxy,
  events and unrelated service identities.
- Keep code, virtual environments, deployment tools and Git metadata
  non-writable by runtime identities. Protect inactive copies as well as the
  effective configuration.
- Inspect Linux permissions, POSIX ACLs, Windows ACLs and WSL interoperability
  together. A Linux mode on a Windows-mounted file does not establish that all
  Windows access paths are blocked.
- Preserve ownership, mode and ACL metadata during replacement. Reapply and
  verify explicit reader ACLs if a replacement creates a new inode. Avoid
  solving an access regression with globally writable permissions.
- Settings backups, editor saves and compiled Python bytecode can contain the
  same secrets as the source settings. Exclude and protect them too.

The deployment preserves its existing Django and database credentials; moving
files and restricting access is not credential rotation. Redis authentication
was introduced separately.

## Backup coverage

Daily private backups retain three successful generations. A complete recovery
set includes application source, private configuration, database dump, problem
data, uploaded media, service definitions, permission metadata and the context
needed to reconstruct the environment. Protected deployment-specific backups
are kept outside ordinary daily rotation.

Validate archive checksums and contents before rotation. Test database restores
against a disposable database, never the live schema. Preserve the last known
good generation when a backup fails. Restoring files alone does not recreate
accounts, ACLs, runtime directories, secrets or service startup ownership.

The daily process has a verified local SQL restore test. A complete recovery on
a replacement machine and verification of a copy downloaded from external
storage remain separate tasks. The ordinary backup records environment
reconstruction information; it is not a tested, bootable copy of the virtual
environment. Backups contain private data and must never be added to Git.

## Public source boundary

Publish application code and reviewed generic examples. Keep real settings,
keys, connection strings, dumps, archives, user submissions, problem packages,
uploads, logs, host ACL inventories and recovery snapshots private. The local
investigation and deployment folders are not intended for bulk publication.

`.gitignore` is an accident-prevention measure, not a secret scanner, and does
not remove already tracked files. Stage explicit paths in an isolated worktree,
review the staged diff and scan the actual proposed content before committing
or pushing.
