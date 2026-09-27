import io
import json
import mimetypes
import os
from itertools import chain
from typing import List
from zipfile import BadZipfile, ZIP_DEFLATED, ZipFile

from django.conf import settings
from django.contrib.auth.decorators import login_required
from django.contrib.auth.mixins import LoginRequiredMixin
from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import UploadedFile
from django.forms import BaseModelFormSet, HiddenInput, ModelForm, NumberInput, Select, formset_factory
from django.http import Http404, HttpResponse, HttpResponseForbidden, HttpResponseRedirect
from django.shortcuts import get_object_or_404, render
from django.urls import reverse
from django.utils.html import escape, format_html
from django.utils.safestring import mark_safe
from django.utils.translation import gettext as _
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_GET
from django.views.generic import DetailView

from judge.highlight_code import highlight_code
from judge.models import Problem, ProblemData, ProblemTestCase, Submission, problem_data_storage
from judge.utils.problem_data import INTERACTIVE_EXAMPLE_DIR, INTERACTOR_LANGUAGES, ProblemDataCompiler, \
    interactor_uses_testlib
from judge.utils.unicode import utf8text
from judge.utils.views import TitleMixin, add_file_response
from judge.views.problem import ProblemMixin

mimetypes.init()
mimetypes.add_type('application/x-yaml', '.yml')

# Two separate ceilings stand between a big data set and a saved problem, and
# neither one announces itself:
#
#  * every case row posts one field per entry of ProblemCaseForm.Meta.fields
#    plus its id and its delete box, so DATA_UPLOAD_MAX_NUMBER_FIELDS turns a
#    large table into a bare 400 before the formset ever runs;
#  * the formset itself stops at absolute_max forms, and Django drops the
#    surplus quietly rather than complaining.
#
# The page hands the browser whichever is lower, so that filling the table from
# a zip can warn instead of building something that cannot be saved.
FIELDS_PER_CASE_ROW = len(('order', 'type', 'input_file', 'output_file', 'points', 'is_pretest',
                           'output_limit', 'output_prefix', 'checker', 'checker_args',
                           'generator_args', 'batch_dependencies', 'id', 'DELETE'))
FIELDS_OUTSIDE_CASES = 20  # management form, the data form above the table, CSRF and slack


def max_case_rows(formset_class):
    ceilings = [formset_class.absolute_max]
    limit = settings.DATA_UPLOAD_MAX_NUMBER_FIELDS
    if limit is not None:
        ceilings.append(max(0, (limit - FIELDS_OUTSIDE_CASES) // FIELDS_PER_CASE_ROW))
    return min(ceilings)


def checker_args_cleaner(self):
    data = self.cleaned_data['checker_args']
    if not data or data.isspace():
        return ''
    try:
        if not isinstance(json.loads(data), dict):
            raise ValidationError(_('Checker arguments must be a JSON object.'))
    except ValueError:
        raise ValidationError(_('Checker arguments is invalid JSON.'))
    return data


INTERACTOR_MAX_SIZE = 1 << 20


class ProblemDataForm(ModelForm):
    def clean_interactor(self):
        interactor = self.cleaned_data['interactor']
        # Only a fresh upload needs checking; a kept file is a FieldFile and a
        # cleared one is False.
        if not isinstance(interactor, UploadedFile):
            return interactor
        extension = os.path.splitext(interactor.name)[1].lower()
        if extension not in INTERACTOR_LANGUAGES:
            raise ValidationError(_('The interactor must be a source file ending in %s.') %
                                  ', '.join(sorted(INTERACTOR_LANGUAGES)))
        if interactor.size > INTERACTOR_MAX_SIZE:
            raise ValidationError(_('The interactor must not be larger than 1 MB.'))
        return interactor

    def clean(self):
        cleaned_data = super().clean()
        interactor, generator = cleaned_data.get('interactor'), cleaned_data.get('generator')
        if interactor and generator and \
                os.path.basename(interactor.name) == os.path.basename(generator.name):
            self.add_error('interactor', _('The generator and the interactor need different file names.'))
        return cleaned_data

    def clean_zipfile(self):
        if hasattr(self, 'zip_valid') and not self.zip_valid:
            raise ValidationError(_('Your zip file is invalid!'))

        zipfile = self.cleaned_data['zipfile']
        if zipfile and not zipfile.name.endswith('.zip'):
            raise ValidationError(_("Zip files must end in '.zip'"))

        return zipfile

    def clean_generator(self):
        generator = self.cleaned_data['generator']
        if generator and generator.name == 'init.yml':
            raise ValidationError(_('Generators must not be named init.yml.'))

        return generator

    clean_checker_args = checker_args_cleaner

    class Meta:
        model = ProblemData
        fields = ['zipfile', 'generator', 'unicode', 'nobigmath', 'output_limit', 'output_prefix',
                  'checker', 'checker_args', 'interactor', 'interactor_feedback']
        widgets = {
            'checker_args': HiddenInput,
        }


class ProblemCaseForm(ModelForm):
    clean_checker_args = checker_args_cleaner

    class Meta:
        model = ProblemTestCase
        fields = ('order', 'type', 'input_file', 'output_file', 'points', 'is_pretest', 'output_limit',
                  'output_prefix', 'checker', 'checker_args', 'generator_args', 'batch_dependencies')
        widgets = {
            'generator_args': HiddenInput,
            'batch_dependencies': HiddenInput,
            'type': Select(attrs={'style': 'width: 100%'}),
            'points': NumberInput(attrs={'style': 'width: 4em'}),
            'output_prefix': NumberInput(attrs={'style': 'width: 4.5em'}),
            'output_limit': NumberInput(attrs={'style': 'width: 6em'}),
            'checker_args': HiddenInput,
        }


class ProblemCaseFormSet(formset_factory(ProblemCaseForm, formset=BaseModelFormSet, extra=1, max_num=1,
                                         can_delete=True)):
    model = ProblemTestCase

    def __init__(self, *args, **kwargs):
        self.valid_files = kwargs.pop('valid_files', None)
        super(ProblemCaseFormSet, self).__init__(*args, **kwargs)

    def _construct_form(self, i, **kwargs):
        form = super(ProblemCaseFormSet, self)._construct_form(i, **kwargs)
        form.valid_files = self.valid_files
        return form


class ProblemManagerMixin(LoginRequiredMixin, ProblemMixin, DetailView):
    def get_object(self, queryset=None):
        problem = super(ProblemManagerMixin, self).get_object(queryset)
        if problem.is_manually_managed:
            raise Http404()
        if self.request.user.is_superuser or problem.is_editable_by(self.request.user):
            return problem
        raise Http404()


class ProblemSubmissionDiff(TitleMixin, ProblemMixin, DetailView):
    template_name = 'problem/submission-diff.html'

    def get_title(self):
        return _('Comparing submissions for {0}').format(self.object.name)

    def get_content_title(self):
        return mark_safe(escape(_('Comparing submissions for {0}')).format(
            format_html('<a href="{1}">{0}</a>', self.object.name, reverse('problem_detail', args=[self.object.code])),
        ))

    def get_object(self, queryset=None):
        problem = super(ProblemSubmissionDiff, self).get_object(queryset)
        if self.request.user.is_superuser or problem.is_editable_by(self.request.user):
            return problem
        raise Http404()

    def get_context_data(self, **kwargs):
        context = super(ProblemSubmissionDiff, self).get_context_data(**kwargs)
        try:
            ids = self.request.GET.getlist('id')
            subs = Submission.objects.filter(id__in=ids)
        except ValueError:
            raise Http404
        if not subs:
            raise Http404

        context['submissions'] = subs

        # If we have associated data we can do better than just guess
        data = ProblemTestCase.objects.filter(dataset=self.object, type='C')
        if data:
            num_cases = data.count()
        else:
            num_cases = subs.first().test_cases.count()
        context['num_cases'] = num_cases
        return context


class ProblemDataView(TitleMixin, ProblemManagerMixin):
    template_name = 'problem/data.html'

    def get_title(self):
        return _('Editing data for {0}').format(self.object.name)

    def get_content_title(self):
        return mark_safe(escape(_('Editing data for %s')) % (
            format_html('<a href="{1}">{0}</a>', self.object.name,
                        reverse('problem_detail', args=[self.object.code]))))

    def get_data_form(self, post=False):
        return ProblemDataForm(data=self.request.POST if post else None, prefix='problem-data',
                               files=self.request.FILES if post else None,
                               instance=ProblemData.objects.get_or_create(problem=self.object)[0])

    def get_case_formset(self, files, post=False):
        return ProblemCaseFormSet(data=self.request.POST if post else None, prefix='cases', valid_files=files,
                                  queryset=ProblemTestCase.objects.filter(dataset_id=self.object.pk).order_by('order'))

    def get_valid_files(self, data, post=False) -> List[str]:
        try:
            if post and 'problem-data-zipfile-clear' in self.request.POST:
                return []
            elif post and 'problem-data-zipfile' in self.request.FILES:
                return ZipFile(self.request.FILES['problem-data-zipfile']).namelist()
            elif data.zipfile:
                return ZipFile(data.zipfile.path).namelist()
        except BadZipfile:
            raise
        return []

    def get_context_data(self, **kwargs):
        context = super(ProblemDataView, self).get_context_data(**kwargs)
        valid_files = []
        if 'data_form' not in context:
            context['data_form'] = self.get_data_form()
            try:
                valid_files = self.get_valid_files(context['data_form'].instance)
            except BadZipfile:
                pass
            # Only for what is saved: a form sent back with errors may hold an
            # interactor that was never written.
            context['interactor_mode'] = self.get_interactor_mode(context['data_form'].instance)
        context['valid_files'] = set(valid_files)
        context['valid_files_json'] = mark_safe(json.dumps(valid_files))
        context['max_case_rows_json'] = mark_safe(json.dumps(max_case_rows(ProblemCaseFormSet)))

        context['cases_formset'] = self.get_case_formset(valid_files)
        context['all_case_forms'] = chain(context['cases_formset'], [context['cases_formset'].empty_form])
        return context

    @staticmethod
    def get_interactor_mode(data):
        if not data.interactor:
            return None
        try:
            with problem_data_storage.open(data.interactor.name, 'rb') as f:
                return 'testlib' if interactor_uses_testlib(data.interactor.name, f.read()) else 'simple'
        except IOError:
            return 'missing'

    def post(self, request, *args, **kwargs):
        self.object = problem = self.get_object()
        data_form = self.get_data_form(post=True)
        try:
            valid_files = self.get_valid_files(data_form.instance, post=True)
            data_form.zip_valid = True
        except BadZipfile:
            valid_files = []
            data_form.zip_valid = False

        cases_formset = self.get_case_formset(valid_files, post=True)
        if data_form.is_valid() and cases_formset.is_valid():
            data = data_form.save()
            for case in cases_formset.save(commit=False):
                case.dataset_id = problem.id
                case.save()
            for case in cases_formset.deleted_objects:
                case.delete()
            ProblemDataCompiler.generate(problem, data, problem.cases.order_by('order'), valid_files)
            return HttpResponseRedirect(request.get_full_path())
        return self.render_to_response(self.get_context_data(data_form=data_form, cases_formset=cases_formset,
                                                             valid_files=valid_files))

    put = post


# The worked example shown in the guide and handed out as a zip. Each source is
# shown with the lexer that goes with it.
INTERACTIVE_EXAMPLE_SOURCES = (
    ('interactor.cpp', 'cpp'),
    ('interactor.py', 'python3'),
    ('interactor_testlib.cpp', 'cpp'),
    ('solucion.cpp', 'cpp'),
    ('solucion.py', 'python3'),
    ('enunciado.md', 'markdown'),
)


def read_interactive_example(name):
    with open(os.path.join(INTERACTIVE_EXAMPLE_DIR, name), encoding='utf-8') as f:
        return f.read()


class ProblemInteractiveGuideView(TitleMixin, ProblemManagerMixin):
    template_name = 'problem/interactive-guide.html'

    def get_title(self):
        return _('Interactive problems')

    def get_content_title(self):
        return mark_safe(escape(_('Interactive problems: %s')) % (
            format_html('<a href="{1}">{0}</a>', self.object.name,
                        reverse('problem_data', args=[self.object.code]))))

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['example'] = {
            name: highlight_code(read_interactive_example(name), lexer) for name, lexer in INTERACTIVE_EXAMPLE_SOURCES
        }
        context['example_cases'] = [
            (name, read_interactive_example(os.path.join('casos', name)).strip())
            for name in sorted(os.listdir(os.path.join(INTERACTIVE_EXAMPLE_DIR, 'casos')))
        ]
        return context


class ProblemInteractiveExampleView(ProblemManagerMixin):
    def get(self, request, *args, **kwargs):
        self.object = self.get_object()
        cases_dir = os.path.join(INTERACTIVE_EXAMPLE_DIR, 'casos')
        cases = io.BytesIO()
        with ZipFile(cases, 'w', ZIP_DEFLATED) as archive:
            for name in sorted(os.listdir(cases_dir)):
                archive.write(os.path.join(cases_dir, name), name)

        bundle = io.BytesIO()
        with ZipFile(bundle, 'w', ZIP_DEFLATED) as archive:
            archive.writestr('ejemplo-interactivo/casos.zip', cases.getvalue())
            for name, lexer in INTERACTIVE_EXAMPLE_SOURCES:
                archive.write(os.path.join(INTERACTIVE_EXAMPLE_DIR, name), 'ejemplo-interactivo/' + name)

        response = HttpResponse(bundle.getvalue(), content_type='application/zip')
        response['Content-Disposition'] = 'attachment; filename="ejemplo-interactivo.zip"'
        return response


@login_required
def problem_data_file(request, problem, path):
    object = get_object_or_404(Problem, code=problem)
    if not object.is_editable_by(request.user):
        raise Http404()

    problem_dir = problem_data_storage.path(problem)
    if os.path.commonpath((problem_data_storage.path(os.path.join(problem, path)), problem_dir)) != problem_dir:
        raise Http404()

    response = HttpResponse()

    if hasattr(settings, 'DMOJ_PROBLEM_DATA_INTERNAL'):
        url_path = '%s/%s/%s' % (settings.DMOJ_PROBLEM_DATA_INTERNAL, problem, path)
    else:
        url_path = None

    try:
        add_file_response(request, response, url_path, os.path.join(problem, path), problem_data_storage)
    except IOError:
        raise Http404()

    response['Content-Type'] = 'application/octet-stream'
    return response


@login_required
def problem_init_view(request, problem):
    problem = get_object_or_404(Problem, code=problem)
    if not problem.is_editable_by(request.user):
        raise Http404()

    try:
        with problem_data_storage.open(os.path.join(problem.code, 'init.yml'), 'rb') as f:
            data = utf8text(f.read()).rstrip('\n')
    except IOError:
        raise Http404()

    return render(request, 'problem/yaml.html', {
        'raw_source': data, 'highlighted_source': highlight_code(data, 'yaml'),
        'title': _('Generated init.yml for %s') % problem.name,
        'content_title': mark_safe(escape(_('Generated init.yml for %s')) % (
            format_html('<a href="{1}">{0}</a>', problem.name,
                        reverse('problem_detail', args=[problem.code])))),
    })


@require_GET
@never_cache
def problem_data_upload_gate(request):
    """Answers Nginx, before it starts buffering, whether this caller could upload at all.

    The upload route carries a 500M body limit because problem data archives
    reach about 100 MB, and Nginx buffers the whole body before handing it to
    uWSGI. Django's own check runs afterwards, so until this existed *anybody*,
    signed in or not, could make the server absorb half a gigabyte on that one
    path. The temporary file also used to live on a tmpfs, which made it RAM.

    `auth_request` runs in Nginx's access phase, before the content handler reads
    the body, so a 403 here costs nothing. The test is deliberately coarse —
    signed in, and holding the permission without which `Problem.is_editable_by`
    can never return true — because it only has to reject people who could never
    upload anything. Which problems this account may actually manage is still
    decided by `ProblemManagerMixin` on the real request; this never widens that.

    It reveals nothing: an empty 204 or an empty 403, and no problem is named.
    """
    if not request.user.is_authenticated:
        return HttpResponseForbidden()
    if request.user.is_superuser or request.user.has_perm('judge.edit_own_problem'):
        return HttpResponse(status=204)
    return HttpResponseForbidden()
