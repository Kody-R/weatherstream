"""RWN Voice & Audio: optional, bounded background generation and channel direction.

PCM contract matches the existing announcement pipe: stereo signed 16-bit, 44100 Hz.
No synthesis or file conversion runs on the video thread.
"""
from __future__ import annotations

import copy
import hashlib
import json
import logging
import math
import os
import re
import struct
import subprocess
import threading
import tempfile
import time
import wave
from abc import ABC, abstractmethod
from array import array
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict
from pathlib import Path

from app.config import CONFIG_DIR
from app.event_identity import identity_key
from app.narration import NarrationPlanner, Script, allowed, MODES
from app.speech import severe_script

log = logging.getLogger(__name__)
PCM_RATE = 44100 * 4
AUDIO_ROOT = Path(os.environ.get('WEATHERSTREAM_AUDIO', str(CONFIG_DIR / 'audio')))
from app.audio_config import PROFILES, DEFAULT_AUDIO, normalize_audio


class TtsProvider(ABC):
    name = 'unknown'
    @abstractmethod
    def synthesize(self, text, profile, output_path): pass
    @abstractmethod
    def health(self): pass
    @abstractmethod
    def available_voices(self): pass


class PiperProvider(TtsProvider):
    name = 'piper'
    def __init__(self, manager): self.manager=manager
    def synthesize(self, text, profile, output_path):
        settings=self.manager.config_store.get()
        settings.setdefault('tts',{}).update({k:profile[k] for k in ('voice','speed','volume') if k in profile})
        wav=output_path.with_suffix('.wav')
        try:
            self.manager._run_piper(text,settings,wav)
            self.manager._wav_to_pcm(wav,output_path,float(profile.get('volume',.92)))
        finally: wav.unlink(missing_ok=True)
    def health(self):
        state=self.manager.status()
        return {'ready':state['package_available'] and state['voice_installed'], 'error':state['last_error']}
    def available_voices(self):
        from app.tts import VOICE_DIR
        return sorted(p.stem for p in VOICE_DIR.glob('*.onnx'))


class AudioCache:
    def __init__(self, root):
        self.root=Path(root); self.root.mkdir(parents=True,exist_ok=True)
        self.lock=threading.RLock()
    @staticmethod
    def key(text, provider, profile):
        return hashlib.sha256(json.dumps([1,text,provider,profile],sort_keys=True,separators=(',',':')).encode()).hexdigest()
    def read(self,key,now=None):
        with self.lock:
            try:
                meta=json.loads((self.root/f'{key}.json').read_text())
                if meta['expires'] <= (time.time() if now is None else now): return None
                pcm=(self.root/f'{key}.s16le').read_bytes()
                if not pcm or len(pcm)%4 or len(pcm)!=meta['bytes'] or hashlib.sha256(pcm).hexdigest()!=meta.get('pcm_hash'): return None
                return pcm,meta
            except (OSError,ValueError,KeyError): return None
    def put(self,key,pcm,metadata,ttl):
        if not pcm or len(pcm)%4: raise ValueError('Invalid stereo PCM')
        now=time.time(); meta={**metadata,'key':key,'generated':now,'expires':now+ttl,'bytes':len(pcm),'duration':len(pcm)/PCM_RATE,'pcm_hash':hashlib.sha256(pcm).hexdigest()}
        with self.lock:
            tmp=self.root/f'{key}.tmp'; tmp.write_bytes(pcm); tmp.replace(self.root/f'{key}.s16le')
            tmp=self.root/f'{key}.json.tmp'; tmp.write_text(json.dumps(meta),encoding='utf-8'); tmp.replace(self.root/f'{key}.json')
        return meta
    def cleanup(self,limit=128,now=None):
        now=time.time() if now is None else now; removed=0
        with self.lock:
            files=sorted(self.root.glob('*.json'),key=lambda p:p.stat().st_mtime,reverse=True)
            for i,path in enumerate(files):
                try:
                    meta=json.loads(path.read_text()); expired=meta['expires']<=now
                except (ValueError,KeyError): expired=True
                if expired or i>=limit:
                    path.unlink(missing_ok=True); path.with_suffix('.s16le').unlink(missing_ok=True); removed+=1
        return removed


class AudioService:
    def __init__(self, manager, root=None):
        self.root=Path(root or AUDIO_ROOT)
        self.storage_error=None
        try:
            for folder in ('station','generated','alerts','stingers','beds','cache'):
                (self.root/folder).mkdir(parents=True,exist_ok=True)
        except OSError as exc:
            self.storage_error=f'Persistent audio storage unavailable: {exc}'
            self.root=Path(tempfile.mkdtemp(prefix='weatherstream-audio-'))
            for folder in ('station','generated','alerts','stingers','beds','cache'): (self.root/folder).mkdir()
        self.cache=AudioCache(self.root/'cache'); self.providers={'piper':PiperProvider(manager)}
        self.executor=ThreadPoolExecutor(max_workers=1,thread_name_prefix='rwn-audio')
        self.alert_executor=ThreadPoolExecutor(max_workers=1,thread_name_prefix='rwn-alert')
        self.lock=threading.RLock(); self.inflight=set(); self.failed={}; self.last_generation=None
        self.last_error=None; self.directors={}; self.closed=False
    def profile(self,settings,name):
        cfg=normalize_audio(settings.get('audio')); profile=copy.deepcopy(PROFILES.get(name,PROFILES['broadcast']))
        profile.update((cfg.get('profiles') or {}).get(name) or {})
        profile.setdefault('voice',(settings.get('tts') or {}).get('voice','en_US-lessac-medium'))
        profile['speed']=max(.7,min(1.35,float(profile.get('speed',1))*cfg['speed']))
        return profile
    def request(self,script,settings,channel='',revision=''):
        cfg=normalize_audio(settings.get('audio')); profile=self.profile(settings,script.profile)
        key=self.cache.key(script.text,cfg['provider'],profile)
        cached=self.cache.read(key)
        if cached: return key,cached
        with self.lock:
            if self.closed or key in self.inflight or len(self.inflight)>=(28 if script.official else 24) or self.failed.get(key,0)>time.time(): return key,None
            self.inflight.add(key)
        executor=self.alert_executor if script.official else self.executor
        executor.submit(self._generate,key,script,profile,cfg,channel,revision)
        return key,None
    def _generate(self,key,script,profile,cfg,channel,revision):
        path=self.root/'generated'/f'{key}.s16le'
        try:
            provider=self.providers.get(cfg['provider'])
            if provider is None: raise RuntimeError('Configured TTS provider is unavailable')
            provider.synthesize(script.text,profile,path)
            pcm=path.read_bytes()
            if len(pcm)>PCM_RATE*180: raise ValueError('Narration exceeds three minute safety limit')
            self.cache.put(key,pcm,{'script':script.text,'script_hash':hashlib.sha256(script.text.encode()).hexdigest(),
                'slide':script.slide,'profile':script.profile,'provider':cfg['provider'],'channel':channel,'revision':str(revision)},cfg['cache_hours']*3600)
            self.cache.cleanup(int(cfg['cache_items']))
            with self.lock: self.last_generation=time.time(); self.last_error=None; self.failed.pop(key,None)
        except Exception as exc:
            with self.lock:
                self.failed={k:v for k,v in self.failed.items() if v>time.time()}
                self.failed[key]=time.time()+60; self.last_error=str(exc)
            log.warning('RWN audio generation skipped: %s',exc)
        finally:
            path.unlink(missing_ok=True)
            with self.lock: self.inflight.discard(key)
    def director(self,key):
        with self.lock:
            if key not in self.directors: self.directors[key]=RwnAudioDirector(self,key)
            return self.directors[key]
    def status(self,settings):
        cfg=normalize_audio(settings.get('audio')); provider=self.providers.get(cfg['provider'])
        try: health=provider.health() if provider else {'ready':False}
        except Exception: health={'ready':False}
        with self.lock:
            state='DISABLED' if not cfg['enabled'] or cfg['mode']=='off' else 'GENERATING' if self.inflight else 'DEGRADED' if self.last_error or self.storage_error else 'READY' if health.get('ready') else 'OFFLINE'
            return {'state':state,'provider':cfg['provider'],'profile':cfg['profile'],'mode':cfg['mode'],
                'queue':len(self.inflight),'cache_count':len(list(self.cache.root.glob('*.json'))),
                'last_generation':self.last_generation,'error':self.last_error or self.storage_error,'errors':len(self.failed)}
    def close(self):
        self.closed=True; self.executor.shutdown(wait=False,cancel_futures=True)
        self.alert_executor.shutdown(wait=False,cancel_futures=True)


class ProgramAudioMixer:
    """Four logical buses; priority arbitration lives in the director."""
    def __init__(self): self.music_level=0.; self.music_muted=False; self.voice_muted=False
    def mix(self,voice,bed,stinger,alert,cfg):
        length=max(len(voice),len(bed),len(stinger),len(alert),4)
        sources=[]; active=bool(alert or (voice and not self.voice_muted))
        target=0 if self.music_muted else 10**(float(cfg['duck_db'] if active and cfg['ducking'] else cfg['music_db'])/20)
        start=self.music_level; step=min(1,(length/PCM_RATE)/float(cfg['fade_seconds'])); self.music_level+= (target-start)*step
        for data,gain in ((alert,cfg['alert_gain']),(b'' if alert or self.voice_muted else voice,cfg['voice_gain']),
                          (b'' if alert or voice else stinger,cfg['stinger_gain']),(bed,1)):
            values=array('h'); values.frombytes(data+b'\0'*(length-len(data))); sources.append((values,gain))
        out=array('h',[0])*(length//2)
        for i in range(len(out)):
            music_gain=start+(self.music_level-start)*i/max(1,len(out)-1)
            value=sum(vals[i]*gain for vals,gain in sources[:3])+sources[3][0][i]*music_gain
            out[i]=max(-30000,min(30000,int(value)))
        return out.tobytes()


def stinger_pcm(kind='station'):
    # Original soft pentatonic plucks, no sustained attention-tone pair or SAME data.
    notes={'station':[523.25,659.25,783.99], 'update':[659.25,783.99,1046.5],
           'severe':[392.,523.25,587.33], 'local':[523.25,783.99,659.25], 'tropical':[587.33,783.99,1046.5]}
    out=bytearray()
    for freq in notes.get(kind,notes['station']):
        for i in range(13230):
            t=i/44100; envelope=min(1,t/.015)*math.exp(-10*t)
            sample=int(7000*envelope*math.sin(2*math.pi*freq*t))
            out.extend(struct.pack('<hh',sample,sample))
    return bytes(out)


class RwnAudioDirector:
    def __init__(self,service,channel):
        self.service=service; self.channel=channel; self.lock=threading.RLock()
        self.scripts={}; self.keys={}; self.overrides={}; self.revision=None; self.desk=None
        self.current=None; self.pending=b''; self.bus='voice'; self.elapsed=0.; self.token=None
        self.spoken=set(); self.cycle=None; self.severe_id=None; self.last_error=None
        self.mixer=ProgramAudioMixer(); self.next_slide=None; self.settings={}; self.context={}
        self.stingers={}; self.bed=b''; self.bed_offset=0; self.bed_key=None; self.bed_loading=False
        self.last_script=None; self.last_information={}; self.bed_gate=0.
    def prepare_story_audio(self,context,sequence,revision):
        settings=context['settings']; cfg=normalize_audio(settings.get('audio')); desk=identity_key(settings)
        signature=(str(revision),json.dumps(cfg,sort_keys=True),desk,tuple(r['slide'] for r in sequence),json.dumps(self.overrides,sort_keys=True))
        with self.lock:
            self.settings=settings; self.context=context
            if signature==self.revision: return
            if desk!=self.desk: self.stop(); self.spoken.clear()
            self.desk=desk; self.revision=signature; self.scripts={}; self.keys={}
            self.budgets={r['slide']:float((settings.get('slides') or {}).get(r['slide'],r['duration_seconds']))+cfg['max_extension']-2.5 for r in sequence}
            if not cfg['enabled'] or cfg['mode']=='off': self.stop(); return
            planner=NarrationPlanner()
            for row in sequence:
                slide=row['slide']; ctx={**context,'duration':max(1,float(row['duration_seconds'])-2)+cfg['max_extension']}
                script=planner.plan(slide,ctx,cfg,desk=desk)
                if slide in self.overrides and allowed(cfg,slide):
                    script=Script(slide,self.overrides[slide],{'manual:'+slide},cfg['profile'],manual=True)
                if script: self.scripts[slide]=script
            for slide in planner.priority(list(self.scripts),desk):
                script=self.scripts[slide]; key,_=self.service.request(script,settings,self.channel,revision); self.keys[slide]=key
    def refresh_cache(self):
        with self.lock:
            for slide,script in list(self.scripts.items()):
                key,cached=self.service.request(script,self.settings,self.channel,self.revision)
                # Shorten only at complete sentence boundaries, then regenerate in background.
                if cached and cached[1]['duration']>self.budgets.get(slide,12) and not script.official:
                    sentences=re.split(r'(?<=[.!?])\s+',script.text)
                    if len(sentences)>1:
                        script=Script(slide,' '.join(sentences[:-1]),script.concepts,script.profile,manual=script.manual)
                        self.scripts[slide]=script
                        key,_=self.service.request(script,self.settings,self.channel,self.revision)
                self.keys[slide]=key

    def prepare_bed(self, settings):
        cfg=normalize_audio(settings.get('audio'))
        if cfg['background_mode']=='none':
            self.bed=b''; self.bed_key=None; return
        name=str(cfg.get('bed_file') or '')
        base=(self.service.root/'beds').resolve()
        path=(base/name).resolve()
        if not name or not path.is_relative_to(base) or not path.is_file():
            self.bed=b''; return
        key=(str(path),path.stat().st_mtime)
        with self.lock:
            if self.bed_key==key or self.bed_loading: return
            self.bed_loading=True; self.bed_key=key
        def load():
            try:
                proc=subprocess.run(['ffmpeg','-v','error','-i',str(path),'-t','120','-af',
                    'loudnorm=I=-18:TP=-2:LRA=7,afade=t=in:d=0.5', '-f','s16le','-ar','44100','-ac','2','pipe:1'],
                    capture_output=True,timeout=90,check=False)
                if proc.returncode or not proc.stdout or len(proc.stdout)%4: raise ValueError('Background audio could not be decoded')
                with self.lock: self.bed=proc.stdout; self.bed_offset=0
            except Exception as exc:
                with self.lock: self.last_error=str(exc); self.bed=b''
            finally:
                with self.lock: self.bed_loading=False
        self.service.executor.submit(load)

    def bed_block(self,size,local8=False):
        with self.lock:
            cfg=normalize_audio(self.settings.get('audio')); mode=cfg['background_mode']
            active=mode=='full_weathercast' or (mode=='local_forecast' and local8) or (mode=='station_id' and self.token and self.token[2]=='station_id')
            if not self.bed or self.severe_id: return b''
            old_gate=self.bed_gate
            step=size/PCM_RATE/float(cfg['fade_seconds'])
            self.bed_gate=max(0,min(1,self.bed_gate+(step if active else -step)))
            if not old_gate and not self.bed_gate: return b''
            # Fade the last and first half second at each loop boundary.
            values=array('h')
            for offset in range(0,size,4):
                pos=(self.bed_offset+offset)%len(self.bed)
                gain=min(1,pos/(PCM_RATE*.5),(len(self.bed)-pos)/(PCM_RATE*.5))
                gain*=old_gate+(self.bed_gate-old_gate)*offset/max(1,size-4)
                left,right=struct.unpack_from('<hh',self.bed,pos)
                values.extend((int(left*gain),int(right*gain)))
            self.bed_offset=(self.bed_offset+size)%len(self.bed)
            return values.tobytes()
    def get_audio_for_slide(self,slide,remaining):
        script=self.scripts.get(slide); key=self.keys.get(slide)
        if not script or not key or script.concepts & self.spoken: return None
        if (self.settings.get('audio') or {}).get('mode')=='minimal' and slide in {'nws_forecast','event_summary'} and self.last_information.get(slide)==script.text: return None
        cached=self.service.cache.read(key)
        if not cached: return None
        pcm,meta=cached
        if meta['duration']+0.35>remaining: return None
        return script,pcm,meta
    def play(self,script,pcm,bus='voice'):
        with self.lock:
            if self.bus=='alert' and self.pending and bus!='alert': return False
            self.current=script; self.last_script=script; self.pending=pcm; self.bus=bus; self.elapsed=0
            self.last_information[script.slide]=script.text
            self.spoken.update(script.concepts); return True
    def stop(self):
        with self.lock: self.pending=b''; self.current=None; self.elapsed=0.; self.last_script=None
    def interrupt(self,alert,settings):
        # Alert updates (including changed instructions) receive a new signature.
        signature=hashlib.sha256(json.dumps(alert,sort_keys=True,default=str).encode()).hexdigest()
        with self.lock:
            if signature!=self.severe_id:
                self.stop(); self.severe_id=signature; self.token=None
            cfg=normalize_audio(settings.get('audio'))
            if not allowed(cfg,'alert',severe=True): return
            concept='warning:'+signature
            if concept in self.spoken: return
            tz=((self.context.get('primary') or {}).get('location') or {}).get('timezone')
            script=Script('alert',severe_script(alert,tz),{concept},'severe',True)
            if not script.text: return
            _,cached=self.service.request(script,settings,self.channel,signature)
            if cached:
                # Never queue decorative sound ahead of a ready warning.
                self.play(script,cached[0],'alert')
            elif cfg['severe_sounder'] and 'sounder:'+signature not in self.spoken:
                self.spoken.add('sounder:'+signature)
                self.play(Script('alert','',set(),'severe',True),self.sounder('severe'),'stinger')
    def return_to_program_audio(self):
        with self.lock:
            if self.severe_id: self.stop(); self.severe_id=None; self.token=None
    def sounder(self,kind):
        if kind not in self.stingers:
            try:
                with wave.open(str(self.service.root/'stingers'/f'{kind}.wav'),'rb') as wav:
                    if (wav.getnchannels(),wav.getsampwidth(),wav.getframerate())!=(2,2,44100) or wav.getnframes()>88200: raise ValueError('Stinger must be stereo 44100 Hz 16-bit PCM, at most two seconds')
                    self.stingers[kind]=wav.readframes(wav.getnframes())
            except (OSError,ValueError,wave.Error): self.stingers[kind]=stinger_pcm(kind)
        return self.stingers[kind]
    def tick(self,playout,token):
        with self.lock:
            self.next_slide=playout.get('next_slide'); cfg=normalize_audio(self.settings.get('audio'))
            cycle=token[0]
            if cycle!=self.cycle: self.spoken.clear(); self.cycle=cycle
            if token!=self.token:
                if self.pending: return  # Ordinary rundown changes wait for admitted speech.
                self.stop(); self.token=token
            if self.pending or self.current or self.mixer.voice_muted: return
            slide=playout.get('current_slide'); remaining=float(playout.get('remaining_seconds') or 0)
            # Wait for entry transition; reject late clips rather than truncate speech.
            if float(playout.get('elapsed_seconds') or 0)<1: return
            match=self.get_audio_for_slide(slide,max(0,remaining-1))
            if match:
                pcm=match[1]
                kind={'station_id':'station','story_brief':'update','tropical_update':'tropical','event_summary':'update'}.get(slide)
                if cfg['sonic_branding'] and kind:
                    sound=self.sounder(kind)
                    if (len(sound)+len(pcm))/PCM_RATE+1<remaining: pcm=sound+pcm
                self.play(match[0],pcm)
            elif cfg['sonic_branding'] and slide=='station_id' and 'stinger:station' not in self.spoken:
                self.spoken.add('stinger:station'); self.play(Script(slide,'',set()),self.sounder('station'),'stinger')
    def block(self,size,bed=b''):
        with self.lock:
            data=self.pending[:size]; self.pending=self.pending[size:]; self.elapsed+=len(data)/PCM_RATE
            cfg=normalize_audio(self.settings.get('audio'))
            if not data: data=b''
            args={'voice':b'','alert':b'','stinger':b''}; args[self.bus]=data
            mixed=self.mixer.mix(bed=bed,**args,cfg=cfg)
            return (mixed+b'\0'*size)[:size]
    def caption(self):
        with self.lock:
            cfg=normalize_audio(self.settings.get('audio')); mode=cfg['captions']
            script=self.current if self.pending else self.last_script if mode=='always' else None
            if not script or mode=='off' or (mode=='severe_only' and not script.official): return ''
            words=script.text.split(); page=min(int(self.elapsed/4),max(0,(len(words)-1)//12)); chunk=words[page*12:(page+1)*12]
            return ' '.join(chunk)
    def get_status(self):
        with self.lock:
            result=self.service.status(self.settings)
            next_cached=self.service.cache.read(self.keys.get(self.next_slide,'')) if self.next_slide in self.keys else None
            result.update({'channel':self.channel,'current':asdict(self.current) if self.current else None,
                'next':self.next_slide,'duration':(len(self.pending)/PCM_RATE+self.elapsed) if self.current else 0,
                'voice_state':'MUTED' if self.mixer.voice_muted else 'PLAYING' if self.pending else 'IDLE',
                'music_state':'MUTED' if self.mixer.music_muted else 'DUCKED' if self.pending else 'READY',
                'scripts':[dict(slide=s.slide,text=s.text,profile=s.profile,manual=s.manual,key=self.keys.get(s.slide)) for s in self.scripts.values()],
                'error':self.last_error or result['error']})
            result['next_duration']=next_cached[1]['duration'] if next_cached else None
            if self.current: result['profile']=self.current.profile
            if result['current']: result['current']['concepts']=sorted(result['current']['concepts'])
            if self.last_error and result['state']!='DISABLED': result['state']='DEGRADED'
            return result
    def override(self,slide,text):
        if slide in {'alert','alert_radar'}: raise ValueError('Official warning scripts cannot be edited')
        with self.lock:
            if text is None: self.overrides.pop(slide,None)
            elif not text.strip() or len(text)>900: raise ValueError('Script must contain 1 to 900 characters')
            else: self.overrides[slide]=text.strip()
            self.revision=None
