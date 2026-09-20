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
from django.core.exceptions import PermissionDenied
from django.utils.translation import gettext

# Se marcan las clases ya envueltas para que instalar dos veces no encadene dos
# capas del mismo guardián. Van separadas porque se comprueban en `__dict__`, y una
# sola bandera heredada haría creer que una subclase ya está protegida.
_PERMISSION_FLAG = '_cpc_delete_permission_guarded'
_VIEW_FLAG = '_cpc_delete_view_guarded'


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


def _guard_permission(cls):
    """Oculta el botón de borrar y retira la acción en lote para quien no es dueño."""
    if _PERMISSION_FLAG in cls.__dict__:
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


def install(site=None):
    """Aplica el guardián a todo lo registrado. Se llama al final de judge/admin/__init__.py."""
    site = site or admin.site
    _guard_views()
    for model_admin in site._registry.values():
        _guard_permission(type(model_admin))
        for inline in getattr(model_admin, 'inlines', ()) or ():
            _guard_permission(inline)


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
