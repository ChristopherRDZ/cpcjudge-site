"""Run synthetic contest tests in a temporary copy without private configuration."""
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[2]
TESTS = Path(__file__).resolve().parent
lab = Path(tempfile.mkdtemp(prefix='dmoj-contest-tests-'))
for folder in ('dmoj', 'judge', 'django_ace', 'martor'):
    for source in (ROOT / folder).rglob('*.py'):
        if source.is_symlink() or source.name == 'local_settings.py' or '__pycache__' in source.parts:
            continue
        target = lab / source.relative_to(ROOT)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, target)
for folder in ('templates', 'locale', 'judge/fixtures'):
    shutil.copytree(ROOT / folder, lab / folder, dirs_exist_ok=True)
for app in ('martor', 'django_ace'):
    if (ROOT / app / 'templates').exists():
        shutil.copytree(ROOT / app / 'templates', lab / app / 'templates', dirs_exist_ok=True)
catalog = lab / 'locale/es/LC_MESSAGES/django.po'
subprocess.run(['msgfmt', '--check', '-o', str(catalog.with_suffix('.mo')), str(catalog)], check=True)
(lab / 'resources').mkdir()
shutil.copyfile(ROOT / 'resources/caniuse.json', lab / 'resources/caniuse.json')
shutil.copyfile(ROOT / 'manage.py', lab / 'manage.py')
for source in TESTS.glob('*.py'):
    if source.name != 'run.py':
        shutil.copyfile(source, lab / source.name)
(lab / 'feature_test_settings.py').write_text('''from dmoj.settings import *
from django.core.management.utils import get_random_secret_key
SECRET_KEY = get_random_secret_key()
LOGGING_CONFIG = None
DEBUG = False
ALLOWED_HOSTS = ['*']
DATABASES = {'default': {'ENGINE':'django.db.backends.sqlite3', 'NAME':':memory:'}}
CACHES = {'default': {'BACKEND':'django.core.cache.backends.locmem.LocMemCache'}}
STATIC_ROOT = %r
MEDIA_ROOT = %r
COMPRESS_ENABLED = False
COMPRESS_OFFLINE = False
EVENT_DAEMON_USE = False
LANGUAGE_CODE = 'es'
PASSWORD_HASHERS = ['django.contrib.auth.hashers.MD5PasswordHasher']
class NoMigrations:
    def __contains__(self, item): return True
    def __getitem__(self, item): return None
MIGRATION_MODULES = NoMigrations()
''' % (str(lab / 'static'), str(lab / 'media')), encoding='utf-8')
env = dict(os.environ, DJANGO_SETTINGS_MODULE='feature_test_settings', PYTHONPATH=str(lab),
           PYTHONDONTWRITEBYTECODE='1')
print('Isolated test copy:', lab, flush=True)
subprocess.run([sys.executable, '-B', 'manage.py', 'compilejsi18n'], cwd=lab, env=env, check=True)
subprocess.run([sys.executable, '-B', 'manage.py', 'test', 'test_balloons', '--noinput'],
               cwd=lab, env=env, check=True)
for name in ('check_freeze.py', 'check_reveal.py', 'check_awards.py'):
    subprocess.run([sys.executable, '-B', name], cwd=lab, env=env, check=True)
