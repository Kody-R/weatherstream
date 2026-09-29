"""Schema 28 audio defaults and normalization."""
import copy
from app.narration import MODES

PROFILES = {
    'broadcast': {'name': 'RWN Broadcast', 'speed': 1.0, 'volume': .92, 'style': 'warm, clear'},
    'local': {'name': 'RWN Local', 'speed': 1.03, 'volume': .92, 'style': 'friendly'},
    'news': {'name': 'RWN News', 'speed': 1.0, 'volume': .92, 'style': 'neutral'},
    'severe': {'name': 'RWN Severe', 'speed': .94, 'volume': .96, 'style': 'deliberate'},
}
DEFAULT_AUDIO = {
    'enabled': False, 'mode': 'off', 'provider': 'piper', 'profile': 'broadcast',
    'speed': 1.0, 'profiles': PROFILES, 'screens': {}, 'story_intro': True,
    'severe_alerts': True, 'interrupt': True, 'immediate_warning': True,
    'official_templates': True, 'sonic_branding': False, 'severe_sounder': False,
    'captions': 'off', 'max_extension': 3, 'cache_hours': 6, 'cache_items': 128,
    'background_mode': 'none', 'bed_file': '', 'ducking': True,
    'music_db': -24, 'duck_db': -34, 'voice_gain': 1.0, 'alert_gain': 1.0,
    'stinger_gain': .5, 'fade_seconds': .5,
    'desk_profiles': {'severe':'severe','flood':'news','winter':'news','heat':'local','wildfire':'news','tropical':'news'},
}


def normalize_audio(value):
    cfg=copy.deepcopy(DEFAULT_AUDIO); cfg.update(value or {})
    if cfg['mode'] not in MODES: cfg['mode']='off'
    if cfg['profile'] not in PROFILES: cfg['profile']='broadcast'
    if cfg['captions'] not in {'off','narration','severe_only','always'}: cfg['captions']='off'
    if cfg['background_mode'] not in {'none','local_forecast','full_weathercast','station_id'}: cfg['background_mode']='none'
    for key,lo,hi in [('speed',.7,1.35),('max_extension',0,5),('cache_hours',1,168),('cache_items',8,512),
                      ('music_db',-60,0),('duck_db',-60,0),('voice_gain',0,1.5),('alert_gain',0,1.5),('stinger_gain',0,1),('fade_seconds',.05,3)]:
        try: cfg[key]=max(lo,min(hi,float(cfg[key])))
        except (ValueError,TypeError): cfg[key]=DEFAULT_AUDIO[key]
    for key in ('profiles','screens','desk_profiles'):
        if not isinstance(cfg.get(key),dict): cfg[key]=copy.deepcopy(DEFAULT_AUDIO[key])
    # Official wording and emergency priority are safety invariants, not override switches.
    cfg['official_templates']=True; cfg['interrupt']=True
    return cfg

