"""Pruebas sintéticas de la congelación del marcador. No tocan la base ni producción.

Se ejecutan sobre el árbol del laboratorio, que es una copia del código con los archivos candidatos
puestos en su sitio. Todo son objetos construidos a mano: ninguna prueba guarda nada, y la única
base configurada es un sqlite en memoria que se queda vacío.

    python tests/contest_features/run.py
"""
import sys
from datetime import datetime, timedelta, timezone as tz

import django

django.setup()

from django.utils.safestring import SafeString  # noqa: E402
from judge.models import Contest, ContestParticipation, Submission  # noqa: E402
from judge.views import contests as V  # noqa: E402

AHORA = datetime(2026, 9, 15, 18, 0, tzinfo=tz.utc)
ok = fallos = 0


def comprobar(condicion, descripcion):
    global ok, fallos
    if condicion:
        ok += 1
        print('  OK    %s' % descripcion)
    else:
        fallos += 1
        print('  FALLA %s' % descripcion)


class Usuario:
    """Lo mínimo que miran `is_editable_by` y compañía."""

    def __init__(self, autenticado=True, permisos=(), profile_id=1):
        self.is_authenticated = autenticado
        self._permisos = set(permisos)
        self.profile = type('P', (), {'id': profile_id})()

    def has_perm(self, permiso):
        return permiso in self._permisos


def concurso(minutos=60, revelado=False, limite=None, ahora=AHORA, editores=()):
    c = Contest(id=1, key='lab', name='Laboratorio',
                start_time=datetime(2026, 9, 15, 14, 0, tzinfo=tz.utc),
                end_time=datetime(2026, 9, 15, 19, 0, tzinfo=tz.utc),
                time_limit=limite, freeze_minutes=minutos, scoreboard_revealed=revelado)
    c.__dict__['_now'] = ahora
    c.__dict__['editor_ids'] = list(editores)
    return c


def participacion(c, virtual=0, real_start=None, ahora=AHORA, **campos):
    p = ContestParticipation(id=7, contest=c, virtual=virtual,
                             real_start=real_start or c.start_time, score=10, cumtime=500, tiebreaker=3,
                             format_data={'1': {'points': 1, 'time': 5}}, **campos)
    p.__dict__['_now'] = ahora
    return p


print('== 1. ventana de congelación del concurso ==')
c = concurso()
comprobar(c.freeze_delta == timedelta(minutes=60), 'sesenta minutos se leen como una hora')
comprobar(c.submission_freeze_cutoff == datetime(2026, 9, 15, 18, 0, tzinfo=tz.utc),
          'el corte es el final menos la congelación')
comprobar(concurso(minutos=None).freeze_delta is None, 'sin minutos no hay congelación')
comprobar(concurso(minutos=None).submission_freeze_cutoff is None, 'sin minutos no hay corte')
comprobar(concurso(ahora=AHORA - timedelta(minutes=1)).freeze_started is False, 'un minuto antes aún no empezó')
comprobar(c.freeze_started is True, 'justo en el corte ya empezó')
comprobar(concurso(limite=timedelta(hours=2)).submission_freeze_cutoff ==
          datetime(2026, 9, 15, 15, 0, tzinfo=tz.utc),
          'con límite por usuario el corte se adelanta al más temprano posible')

print()
print('== 2. quién ve el marcador congelado ==')
comprobar(c.is_frozen_for(Usuario(autenticado=False)) is True, 'un anónimo lo ve congelado')
comprobar(c.is_frozen_for(Usuario()) is True, 'un concursante lo ve congelado')
comprobar(c.is_frozen_for(Usuario(permisos=['judge.edit_all_contest'])) is False,
          'quien puede editar cualquier concurso ve el real')
comprobar(concurso(editores=[1]).is_frozen_for(Usuario(permisos=['judge.edit_own_contest'], profile_id=1)) is False,
          'el autor del concurso ve el real')
comprobar(concurso(editores=[2]).is_frozen_for(Usuario(permisos=['judge.edit_own_contest'], profile_id=1)) is True,
          'el autor de OTRO concurso no')
comprobar(c.is_frozen_for(Usuario(permisos=['judge.view_all_submission'])) is True,
          'ver todos los envíos no es editar el concurso: sigue congelado')
comprobar(concurso(revelado=True).is_frozen_for(Usuario()) is False, 'revelado deja de congelar')
comprobar(concurso(minutos=None).is_frozen_for(Usuario()) is False, 'sin congelación configurada, nada cambia')
comprobar(concurso(ahora=AHORA + timedelta(days=3)).is_frozen_for(Usuario()) is True,
          'tres días después del final sigue congelado hasta que lo revelen')

print()
print('== 3. cada participación con su propio tramo ==')
viva = participacion(c)
comprobar(viva.freeze_starts_at == datetime(2026, 9, 15, 18, 0, tzinfo=tz.utc),
          'la participación en vivo se congela con el concurso')
comprobar(viva.is_frozen is True, 'y ahora mismo está congelada')
virtual = participacion(c, virtual=1, real_start=datetime(2026, 9, 15, 17, 0, tzinfo=tz.utc))
comprobar(virtual.freeze_starts_at == datetime(2026, 9, 15, 21, 0, tzinfo=tz.utc),
          'la virtual se congela en SUS últimos sesenta minutos, no en los del concurso')
comprobar(virtual.is_frozen is False, 'y todavía no le toca')
comprobar(participacion(concurso(minutos=None)).freeze_starts_at is None, 'sin congelación no hay tramo')

print()
print('== 4. la copia se toma una vez y no se vuelve a tocar ==')
guardados = []
p = participacion(c)
p.save = lambda *a, **k: guardados.append(k.get('update_fields'))
p.freeze_scoreboard()
comprobar(p.frozen_at is not None and p.frozen_score == 10 and p.frozen_cumtime == 500,
          'entrando congelada, se copia el marcador de ese momento')
comprobar(p.frozen_format_data == {'1': {'points': 1, 'time': 5}}, 'y también el detalle por problema')
comprobar(guardados and set(guardados[0]) == {'frozen_score', 'frozen_cumtime', 'frozen_tiebreaker',
                                              'frozen_format_data', 'frozen_at'},
          'se guardan sólo los cinco campos de la copia')
p.score = 99
p.freeze_scoreboard()
comprobar(p.frozen_score == 10 and len(guardados) == 1, 'la segunda llamada no pisa la copia')

antes = AHORA - timedelta(minutes=30)
fuera = participacion(concurso(ahora=antes), ahora=antes)
fuera.save = lambda *a, **k: guardados.append('no deberia')
fuera.freeze_scoreboard()
comprobar(fuera.frozen_at is None and len(guardados) == 1, 'fuera de la ventana no se copia nada')

revelada = participacion(concurso(revelado=True))
revelada.save = lambda *a, **k: guardados.append('no deberia')
revelada.freeze_scoreboard()
comprobar(revelada.frozen_at is None and len(guardados) == 1, 'con el marcador revelado tampoco')

print()
print('== 5. la vista congelada de una participación ==')
sin_copia = participacion(c)
vista = V.FrozenParticipation(sin_copia)
comprobar((vista.score, vista.cumtime, vista.tiebreaker) == (10, 500, 3),
          'sin copia se ven los campos vivos, que son los de la congelación')
con_copia = participacion(c, frozen_score=4, frozen_cumtime=120, frozen_tiebreaker=1,
                          frozen_format_data={'1': {'points': 0, 'time': 1}}, frozen_at=AHORA)
vista2 = V.FrozenParticipation(con_copia)
comprobar((vista2.score, vista2.cumtime, vista2.tiebreaker) == (4, 120, 1), 'con copia se ve la copia')
comprobar(vista2.format_data == {'1': {'points': 0, 'time': 1}}, 'el detalle por problema también')
comprobar(vista2.id == 7 and vista2.virtual == 0, 'el resto se delega intacto')
try:
    vista2.score = 1000
    comprobar(False, 'escribir en la vista congelada tiene que fallar')
except AttributeError:
    comprobar(True, 'escribir en la vista congelada falla')
for metodo in ('save', 'recompute_results'):
    try:
        getattr(vista2, metodo)()
        comprobar(False, '%s() tiene que fallar' % metodo)
    except TypeError:
        comprobar(True, '%s() sobre una vista congelada falla' % metodo)
comprobar(con_copia.score == 10, 'y la participación real no se tocó')

print()
print('== 6. la marca de envíos pendientes ==')
celda = SafeString('<td class="full-score"><a href="/x">100<div class="solving-time">1:00</div></a></td>')
comprobar(V.with_pending_marker(celda, 0) is celda, 'sin pendientes la celda no se toca')
marcada = str(V.with_pending_marker(celda, 1))
comprobar(marcada.endswith('</span></td>') and 'frozen-pending' in marcada, 'la marca entra dentro de la celda')
comprobar(marcada.count('<td') == 1 and marcada.count('</td>') == 1, 'y no rompe la celda')
comprobar('>?<' in marcada, 'un pendiente se dibuja como interrogación')
comprobar('>?3<' in str(V.with_pending_marker(celda, 3)), 'varios llevan la cuenta')
vacia = V.with_pending_marker(SafeString('<td></td>'), 2)
comprobar(str(vacia).startswith('<td>') and 'frozen-pending' in str(vacia), 'una celda vacía también se marca')
raro = SafeString('cualquier otra cosa')
comprobar(V.with_pending_marker(raro, 2) is raro, 'lo que no parece una celda se devuelve igual')

print()
print('== 7. qué envíos tapa la congelación ==')


def envio(contest, propietario=2, cuando=None):
    s = Submission(id=1, user_id=propietario, date=cuando or datetime(2026, 9, 15, 18, 30, tzinfo=tz.utc))
    s.contest_object = contest
    return s


espectador = Usuario(profile_id=1)
comprobar(envio(None).is_hidden_by_freeze(espectador) is False, 'un envío fuera de concurso nunca se tapa')
comprobar(envio(concurso(minutos=None)).is_hidden_by_freeze(espectador) is False, 'sin congelación tampoco')
comprobar(envio(concurso(revelado=True)).is_hidden_by_freeze(espectador) is False, 'revelado tampoco')
comprobar(envio(c).is_hidden_by_freeze(espectador) is True, 'el envío ajeno posterior al corte se tapa')
comprobar(envio(c, propietario=1).is_hidden_by_freeze(espectador) is False, 'el propio nunca se tapa')
comprobar(envio(c, cuando=datetime(2026, 9, 15, 17, 59, tzinfo=tz.utc)).is_hidden_by_freeze(espectador) is False,
          'lo anterior al corte se sigue viendo')
comprobar(envio(c).is_hidden_by_freeze(Usuario(permisos=['judge.edit_all_contest'])) is False,
          'el jurado lo ve')
comprobar(envio(c).is_hidden_by_freeze(Usuario(autenticado=False)) is True, 'un anónimo no')

print()
print('== 8. el orden del marcador público ==')
capturado = {}


def espia(contest, problems, queryset, frozen=False, own_profile_id=None):
    capturado['orden'] = queryset.query.order_by
    capturado['anotaciones'] = set(queryset.query.annotations)
    return []


original = V.base_contest_ranking_list
V.base_contest_ranking_list = espia
try:
    V.contest_ranking_list(c, [], frozen=True)
    comprobar(capturado['orden'] == ('is_disqualified', '-shown_score', 'shown_cumtime', 'shown_tiebreaker', 'id'),
              'congelado se ordena por los valores congelados')
    comprobar(capturado['anotaciones'] == {'shown_score', 'shown_cumtime', 'shown_tiebreaker'},
              'con las tres anotaciones que los calculan')
    comprobar('submission_cnt' not in capturado['anotaciones'],
              'y sin contar envíos, que desempataría con lo que se está tapando')
    V.contest_ranking_list(c, [], frozen=False)
    comprobar(capturado['orden'] == ('is_disqualified', '-score', 'cumtime', 'tiebreaker', '-submission_cnt'),
              'en vivo se ordena como siempre')
    comprobar('submission_cnt' in capturado['anotaciones'], 'y conserva su desempate por número de envíos')
finally:
    V.base_contest_ranking_list = original

print()
print('== 9. la cuenta de pendientes usa el corte de CADA participación ==')


class ConsultaFalsa:
    def __init__(self, filas):
        self.filas = filas

    def filter(self, *a, **k):
        return self

    def values_list(self, *campos):
        return self.filas


tarde = datetime(2026, 9, 15, 18, 30, tzinfo=tz.utc)
temprano = datetime(2026, 9, 15, 16, 0, tzinfo=tz.utc)
p_viva = participacion(c)
p_virtual = participacion(c, virtual=1, real_start=datetime(2026, 9, 15, 17, 0, tzinfo=tz.utc))
p_virtual.id = 8
filas = [
    (7, 100, tarde, 'D'),
    (7, 100, tarde, 'D'),
    (7, 101, temprano, 'D'),
    (7, 102, temprano, 'G'),
    (8, 100, tarde, 'D'),
]
objetos_original = V.ContestSubmission.objects
V.ContestSubmission.objects = ConsultaFalsa(filas)
try:
    mapa = V.frozen_pending_map(c, [p_viva, p_virtual])
finally:
    V.ContestSubmission.objects = objetos_original
comprobar(mapa.get((7, 100)) == 2, 'dos envíos posteriores al corte cuentan dos')
comprobar(mapa.get((7, 101)) is None, 'uno anterior y ya juzgado no cuenta')
comprobar(mapa.get((7, 102)) == 1, 'uno anterior pero todavía en la cola sí cuenta')
comprobar(mapa.get((8, 100)) is None, 'lo de la virtual no cuenta: su ventana no ha empezado')


print()
print('== 10. el filtro que tapa los envíos en las listas y en la API ==')
from django.core.cache import cache  # noqa: E402
from django.db.models import Q  # noqa: E402

corte = datetime(2026, 9, 15, 18, 0, tzinfo=tz.utc)


def con_ventanas(ventanas, usuario, editables=None):
    """Siembra la caché para no consultar la base, y espía la consulta de editores si hace falta."""
    cache.set(Contest.FROZEN_WINDOWS_CACHE_KEY, ventanas, 30)

    class GestorFalso:
        def filter(self, *a, **k):
            return self

        def values_list(self, *campos, **k):
            return list(editables or [])

    gestor = Contest.objects
    if editables is not None:
        Contest.objects = GestorFalso()
    try:
        return Contest.frozen_submission_filter(usuario)
    finally:
        Contest.objects = gestor
        cache.delete(Contest.FROZEN_WINDOWS_CACHE_KEY)


filtro = con_ventanas([(1, corte)], Usuario(autenticado=False))
comprobar(filtro is not None and set(filtro.children) == {('contest_object_id', 1), ('date__gte', corte)},
          'a un anónimo se le tapan los envíos del concurso posteriores al corte')
comprobar(con_ventanas([], Usuario()) is None, 'sin concursos congelados el filtro no existe')
comprobar(con_ventanas([(1, corte)], Usuario(permisos=['judge.edit_all_contest'])) is None,
          'quien edita cualquier concurso no tiene nada tapado')
comprobar(con_ventanas([(1, corte)], Usuario(permisos=['judge.view_all_submission'])) is not None,
          'ver todos los envíos no levanta la congelación')
comprobar(con_ventanas([(1, corte)], Usuario(permisos=['judge.edit_own_contest'], profile_id=1),
                       editables=[1]) is None,
          'el autor de ESE concurso no tiene nada tapado')
comprobar(con_ventanas([(1, corte)], Usuario(permisos=['judge.edit_own_contest'], profile_id=1),
                       editables=[]) is not None,
          'el autor de otro concurso sí')
dos = con_ventanas([(1, corte), (2, corte + timedelta(hours=1))], Usuario())
comprobar(dos is not None and len(dos.children) == 2 and dos.connector == 'OR',
          'dos concursos congelados se combinan con OR, cada uno con su corte')

print()
print('== 11. la marca sólo en las filas propias ==')
preguntado = {}


def espia_pendientes(contest, participations):
    preguntado['ids'] = [p.id for p in participations]
    return {}


def perfil_falso(contest, participation, problems, frozen=False, pending=None):
    preguntado.setdefault('filas', []).append((participation.id, pending))
    return participation


class ConsultaLista:
    def __init__(self, filas):
        self.filas = filas

    def select_related(self, *a, **k):
        return self

    def defer(self, *a, **k):
        return self

    def __iter__(self):
        return iter(self.filas)


mia = participacion(c)
mia.id, mia.user_id = 11, 1
ajena = participacion(c)
ajena.id, ajena.user_id = 12, 2

orig_map, orig_perfil = V.frozen_pending_map, V.make_contest_ranking_profile
V.frozen_pending_map, V.make_contest_ranking_profile = espia_pendientes, perfil_falso
try:
    preguntado.clear()
    V.base_contest_ranking_list(c, [], ConsultaLista([mia, ajena]), frozen=True, own_profile_id=1)
    comprobar(preguntado.get('ids') == [11], 'sólo se consultan los pendientes de la fila propia')
    comprobar(all(p is not None for _fila, p in preguntado['filas']),
              'el mapa se pasa a las dos filas, pero sólo tiene claves de la propia')

    preguntado.clear()
    V.base_contest_ranking_list(c, [], ConsultaLista([mia, ajena]), frozen=True, own_profile_id=None)
    comprobar('ids' not in preguntado, 'un anónimo no genera ninguna consulta de pendientes')
    comprobar(all(p is None for _fila, p in preguntado['filas']), 'y ninguna fila lleva marca')

    preguntado.clear()
    V.base_contest_ranking_list(c, [], ConsultaLista([ajena]), frozen=True, own_profile_id=1)
    comprobar('ids' not in preguntado, 'si ninguna fila es propia, tampoco se consulta nada')

    preguntado.clear()
    V.base_contest_ranking_list(c, [], ConsultaLista([mia, ajena]), frozen=False, own_profile_id=1)
    comprobar('ids' not in preguntado and all(p is None for _f, p in preguntado['filas']),
              'sin congelación no hay marcas ni consulta')
finally:
    V.frozen_pending_map, V.make_contest_ranking_profile = orig_map, orig_perfil

print()
print('== 12. el canal de eventos no difunde lo congelado ==')
comprobar(envio(c).in_frozen_window is True, 'un envío dentro de la ventana no se difunde')
comprobar(envio(c, propietario=1).in_frozen_window is True,
          'tampoco el propio: el canal es una difusión a todos, no se puede dirigir a su autor')
comprobar(envio(c, cuando=datetime(2026, 9, 15, 17, 59, tzinfo=tz.utc)).in_frozen_window is False,
          'lo anterior al corte se difunde igual que siempre')
comprobar(envio(None).in_frozen_window is False, 'lo que no es de concurso se difunde igual que siempre')
comprobar(envio(concurso(revelado=True)).in_frozen_window is False, 'y tras revelar, todo vuelve a difundirse')

from judge.bridge.judge_handler import JudgeHandler  # noqa: E402


class GestorUnConcurso:
    def __init__(self, contest):
        self.contest = contest

    def filter(self, *a, **k):
        return self

    def only(self, *a, **k):
        return self

    def first(self):
        return self.contest


def pregunta_al_puente(contest, cuando):
    gestor = Contest.objects
    import judge.bridge.judge_handler as H
    H.Contest.objects = GestorUnConcurso(contest)
    try:
        return JudgeHandler._in_frozen_window(None, {'contest_object_id': 1 if contest else None, 'date': cuando})
    finally:
        H.Contest.objects = gestor


comprobar(pregunta_al_puente(c, tarde) is True, 'el puente calla el envío de dentro de la ventana')
comprobar(pregunta_al_puente(c, temprano) is False, 'y difunde el de antes del corte')
comprobar(pregunta_al_puente(concurso(revelado=True), tarde) is False, 'tras revelar, el puente vuelve a difundir')
comprobar(pregunta_al_puente(None, tarde) is False, 'sin concurso no hay nada que callar')

print()
print('Resultado: %d correctas, %d fallidas.' % (ok, fallos))
sys.exit(1 if fallos else 0)
