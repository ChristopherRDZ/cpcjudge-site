"""Run the interactive problem tests in a temporary copy without private configuration."""
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[2]
TESTS = Path(__file__).resolve().parent
lab = Path(tempfile.mkdtemp(prefix='dmoj-interactive-tests-'))
ignore = shutil.ignore_patterns('local_settings.py', '__pycache__', '*.pyc')
for folder in ('dmoj', 'judge', 'django_ace', 'martor', 'templates', 'locale'):
    shutil.copytree(ROOT / folder, lab / folder, ignore=ignore, symlinks=False,
                    ignore_dangling_symlinks=True)
for catalog in (lab / 'locale').glob('*/LC_MESSAGES/django.po'):
    if catalog.parent.parent.name in ('es', 'en'):
        subprocess.run(['msgfmt', '--check', '-o', str(catalog.with_suffix('.mo')), str(catalog)], check=True)
(lab / 'resources').mkdir()
shutil.copyfile(ROOT / 'resources/caniuse.json', lab / 'resources/caniuse.json')
shutil.copyfile(ROOT / 'manage.py', lab / 'manage.py')
for source in TESTS.glob('*.py'):
    if source.name != 'run.py':
        shutil.copyfile(source, lab / source.name)
(lab / 'interactive_test_settings.py').write_text('''from dmoj.settings import *
from django.core.management.utils import get_random_secret_key
SECRET_KEY = get_random_secret_key()
LOGGING_CONFIG = None
DEBUG = False
ALLOWED_HOSTS = ['*']
DATABASES = {'default': {'ENGINE':'django.db.backends.sqlite3', 'NAME':':memory:'}}
CACHES = {'default': {'BACKEND':'django.core.cache.backends.locmem.LocMemCache'}}
STATIC_ROOT = %r
MEDIA_ROOT = %r
DMOJ_PROBLEM_DATA_ROOT = %r
COMPRESS_ENABLED = False
COMPRESS_OFFLINE = False
EVENT_DAEMON_USE = False
LANGUAGE_CODE = 'en'
PASSWORD_HASHERS = ['django.contrib.auth.hashers.MD5PasswordHasher']
class NoMigrations:
    def __contains__(self, item): return True
    def __getitem__(self, item): return None
MIGRATION_MODULES = NoMigrations()
''' % (str(lab / 'static'), str(lab / 'media'), str(lab / 'problems')), encoding='utf-8')
(lab / 'problems').mkdir()
env = dict(os.environ, DJANGO_SETTINGS_MODULE='interactive_test_settings', PYTHONPATH=str(lab),
           PYTHONDONTWRITEBYTECODE='1')
print('Isolated test copy:', lab, flush=True)
subprocess.run([sys.executable, '-B', 'manage.py', 'compilejsi18n'], cwd=lab, env=env, check=True)
subprocess.run([sys.executable, '-B', 'manage.py', 'test', 'test_interactive_problems', '--noinput'],
               cwd=lab, env=env, check=True)
