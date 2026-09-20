from django import forms
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from django.http import Http404, HttpResponseRedirect, JsonResponse
from django.shortcuts import get_object_or_404, render
from django.urls import reverse
from django.utils.translation import gettext as _
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_GET, require_POST

from judge.models.team import Team, TeamInvitation
from judge.models import Contest
from judge import team_services


class TeamForm(forms.Form):
    name = forms.CharField(label=_('Nombre del equipo'), max_length=60)


def _context(request, form=None, error=None):
    return {
        'title': _('Mis equipos'), 'form': form or TeamForm(), 'error': error,
        'teams': Team.objects.filter(members=request.profile, is_active=True).select_related('owner__user')
                     .prefetch_related('members__user'),
        'invitations': TeamInvitation.objects.filter(recipient=request.profile, status=TeamInvitation.PENDING,
                                                     team__is_active=True).select_related('team', 'sender__user'),
    }


@login_required
@never_cache
def my_teams(request):
    if request.method == 'POST':
        form = TeamForm(request.POST)
        if form.is_valid():
            try:
                team = team_services.create_team(request.profile, form.cleaned_data['name'])
            except ValidationError as exc:
                form.add_error(None, exc)
            else:
                return HttpResponseRedirect(team.get_absolute_url())
        return render(request, 'team/list.html', _context(request, form), status=400)
    if request.method != 'GET':
        from django.http import HttpResponseNotAllowed
        return HttpResponseNotAllowed(['GET', 'POST'])
    return render(request, 'team/list.html', _context(request))


@login_required
@require_GET
@never_cache
def invitation_count(request):
    count = TeamInvitation.objects.filter(recipient=request.profile, status=TeamInvitation.PENDING,
                                           team__is_active=True).count()
    return JsonResponse({'count': count})


@login_required
@require_POST
def invitation_action(request, pk):
    try:
        team = team_services.respond_invitation(request.profile, pk, request.POST.get('action'))
    except ValidationError as exc:
        return render(request, 'team/list.html', _context(request, error=' '.join(exc.messages)), status=400)
    if request.POST.get('action') == 'cancel':
        return HttpResponseRedirect(team.get_absolute_url())
    return HttpResponseRedirect(reverse('my_teams'))


@login_required
@never_cache
def team_detail(request, pk):
    team = get_object_or_404(Team.objects.select_related('owner__user'), pk=pk, is_active=True)
    if not team.members.filter(pk=request.profile.pk).exists():
        raise Http404
    error = None
    if request.method == 'POST':
        action = request.POST.get('action')
        try:
            if action == 'invite':
                team_services.invite_member(request.profile, pk, request.POST.get('username', ''))
            elif action in ('leave', 'kick', 'transfer'):
                try:
                    target = request.profile.pk if action == 'leave' else int(request.POST.get('member', ''))
                except (ValueError, TypeError):
                    raise ValidationError(_('Selecciona un integrante válido.'))
                if action == 'transfer':
                    team_services.transfer_team(request.profile, pk, target)
                else:
                    team_services.remove_member(request.profile, pk, target)
                if action == 'leave':
                    return HttpResponseRedirect(reverse('my_teams'))
            elif action == 'delete':
                if request.POST.get('confirm_name') != team.name:
                    raise ValidationError(_('Escribe el nombre exacto del equipo para confirmar su eliminación.'))
                team_services.delete_team(request.profile, pk)
                return HttpResponseRedirect(reverse('my_teams'))
            else:
                raise ValidationError(_('Acción no válida.'))
        except Team.DoesNotExist:
            raise Http404
        except ValidationError as exc:
            error = ' '.join(exc.messages)
        else:
            return HttpResponseRedirect(team.get_absolute_url())
    elif request.method != 'GET':
        from django.http import HttpResponseNotAllowed
        return HttpResponseNotAllowed(['GET', 'POST'])
    return render(request, 'team/detail.html', {
        'title': team.name, 'team': team, 'error': error, 'is_owner': team.owner_id == request.profile.pk,
        'members': team.memberships.select_related('profile__user').order_by('joined', 'id'),
        'pending': team.invitations.filter(status=TeamInvitation.PENDING).select_related('recipient__user'),
        'history': team.participations.filter(contest__in=Contest.get_visible_contests(request.user))
                    .select_related('contest').order_by('-real_start')[:50],
    }, status=400 if error else 200)
