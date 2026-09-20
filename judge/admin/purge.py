# -*- coding: utf-8 -*-
"""Borrado total de equipos y concursos, reservado a la cuenta dueña del servidor.

El sitio tiene cinco llaves foráneas `PROTECT` puestas a propósito para que nadie
pierda el historial de un concurso por accidente:

* `ContestParticipation.team` y `ContestTeamLocation.team` protegen al equipo;
* `ContestClarification.team_participation` protege a la participación, y es la que
  impide borrar un concurso en el que jugó un equipo que preguntó algo;
* `Team.owner` y `ContestTeamMember.profile` protegen al perfil.

Aquí no se quita ninguna: se conservan como red de seguridad para todo el mundo y
se añade un camino explícito que desmonta las dependencias en orden antes de
borrar. Ese camino pasa por una página de confirmación que dice, contado, qué se va
a destruir, y sólo lo ve y lo puede ejecutar quien figura en `CPC_SERVER_OWNERS`.

Lo que **no** se borra: los envíos en sí. Se borra `ContestSubmission`, que es el
vínculo del envío con el concurso; el código enviado y su resultado siguen en el
historial personal de quien lo mandó.
"""
from collections import OrderedDict

from django.contrib import messages
from django.core.exceptions import PermissionDenied
from django.db import transaction
from django.template.response import TemplateResponse
from django.utils.translation import gettext, gettext_lazy as _

from judge.admin_owner import OwnerOnlyActionsMixin, is_server_owner
from judge.models import ContestClarification, ContestParticipation, ContestSubmission
from judge.models.balloon import ContestBalloonAction
from judge.models.team import ContestTeamLocation, ContestTeamMember, TeamInvitation, TeamMembership

CONFIRMATION_TEMPLATE = 'admin/judge/purge_confirmation.html'


def team_plan(team):
    """Qué desaparece al borrar este equipo, en el orden en que hay que quitarlo."""
    participations = ContestParticipation.objects.filter(team=team)
    return OrderedDict((
        (_('Aclaraciones preguntadas por el equipo'),
         ContestClarification.objects.filter(team_participation__team=team)),
        (_('Movimientos de globo'), ContestBalloonAction.objects.filter(participation__team=team)),
        (_('Ubicaciones de globos'), ContestTeamLocation.objects.filter(team=team)),
        (_('Envíos dentro de concursos'), ContestSubmission.objects.filter(participation__team=team)),
        (_('Listas inscritas'), ContestTeamMember.objects.filter(participation__team=team)),
        (_('Participaciones en concursos'), participations),
        (_('Integrantes'), TeamMembership.objects.filter(team=team)),
        (_('Invitaciones'), TeamInvitation.objects.filter(team=team)),
    ))


def purge_team(team):
    """Desmonta y borra. Las cascadas hacen casi todo; aquí sólo van las protegidas."""
    with transaction.atomic():
        ContestClarification.objects.filter(team_participation__team=team).delete()
        ContestTeamLocation.objects.filter(team=team).delete()
        # Arrastra ContestTeamMember, ContestSubmission y BalloonAction por cascada.
        ContestParticipation.objects.filter(team=team).delete()
        # Arrastra TeamMembership y TeamInvitation por cascada.
        team.delete()


def contest_plan(contest):
    participations = ContestParticipation.objects.filter(contest=contest)
    return OrderedDict((
        (_('Aclaraciones'), ContestClarification.objects.filter(contest=contest)),
        (_('Movimientos de globo'), ContestBalloonAction.objects.filter(participation__contest=contest)),
        (_('Envíos dentro del concurso'), ContestSubmission.objects.filter(participation__contest=contest)),
        (_('Listas inscritas'), ContestTeamMember.objects.filter(participation__contest=contest)),
        (_('Participaciones'), participations),
        (_('De ellas, de equipo'), participations.filter(team__isnull=False)),
        (_('Problemas del concurso'), contest.contest_problems.all()),
    ))


def purge_contest(contest):
    with transaction.atomic():
        # La aclaración de un equipo protege a su participación, y la participación
        # es lo que el borrado del concurso intenta arrastrar. Por eso va primero,
        # incluida la que apunte a una participación de este concurso desde otro.
        ContestClarification.objects.filter(contest=contest).delete()
        ContestClarification.objects.filter(team_participation__contest=contest).delete()
        contest.delete()


class PurgeMixin(OwnerOnlyActionsMixin):
    """Acción destructiva con página de confirmación previa.

    La subclase define `purge_action_name`, `purge_plan` y `purge_object`.
    """
    purge_title = _('Eliminar definitivamente')
    purge_warning = _('Esto no se puede deshacer y no queda copia.')

    def purge_plan(self, obj):
        raise NotImplementedError

    def purge_object(self, obj):
        raise NotImplementedError

    def run_purge(self, request, queryset):
        if not is_server_owner(request):
            # El nombre de la acción viaja en el POST: esconderla no basta.
            raise PermissionDenied(gettext('Sólo la cuenta dueña del servidor puede borrar aquí.'))

        objects = list(queryset)
        if request.POST.get('confirmar') != 'si':
            rows = []
            for obj in objects:
                rows.append({
                    'label': str(obj),
                    'lines': [(label, items.count()) for label, items in self.purge_plan(obj).items()],
                })
            context = {
                **self.admin_site.each_context(request),
                'title': self.purge_title,
                'warning': self.purge_warning,
                'rows': rows,
                'objects': objects,
                'action_name': self.purge_action_name,
                'opts': self.model._meta,
                'media': self.media,
            }
            return TemplateResponse(request, CONFIRMATION_TEMPLATE, context)

        names = []
        for obj in objects:
            names.append(str(obj))
            self.purge_object(obj)
        self.message_user(request, gettext('Eliminado definitivamente: %s.') % ', '.join(names),
                          level=messages.WARNING)
        return None
