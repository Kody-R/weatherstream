"""Authenticated Studio routes. Preview never enters a live director's buses."""
from io import BytesIO
import wave
from fastapi import APIRouter, HTTPException
from fastapi.responses import Response
from pydantic import BaseModel
from app.narration import Script
from app.studio import AVAILABLE_SLIDES


class AudioCommand(BaseModel):
    action: str
    slide: str = 'current'
    text: str | None = None
    muted: bool = True


def audio_router(streamer, config_store):
    router=APIRouter(prefix='/api/studio/audio')
    def director(key):
        with streamer._lock: worker=streamer._workers.get(key)
        if worker is None: raise HTTPException(404,'Unknown channel')
        worker.prepare_audio()
        return worker.audio

    @router.get('/{key}')
    def status(key: str): return director(key).get_status()

    @router.post('/{key}')
    def command(key: str, payload: AudioCommand):
        d=director(key); action=payload.action
        if action in {'edit','reset'}:
            if payload.slide not in AVAILABLE_SLIDES: raise HTTPException(400,'Unknown slide')
            try: d.override(payload.slide,payload.text if action=='edit' else None)
            except ValueError as exc: raise HTTPException(400,str(exc))
        elif action=='regenerate':
            # Evict selected generated entry only; live PCM remains owned by director.
            k=d.keys.get(payload.slide)
            if k:
                with d.service.cache.lock:
                    (d.service.cache.root/f'{k}.json').unlink(missing_ok=True)
                    (d.service.cache.root/f'{k}.s16le').unlink(missing_ok=True)
                d.service.failed.pop(k,None)
            d.revision=None
        elif action=='mute_voice':
            d.mixer.voice_muted=payload.muted
            if payload.muted and d.bus!='alert': d.stop()
        elif action=='mute_music': d.mixer.music_muted=payload.muted
        elif action=='clear_expired': d.service.cache.cleanup(int((config_store.get().get('audio') or {}).get('cache_items',128)))
        elif action=='test':
            script=Script('test','This is Roller Weather Network. The voice system is ready.',set(),'broadcast')
            k,_=d.service.request(script,config_store.get(),key,'preview')
            return {'preview_key':k}
        else: raise HTTPException(400,'Unknown audio operation')
        return director(key).get_status()

    @router.get('/{key}/preview/{cache_key}')
    def preview(key: str,cache_key: str):
        d=director(key)
        if len(cache_key)!=64 or any(c not in '0123456789abcdef' for c in cache_key): raise HTTPException(400,'Invalid clip key')
        clip=d.service.cache.read(cache_key)
        if not clip: raise HTTPException(404,'Clip is not ready or has expired')
        output=BytesIO()
        with wave.open(output,'wb') as wav:
            wav.setnchannels(2); wav.setsampwidth(2); wav.setframerate(44100); wav.writeframes(clip[0])
        return Response(output.getvalue(),media_type='audio/wav',headers={'Cache-Control':'no-store'})
    return router
