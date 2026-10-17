# coding=utf-8
import logging

from django import forms
from django.conf import settings
from django.contrib.auth.models import User
from django.contrib.auth.password_validation import get_default_password_validators
from django.contrib.sites.shortcuts import get_current_site
from django.db import transaction
from django.forms import ChoiceField, ModelChoiceField
from django.shortcuts import render
from django.utils.translation import gettext, gettext_lazy as _, ngettext
from registration.backends.default.views import (ActivationView as OldActivationView,
                                                 RegistrationView as OldRegistrationView)
from registration.forms import RegistrationForm
from sortedm2m.forms import SortedMultipleChoiceField

from judge.models import Language, Organization, Profile, TIMEZONE
from judge.utils.mail import SEND_ERRORS, email_budget_available, spend_email_budget, validate_email_domain
from judge.utils.recaptcha import ReCaptchaField, ReCaptchaWidget
from judge.utils.subscription import Subscription, newsletter_id
from judge.utils.views import generic_message
from judge.widgets import Select2MultipleWidget, Select2Widget

logger = logging.getLogger('judge.mail')


class CustomRegistrationForm(RegistrationForm):
    username = forms.RegexField(regex=r'^\w+$', max_length=30, label=_('Username'),
                                error_messages={'invalid': _('A username must contain letters, '
                                                             'numbers, or underscores.')})
    first_name = forms.CharField(max_length=50, label=_('Nombre'))
    last_name = forms.CharField(max_length=50, label=_('Apellidos'))
    timezone = ChoiceField(label=_('Timezone'), choices=TIMEZONE,
                           widget=Select2Widget(attrs={'style': 'width:100%'}))
    language = ModelChoiceField(queryset=Language.objects.all(), label=_('Preferred language'), empty_label=None,
                                widget=Select2Widget(attrs={'style': 'width:100%'}))
    organizations = SortedMultipleChoiceField(queryset=Organization.objects.filter(is_open=True),
                                              label=_('Organizations'), required=False,
                                              widget=Select2MultipleWidget(attrs={'style': 'width:100%'}))

    if newsletter_id is not None:
        newsletter = forms.BooleanField(label=_('Subscribe to newsletter?'), initial=True, required=False)

    if ReCaptchaField is not None:
        captcha = ReCaptchaField(widget=ReCaptchaWidget())

    def clean_email(self):
        if User.objects.filter(email=self.cleaned_data['email']).exists():
            raise forms.ValidationError(gettext('The email address "%s" is already taken. Only one registration '
                                                'is allowed per address.') % self.cleaned_data['email'])
        validate_email_domain(self.cleaned_data['email'])
        return self.cleaned_data['email']

    def clean_organizations(self):
        organizations = self.cleaned_data.get('organizations') or []
        max_orgs = settings.DMOJ_USER_MAX_ORGANIZATION_COUNT
        if len(organizations) > max_orgs:
            raise forms.ValidationError(ngettext('You may not be part of more than {count} public organization.',
                                                 'You may not be part of more than {count} public organizations.',
                                                 max_orgs).format(count=max_orgs))
        return self.cleaned_data['organizations']


class ActivationEmailNotSent(Exception):
    pass


class RegistrationView(OldRegistrationView):
    title = _('Register')
    form_class = CustomRegistrationForm
    template_name = 'registration/registration_form.html'
    # register() sends the activation email itself, inside the transaction that creates the account: if
    # it cannot be sent, the account is not created and the address can be used to register again.
    SEND_ACTIVATION_EMAIL = False

    def form_valid(self, form):
        if not email_budget_available(reserve=settings.DMOJ_EMAIL_DAILY_RESERVE):
            return self.activation_email_not_sent(status=429)
        try:
            return super(RegistrationView, self).form_valid(form)
        except ActivationEmailNotSent:
            return self.activation_email_not_sent(status=503)

    def activation_email_not_sent(self, status):
        return generic_message(self.request, gettext('Account not created'),
                               gettext('We could not send the activation email, so your account was not created. '
                                       'Please try again later.'), status=status)

    def get_context_data(self, **kwargs):
        if 'title' not in kwargs:
            kwargs['title'] = self.title
        kwargs['TIMEZONE_MAP'] = settings.TIMEZONE_MAP
        kwargs['password_validators'] = get_default_password_validators()
        kwargs['tos_url'] = settings.TERMS_OF_SERVICE_URL
        return super(RegistrationView, self).get_context_data(**kwargs)

    def register(self, form):
        with transaction.atomic():
            user = super(RegistrationView, self).register(form)

            # Aqui asignamos first_name y last_name del formulario al usuario
            cleaned_data = form.cleaned_data
            user.first_name = cleaned_data['first_name']
            user.last_name = cleaned_data['last_name']
            user.save()  # con esto se guardan

            profile, _ = Profile.objects.get_or_create(user=user, defaults={
                'language': Language.get_default_language(),
            })

            profile.timezone = cleaned_data['timezone']
            profile.language = cleaned_data['language']
            profile.organizations.add(*cleaned_data['organizations'])
            profile.save()

            if newsletter_id is not None and cleaned_data.get('newsletter'):
                Subscription(user=user, newsletter_id=newsletter_id, subscribed=True).save()

            try:
                registration = self.registration_profile.objects.get(user=user)
                registration.send_activation_email(get_current_site(self.request), self.request)
            except SEND_ERRORS as error:
                logger.exception('Activation email could not be sent; the account was not created')
                raise ActivationEmailNotSent from error

        spend_email_budget()
        return user

    def get_initial(self, *args, **kwargs):
        initial = super(RegistrationView, self).get_initial(*args, **kwargs)
        initial['timezone'] = settings.DEFAULT_USER_TIME_ZONE
        initial['language'] = Language.objects.get(key=settings.DEFAULT_USER_LANGUAGE)
        return initial


class ActivationView(OldActivationView):
    title = _('Activation Key Invalid')
    template_name = 'registration/activate.html'

    def get_context_data(self, **kwargs):
        if 'title' not in kwargs:
            kwargs['title'] = self.title
        return super(ActivationView, self).get_context_data(**kwargs)


def social_auth_error(request):
    return render(request, 'generic-message.html', {
        'title': gettext('Authentication failure'),
        'message': request.GET.get('message'),
    })
