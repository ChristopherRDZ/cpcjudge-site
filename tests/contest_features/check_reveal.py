"""Pruebas sintéticas del revelador. No tocan la base ni producción.

Se ejecutan sobre la copia temporal del código que prepara `run.py`. Todo son objetos construidos a mano;
la única base configurada es un sqlite en memoria que se queda vacío.

    python tests/contest_features/run.py
"""
import json
import random
import sys

import django

django.setup()

from django.contrib.auth.models import AnonymousUser  # noqa: E402
from django.template.loader import get_template, render_to_string  # noqa: E402
from django.test import RequestFactory  # noqa: E402
from django.urls import reverse  # noqa: E402
from django.utils import translation  # noqa: E402

from judge.views import contest_reveal as R  # noqa: E402

ok = fallos = 0


def comprobar(condicion, descripcion):
    global ok, fallos
    if condicion:
        ok += 1
        print('  OK    %s' % descripcion)
    else:
        fallos += 1
        print('  FALLA %s' % descripcion)


# ---------------------------------------------------------------------------------------------------------------
print('-- totales por formato, calculados como cada formato los guarda')

icpc = {'1': {'time': 600.5, 'points': 1, 'penalty': 2}, '2': {'time': 1200, 'points': 0, 'penalty': 3},
        '3': {'time': 900, 'points': 1, 'penalty': 0}}
comprobar(R.format_totals('icpc', {'penalty': 20}, icpc) == (2, 600.5 + 900 + 2 * 20 * 60, 900),
          'icpc: puntos, tiempo con 20 min por intento previo sólo en resueltos, desempate el último resuelto')
comprobar(R.format_totals('icpc', {'penalty': 0}, icpc) == (2, 1500.5, 900), 'icpc sin penalización')
comprobar(R.format_totals('icpc', {}, {}) == (0, 0, 0), 'icpc vacío')
atc = {'1': {'time': 600, 'points': 100, 'penalty': 1}, '2': {'time': 900, 'points': 200, 'penalty': 0},
       '3': {'time': 1500, 'points': 0, 'penalty': 4}}
comprobar(R.format_totals('atcoder', {'penalty': 5}, atc) == (300, 900 + 5 * 60, 0),
          'atcoder: el mayor tiempo resuelto más la penalización de los resueltos')
default = {'1': {'time': 100, 'points': 5}, '2': {'time': 50, 'points': 0}}
comprobar(R.format_totals('default', None, default) == (5, 100, 0), 'default')
comprobar(R.format_totals('ioi', {'cumtime': False}, {'1': {'time': 0, 'points': 7.5}}) == (7.5, 0, 0),
          'ioi sin tiempo')
comprobar(R.format_totals('ioi16', {'cumtime': True}, {'1': {'time': 30, 'points': 7.5},
                                                       '2': {'time': 90, 'points': 0}}) == (7.5, 30, 0),
          'ioi16 con tiempo, sólo lo que puntúa')
ecoo = {'1': {'time': 100, 'points': 10, 'bonus': 3}, '2': {'time': 40, 'points': 0, 'bonus': 0}}
comprobar(R.format_totals('ecoo', {'cumtime': True}, ecoo) == (13, 140, 0), 'ecoo con bono y tiempo de todos')
comprobar(R.format_totals('ecoo', {'cumtime': False}, ecoo) == (13, 0, 0), 'ecoo sin tiempo')
comprobar(R.format_totals('inventado', {}, ecoo) is None, 'formato desconocido: None, no un cálculo inventado')
comprobar(R.totals_match((2, 3000.9, 900), (2, 3000, 900.0004), 0), 'cumtime truncado a entero cuenta como igual')
comprobar(not R.totals_match((2, 3000, 900), (3, 3000, 900), 0), 'puntos distintos no cuadran')
comprobar(not R.totals_match(None, (0, 0, 0), 3), 'sin cálculo no cuadra')
comprobar([R.time_matters(f, {}) for f in ('icpc', 'atcoder', 'default', 'ioi', 'ioi16', 'ecoo')] ==
          [True, True, True, False, False, False], 'qué formatos muestran tiempo sin configuración')

# ---------------------------------------------------------------------------------------------------------------
print('-- celdas')

comprobar(R.cell_view('icpc', None, 1, True) == {'state': 'empty', 'main': '', 'sub': ''}, 'celda vacía')
comprobar(R.cell_view('icpc', {'points': 1, 'time': 3725, 'penalty': 0}, 1, True) ==
          {'state': 'full', 'main': '+', 'sub': '01:02:05'}, 'icpc resuelto al primer intento')
comprobar(R.cell_view('icpc', {'points': 1, 'time': 60, 'penalty': 2}, 1, True)['main'] == '+2',
          'icpc resuelto tras dos fallos')
comprobar(R.cell_view('icpc', {'points': 0, 'time': 60, 'penalty': 3}, 1, True) ==
          {'state': 'failed', 'main': '-3', 'sub': ''}, 'icpc fallido con tres intentos, sin tiempo')
comprobar(R.cell_view('icpc', {'points': 0.5, 'time': 60, 'penalty': 0}, 1, True)['state'] == 'partial',
          'icpc parcial')
comprobar(R.cell_view('ecoo', {'points': 10, 'time': 60, 'bonus': 4}, 10, False) ==
          {'state': 'full', 'main': '10', 'sub': '+4'}, 'ecoo con bono')
vista_ioi = R.cell_view('ioi', {'points': 33.5, 'time': 0}, 100, False)
comprobar(vista_ioi['state'] == 'partial' and vista_ioi['main'] in ('33.5', '33,5') and vista_ioi['sub'] == '',
          'ioi parcial sin tiempo, con el separador decimal del idioma activo')

# ---------------------------------------------------------------------------------------------------------------
print('-- orden de revelación en un caso escrito a mano (ICPC, un punto por problema)')


def fila(indice, congeladas, finales, pendientes):
    config = {'penalty': 20}
    cong = {str(i): c for i, c in enumerate(congeladas) if c}
    fin = {str(i): c for i, c in enumerate(finales) if c}
    return R.RevealRow(indice, congeladas, finales, R.format_totals('icpc', config, cong),
                       R.format_totals('icpc', config, fin), pendientes)


def intermedios(row, cells):
    return R.format_totals('icpc', {'penalty': 20}, {str(i): c for i, c in enumerate(cells) if c})


AC = lambda t, p=0: {'points': 1, 'time': t, 'penalty': p}  # noqa: E731
WA = lambda t, p=1: {'points': 0, 'time': t, 'penalty': p}  # noqa: E731

filas = [
    # 0: primero congelado con dos resueltos, sin nada pendiente
    fila(0, [AC(100), AC(200)], [AC(100), AC(200)], [0, 0]),
    # 1: un resuelto; durante la congelación resuelve B -> empata en problemas y gana por tiempo
    fila(1, [AC(50), None], [AC(50), AC(120)], [0, 2]),
    # 2: nada resuelto; envía A y falla
    fila(2, [None, None], [WA(1000), None], [1, 0]),
    # 3: nada resuelto y no envía nada durante la congelación
    fila(3, [None, None], [None, None], [0, 0]),
]
orden, rangos, pasos = R.plan_reveal(filas, 0, intermedios)
comprobar(orden == [0, 1, 2, 3] and rangos == [1, 2, 3, 3], 'marcador congelado inicial y empates en el rango')
resumen = [(p['kind'], p['row']) + ((p['column'], p['from'], p['to']) if p['kind'] == 'reveal' else ())
           for p in pasos]
esperado = [
    ('finalize', 3),               # la fila de abajo no oculta nada, pero se pasa por ella
    ('reveal', 2, 0, 2, 2),        # la fila 2 descubre A: falla y no se mueve
    ('finalize', 2),
    ('reveal', 1, 1, 1, 0),        # la fila 1 descubre B: sube al primer lugar
    ('finalize', 0),               # ahora la fila de abajo sin cerrar es la 0, que no oculta nada
    ('finalize', 1),
]
comprobar(resumen == esperado, 'secuencia completa: %s' % resumen)
comprobar(pasos[3].get('order') == [1, 0, 2, 3] and 'ranks' not in pasos[3],
          'el paso que mueve una fila lleva el orden nuevo; los rangos, sólo si cambian')
comprobar('order' not in pasos[1], 'un paso que no mueve nada no repite el orden')
comprobar(pasos[1]['totals'] == filas[2].final_totals, 'la última celda de una fila deja sus totales reales')
comprobar(filas[3].pending == [] and filas[0].pending == [], 'sin envíos ni cambios no hay nada pendiente')

# Una celda que cambia sin envíos en la ventana (enviado antes del corte, juzgado después) también se descubre.
tardia = fila(0, [None], [AC(10)], [0])
comprobar(tardia.pending == [0], 'celda cambiada sin envíos en la ventana: se descubre igual')

# Empate: la fila que sólo iguala a la de arriba no la adelanta.
empate = [fila(0, [AC(100)], [AC(100)], [0]), fila(1, [None], [AC(100)], [1])]
_o, _r, p_empate = R.plan_reveal(empate, 0, intermedios)
comprobar(p_empate[0]['to'] == 1 and 'order' not in p_empate[0] and p_empate[0].get('ranks') == [1, 1],
          'un empate exacto no adelanta a la fila de arriba, pero comparte rango')

# ---------------------------------------------------------------------------------------------------------------
print('-- propiedades sobre 800 marcadores aleatorios (también con formatos que pueden bajar)')

rng = random.Random(20260916)
malos = []
for caso in range(800):
    n, p = rng.randint(0, 25), rng.randint(0, 8)
    monotono = caso % 2 == 0
    rows = []
    for i in range(n):
        congeladas, finales, pend = [], [], []
        for j in range(p):
            c = rng.choice([None, AC(rng.randint(1, 18000), rng.randint(0, 3)), WA(rng.randint(1, 18000))])
            f = c
            k = 0
            if rng.random() < 0.35:
                k = rng.randint(0, 3)
                if monotono:
                    f = c if (c and c['points']) else rng.choice([c, AC(18000 + rng.randint(1, 3600), 1),
                                                                  WA(18000, 2)])
                else:
                    f = rng.choice([None, AC(rng.randint(1, 20000)), WA(rng.randint(1, 20000), 4)])
            congeladas.append(c)
            finales.append(f)
            pend.append(k)
        rows.append(fila(i, congeladas, finales, pend))

    orden0, rangos0, pasos = R.plan_reveal(rows, 0, intermedios)
    total_pendientes = sum(len(r.pending) for r in rows)
    reveals = [s for s in pasos if s['kind'] == 'reveal']
    finals = [s for s in pasos if s['kind'] == 'finalize']

    # Reproducir los pasos como lo hace el navegador.
    orden, rangos, cerradas, descubiertas = list(orden0), list(rangos0), set(), set()
    ok_caso = True
    for s in pasos:
        cursor = max(i for i, r in enumerate(orden) if r not in cerradas)
        if orden[cursor] != s['row']:
            ok_caso = False
        if s['kind'] == 'reveal':
            if (s['row'], s['column']) in descubiertas or s['column'] not in rows[s['row']].pending:
                ok_caso = False
            descubiertas.add((s['row'], s['column']))
            orden = s.get('order', orden)
            rangos = s.get('ranks', rangos)
            if orden[s['to']] != s['row']:
                ok_caso = False
        else:
            if s['row'] in cerradas:
                ok_caso = False
            if any((s['row'], c) not in descubiertas for c in rows[s['row']].pending):
                ok_caso = False
            cerradas.add(s['row'])

    claves = {r.index: R.sort_key(r.final_totals, 0) for r in rows}
    if not (ok_caso and len(reveals) == total_pendientes and len(finals) == n and
            [claves[i] for i in orden] == sorted(claves[i] for i in orden) and
            rangos == R.ranks_for(orden, claves) and sorted(orden) == list(range(n))):
        malos.append(caso)
comprobar(not malos, 'cada celda pendiente se descubre una vez, cada fila se cierra una vez, siempre la de más '
                     'abajo sin cerrar, y el marcador final queda ordenado por los totales reales%s'
          % ('' if not malos else ' (casos %s)' % malos[:10]))

# ---------------------------------------------------------------------------------------------------------------
print('-- armado de datos sin base')

problemas = [R.RevealProblem(11, 1, 'A', 'Suma'), R.RevealProblem(12, 1, 'B', 'Grafos')]
entradas = [
    R.RevealEntry(1, 'Equipo </script><b>x</b>', 'eq1', 'UAEH', 2, 1500 + 2400, 900,
                  {'11': AC(600, 2), '12': AC(900)}, frozen_at='2026-09-16', frozen_score=1,
                  frozen_cumtime=600 + 2400, frozen_tiebreaker=600, frozen_format_data={'11': AC(600, 2)}),
    R.RevealEntry(2, 'Sin cambios', 'eq2', '', 1, 300, 300, {'11': AC(300)}),
    R.RevealEntry(3, 'Nadie', 'eq3', None, 0, 0, 0, None),
]
datos, inexactos = R.assemble_reveal('icpc', {'penalty': 20}, 0, problemas, entradas, {(1, 12): 3})
comprobar(inexactos == 0, 'los totales guardados cuadran con el cálculo del formato')
comprobar(datos['rows'][0]['pending'] == [-1, 3], 'marca de pendientes: -1 nada, 3 envíos ocultos')
comprobar(datos['rows'][0]['score'] == '1' and datos['rows'][0]['time'] == '00:50:00',
          'la fila empieza con los totales congelados')
comprobar(datos['rows'][1]['pending'] == [-1, -1] and datos['rows'][1]['frozen'] == datos['rows'][1]['final'],
          'sin copia congelada, la fila congelada es la real')
comprobar(datos['rows'][2]['organization'] == '' and datos['rows'][2]['frozen'][0]['state'] == 'empty',
          'participante sin organización ni envíos')
reveal = [s for s in datos['steps'] if s['kind'] == 'reveal']
comprobar(len(reveal) == 1 and reveal[0]['score'] == '2' and reveal[0]['time'] == '01:05:00' and
          'totals' not in reveal[0], 'el paso lleva los totales como texto y no como números')
comprobar(datos['order'][0] == 1 and datos['steps'][-1]['kind'] == 'finalize', 'orden y cierre')
json.dumps(datos)
comprobar(True, 'todo el armado se serializa a JSON')
datos2, _ = R.assemble_reveal('icpc', {'penalty': 20}, 0, problemas, entradas, {(1, 12): 3})
comprobar(datos['signature'] == datos2['signature'], 'la firma es estable con los mismos datos')
datos3, _ = R.assemble_reveal('icpc', {'penalty': 20}, 0, problemas, entradas, {(1, 12): 2})
comprobar(datos['signature'] != datos3['signature'], 'y cambia si cambian los datos')
_, inexactos_mal = R.assemble_reveal('icpc', {'penalty': 20}, 0, problemas,
                                     [R.RevealEntry(9, 'x', 'x', '', 5, 0, 0, {'11': AC(10)})], {})
comprobar(inexactos_mal == 1, 'unos totales que no cuadran se cuentan para avisar')
_, inexactos_desc = R.assemble_reveal('inventado', {}, 0, problemas, entradas, {})
comprobar(inexactos_desc == 3, 'con un formato desconocido se avisa de todas las filas')

con_avatar = R.RevealEntry(4, 'A', 'a', '', 0, 0, 0, None, avatar='https://www.gravatar.com/avatar/x?d=identicon')
datos_av, _ = R.assemble_reveal('icpc', {'penalty': 20}, 0, problemas, [con_avatar], {})
comprobar(datos_av['rows'][0]['avatar'].startswith('https://www.gravatar.com/') and datos['rows'][0]['avatar'] == '',
          'avatar de Gravatar en la fila, vacío si no hay')

print('-- formatos por puntos: IOI y ECOO')

pioi = [R.RevealProblem(21, 100, 'A', 'x'), R.RevealProblem(22, 100, 'B', 'y')]
ioi = [
    R.RevealEntry(1, 'Uno', 'u1', '', 140, 0, 0, {'21': {'points': 100, 'time': 0}, '22': {'points': 40, 'time': 0}},
                  frozen_at='x', frozen_score=110, frozen_cumtime=0, frozen_tiebreaker=0,
                  frozen_format_data={'21': {'points': 100, 'time': 0}, '22': {'points': 10, 'time': 0}}),
    R.RevealEntry(2, 'Dos', 'u2', '', 125.5, 0, 0, {'21': {'points': 25.5, 'time': 0},
                                                    '22': {'points': 100, 'time': 0}},
                  frozen_at='x', frozen_score=25.5, frozen_cumtime=0, frozen_tiebreaker=0,
                  frozen_format_data={'21': {'points': 25.5, 'time': 0}}),
]
d_ioi, inex_ioi = R.assemble_reveal('ioi16', {'cumtime': False}, 2, pioi, ioi, {(1, 22): 2, (2, 22): 1})
comprobar(inex_ioi == 0 and not d_ioi['showTime'], 'ioi16: totales cuadran y no se muestra tiempo')
comprobar(d_ioi['rows'][1]['frozen'][1]['state'] == 'empty' and d_ioi['rows'][1]['final'][1]['state'] == 'full' and
          d_ioi['rows'][0]['final'][1]['state'] == 'partial', 'ioi16: celdas completas, parciales y vacías')
pasos_ioi = [(s['kind'], s['row']) for s in d_ioi['steps']]
comprobar(pasos_ioi == [('reveal', 1), ('reveal', 0), ('finalize', 1), ('finalize', 0)] and
          d_ioi['steps'][0].get('order') == [1, 0] and d_ioi['steps'][1].get('order') == [0, 1],
          'ioi16: «Dos» descubre y sube; «Uno», ahora abajo, descubre la suya y recupera el primer lugar')
comprobar(d_ioi['steps'][0]['score'] in ('125.5', '125,5', '125.50', '125,50'), 'ioi16: puntos con decimales')

pecoo = [R.RevealProblem(31, 50, 'A', 'x')]
ecoo_e = [R.RevealEntry(1, 'Uno', 'u1', '', 50 + 10 + 12, 0, 0, {'31': {'points': 50, 'time': 100, 'bonus': 22}},
                        frozen_at='x', frozen_score=20, frozen_cumtime=0, frozen_tiebreaker=0,
                        frozen_format_data={'31': {'points': 20, 'time': 50, 'bonus': 0}})]
d_ecoo, inex_ecoo = R.assemble_reveal('ecoo', {'cumtime': False}, 0, pecoo, ecoo_e, {(1, 31): 1})
comprobar(inex_ecoo == 0 and d_ecoo['rows'][0]['final'][0] == {'state': 'full', 'main': '50', 'sub': '+22'},
          'ecoo: el bono entra en el total y se muestra en la celda')

# ---------------------------------------------------------------------------------------------------------------
print('-- rutas, vistas y administración')

comprobar(reverse('contest_reveal', args=['final2026']) == '/contest/final2026/reveal', 'ruta de la página')
comprobar(reverse('contest_reveal_publish', args=['final2026']) == '/contest/final2026/reveal/publish',
          'ruta de publicar')
fabrica = RequestFactory()
peticion = fabrica.get('/contest/x/reveal')
peticion.user = AnonymousUser()
respuesta = R.contest_reveal(peticion, contest='x')
comprobar(respuesta.status_code == 302 and '/accounts/login/' in respuesta['Location'],
          'anónimo: redirige al login sin consultar nada')
peticion = fabrica.get('/contest/x/reveal/publish')
peticion.user = AnonymousUser()
comprobar(R.contest_reveal_publish(peticion, contest='x').status_code == 405, 'publicar por GET: 405')
peticion = fabrica.post('/contest/x/reveal/publish')
peticion.user = AnonymousUser()
comprobar(R.contest_reveal_publish(peticion, contest='x').status_code == 302, 'publicar anónimo: al login')

from django.contrib import admin  # noqa: E402
from judge.models import Contest  # noqa: E402

modelo_admin = admin.site._registry[Contest]
comprobar('reveal_link' in modelo_admin.list_display, 'columna en la lista de concursos')
comprobar(modelo_admin.reveal_link(Contest(key='a', freeze_minutes=None)) == '', 'sin congelación: sin enlace')
enlace = str(modelo_admin.reveal_link(Contest(key='final', freeze_minutes=60)))
comprobar('href="/contest/final/reveal"' in enlace and 'target="_blank"' in enlace, 'con congelación: enlace')
comprobar(hasattr(modelo_admin, 'reveal_scoreboard') and 'freeze_minutes' in str(modelo_admin.fieldsets),
          'la congelación sigue en la administración')
try:
    get_template('admin/judge/contest/change_form.html')
    comprobar(True, 'la plantilla del formulario de administración compila')
except Exception as error:  # noqa: B902
    comprobar(False, 'la plantilla del formulario de administración compila: %r' % error)

# ---------------------------------------------------------------------------------------------------------------
print('-- plantilla y catálogo')

translation.activate('es')
comprobar(R.reveal_labels()['frozen'] == 'Marcador congelado', 'catálogo: texto del script')
comprobar(translation.gettext('Reveal scoreboard') == 'Revelar marcador', 'catálogo: botón de administración')
comprobar(translation.gettext('Rankings') == 'Valoraciones' and
          translation.gettext('scoreboard freeze') == 'congelación del marcador',
          'catálogo: traducciones previas y de la congelación intactas')
comprobar('juzgando' in translation.ngettext('%d submission is still being judged. Wait for it before revealing.',
                                             '%d submissions are still being judged. Wait for them before revealing.',
                                             2), 'catálogo: plural')


class ConcursoFalso:
    id = 5
    key = 'final2026'
    name = 'Final <CPC>'


contexto = R.reveal_context(ConcursoFalso(), dict(datos, contest='final2026', published=False, canPublish=True,
                                                  publishUrl='/contest/final2026/reveal/publish'),
                            [('warning', 'Aviso <i>'), ('error', 'Error')])
html = render_to_string('contest/reveal.html', contexto)
comprobar('<script id="reveal-data" type="application/json">' in html, 'los datos van en un bloque JSON')
comprobar('</script><b>' not in html and 'Equipo \\u003C/script\\u003E' in html,
          'un nombre con HTML no rompe el bloque JSON')
comprobar('Final &lt;CPC&gt;' in html and 'Aviso &lt;i&gt;' in html, 'nombre del concurso y avisos escapados')
comprobar('/admin/judge/contest/5/change/' in html, 'el aviso de error enlaza a los ajustes del concurso')
comprobar('Revelación del marcador' in html and 'Participante' in html, 'la página sale en español')
comprobar('{%' not in html and '{{' not in html, 'no queda sintaxis de plantilla sin procesar')
comprobar('{% compress' not in open('templates/contest/reveal.html', encoding='utf-8').read(),
          'la plantilla no usa compresión: no hay manifiesto que regenerar')
translation.deactivate()

print()
print('%d correctas, %d fallidas' % (ok, fallos))
sys.exit(1 if fallos else 0)
