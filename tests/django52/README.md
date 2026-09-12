# Opt-in synthetic compatibility tests

`compat_tests.py` is the unchanged additional test module from the isolated
Django 5.2 laboratory. Its users, passwords, judge key and event data are
fictitious fixtures. It is stored outside Django application's normal test
discovery paths because it relies on a specially provisioned environment.

The module is not a standalone test runner. Before loading it through Django's
test runner, provide all of the following in a disposable environment:

- An independent application copy and the recorded candidate dependencies.
- Synthetic settings, a disposable MariaDB database and a process-local
  `LocMemCache`. The test setup calls `cache.clear()`.
- Private writable problem, media, static and user-cache directories containing
  no user data, with generated static assets already available.
- A local-memory mail backend, disabled external integrations, the tested
  application URLs/templates and appropriate fixture timezone/settings.
- No access to production settings, data, database/Redis sockets, judges or
  external network destinations. The original harness enforced separate mount
  and network namespaces and an unprivileged identity.

Never load this module with production settings. Database transactions do not
protect external files, cache contents or outbound effects. In the original
harness the module was importable as `compat_tests` and passed explicitly to
Django's test runner. Reconstruct and validate an equivalent isolated harness
before reusing it; the host-specific launcher is deliberately kept private.
