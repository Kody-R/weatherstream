import copy
import json
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from PIL import Image
from fastapi import FastAPI
from fastapi.testclient import TestClient
from app.config import DEFAULT_SETTINGS, ConfigStore
from app.audio import AudioCache, AudioService, ProgramAudioMixer, PCM_RATE, Script, stinger_pcm
from app.audio_config import DEFAULT_AUDIO, normalize_audio
from app.audio_api import audio_router
from app.audio_captions import draw_caption
from app.narration import NarrationPlanner, allowed
from app.speech import WeatherSpeechFormatter as F, severe_script


def settings():
    s=copy.deepcopy(DEFAULT_SETTINGS); s['audio'].update(enabled=True,mode='full_weathercast')
    return s


def context():
    return {'settings':settings(),'story':{'id':'rain'},'duration':18,'primary':{
        'location':{'name':'Ruston','timezone':'America/Chicago'},
        'current':{'temperature_2m':82,'description':'Partly cloudy','time':'2026-09-29T16:00','wind_speed_10m':9,'wind_cardinal':'SSW'},
        'hourly':{'time':['2026-09-29T15:00','2026-09-29T16:00','2026-09-29T17:00'],
                  'temperature_2m':[99,84,85],'precipitation_probability':[100,20,70], 'precipitation':[1,.1,.15]},
        'daily':{'temperature_2m_max':[88,89,87]},'air_quality':{'aqi':42},
        'nws':{'periods':[{'name':'Tonight','shortForecast':'Showers likely'}]},
        'rivers':{'gauges':[{'name':'Ouachita River','gage_height_ft':27.4}]},
        'climate':{'normal_high_f':85}}}


class FakeProvider:
    def synthesize(self,text,profile,path): path.write_bytes(b'\x01\0\x01\0'*44100)
    def health(self): return {'ready':True}
    def available_voices(self): return ['test']


class AudioTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.manager=SimpleNamespace(config_store=SimpleNamespace(get=settings))
        self.service=AudioService(self.manager,Path(self.temp.name)/'audio'); self.service.providers['piper']=FakeProvider()
        self.addCleanup(lambda:self.service.executor.shutdown(wait=True))
        self.addCleanup(lambda:self.service.alert_executor.shutdown(wait=True))
        self.d=self.service.director('test'); self.d.settings=settings()
    def ready(self,script):
        key,_=self.service.request(script,settings(),'test','rev')
        self.service.executor.submit(lambda:None).result(5)
        return key,self.service.cache.read(key)
    def test_migration_preserves_sections_and_legacy(self):
        path=Path(self.temp.name)/'settings.json'
        old={'version':27,'tts':{'enabled':True,'voice':'custom','speed':1.1},'studio':{'sequences':{'local':['current']}},'channels':{'max_zip_channels':7}}
        path.write_text(json.dumps(old))
        with patch('app.config.CONFIG_DIR',path.parent),patch('app.config.SETTINGS_PATH',path): s=ConfigStore().get()
        self.assertEqual(s['version'],28); self.assertTrue(s['audio']['enabled']); self.assertEqual(s['audio']['mode'],'local_on_8s')
        self.assertEqual(s['tts']['voice'],'custom'); self.assertEqual(s['studio']['sequences']['local'],['current']); self.assertEqual(s['channels']['max_zip_channels'],7)
    def test_safe_defaults(self): self.assertFalse(DEFAULT_SETTINGS['audio']['enabled'])
    def test_modes(self):
        cfg=copy.deepcopy(DEFAULT_AUDIO); cfg['enabled']=True
        for mode in ['off','minimal','local_on_8s','full_weathercast','severe_only']:
            cfg['mode']=mode
            self.assertEqual(allowed(cfg,'current'),mode=='full_weathercast')
            self.assertEqual(allowed(cfg,'alert',severe=True),mode!='off')
        cfg['mode']='local_on_8s'; self.assertTrue(allowed(cfg,'current',local8=True))
    def test_formatting(self):
        self.assertEqual(F.temperature(82),'82 degrees'); self.assertIn('south-southwest',F.wind(12,'SSW',24))
        self.assertEqual(F.rainfall(.28),'about a quarter inch'); self.assertIn('70 percent',F.probability(70))
        self.assertEqual(F.height(27.4),'27.4 feet'); self.assertEqual(F.temperature(float('nan')),'')
        self.assertEqual(F.probability(120),''); self.assertIn('miles',F.distance(30)); self.assertIn('moving north',F.motion(0,20))
    def test_time_and_expiration_timezone(self):
        self.assertIn('3:45 PM',F.time('2026-09-29T20:45:00Z','America/Chicago'))
    def test_story_uses_existing_ids(self):
        planner=NarrationPlanner(); script=planner.plan('story_brief',context(),settings()['audio'])
        self.assertIn('Rain Approaching',script.text)
    def test_duplicate_concepts_and_reset(self):
        p=NarrationPlanner(); ctx=context(); p.plan('story_brief',ctx,settings()['audio'])
        current=p.plan('current',ctx,settings()['audio']); self.assertNotIn('82',current.text)
        self.assertIsNone(p.plan('current',ctx,settings()['audio'])); p.reset()
        self.assertIn('82',p.plan('current',ctx,settings()['audio']).text)
    def test_slide_scripts(self):
        for slide in ['current','hourly','precipitation','rain_accumulation','seven_day','nws_forecast','air_quality','local_rivers','climate_context']:
            with self.subTest(slide=slide): self.assertIsNotNone(NarrationPlanner().plan(slide,context(),settings()['audio']))
    def test_hourly_excludes_past(self):
        script=NarrationPlanner().plan('hourly',context(),settings()['audio']); self.assertNotIn('99',script.text)
    def test_missing_data_silent(self): self.assertIsNone(NarrationPlanner().plan('current',{'primary':{}},settings()['audio']))
    def test_warning_official_instruction_exact(self):
        instruction='Take shelter now. Do not wait for confirmation.'
        text=severe_script({'event':'Tornado Warning','areaDesc':'Lincoln Parish','instruction':instruction,'expires':'2026-09-29T20:45:00Z'},'America/Chicago')
        self.assertTrue(text.endswith(instruction)); self.assertIn('3:45 PM',text)
        self.assertNotIn('shelter',severe_script({'event':'Warning','areaDesc':'Area'}))
    def test_cache_key_all_voice_parameters(self):
        a=AudioCache.key('a','piper',{'speed':1,'voice':'a'})
        self.assertEqual(a,AudioCache.key('a','piper',{'voice':'a','speed':1}))
        self.assertNotEqual(a,AudioCache.key('a','piper',{'speed':1.1,'voice':'a'}))
    def test_expiration_and_corruption(self):
        cache=self.service.cache; cache.put('a',b'\0'*8,{},1)
        self.assertIsNone(cache.read('a',time.time()+2)); self.assertEqual(cache.cleanup(now=time.time()+2),1)
        cache.put('b',b'\0'*8,{},100); (cache.root/'b.s16le').write_bytes(b'x'); self.assertIsNone(cache.read('b'))
    def test_cache_bounded(self):
        for i in range(5): self.service.cache.put(str(i),b'\0'*8,{},100)
        self.assertEqual(self.service.cache.cleanup(2),3)
    def test_provider_failure_and_retry_backoff(self):
        self.service.providers['piper'].synthesize=lambda *a: (_ for _ in ()).throw(RuntimeError('intentional outage'))
        key,clip=self.ready(Script('current','Example.',set()))
        self.assertIsNone(clip); self.assertEqual(self.service.status(settings())['state'],'DEGRADED')
        self.assertIn(key,self.service.failed)
    def test_disabled_and_offline_health(self):
        self.assertEqual(self.service.status(DEFAULT_SETTINGS)['state'],'DISABLED')
        self.service.providers.clear(); self.assertEqual(self.service.status(settings())['state'],'OFFLINE')
    def test_duration_rejects_overlong_no_truncation(self):
        script=Script('current','Current weather.',{'temp'}); key,_=self.ready(script)
        self.d.scripts={'current':script}; self.d.keys={'current':key}
        self.assertIsNone(self.d.get_audio_for_slide('current',.5)); self.assertIsNotNone(self.d.get_audio_for_slide('current',2))
    def test_severe_interrupts_and_updates(self):
        self.d.play(Script('current','Normal.',set()),b'\0'*PCM_RATE)
        alert={'id':'a','event':'Tornado Warning','areaDesc':'Lincoln Parish'}
        self.d.interrupt(alert,settings()); self.assertEqual(self.d.pending,b'')
        self.service.alert_executor.submit(lambda:None).result(5); self.d.interrupt(alert,settings())
        self.assertEqual(self.d.bus,'alert'); self.assertTrue(self.d.pending)
        alert['instruction']='Take shelter now.'; self.d.interrupt(alert,settings()); self.assertFalse(self.d.pending)
    def test_alert_priority_over_manual_voice(self):
        self.d.play(Script('alert','Warning',set(),official=True),b'\0'*20,'alert')
        self.assertFalse(self.d.play(Script('current','Other',set()),b'\0'*20))
    def test_return_to_program(self):
        self.d.severe_id='a'; self.d.play(Script('alert','Warning',set()),b'\0'*20,'alert')
        self.d.return_to_program_audio(); self.assertFalse(self.d.pending); self.assertIsNone(self.d.severe_id)
    def test_desk_priority_and_profile(self):
        self.assertEqual(NarrationPlanner.priority(['current','local_rivers','alert'],'flood')[0],'alert')
        script=NarrationPlanner().plan('current',context(),settings()['audio'],desk='severe'); self.assertEqual(script.profile,'severe')
    def test_override_scope_and_official_lock(self):
        self.d.override('hourly','Operator script.'); self.assertNotIn('current',self.d.overrides)
        with self.assertRaises(ValueError): self.d.override('alert','Edited warning')
        self.d.override('hourly',None); self.assertFalse(self.d.overrides)
    def test_ducking_gain_and_clipping(self):
        m=ProgramAudioMixer(); cfg=settings()['audio']; tone=b'\xff\x7f'*4410
        m.mix(b'',tone,b'',b'',cfg); normal=m.music_level
        for _ in range(30): m.mix(tone,tone,b'',b'',cfg)
        self.assertLess(m.music_level,10**(cfg['music_db']/20))
        import array
        out=array.array('h'); out.frombytes(m.mix(tone,tone,tone,tone,cfg)); self.assertLessEqual(max(out),30000)
    def test_captions_safe_position(self):
        image=Image.new('RGB',(1280,720),'black'); draw_caption(image,'Currently 82 degrees in Ruston.')
        self.assertEqual(image.getpixel((100,700)),(0,0,0)); self.assertNotEqual(image.getbbox(),None)
        self.d.settings['audio']['captions']='severe_only'; self.d.play(Script('current','Current',set()),b'\0'*20)
        self.assertEqual(self.d.caption(),'')
    def test_stingers_original_duration(self):
        for kind in ['station','update','severe','local','tropical']:
            self.assertAlmostEqual(len(stinger_pcm(kind))/PCM_RATE,.9)
    def test_api_preview_does_not_change_live(self):
        script=Script('current','Current weather.',{'temp'}); key,_=self.ready(script)
        import threading
        worker=SimpleNamespace(audio=self.d,prepare_audio=lambda:None)
        streamer=SimpleNamespace(_lock=threading.RLock(),_workers={'test':worker})
        app=FastAPI(); app.include_router(audio_router(streamer,SimpleNamespace(get=settings)))
        with TestClient(app) as client:
            self.assertEqual(client.get('/api/studio/audio/test').status_code,200)
            res=client.get('/api/studio/audio/test/preview/'+key); self.assertEqual(res.status_code,200); self.assertTrue(res.content.startswith(b'RIFF'))
            self.assertIsNone(self.d.current)
            self.assertEqual(client.post('/api/studio/audio/test',json={'action':'edit','slide':'hourly','text':'Manual.'}).status_code,200)
            self.assertEqual(client.post('/api/studio/audio/test',json={'action':'edit','slide':'alert','text':'Bad.'}).status_code,400)
            self.assertEqual(client.get('/api/studio/audio/missing').status_code,404)

    def worker(self, cfg=None):
        from app.renderer import WeatherRenderer
        from app.streamer import ChannelWorker
        from app.tts import TTSManager
        cfg=cfg or settings(); p=context()['primary']; p['location']['id']='ruston'
        cfg['primary_location_id']='ruston'; cfg['locations']=[p['location']]
        snapshot={'locations':{'ruston':p},'alerts':[],'alerts_by_location':{'ruston':[]}}
        class Source:
            def __init__(self,value): self.value=value
            def get(self): return copy.deepcopy(self.value)
            def revision(self): return 1
            def snapshot_if_changed(self,previous): return (1,None if previous==1 else self.get())
        store=Source(cfg); renderer=WeatherRenderer(store,Source(snapshot))
        tts=TTSManager.__new__(TTSManager); tts.config_store=store; tts.audio_service=self.service
        return ChannelWorker(store,renderer,tts,'test','ruston','local')

    def test_worker_preparation_and_duration_extension(self):
        w=self.worker(); w.prepare_audio()
        self.assertIsNone(w.audio.last_error)
        self.service.executor.submit(lambda:None).result(5); w.prepare_audio()
        self.assertTrue(w.audio.scripts)
        cfg,snap=w.renderer._channel_context('ruston','local')
        baseline=dict(w.renderer._sequence(cfg,snap,time.time()))
        w.renderer.audio_durations[('ruston','local')]={'current':999}
        self.assertLessEqual(dict(w.renderer._sequence(cfg,snap,time.time()))['current'],baseline['current']+3)

    def test_local8_phase_uses_measured_duration(self):
        w=self.worker(); cfg=w._effective_settings(settings()); primary=context()['primary']
        cfg['presentation']['scheduled_updates']['enabled']=True
        w._start_local8('block',cfg,primary,time.monotonic())
        state=w._local8_audio_snapshot(); self.assertTrue(state['active'])
        self.assertTrue(w._mark_local8_audio_queued(state['phase_token'],19))
        self.assertEqual(w._local8_audio_duration,19)
        self.assertFalse(w._mark_local8_audio_queued(state['phase_token']-1,2))

    def test_manual_takeover_clears_audio(self):
        w=self.worker(); w.audio.play(Script('current','Normal.',set()),b'\0'*PCM_RATE)
        ok,_=w.set_manual_takeover('hourly',30)
        self.assertTrue(ok); self.assertFalse(w.audio.pending)

    def test_desk_change_invalidates_pending(self):
        ctx=context(); self.d.prepare_story_audio(ctx,[{'slide':'current','duration_seconds':15}],1)
        self.d.play(Script('current','Normal.',set()),b'\0'*PCM_RATE)
        ctx['settings']['_channel_mode']='event_flood'
        self.d.prepare_story_audio(ctx,[{'slide':'current','duration_seconds':15}],2)
        self.assertFalse(self.d.pending); self.assertEqual(self.d.desk,'flood')

    def test_disabled_worker_prepares_no_audio(self):
        w=self.worker(copy.deepcopy(DEFAULT_SETTINGS)); w.prepare_audio()
        self.assertFalse(w.audio.scripts); self.assertFalse(self.service.inflight)

    def test_normal_transition_waits_for_admitted_clip(self):
        self.d.token=(0,0,'current',None); self.d.cycle=0
        self.d.play(Script('current','Normal.',set()),b'\0'*PCM_RATE)
        self.d.tick({'current_slide':'hourly','elapsed_seconds':2,'remaining_seconds':10},(0,1,'hourly',None))
        self.assertTrue(self.d.pending); self.assertEqual(self.d.current.slide,'current')


if __name__=='__main__': unittest.main()
