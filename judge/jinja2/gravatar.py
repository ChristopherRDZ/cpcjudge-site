"""The pictures of the accounts. They still come from Gravatar, but through this site (privacidad-datos-personales-1,
2026-10-28): a Gravatar URL carries the MD5 of the email, and the user search handed those of every account to
anyone, which reverses most of them in seconds (112 have the form aa999999@uaeh.edu.mx). The browser now gets
/avatar/<profile id>/<size>; judge.views.user.user_avatar asks Gravatar for the picture from the server and keeps it
in the cache, so the hash never leaves the server.
"""
import hashlib
import logging
import time

import requests
from django.contrib.auth.models import AbstractUser
from django.core.cache import cache
from django.urls import reverse
from django.utils.http import urlencode

from judge.models import Profile
from judge.utils.unicode import utf8bytes
from . import registry

logger = logging.getLogger('judge.gravatar')

# Every size the site asks for (512 is the countdown of the arena). Any other takes the next one up, so that nobody
# fills the cache with sizes the site never shows.
AVATAR_SIZES = (10, 32, 48, 64, 75, 80, 128, 135, 256, 512)
PICTURE_TYPES = ('image/png', 'image/jpeg', 'image/gif', 'image/webp')
# A 512 px photo from Gravatar weighs tens of KB; anything bigger is not kept (the page shows the grey square).
PICTURE_MAX_BYTES = 256 * 1024
# A day for a picture; five minutes for a failure, so that Gravatar being down is not asked again on every page.
PICTURE_TIMEOUT = 86400
FAILURE_TIMEOUT = 300
# Each picture is asked while a worker waits. If Gravatar does not answer, nobody asks it again for a minute: a page
# with a hundred pictures (the reveal of a contest) would otherwise hold every worker of the site for seconds.
OUTAGE_KEY = 'avatar:gravatar-down'
OUTAGE_TIMEOUT = 60
CONNECT_TIMEOUT, READ_TIMEOUT, TOTAL_TIMEOUT = 2, 4, 6
SLOW_SECONDS = 2


def avatar_size(size):
    try:
        size = int(size)
    except (TypeError, ValueError):
        return 80
    return next((allowed for allowed in AVATAR_SIZES if allowed >= size), AVATAR_SIZES[-1])


@registry.function
def gravatar(user, size=80, default=None):
    """The address of the picture of a profile, of a user or of a profile id. `default` is kept for the callers
    but no longer used: the view itself gives the identicon to muted accounts, from their profile."""
    if isinstance(user, Profile):
        profile_id = user.id
    elif isinstance(user, AbstractUser):
        profile_id = user.profile.id
    else:
        profile_id = int(user)
    return reverse('user_avatar', args=(profile_id, avatar_size(size)))


def gravatar_picture(email, size, identicon):
    """(content type, bytes) of the picture of this email, or None if Gravatar did not give one. Asked from the
    server and kept in the cache; `identicon` asks for the generated picture even if the person has a photo."""
    digest = hashlib.md5(utf8bytes(email.strip().lower())).hexdigest()
    key = 'avatar:%s:%d:%d' % (digest, size, bool(identicon))
    cached = cache.get(key)
    if cached is not None:
        return cached or None
    if cache.get(OUTAGE_KEY):
        return None
    args = {'d': 'identicon', 's': str(size)}
    if identicon:
        args['f'] = 'y'
    picture = None
    started = time.monotonic()
    deadline = started + TOTAL_TIMEOUT
    try:
        with requests.get('https://www.gravatar.com/avatar/%s?%s' % (digest, urlencode(args)), stream=True,
                          timeout=(CONNECT_TIMEOUT, READ_TIMEOUT)) as response:
            kind = response.headers.get('Content-Type', '').split(';')[0].strip().lower()
            if response.status_code == 200 and kind in PICTURE_TYPES:
                body = b''
                for chunk in response.iter_content(65536):
                    body += chunk
                    if len(body) > PICTURE_MAX_BYTES or time.monotonic() > deadline:
                        break
                else:
                    picture = (kind, body) if body else None
    except requests.RequestException:
        cache.set(OUTAGE_KEY, True, OUTAGE_TIMEOUT)
        logger.warning('Gravatar did not answer for a picture of size %d', size, exc_info=True)
    else:
        # A slow answer holds a worker as much as none (2026-10-28, adversarial review): nobody waits for it again
        # for a minute either.
        if time.monotonic() - started > SLOW_SECONDS:
            cache.set(OUTAGE_KEY, True, OUTAGE_TIMEOUT)
            logger.warning('Gravatar was slow for a picture of size %d', size)
    cache.set(key, picture or False, PICTURE_TIMEOUT if picture else FAILURE_TIMEOUT)
    return picture
