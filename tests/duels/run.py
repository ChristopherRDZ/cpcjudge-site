"""Run the duel tests in a temporary copy without private configuration, in Spanish and in English."""
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[2]
TESTS = Path(__file__).resolve().parent
lab = Path(tempfile.mkdtemp(prefix='dmoj-duel-tests-'))
ignore = shutil.ignore_patterns('local_settings.py', '__pycache__', '*.pyc')
for folder in ('dmoj', 'judge', 'django_ace', 'martor', 'templates', 'locale'):
    shutil.copytree(ROOT / folder, lab / folder, ignore=ignore, symlinks=False,
                    ignore_dangling_symlinks=True)
for catalog in (lab / 'locale').glob('*/LC_MESSAGES/*.po'):
    if catalog.parent.parent.name in ('es', 'en'):
        subprocess.run(['msgfmt', '--check', '-o', str(catalog.with_suffix('.mo')), str(catalog)], check=True)
(lab / 'resources').mkdir()
shutil.copyfile(ROOT / 'resources/caniuse.json', lab / 'resources/caniuse.json')
shutil.copyfile(ROOT / 'manage.py', lab / 'manage.py')
for source in TESTS.glob('*.py'):
    if source.name != 'run.py':
        shutil.copyfile(source, lab / source.name)
(lab / 'duel_test_settings.py').write_text('''from dmoj.settings import *
from django.core.management.utils import get_random_secret_key
SECRET_KEY = get_random_secret_key()
LOGGING_CONFIG = None
DEBUG = False
ALLOWED_HOSTS = ['testserver', 'localhost', '127.0.0.1']
DATABASES = {'default': {'ENGINE':'django.db.backends.sqlite3', 'NAME':':memory:'}}
CACHES = {'default': {'BACKEND':'django.core.cache.backends.locmem.LocMemCache'}}
STATIC_ROOT = %r
MEDIA_ROOT = %r
DMOJ_PROBLEM_DATA_ROOT = %r
COMPRESS_ENABLED = False
COMPRESS_OFFLINE = False
EVENT_DAEMON_USE = False
EMAIL_BACKEND = 'django.core.mail.backends.locmem.EmailBackend'
CELERY_BROKER_URL = 'memory://'
CELERY_RESULT_BACKEND = 'cache+memory://'
CPC_SERVER_OWNERS = ()
LANGUAGE_CODE = 'es'
PASSWORD_HASHERS = ['django.contrib.auth.hashers.MD5PasswordHasher']
class NoMigrations:
    def __contains__(self, item): return True
    def __getitem__(self, item): return None
MIGRATION_MODULES = NoMigrations()
''' % (str(lab / 'static'), str(lab / 'media'), str(lab / 'problems')), encoding='utf-8')
(lab / 'duel_test_english.py').write_text("from duel_test_settings import *\nLANGUAGE_CODE = 'en'\n",
                                          encoding='utf-8')
(lab / 'problems').mkdir()
env = dict(os.environ, PYTHONPATH=str(lab), PYTHONDONTWRITEBYTECODE='1')
print('Isolated test copy:', lab, flush=True)
subprocess.run([sys.executable, '-B', 'manage.py', 'compilejsi18n'], cwd=lab,
               env=dict(env, DJANGO_SETTINGS_MODULE='duel_test_settings'), check=True)

everything = sorted(source.stem for source in TESTS.glob('test_*.py'))
# The pages and messages, once more in English; the engine does not depend on the language.
english = ['test_arena_views', 'test_arena_organizing', 'test_arena_games_and_photos',
           'test_duels_address_and_contests']
failed = False
for module, labels in (('duel_test_settings', everything), ('duel_test_english', english)):
    print('==', module, flush=True)
    result = subprocess.run([sys.executable, '-B', 'manage.py', 'test', *labels, '--noinput'],
                            cwd=lab, env=dict(env, DJANGO_SETTINGS_MODULE=module))
    failed = failed or result.returncode != 0
sys.exit(1 if failed else 0)
