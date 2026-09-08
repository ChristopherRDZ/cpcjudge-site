"""Example local configuration for a DMOJ deployment.

Copy this file to ``dmoj/local_settings.py`` and adapt it locally. Never commit
the resulting production file or place real credentials in this example.
"""

import os

DEBUG = False
ALLOWED_HOSTS = ['judge.example.org']
SECRET_KEY = os.environ['DJANGO_SECRET_KEY']

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
        'LOCATION': os.environ.get('DMOJ_REDIS_URL', 'redis://127.0.0.1:6379/1'),
        'OPTIONS': {'CLIENT_CLASS': 'django_redis.client.DefaultClient'},
    },
}

CELERY_BROKER_URL = os.environ.get('DMOJ_CELERY_BROKER_URL', 'redis://127.0.0.1:6379/0')
CELERY_RESULT_BACKEND = os.environ.get('DMOJ_CELERY_RESULT_BACKEND', 'redis://127.0.0.1:6379/0')

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
