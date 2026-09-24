"""Example local configuration for a DMOJ deployment.

Copy this file to ``dmoj/local_settings.py`` and adapt it locally. Never commit
the resulting production file or place real credentials in this example.
"""

import os

DEBUG = False
ALLOWED_HOSTS = ['judge.example.org']
SECRET_KEY = os.environ['DJANGO_SECRET_KEY']

# For a site served over HTTPS by a trusted edge proxy/tunnel.
SESSION_COOKIE_SECURE = True
CSRF_COOKIE_SECURE = True
# Coordinate trusted proxy-scheme handling before enabling Django redirects.
SECURE_SSL_REDIRECT = False
# HSTS is configured at the HTTPS edge; see docs/security/https.md.

SITE_NAME = 'Example Judge'
SITE_LONG_NAME = 'Example Online Judge'
SITE_DOMAIN = 'judge.example.org'
SITE_ADMIN_EMAIL = 'admin@example.org'

DATABASES = {
    'default': {
        'ENGINE': 'django.db.backends.mysql',
        'NAME': os.environ.get('DMOJ_DB_NAME', 'dmoj'),
        'USER': os.environ.get('DMOJ_DB_USER', 'dmoj'),
        'PASSWORD': os.environ['DMOJ_DB_PASSWORD'],
        'HOST': os.environ.get('DMOJ_DB_HOST', '127.0.0.1'),
        'PORT': os.environ.get('DMOJ_DB_PORT', '3306'),
        'OPTIONS': {'charset': 'utf8mb4'},
    },
}

CACHES = {
    'default': {
        'BACKEND': 'django_redis.cache.RedisCache',
        # Provision an authenticated Redis ACL user; keep the URL private.
        'LOCATION': os.environ['DMOJ_REDIS_URL'],
        'OPTIONS': {'CLIENT_CLASS': 'django_redis.client.DefaultClient'},
    },
}

CELERY_BROKER_URL = os.environ['DMOJ_CELERY_BROKER_URL']
CELERY_RESULT_BACKEND = os.environ['DMOJ_CELERY_RESULT_BACKEND']

DMOJ_PROBLEM_DATA_ROOT = '/srv/dmoj/problems'
STATIC_ROOT = '/srv/dmoj/static'
MEDIA_ROOT = '/srv/dmoj/media'

BRIDGED_JUDGE_ADDRESS = ('127.0.0.1', 9999)
EVENT_DAEMON_USE = True
EVENT_DAEMON_GET = 'http://127.0.0.1:15100/'
EVENT_DAEMON_POST = 'http://127.0.0.1:15101/'
EVENT_DAEMON_POLL = 'http://127.0.0.1:15102/'

DMOJ_SSL = 1
DMOJ_HTTPS = True

# Username(s) allowed to delete from the admin and protected from other
# superusers. Empty means nobody can delete. See judge/admin_owner.py.
CPC_SERVER_OWNERS = ('your-owner-username',)

# django-impersonate reads only this dictionary; the loose IMPERSONATE_*
# variables shipped by DMOJ are ignored by the installed version.
IMPERSONATE = {
    'REQUIRE_SUPERUSER': True,
    'ALLOW_SUPERUSER': False,
    'DISABLE_LOGGING': False,
    'URI_EXCLUSIONS': (r'^admin/',),
}

# Each test-case row of the problem data form posts 14 fields. The default of
# 1000 caps the form at about 70 rows; 10240 allows about 730.
DATA_UPLOAD_MAX_NUMBER_FIELDS = 10240

# Custom-test ceilings per user. Zero or less disables one.
CPC_CUSTOM_TEST_MAX_IN_FLIGHT = 2
CPC_CUSTOM_TEST_MAX_PER_MINUTE = 12
CPC_CUSTOM_TEST_MAX_PER_HOUR = 200
# Bytes of program output kept for display, before the judge truncates it.
CPC_CUSTOM_TEST_OUTPUT_PREFIX = 65536

# Teams. False pauses creating teams, sending or accepting invitations and new team
# registrations; existing teams, running attempts and contest history are kept.
CPC_TEAMS_ENABLED = True
# Active teams one account may own, and invitations one account may send per hour.
CPC_MAX_OWNED_TEAMS = 20
CPC_TEAM_INVITES_PER_HOUR = 20
