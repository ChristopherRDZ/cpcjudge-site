# -*- coding: utf-8 -*-
"""Quién puede borrar desde el admin, y qué queda reservado a la cuenta dueña.

`is_superuser` no sirve para separar niveles. `PermissionsMixin.has_perm` corta por
lo sano:

    if self.is_active and self.is_superuser:
        return True

y devuelve `True` sin mirar permisos ni backends. Por eso todos los superusuarios
del sitio son exactamente igual de poderosos y un permiso nuevo no cambiaría nada:
la única forma de que unos puedan borrar y otros no es interceptarlo en el admin,
que es lo que hace este módulo.

La lista de dueños vive en `dmoj/settings.py`, que es código `root:root 0755`, y no
en la base de datos. Si fuera una marca, un grupo o un permiso, cualquier
superusuario se lo concedería a sí mismo desde el propio admin en dos clics.
"""
from django.conf import settings
from django.contrib import admin
from django.contrib.admin.utils import unquote
from django.contrib.contenttypes.models import ContentType
from django.core.exceptions import PermissionDenied
from django.http import Http404
from django.utils.translation import gettext
from reversion.admin import VersionAdmin
from reversion.models import Version

# Se marcan las clases ya envueltas para que instalar dos veces no encadene dos
# capas del mismo guardián. Van separadas porque se comprueban en `__dict__`, y una
# sola bandera heredada haría creer que una subclase ya está protegida.
_PERMISSION_FLAG = '_cpc_delete_permission_guarded'
_VIEW_FLAG = '_cpc_delete_view_guarded'
_REVERSION_FLAG = '_cpc_reversion_guarded'

# Plantillas que esconden los enlaces de revertir y recuperar a quien no es la cuenta
# dueña. Se cambian sólo si el admin usa las de django-reversion tal cual.
REVERSION_TEMPLATES = {
    'change_list_template': ('reversion/change_list.html', 'admin/cpc_reversion/change_list.html'),
    'object_history_template': ('reversion/object_history.html', 'admin/cpc_reversion/object_history.html'),
}

# Un inline puede quedar fuera del candado declarando `owner_lock_exempt = True` en su
# propia clase. Se mira en `__dict__`, como las banderas, para que una subclase no herede
# la excepción sin declararla. Sólo vale para inlines: un ModelAdmin exento enseñaría un
# botón de borrar que la segunda barrera convierte en 403. Hoy la usa únicamente la lista
# de problemas de un concurso (`ContestProblemInline`).
EXEMPT_ATTRIBUTE = 'owner_lock_exempt'


def server_owners():
    """Nombres de usuario con permiso para borrar. Vacío significa que nadie borra."""
    return tuple(getattr(settings, 'CPC_SERVER_OWNERS', ()))


def is_server_owner(request):
    user = getattr(request, 'user', None)
    if user is None or not user.is_authenticated or not user.is_active:
        return False
    # Una sesión suplantada no hereda esto. Hoy no se puede suplantar a un
    # superusuario (IMPERSONATE ALLOW_SUPERUSER), pero si eso cambiara, entrar
    # como el dueño no debe dar sus poderes de borrado.
    if getattr(user, 'is_impersonate', False) or getattr(request, 'impersonator', None) is not None:
        return False
    return user.is_superuser and user.get_username() in server_owners()


def is_owner_account(user):
    """¿Esta cuenta es una de las dueñas? Sirve para protegerla de las demás."""
    return bool(user) and getattr(user, 'username', None) in server_owners()


def _guard_permission(cls, exempt_allowed=False):
    """Oculta el botón de borrar y retira la acción en lote para quien no es dueño."""
    if _PERMISSION_FLAG in cls.__dict__:
        return
    if exempt_allowed and cls.__dict__.get(EXEMPT_ATTRIBUTE):
        return
    original = cls.has_delete_permission

    def has_delete_permission(self, request, obj=None):
        if not is_server_owner(request):
            return False
        return original(self, request, obj)

    cls.has_delete_permission = has_delete_permission
    setattr(cls, _PERMISSION_FLAG, True)


def _guard_views():
    """Segunda barrera, en las vistas que borran de verdad.

    Hay admins que **reemplazan** `has_delete_permission` sin llamar a `super()`
    —`ProblemTranslationInline` lo asigna directamente—, así que envolver el método
    no siempre encadena. Esto corta el paso aunque alguien escriba la URL de borrado
    a mano.
    """
    if _VIEW_FLAG in admin.ModelAdmin.__dict__:
        return

    def make_guard(original):
        def guarded(self, request, *args, **kwargs):
            if not is_server_owner(request):
                raise PermissionDenied(gettext('Sólo la cuenta dueña del servidor puede borrar aquí.'))
            return original(self, request, *args, **kwargs)
        return guarded

    for name in ('delete_view', 'delete_model', 'delete_queryset'):
        setattr(admin.ModelAdmin, name, make_guard(getattr(admin.ModelAdmin, name)))
    setattr(admin.ModelAdmin, _VIEW_FLAG, True)


def _guard_reversion(cls):
    """El historial de django-reversion, sólo para la cuenta dueña.

    Revertir aplica la revisión entera con `Revision.revert(delete=True)`: devuelve a
    su estado guardado todos los objetos de esa revisión, de cualquier modelo, y borra
    las filas seguidas que se crearon después. Ese borrado no pasa por `delete_view`
    ni por `has_delete_permission`, así que las dos capas de arriba no lo ven; y
    `recover_view` acepta el id de cualquier versión, de cualquier modelo. Revertir es
    borrar, así que queda para quien puede borrar, y aun así sólo con versiones del
    modelo de ese admin. La lista de borrados también, porque enseña los de todos.
    `history_view` sólo lee y se queda como está.
    """
    if not issubclass(cls, VersionAdmin) or _REVERSION_FLAG in cls.__dict__:
        return

    def owner_only(original, check_version=None):
        def guarded(self, request, *args, **kwargs):
            # Antes de buscar nada: a quien no es la dueña no se le dice si la versión existe.
            if not is_server_owner(request):
                raise PermissionDenied
            if check_version is not None:
                check_version(self, *args)
            return original(self, request, *args, **kwargs)
        return guarded

    def recover_version(self, version_id, *args):
        if not Version.objects.filter(pk=version_id,
                                      content_type=ContentType.objects.get_for_model(self.model)).exists():
            raise Http404

    def revision_version(self, object_id, version_id, *args):
        if not Version.objects.filter(pk=version_id, object_id=unquote(object_id),
                                      content_type=ContentType.objects.get_for_model(self.model)).exists():
            raise Http404

    # Las plantillas de abajo enseñan los enlaces sólo con `cpc_can_revert`.
    def flag(request, extra_context):
        return dict(extra_context or {}, cpc_can_revert=is_server_owner(request))

    original_changelist, original_history = cls.changelist_view, cls.history_view

    def changelist_view(self, request, extra_context=None):
        return original_changelist(self, request, extra_context=flag(request, extra_context))

    def history_view(self, request, object_id, extra_context=None):
        return original_history(self, request, object_id, extra_context=flag(request, extra_context))

    cls.recoverlist_view = owner_only(cls.recoverlist_view)
    cls.recover_view = owner_only(cls.recover_view, recover_version)
    cls.revision_view = owner_only(cls.revision_view, revision_version)
    cls.changelist_view = changelist_view
    cls.history_view = history_view
    for attribute, (original, replacement) in REVERSION_TEMPLATES.items():
        if getattr(cls, attribute) == original:
            setattr(cls, attribute, replacement)
    setattr(cls, _REVERSION_FLAG, True)


def install(site=None):
    """Aplica el guardián a todo lo registrado. Se llama al final de judge/admin/__init__.py."""
    site = site or admin.site
    _guard_views()
    for model_admin in site._registry.values():
        _guard_permission(type(model_admin))
        _guard_reversion(type(model_admin))
        for inline in getattr(model_admin, 'inlines', ()) or ():
            _guard_permission(inline, exempt_allowed=True)


class OwnerOnlyActionsMixin:
    """Esconde de la lista de acciones las que sólo debe ver la cuenta dueña.

    Ocultarlas no es la protección: cada acción vuelve a comprobar `is_server_owner`
    antes de tocar nada, porque el nombre de la acción viaja en el POST y se puede
    escribir a mano.
    """
    owner_only_actions = ()

    def get_actions(self, request):
        actions = super().get_actions(request)
        if not is_server_owner(request):
            for name in self.owner_only_actions:
                actions.pop(name, None)
        return actions
