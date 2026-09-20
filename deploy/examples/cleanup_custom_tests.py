# -*- coding: utf-8 -*-
"""Retira las pruebas personalizadas terminadas de TODAS las cuentas.

Cada ejecución de la prueba personalizada deja tres cosas: un directorio
`/srv/dmoj/problems/ct_<12 hex>` con `input.txt`, `output.txt` e `init.yml`; una fila de
`Problem` con ese mismo código y nombre «Prueba Personalizada»; y el `Submission`
con su fuente. Lo que ensucia el listado de problemas del admin es la **fila**.

El sitio ya sabe retirarlas: `_cleanup_owned_custom_tests()` lo hace al principio de
cada `POST /custom-test/run/`. El problema no es el criterio, es el **disparo**. Esa
limpieza sólo corre cuando alguien manda otra prueba, sólo toca las suyas
(`user=profile`) y como mucho diez por llamada. Quien prueba una vez y no vuelve deja
su rastro para siempre.

## Por qué esto no llama a aquella función

Aquella fija el plazo en cinco minutos, en duro, dentro del cuerpo de la función. Aquí
el plazo es configurable —20 minutos por omisión, a petición del propietario— así que
el recorrido se reescribe. Lo que **sí** se reutiliza es la parte delicada:
`_is_owned_custom_test()`, que comprueba la firma que la vista dejó en `summary`, y las
dos funciones que resuelven y retiran el directorio.

**Si algún día cambian las salvaguardas de `_cleanup_owned_custom_tests()`, hay que
revisar las de aquí.** Son las mismas, a propósito, y las pruebas del expediente las
comprueban una por una:

* nunca se toca un envío en cola, en proceso o calificándose (`QU`, `P`, `G`);
* nunca se toca nada más reciente que el plazo, para no borrarle a alguien el
  resultado que está mirando;
* la firma tiene que cuadrar: que el código empiece por `ct_` no basta;
* se deja en paz lo que adquirió autores, curadores, concursos o un segundo envío,
  es decir, lo que dejó de ser desechable.

Después hay una segunda pasada con los directorios que **no tiene ninguna fila** que
los reclame: basura pura en disco, como la que deja un `rm -rf` de los directorios sin
tocar la base, o un fallo a medio camino. Se exige una hora de antigüedad para no
pisar una prueba que se esté creando en ese instante.

Por omisión **sólo informa**. Hay que pasar `--ejecutar` para que borre.

Uso:
    /srv/dmoj/venv/bin/python -B cleanup_custom_tests.py
    /srv/dmoj/venv/bin/python -B cleanup_custom_tests.py --ejecutar
    ... y --minutos N para cambiar el plazo de gracia.
"""
import argparse
import os
import shutil
import sys
import time


SITE = '/srv/dmoj/site'
IN_JUDGE = ('QU', 'P', 'G')


def setup_django():
    """Arranca Django con los ajustes del sitio pero sin montar su registro.

    `LOGGING` vive en la configuración privada y sus manejadores escriben donde
    escribe el servicio web. Construirlos desde una tarea suelta falla —el
    `configure_handler` revienta antes de que el script haga nada— y, aunque no
    fallara, el mantenimiento no tiene por qué escribir en el diario de la web ni
    activar su manejador de correo.

    La forma limpia de evitarlo es poner `LOGGING_CONFIG` a `None` **antes** de
    `django.setup()`, que es quien llama a `configure_logging()`. Asignar sobre
    `django.conf.settings` fuerza la carga completa de los ajustes y luego cambia ese
    único valor sobre el objeto ya cargado; no hace falta un módulo de ajustes propio,
    que además daría un import circular porque `dmoj/__init__.py` importa Celery y
    Celery lee `settings` durante la propia importación.

    Sin configuración de registro, Python manda los avisos de nivel WARNING o superior
    a la salida de error, que bajo systemd es el journal de esta unidad.
    """
    sys.path.insert(0, SITE)
    os.chdir(SITE)
    os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'dmoj.settings')
    from django.conf import settings
    settings.LOGGING_CONFIG = None
    import django
    django.setup()


def purge_profile(profile, cutoff):
    """Retira las pruebas retirables de una cuenta. Devuelve cuántas."""
    from django.db import transaction
    from judge.models import Submission
    from judge.views.problem import (CUSTOM_TEST_MARKER, _custom_test_directory,
                                     _is_owned_custom_test, _remove_custom_test_directory)

    candidates = list(Submission.objects.filter(
        user=profile, date__lt=cutoff, problem__summary__startswith=CUSTOM_TEST_MARKER,
    ).exclude(status__in=IN_JUDGE).order_by('id').values_list('id', flat=True))

    removed = 0
    for submission_id in candidates:
        try:
            with transaction.atomic():
                submission = Submission.objects.select_for_update().select_related('problem').get(
                    pk=submission_id, user=profile)
                # Se vuelve a comprobar todo con la fila bloqueada: entre la consulta
                # de arriba y este punto el envío pudo reencolarse.
                if submission.status in IN_JUDGE or submission.date >= cutoff:
                    continue
                if not _is_owned_custom_test(submission, profile):
                    continue
                problem = submission.problem
                if (problem.authors.exists() or problem.curators.exists() or problem.contests.exists() or
                        Submission.objects.filter(problem=problem).exclude(pk=submission.pk).exists()):
                    continue
                _, identity = _custom_test_directory(problem.code)
                code = problem.code
                problem.delete()
                # El directorio se retira sólo si la transacción llega a confirmarse, y
                # sólo si sigue siendo el mismo inodo que se miró.
                transaction.on_commit(lambda code=code, identity=identity:
                                      _remove_custom_test_directory(code, identity))
                removed += 1
        except Exception as error:  # noqa: BLE001, una fila mala no debe parar la pasada
            print('Se omite el envío %s: %s' % (submission_id, error))
    return removed


def main(argv=None):
    """Django ya debe estar en marcha. Lo arranca el bloque `__main__`, no esto, para
    que las pruebas puedan llamar a `main()` dentro de su propio laboratorio."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument('--ejecutar', action='store_true',
                        help='borra de verdad; sin esto sólo informa')
    parser.add_argument('--minutos', type=int, default=20,
                        help='plazo de gracia antes de retirar una prueba terminada (20 por omisión)')
    args = parser.parse_args(argv)

    from datetime import timedelta
    from django.conf import settings
    from django.utils import timezone
    from judge.models import Problem, Profile, Submission
    from judge.views.problem import CUSTOM_TEST_MARKER

    def marked_problems():
        return Problem.objects.filter(summary__startswith=CUSTOM_TEST_MARKER)

    def custom_test_root():
        return os.path.realpath(settings.DMOJ_PROBLEM_DATA_ROOT)

    def directories():
        try:
            names = os.listdir(custom_test_root())
        except OSError:
            return []
        return sorted(n for n in names if n.startswith('ct_'))

    cutoff = timezone.now() - timedelta(minutes=args.minutos)
    print('Plazo de gracia: %d minutos.' % args.minutos)
    print('Antes: %d problemas marcados, %d directorios.'
          % (marked_problems().count(), len(directories())))

    profile_ids = list(Submission.objects.filter(
        problem__summary__startswith=CUSTOM_TEST_MARKER,
    ).values_list('user', flat=True).distinct())

    if not args.ejecutar:
        ready = Submission.objects.filter(
            date__lt=cutoff, problem__summary__startswith=CUSTOM_TEST_MARKER,
        ).exclude(status__in=IN_JUDGE).count()
        held = Submission.objects.filter(
            problem__summary__startswith=CUSTOM_TEST_MARKER, status__in=IN_JUDGE).count()
        recent = Submission.objects.filter(
            date__gte=cutoff, problem__summary__startswith=CUSTOM_TEST_MARKER,
        ).exclude(status__in=IN_JUDGE).count()
        known = set(marked_problems().values_list('code', flat=True))
        stray = [n for n in directories() if n not in known]
        print('Cuentas con rastro: %d.' % len(profile_ids))
        print('Simulación: %d cumplen el plazo y se retirarían; %d siguen en el juez; '
              '%d son más recientes que el plazo; %d directorios sin fila que los reclame.'
              % (ready, held, recent, len(stray)))
        print('Nada se ha borrado. Repite con --ejecutar.')
        return 0

    print('Cuentas con rastro: %d.' % len(profile_ids))
    removed_rows = 0
    for profile_id in profile_ids:
        try:
            profile = Profile.objects.get(pk=profile_id)
        except Profile.DoesNotExist:
            continue
        removed_rows += purge_profile(profile, cutoff)

    known = set(marked_problems().values_list('code', flat=True))
    root = custom_test_root()
    horizon = time.time() - 3600
    removed_dirs = 0
    for name in directories():
        if name in known:
            continue
        path = os.path.join(root, name)
        try:
            if os.path.realpath(path) != path or not os.path.isdir(path):
                continue
            if os.lstat(path).st_mtime > horizon:
                continue  # podría estar creándose ahora mismo
            if not shutil.rmtree.avoids_symlink_attacks:
                print('El sistema no ofrece borrado seguro de árboles; se omite %s.' % name)
                continue
            shutil.rmtree(path)
            removed_dirs += 1
        except OSError as error:
            print('No se pudo retirar %s: %s' % (name, error))

    remaining = marked_problems().count()
    print('Retiradas %d filas y %d directorios sueltos.' % (removed_rows, removed_dirs))
    print('Después: %d problemas marcados, %d directorios.' % (remaining, len(directories())))
    if remaining:
        print('Lo que queda está en el juez, es más reciente que el plazo, o dejó de ser '
              'desechable. Es correcto que siga ahí.')
    return 0


if __name__ == '__main__':
    setup_django()
    raise SystemExit(main())
