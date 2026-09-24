"""Private-settings changes needed when moving an existing DMOJ to Django 5.2.

Not a complete configuration: merge these changes into your local_settings.py and
keep your SECRET_KEY, database credentials, endpoints and paths.
"""

# Replace STATICFILES_STORAGE with STORAGES; remove the old setting afterwards.
STORAGES = {
    'default': {'BACKEND': 'django.core.files.storage.FileSystemStorage'},
    'staticfiles': {'BACKEND': 'django.contrib.staticfiles.storage.ManifestStaticFilesStorage'},
}

# The base settings already include 'compressor.finders.CompressorFinder'.
# If your private settings append it, remove that duplicate append.
# If your private settings replace the finder list, keep this finder exactly once.

# Remove USE_L10N if your private settings still declare it. Keep USE_I18N and USE_TZ.
