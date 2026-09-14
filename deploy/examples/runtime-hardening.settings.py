"""Optional fragment to merge into private settings after adapting the build.

This is not a complete settings module or an automatic deployment procedure.
Generate and verify all compressed variants before enabling offline mode.
"""

COMPRESS_OFFLINE = True

CPC_CUSTOM_TEST_MAX_IN_FLIGHT = 2
CPC_CUSTOM_TEST_MAX_PER_MINUTE = 12
CPC_CUSTOM_TEST_MAX_PER_HOUR = 200

# Merge this into the existing LOGGING dictionary, preserving other handlers:
# LOGGING['loggers']['django.request']['handlers'] = ['console', 'mail_admins']
# Ensure 'console' is a logging.StreamHandler routed to the service journal.
