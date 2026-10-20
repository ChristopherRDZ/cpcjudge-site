"""The rules of an arena game: what an organizer may configure, its defaults and its bounds.

Rules are stored as small JSON objects at three levels (tournament, round, match) and merged in that order, so a
match only stores what it changes. Whatever comes from a request goes through `clean_rules`, which only keeps known
keys and refuses any value of the wrong type or out of bounds: nothing loosely typed ever reaches the engine.
"""
from django.core.exceptions import ValidationError
from django.utils.translation import gettext as _, gettext_lazy

DEFAULTS = {
    # Minutes of play once the countdown is over.
    'duration': 15,
    # Seconds of countdown shown to everybody before the statements appear.
    'countdown': 10,
    # Whether a problem gives part of its points for part of its test cases, or all or nothing.
    'partial': False,
    # Minutes added to the time a player reached their score, for each failed attempt before their best
    # submission on a problem that gave points. Zero means no penalty: only who got there first matters.
    'penalty': 0,
    # Attempts per problem; zero means unlimited. Compile errors and internal errors do not count.
    'max_attempts': 0,
    # Language keys; empty means every language the problem allows.
    'languages': [],
    'block_paste': True,
    # Whether a player sees how the rival is doing (points, attempts, judging). Never their code.
    'show_rival': True,
    # Whether the projector screens show what each player types.
    'screen_code': True,
}

BOUNDS = {
    'duration': (1, 300),
    'countdown': (3, 60),
    'penalty': (0, 60),
    'max_attempts': (0, 50),
}
BOOLEANS = ('partial', 'block_paste', 'show_rival', 'screen_code')
LABELS = {
    'duration': gettext_lazy('Duration'),
    'countdown': gettext_lazy('Countdown'),
    'partial': gettext_lazy('Partial points'),
    'penalty': gettext_lazy('Penalty per failed attempt'),
    'max_attempts': gettext_lazy('Maximum attempts per problem'),
    'languages': gettext_lazy('Languages'),
    'block_paste': gettext_lazy('Block pasting'),
    'show_rival': gettext_lazy('Show rival progress'),
    'screen_code': gettext_lazy('Code on the screens'),
}
MAX_LANGUAGES = 40

DEFAULT_POINTS = 100
POINT_BOUNDS = (1, 10000)
BEST_OF_CHOICES = (1, 3, 5, 7, 9)


def _int(value, name):
    if isinstance(value, bool):
        raise ValidationError(_('Invalid value for %(name)s.') % {'name': LABELS[name]})
    if isinstance(value, str) and value.strip().lstrip('-').isdigit():
        value = int(value.strip())
    if not isinstance(value, int):
        raise ValidationError(_('Invalid value for %(name)s.') % {'name': LABELS[name]})
    low, high = BOUNDS[name]
    if not low <= value <= high:
        raise ValidationError(_('%(name)s must be between %(low)s and %(high)s.') %
                              {'name': LABELS[name], 'low': low, 'high': high})
    return value


def _bool(value, name):
    if isinstance(value, bool):
        return value
    if value in ('true', 'on', '1', 1):
        return True
    if value in ('false', 'off', '0', 0, ''):
        return False
    raise ValidationError(_('Invalid value for %(name)s.') % {'name': LABELS[name]})


def clean_rules(data):
    """Validated copy of the known keys of `data`. Unknown keys are dropped, bad values are refused."""
    if data is None:
        return {}
    if not isinstance(data, dict):
        raise ValidationError(_('Invalid rules.'))
    cleaned = {}
    for name, value in data.items():
        if name in BOUNDS:
            cleaned[name] = _int(value, name)
        elif name in BOOLEANS:
            cleaned[name] = _bool(value, name)
        elif name == 'languages':
            if not isinstance(value, list) or len(value) > MAX_LANGUAGES or \
                    not all(isinstance(key, str) and 0 < len(key) <= 6 for key in value):
                raise ValidationError(_('Invalid value for %(name)s.') % {'name': LABELS[name]})
            cleaned[name] = sorted(set(value))
    return cleaned


# How a tournament gets the problems of its matches when it uses a problem bank. Stored in the same JSON as the
# rules of the tournament and of each round, but never merged into the rules of a game (merge() only copies the
# keys of DEFAULTS): they say how problems are chosen, not how a game is played.
#   auto_draw    the tournament gives each game its problems from the bank by itself (tournament only)
#   draw_count   problems per game (tournament default, a round can change it)
#   level_min    lowest and highest difficulty of the bank to draw from (per round; empty = any)
#   level_max
DRAW_KEYS = ('auto_draw', 'draw_count', 'level_min', 'level_max')
DRAW_BOUNDS = {'draw_count': (1, 5), 'level_min': (1, 5), 'level_max': (1, 5)}


def clean_draw(data):
    """Validated copy of the problem drawing settings in `data`. A key set to None or '' is removed."""
    if data is None:
        return {}
    if not isinstance(data, dict):
        raise ValidationError(_('Invalid settings.'))
    cleaned = {}
    for name in DRAW_KEYS:
        if name not in data:
            continue
        value = data[name]
        if value in (None, ''):
            cleaned[name] = None
        elif name == 'auto_draw':
            cleaned[name] = bool(value) if isinstance(value, bool) else value in ('true', 'on', '1', 1)
        else:
            if isinstance(value, str) and value.strip().isdigit():
                value = int(value.strip())
            low, high = DRAW_BOUNDS[name]
            if isinstance(value, bool) or not isinstance(value, int) or not low <= value <= high:
                raise ValidationError(_('Problems per game and difficulty go from %(low)s to %(high)s.') %
                                      {'low': low, 'high': high})
            cleaned[name] = value
    low, high = cleaned.get('level_min'), cleaned.get('level_max')
    if low and high and low > high:
        raise ValidationError(_('The lowest difficulty cannot be above the highest.'))
    return cleaned


def with_draw(stored, rules=None, draw=None):
    """`stored` with its game rules replaced by `rules` (if given) and its drawing settings updated by `draw`.

    Saving the game rules from the form must not lose the drawing settings, and the other way round.
    """
    stored = dict(stored or {})
    result = {key: value for key, value in stored.items() if key not in DRAW_KEYS} if rules is None else dict(rules)
    for key in DRAW_KEYS:
        if key in stored:
            result[key] = stored[key]
    for key, value in (draw or {}).items():
        if value is None:
            result.pop(key, None)
        else:
            result[key] = value
    return result


def merge(*layers):
    rules = dict(DEFAULTS)
    for layer in layers:
        if layer:
            for name, value in layer.items():
                if name in DEFAULTS:
                    rules[name] = value
    return rules


def match_rules(match):
    """The rules a new game of this match would get."""
    layers = []
    if match.tournament_id:
        layers.append(match.tournament.rules)
    if match.round_id:
        layers.append(match.round.rules)
    layers.append(match.rules)
    return merge(*layers)


def clean_points(value):
    if isinstance(value, bool):
        raise ValidationError(_('Invalid points.'))
    if isinstance(value, str) and value.strip().isdigit():
        value = int(value.strip())
    if not isinstance(value, int) or not POINT_BOUNDS[0] <= value <= POINT_BOUNDS[1]:
        raise ValidationError(_('Points must be between %(low)s and %(high)s.') %
                              {'low': POINT_BOUNDS[0], 'high': POINT_BOUNDS[1]})
    return value


def clean_best_of(value):
    if isinstance(value, str) and value.strip().isdigit():
        value = int(value.strip())
    if isinstance(value, bool) or value not in BEST_OF_CHOICES:
        raise ValidationError(_('Best of must be an odd number between 1 and 9.'))
    return value
