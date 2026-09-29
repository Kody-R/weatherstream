"""Deterministic weather speech. Missing observations produce no invented facts."""
from __future__ import annotations

import datetime as dt
import math
import re
from zoneinfo import ZoneInfo


def number(value):
    try:
        value = float(value)
        return value if math.isfinite(value) else None
    except (TypeError, ValueError):
        return None


def clean(value):
    text = re.sub(r"\s+", " ", str(value or "")).strip()
    for old, new in (("°F", " degrees Fahrenheit"), ("°C", " degrees Celsius"),
                     ("%", " percent"), ("mph", "miles per hour")):
        text = text.replace(old, new)
    return text


class WeatherSpeechFormatter:
    DIRECTIONS = dict(zip(
        'N NNE NE ENE E ESE SE SSE S SSW SW WSW W WNW NW NNW'.split(),
        ['north', 'north-northeast', 'northeast', 'east-northeast', 'east',
         'east-southeast', 'southeast', 'south-southeast', 'south', 'south-southwest',
         'southwest', 'west-southwest', 'west', 'west-northwest', 'northwest', 'north-northwest']))

    @staticmethod
    def temperature(value):
        n = number(value)
        return '' if n is None else f'{n:.0f} degrees'

    @classmethod
    def direction(cls, value):
        n = number(value)
        if n is not None:
            return list(cls.DIRECTIONS.values())[int((n % 360 + 11.25) // 22.5) % 16]
        return cls.DIRECTIONS.get(str(value).upper(), '')

    @classmethod
    def wind(cls, speed, direction=None, gust=None):
        n, g = number(speed), number(gust)
        if n is None: return ''
        text = f'{cls.direction(direction)} winds around {n:.0f} miles per hour'.strip()
        if g is not None and g > n: text += f', with gusts up to {g:.0f}'
        return text + '.'

    @staticmethod
    def probability(value):
        n = number(value)
        return f'a {n:.0f} percent chance of precipitation' if n is not None and 0 <= n <= 100 else ''

    @staticmethod
    def rainfall(value):
        n = number(value)
        if n is None or n < 0: return ''
        if 0.22 <= n <= 0.28: return 'about a quarter inch'
        if 0.45 <= n <= 0.55: return 'about half an inch'
        return f'{n:.2f} inches'

    @staticmethod
    def height(value):
        n = number(value)
        return '' if n is None else f'{n:.1f} feet'

    @staticmethod
    def aqi(value):
        n = number(value)
        return '' if n is None or n < 0 else f'an air quality index of {n:.0f}'

    @staticmethod
    def time(value, timezone=None, include_date=False):
        try:
            d = dt.datetime.fromisoformat(str(value).replace('Z', '+00:00'))
            if timezone and d.tzinfo:
                try: d = d.astimezone(ZoneInfo(timezone))
                except (KeyError, ValueError): pass  # Retain the explicit source offset.
            label = d.strftime('%I:%M %p').lstrip('0').replace(':00', '')
            if d.tzinfo: label += ' ' + d.strftime('%Z')
            return (d.strftime('%A, %B ') + str(d.day) + ' at ' if include_date else '') + label
        except (ValueError, TypeError, KeyError): return ''

    @staticmethod
    def date(value):
        try: return dt.date.fromisoformat(str(value)[:10]).strftime('%A, %B %d')
        except (ValueError, TypeError): return ''

    @classmethod
    def motion(cls, direction, speed):
        d, n = cls.direction(direction), number(speed)
        return f'moving {d} at {n:.0f} miles per hour' if d and n is not None else ''

    @staticmethod
    def tropical_wind(value):
        n = number(value)
        return '' if n is None else f'maximum sustained winds of {n:.0f} miles per hour'

    @staticmethod
    def distance(value):
        n = number(value)
        return '' if n is None else f'{n:.0f} miles'


def severe_script(alert, timezone=None):
    """Keep official action language verbatim; never infer shelter instructions."""
    event, area = clean(alert.get('event')), clean(alert.get('areaDesc'))
    if not event or not area: return ''
    expires = WeatherSpeechFormatter.time(alert.get('expires') or alert.get('ends'), timezone, True)
    text = f'{event} for {area}' + (f' until {expires}' if expires else '') + '.'
    instruction = re.sub(r'\s+', ' ', str(alert.get('instruction') or '')).strip()
    return text + (' ' + instruction if instruction else '')
