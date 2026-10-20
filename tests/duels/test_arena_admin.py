"""The «Battles» section of the admin side menu, and who sees it."""
from django.core.cache import cache
from django.test import TestCase
from django.urls import reverse

from arena_testutil import organizer
from judge.models.tests.util import create_user


class AdminMenuTests(TestCase):
    fixtures = ['language_small.json']

    @classmethod
    def setUpTestData(cls):
        cls.organizer = organizer()
        cls.staff = create_user(username='plainstaff', is_staff=True)
        cls.superuser = create_user(username='root', is_staff=True, is_superuser=True)

    def setUp(self):
        cache.clear()

    def test_organizers_see_the_section(self):
        self.client.force_login(self.organizer)
        page = self.client.get(reverse('admin:index')).content.decode('utf-8')
        self.assertIn('fa-bolt', page)
        self.assertIn(reverse('admin:judge_arenapanel_changelist'), page)
        response = self.client.get(reverse('admin:judge_arenapanel_changelist'))
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response['Location'], reverse('arena_manage'))
        self.assertEqual(self.client.get(reverse('admin:judge_arenamatch_changelist')).status_code, 200)
        self.assertEqual(self.client.get(reverse('admin:judge_arenamatch_add')).status_code, 403)

    def test_superusers_see_the_section(self):
        self.client.force_login(self.superuser)
        self.assertIn('fa-bolt', self.client.get(reverse('admin:index')).content.decode('utf-8'))

    def test_other_staff_do_not(self):
        self.client.force_login(self.staff)
        page = self.client.get(reverse('admin:index')).content.decode('utf-8')
        self.assertNotIn('fa-bolt', page)
        self.assertEqual(self.client.get(reverse('admin:judge_arenapanel_changelist')).status_code, 403)
        self.assertEqual(self.client.get(reverse('admin:judge_arenamatch_changelist')).status_code, 403)
