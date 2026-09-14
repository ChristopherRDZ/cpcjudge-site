# Offline compression — deployed 2026-09-14

The web service has a read-only source/static tree. Runtime compressor output
generation was therefore failing for uncached combinations of language and
template context. Before correction, 52 of 114 anonymous page/language probes
returned HTTP 500. After publishing generated output and enabling
`COMPRESS_OFFLINE = True`, all 114 returned 200.

Generated cache files and manifests are deployment artifacts, excluded from
this source repository. Preserve matching source and build outputs in the
private release backup. Do not make the static tree writable by the web user
to hide a missing-build error.

## Build and rollout contract

1. Build in an isolated copy with the release's pinned compressor/minifier
   dependencies, templates, resources and compiled translations. Keep its
   database, cache and external integrations isolated from production.
2. Collect static assets and generate the offline compressor manifest. Cover
   both template engines and every relevant context: configured languages,
   anonymous/authenticated users, permissions, themes, event URL schemes and
   statistics intervals. A build using only the default context is insufficient.
3. Verify that every rendered compress-block key resolves, all referenced files
   exist, and reproduced outputs match known-good output where available.
4. Publish the validated outputs and matching manifest with read-only service
   permissions, enable offline mode in private settings, and restart the web
   service in the authorized rollout window.
5. Probe affected pages across languages and authenticated states, inspect the
   journal for `OfflineGenerationError`, and verify requests write no static
   assets. Check actual NTFS/Linux metadata on WSL: replacing a file may lose
   extended ownership attributes even when `chmod` reports success.

Rebuild whenever compressed templates, CSS/JS, translations, configured
languages or compressor/minifier versions change. Missing variants cause HTTP
500. Rollback must keep configuration, source and generated output consistent.

Historical validation covered 9,120 manifest-key checks with no missing keys,
63 outputs identical to runtime-generated counterparts with none differing,
and 64 later requests without static writes. Authenticated context coverage was
analytical; no complete authenticated browser or host-recovery test is claimed.
