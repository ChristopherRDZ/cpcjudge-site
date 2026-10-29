from django.conf import settings
from django.http import HttpResponse, HttpResponseBadRequest, HttpResponseForbidden
from django.utils.cache import patch_cache_control
from django.utils.translation import gettext as _
from django.views.decorators.http import require_safe
from django.views.generic.base import ContextMixin, TemplateResponseMixin, View

from judge.jinja2.markdown import mathjax_config

# The longest text stored in production on 2026-10-28 is a problem statement of about 15 000 characters.
PREVIEW_MAX_LENGTH = getattr(settings, 'DMOJ_MARKDOWN_PREVIEW_MAX_LENGTH', 100000)


class MarkdownPreviewView(TemplateResponseMixin, ContextMixin, View):
    def post(self, request, *args, **kwargs):
        # Only for accounts, and only for texts of a sensible length (markdown-enunciados-1, 2026-10-28): a preview
        # renders whatever it is sent, without cache. Every editor that previews is behind a login, and the request
        # is made by the page, so an anonymous one gets a plain 403 instead of the login page.
        if not request.user.is_authenticated:
            return HttpResponseForbidden()
        try:
            self.preview_data = data = request.POST['content']
        except KeyError:
            return HttpResponseBadRequest('No preview data specified.')
        if len(data) > PREVIEW_MAX_LENGTH:
            return HttpResponse(_('This text is too long to preview.'), status=413, content_type='text/plain')

        return self.render_to_response(self.get_context_data(
            preview_data=data,
        ))


@require_safe
def mathjax_config_js(request):
    """The MathJax configuration that the previews of the editors load (resources/martor-mathjax.js), the same as
    the one of every page (judge.jinja2.markdown.mathjax_config)."""
    response = HttpResponse('window.MathJax = %s;\n' % mathjax_config(), content_type='text/javascript; charset=utf-8')
    patch_cache_control(response, public=True, max_age=3600)
    return response


class ProblemMarkdownPreviewView(MarkdownPreviewView):
    template_name = 'problem/preview.html'


class BlogMarkdownPreviewView(MarkdownPreviewView):
    template_name = 'blog/preview.html'


class ContestMarkdownPreviewView(MarkdownPreviewView):
    template_name = 'contest/preview.html'


class CommentMarkdownPreviewView(MarkdownPreviewView):
    template_name = 'comments/preview.html'


class FlatPageMarkdownPreviewView(MarkdownPreviewView):
    template_name = 'flatpage-preview.html'


class ProfileMarkdownPreviewView(MarkdownPreviewView):
    template_name = 'user/preview.html'


class OrganizationMarkdownPreviewView(MarkdownPreviewView):
    template_name = 'organization/preview.html'


class SolutionMarkdownPreviewView(MarkdownPreviewView):
    template_name = 'solution-preview.html'


class LicenseMarkdownPreviewView(MarkdownPreviewView):
    template_name = 'license-preview.html'


class TicketMarkdownPreviewView(MarkdownPreviewView):
    template_name = 'ticket/preview.html'


class DefaultMarkdownPreviewView(MarkdownPreviewView):
    template_name = 'default-preview.html'
