"""Generic private-settings fragment for the Django 5.2 source update.

Not a complete configuration. Do not replace local_settings.py with this file.
Keep the current SECRET_KEY, database credentials, endpoints and protected paths.
This example contains no operational configuration and executes no deployment.
"""

# Replace STATICFILES_STORAGE with STORAGES; remove the old setting afterward.
STORAGES = {
    'default': {'BACKEND': 'django.core.files.storage.FileSystemStorage'},
    'staticfiles': {'BACKEND': 'django.contrib.staticfiles.storage.ManifestStaticFilesStorage'},
}

# The base settings already include 'compressor.finders.CompressorFinder'.
# If private settings previously appended it, remove that duplicate append.
# If private settings replace the finder list, preserve this finder exactly once.

# Remove USE_L10N if a private override still declares it. Keep USE_I18N and USE_TZ.
# Preserve MEDIA_ROOT, STATIC_ROOT and DMOJ_PROBLEM_DATA_ROOT from the existing
# service-specific configuration; /lab paths are exclusively synthetic fixtures.
