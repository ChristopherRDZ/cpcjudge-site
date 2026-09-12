"""Required changes for the existing private settings, for a future approved session.

Not a complete configuration. Do not replace local_settings.py with this file.
Keep the current SECRET_KEY, database credentials, endpoints and protected paths.
No production action is executed by this laboratory artifact.
"""

# Replace STATICFILES_STORAGE with STORAGES; remove the old setting afterward.
STORAGES = {
    'default': {'BACKEND': 'django.core.files.storage.FileSystemStorage'},
    'staticfiles': {'BACKEND': 'django.contrib.staticfiles.storage.ManifestStaticFilesStorage'},
}

# candidate.patch adds this finder to the base settings. If a future private
# configuration overrides the finders, it must preserve this entry as well.
# STATICFILES_FINDERS includes 'compressor.finders.CompressorFinder'.

# Remove USE_L10N if a private override still declares it. Keep USE_I18N and USE_TZ.
# Preserve MEDIA_ROOT, STATIC_ROOT and DMOJ_PROBLEM_DATA_ROOT from the existing
# service-specific configuration; /lab paths are exclusively synthetic fixtures.
