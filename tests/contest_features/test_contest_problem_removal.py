"""Removing a problem from a contest in the admin, without opening the rest of the deletion lock.

`ContestProblemInline` declares `owner_lock_exempt = True` and its delete permission is its change
permission: whoever may change the problems of a contest they edit may also remove one. These tests
show that:

* an account with the usual contest-author permissions removes a problem from its own contest through
  the real form, and the problem still exists, as do the submissions (only their link to the contest
  goes);
* it cannot do so in somebody else's contest, nor without the permission to change contest problems;
* the exemption is exactly that one: no other admin or inline leaves the lock, a subclass does not
  inherit it and a ModelAdmin cannot declare it.
"""
from adminsortable2.admin import SortableAdminBase
from django.contrib import admin
from django.contrib.auth.models import Group, Permission, User
from django.test import RequestFactory, TestCase, override_settings

from judge.admin.contest import ContestProblemInline
from judge.admin_owner import EXEMPT_ATTRIBUTE, install
from judge.models import Contest, ContestProblem, ContestSubmission, Language, Problem, Submission
from judge.models.tests.util import (create_contest, create_contest_participation, create_contest_problem,
                                     create_problem, create_user)

OWNER = 'site_owner'

# A typical contest author group.
CONTEST_AUTHOR = [
    'add_contest', 'add_contestclarification', 'add_contestproblem', 'change_contest', 'change_contest_visibility',
    'change_contestclarification', 'change_contestproblem', 'contest_access_code', 'contest_rating',
    'create_private_contest', 'edit_own_contest', 'lock_contest', 'moss_contest', 'view_contest',
    'view_contestclarification', 'view_contestproblem', 'view_contesttag',
]


def request_for(user):
    request = RequestFactory().get('/admin/')
    request.user = User.objects.get(pk=user.pk)
    return request


@override_settings(CPC_SERVER_OWNERS=(OWNER,))
class ContestProblemRemovalTests(TestCase):
    fixtures = ['language_small.json']

    def setUp(self):
        self.owner = create_user(username=OWNER, is_staff=True, is_superuser=True)
        self.other_super = create_user(username='other_admin', is_staff=True, is_superuser=True)
        group = Group.objects.create(name='Contest authors')
        group.permissions.set(Permission.objects.filter(content_type__app_label='judge', codename__in=CONTEST_AUTHOR))
        self.assertEqual(group.permissions.count(), len(CONTEST_AUTHOR))
        self.organizer = create_user(username='organizer', is_staff=True)
        self.organizer.groups.add(group)
        self.stranger = create_user(username='outsider', is_staff=True)
        self.stranger.groups.add(group)

        self.first = create_problem(code='first', name='First')
        self.second = create_problem(code='second', name='Second')
        self.contest = create_contest(key='own', authors=['organizer'], is_visible=True)
        self.keep = create_contest_problem(contest=self.contest, problem=self.first, order=1)
        self.drop = create_contest_problem(contest=self.contest, problem=self.second, order=2)

    def login(self, user):
        self.client.force_login(User.objects.get(pk=user.pk))

    def url(self, contest):
        return '/admin/judge/contest/%s/change/' % contest.pk

    def form_values(self, response):
        """What a browser would post without touching anything."""
        from lxml import html
        self.assertEqual(response.status_code, 200)
        form = html.fromstring(response.content).xpath('//form[@id="contest_form"]')
        self.assertEqual(len(form), 1)
        data = {}
        for field in form[0].xpath('.//input[@name] | .//select[@name] | .//textarea[@name]'):
            name = field.get('name')
            if field.get('disabled') is not None or '__prefix__' in name:
                continue
            kind = field.get('type', '').lower()
            if kind in ('submit', 'button', 'file') or (kind in ('checkbox', 'radio') and field.get('checked') is None):
                continue
            if field.tag == 'select':
                options = field.xpath('.//option[@selected]')
                if not options and field.get('multiple') is None:
                    options = field.xpath('.//option')[:1]
                value = [o.get('value', o.text or '') for o in options]
                if field.get('multiple') is None:
                    value = value[0] if value else ''
            else:
                value = field.text or '' if field.tag == 'textarea' else field.get('value', '')
            if name in data:
                previous = data[name] if isinstance(data[name], list) else [data[name]]
                data[name] = previous + (value if isinstance(value, list) else [value])
            else:
                data[name] = value
        for key, value in list(data.items()):
            if isinstance(value, str) and value.startswith('\n'):
                data[key] = value[1:]
            if key.endswith('-TOTAL_FORMS'):
                data[key] = data[key[:-11] + 'INITIAL_FORMS']
        data['_save'] = 'Save'
        return data

    def problem_rows(self, response):
        formset = response.context['inline_admin_formsets'][0].formset
        rows = {form.instance.pk: form.prefix for form in formset.initial_forms}
        return formset, rows

    def assert_saved(self, response):
        errors = []
        if response.context:
            adminform = response.context.get('adminform')
            if adminform:
                errors.append(str(adminform.form.errors))
            for inline in response.context.get('inline_admin_formsets', []):
                errors.append(str(inline.formset.errors))
                errors.append(str(inline.formset.non_form_errors()))
        self.assertEqual(response.status_code, 302, '\n'.join(errors))

    def remove(self, user, contest, contest_problem):
        self.login(user)
        response = self.client.get(self.url(contest))
        formset, rows = self.problem_rows(response)
        data = self.form_values(response)
        data[rows[contest_problem.pk] + '-DELETE'] = 'on'
        return formset, self.client.post(self.url(contest), data)

    def test_the_organizer_sees_the_delete_box_and_removes_a_problem(self):
        formset, response = self.remove(self.organizer, self.contest, self.drop)
        self.assertTrue(formset.can_delete)
        self.assert_saved(response)
        self.assertEqual(list(ContestProblem.objects.filter(contest=self.contest).values_list('pk', flat=True)),
                         [self.keep.pk])
        self.assertTrue(Problem.objects.filter(pk=self.second.pk).exists())

    def test_the_submissions_stay_and_only_their_contest_link_goes(self):
        participation = create_contest_participation(contest=self.contest, user='contestant')
        submission = Submission.objects.create(user=participation.user, problem=self.second,
                                               language=Language.objects.first(), contest_object=self.contest)
        link = ContestSubmission.objects.create(submission=submission, problem=self.drop,
                                                participation=participation, points=0)
        formset, response = self.remove(self.organizer, self.contest, self.drop)
        self.assert_saved(response)
        self.assertTrue(Submission.objects.filter(pk=submission.pk).exists())
        self.assertFalse(ContestSubmission.objects.filter(pk=link.pk).exists())

    def test_a_contest_of_someone_else_is_out_of_reach(self):
        self.login(self.stranger)
        response = self.client.get(self.url(self.contest))
        self.assertNotEqual(response.status_code, 200)
        data = {'contest_problems-TOTAL_FORMS': '2', 'contest_problems-INITIAL_FORMS': '2',
                'contest_problems-1-id': str(self.drop.pk), 'contest_problems-1-DELETE': 'on', '_save': 'Save'}
        self.client.post(self.url(self.contest), data)
        self.assertTrue(ContestProblem.objects.filter(pk=self.drop.pk).exists())

    def test_without_the_contest_problem_permission_there_is_no_delete(self):
        limited = create_user(username='limited', is_staff=True)
        limited.user_permissions.set(Permission.objects.filter(
            content_type__app_label='judge',
            codename__in=['edit_own_contest', 'change_contest', 'view_contest', 'view_contestproblem']))
        self.contest.authors.add(limited.profile)
        request = request_for(limited)
        self.assertFalse(ContestProblemInline(Contest, admin.site).has_delete_permission(request, self.contest))

    def test_owner_and_other_superusers_can_remove_too(self):
        for user in (self.owner, self.other_super):
            with self.subTest(user=user.username):
                request = request_for(user)
                self.assertTrue(ContestProblemInline(Contest, admin.site).has_delete_permission(request, self.contest))

    def test_the_contest_itself_is_still_locked(self):
        request = request_for(self.organizer)
        self.assertFalse(admin.site._registry[Contest].has_delete_permission(request, self.contest))
        request = request_for(self.other_super)
        self.assertFalse(admin.site._registry[Contest].has_delete_permission(request, self.contest))

    def test_the_only_exemption_is_the_contest_problem_list(self):
        request = request_for(self.other_super)
        leaky, exempt = [], []
        for model, model_admin in admin.site._registry.items():
            if EXEMPT_ATTRIBUTE in type(model_admin).__dict__:
                exempt.append(model._meta.label)
            if model_admin.has_delete_permission(request):
                leaky.append(model._meta.label)
            for inline in model_admin.inlines or ():
                if inline.__dict__.get(EXEMPT_ATTRIBUTE):
                    exempt.append('%s / %s' % (model._meta.label, inline.__name__))
                if inline(model, admin.site).has_delete_permission(request):
                    leaky.append('%s / %s' % (model._meta.label, inline.__name__))
        self.assertEqual(exempt, ['judge.Contest / ContestProblemInline'])
        self.assertEqual(leaky, ['judge.Contest / ContestProblemInline'])

    def test_a_subclass_does_not_inherit_the_exemption(self):
        class Subclass(ContestProblemInline):
            pass

        class TestContestAdmin(SortableAdminBase, admin.ModelAdmin):
            inlines = [Subclass]

        site = admin.AdminSite(name='test_subclass')
        site.register(Contest, TestContestAdmin)
        install(site)
        request = request_for(self.other_super)
        self.assertFalse(Subclass(Contest, site).has_delete_permission(request, self.contest))
        self.assertTrue(Subclass(Contest, site).has_delete_permission(request_for(self.owner), self.contest))

    def test_a_model_admin_cannot_declare_itself_exempt(self):
        class TestProblemAdmin(admin.ModelAdmin):
            owner_lock_exempt = True

        site = admin.AdminSite(name='test_modeladmin')
        site.register(Problem, TestProblemAdmin)
        install(site)
        self.assertFalse(site._registry[Problem].has_delete_permission(request_for(self.other_super)))
        self.assertTrue(site._registry[Problem].has_delete_permission(request_for(self.owner)))
