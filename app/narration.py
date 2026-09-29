"""Fact-based scripts and rundown concept memory, independent of synthesis."""
from __future__ import annotations

from dataclasses import dataclass, field
from app.speech import WeatherSpeechFormatter as F, clean, number, severe_script
from app.story import STORY_TITLES

MODES = {'off', 'minimal', 'local_on_8s', 'full_weathercast', 'severe_only'}
DESK_SLIDES = {
    'severe': ['alert', 'event_summary', 'wind_outlook', 'precipitation'],
    'flood': ['alert', 'rain_accumulation', 'local_rivers', 'precipitation'],
    'winter': ['alert', 'nws_forecast', 'temperature_trend', 'hourly'],
    'heat': ['alert', 'current', 'humidity_outlook', 'seven_day'],
    'wildfire': ['alert', 'wind_outlook', 'humidity_outlook', 'air_quality'],
    'tropical': ['alert', 'tropical_update', 'tropical_systems', 'tropical_local'],
}


@dataclass
class Script:
    slide: str
    text: str
    concepts: set[str] = field(default_factory=set)
    profile: str = 'broadcast'
    official: bool = False
    manual: bool = False


def allowed(config, slide, local8=False, severe=False):
    if not config.get('enabled') or config.get('mode', 'off') == 'off': return False
    mode = config.get('mode')
    if severe: return bool(config.get('severe_alerts', True))
    if mode == 'severe_only': return False
    if mode == 'local_on_8s': return local8
    if mode == 'minimal' and slide not in {'station_id', 'story_brief', 'event_summary', 'nws_forecast'}: return False
    return bool((config.get('screens') or {}).get(slide, True))


class NarrationPlanner:
    def __init__(self): self.spoken = set()
    def reset(self): self.spoken.clear()

    def plan(self, slide, context, config, *, reserve=True, desk=None):
        if not allowed(config, slide): return None
        p = context.get('primary') or {}; story = context.get('story') or {}
        cur, hourly, daily = (p.get(k) or {} for k in ('current', 'hourly', 'daily'))
        facts = []
        def add(key, text):
            if text and key not in self.spoken: facts.append((key, text))
        def temp(key, label, value):
            spoken = F.temperature(value)
            if spoken: add(key, f'{label} {spoken}.')
        def values(key):
            # Only current/future forecast hours, never the morning already past.
            times = hourly.get('time') or []; now = str(cur.get('time') or '')
            return [(i, number(v)) for i, v in enumerate(hourly.get(key) or [])
                    if number(v) is not None and (not now or (i < len(times) and str(times[i]) >= now))][:24]
        loc = clean((p.get('location') or {}).get('name') or 'your area')
        if slide == 'station_id':
            add('station', f"This is {clean((context.get('settings') or {}).get('station_name') or 'Roller Weather Network')}.")
        elif slide == 'story_brief' and config.get('story_intro', True):
            sid = story.get('id')
            if sid in STORY_TITLES:
                add('story', f"Your weather focus: {clean(STORY_TITLES[sid])}.")
                temp('current_temperature', f'Currently in {loc},', cur.get('temperature_2m'))
        elif slide == 'current':
            temp('current_temperature', f'Currently in {loc},', cur.get('temperature_2m'))
            if cur.get('description'): add('current_condition', clean(cur['description']) + '.')
            add('current_wind', F.wind(cur.get('wind_speed_10m'), cur.get('wind_cardinal'), cur.get('wind_gusts_10m')))
        elif slide in {'hourly', 'temperature_trend'}:
            vv = values('temperature_2m')[:6]
            if vv: add('hourly_range', f'Over the next {len(vv)} forecast hours, temperatures range from {min(v for _,v in vv):.0f} to {max(v for _,v in vv):.0f} degrees.')
        elif slide == 'precipitation':
            vv = values('precipitation_probability')[:12]
            if vv:
                i, peak = max(vv, key=lambda x:x[1]); times = hourly.get('time') or []
                when = F.time(times[i]) if i < len(times) else ''
                if peak > 0: add('rain_peak', f'The next {len(vv)} forecast hours peak at {F.probability(peak)}' + (f' around {when}.' if when else '.'))
        elif slide == 'rain_accumulation':
            vv = values('precipitation')
            if vv: add('rainfall_total', f'The next {len(vv)} forecast hours total {F.rainfall(sum(v for _,v in vv))} of precipitation.')
        elif slide == 'wind_outlook':
            vv = values('wind_gusts_10m')[:12]
            if vv: add('peak_gust', f'Forecast gusts reach {max(v for _,v in vv):.0f} miles per hour over the next {len(vv)} hours.')
        elif slide == 'humidity_outlook':
            from app.weather import dew_point_f
            temp('dew_point', 'The current dew point is', dew_point_f(cur.get('temperature_2m'),cur.get('relative_humidity_2m')))
            temp('feels_like', 'The current feels like temperature is', cur.get('apparent_temperature'))
        elif slide == 'seven_day':
            highs = [number(v) for v in (daily.get('temperature_2m_max') or [])[:7]]
            highs = [v for v in highs if v is not None]
            if highs: add('week_highs', f'Over the next {len(highs)} forecast days, highs range from {min(highs):.0f} to {max(highs):.0f} degrees.')
        elif slide == 'nws_forecast':
            guidance = p.get('nws') or {}
            periods = guidance.get('periods') or [] if isinstance(guidance, dict) else []
            if periods:
                row=periods[0]; text=clean(row.get('shortForecast'))
                if text: add('nws_period', f"{clean(row.get('name'))}: {text}.")
        elif slide == 'air_quality':
            aq = p.get('air_quality') or {}
            spoken = F.aqi(aq.get('aqi'))
            if spoken: add('aqi', f'Current air quality has {spoken}.')
        elif slide == 'local_rivers':
            river = p.get('rivers') or {}; gauges = river.get('gauges') or []
            if gauges:
                row=gauges[0]; height=F.height(row.get('gage_height_ft'))
                if height: add('river_height', f"{clean(row.get('name'))} is at {height}.")
                hist=river.get('primary_history') or []
                if len(hist)>1 and number(hist[-1].get('value')) is not None and number(hist[0].get('value')) is not None:
                    delta=float(hist[-1]['value'])-float(hist[0]['value'])
                    add('river_trend', f"It has {'risen' if delta>=0 else 'fallen'} {F.height(abs(delta))} across the displayed observation period.")
        elif slide == 'climate_context':
            climate=p.get('climate') or {}
            temp('normal_high', 'The official climate normal high is', climate.get('normal_high_f'))
            temp('normal_low', 'The normal low is', climate.get('normal_low_f'))
        elif slide in {'today_so_far', 'past_24_hours'}:
            summary=context.get(slide) or {}
            if summary.get('samples'):
                label='Today so far' if slide=='today_so_far' else 'In the past 24 hours'
                temp('observed_high_'+slide, label+', the observed high was', summary.get('high'))
                temp('observed_low_'+slide, 'The observed low was', summary.get('low'))
        elif slide == 'event_summary':
            for alert in (context.get('snapshot') or {}).get('alerts') or []:
                event,area=clean(alert.get('event')),clean(alert.get('areaDesc'))
                if event and area: add('event_'+str(alert.get('id')), f'{event} for {area}.')
        elif slide.startswith('tropical_'):
            tropical=(context.get('settings') or {}).get('_tropical') or {}
            systems=tropical.get('systems') or []
            if systems and slide in {'tropical_update','tropical_systems'}:
                storm=systems[0]; name=clean(storm.get('name'))
                intensity=F.tropical_wind(storm.get('intensity_mph'))
                if name and intensity: add('tropical_intensity',f'{name} has {intensity}.')
                motion=F.motion(storm.get('movement_degrees'),storm.get('movement_mph'))
                if name and motion: add('tropical_motion',f'{name} is {motion}.')
        if not facts: return None
        budget=max(4, int(float(context.get('duration',15)) * 2.1 * float(config.get('speed',1))))
        selected=[]; words=0
        for key,text in facts:
            count=len(text.split())
            if words+count<=budget: selected.append((key,text)); words+=count
        if not selected: return None
        keys={key for key,_ in selected}
        if reserve: self.spoken.update(keys)
        profile=(config.get('desk_profiles') or {}).get(desk, config.get('profile','broadcast'))
        return Script(slide, ' '.join(text for _,text in selected), keys, profile)

    @staticmethod
    def priority(slides, desk):
        preferred=DESK_SLIDES.get(desk, [])
        return sorted(slides, key=lambda slide: preferred.index(slide) if slide in preferred else len(preferred))
