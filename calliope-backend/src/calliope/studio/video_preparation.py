import asyncio
import base64
import shutil
import time
from pathlib import Path

from calliope.config import settings
from calliope.engines import modal_h3
from calliope.studio import service
from calliope.studio.h3_prompt import compile_video_prompt
from calliope.studio.visual_identity import file_hash, reference_bindings, validate_value_manifest


async def prepare_video_batch(shots, should_stop=lambda: False):
    from calliope.studio.production import reusable_video

    if settings.dry_run:
        return None
    started = time.perf_counter()
    for binary in ('ffmpeg', 'ffprobe'):
        if not shutil.which(binary):
            raise ValueError(f'{binary} est requis avant de lancer les rendus.')
    prepared = {}
    for initial in shots:
        if should_stop():
            raise asyncio.CancelledError()
        with service.database() as conn:
            shot = service.public_shot(conn, initial['id'])
            if reusable_video(shot):
                continue
            context = service.shot_context(conn, shot['id'])
            aspect = conn.execute('SELECT aspect_ratio FROM studio_projects WHERE project_id=?',
                                  (shot['project_id'],)).fetchone()[0]
            validate_value_manifest(shot, context)
            if context.get('directing_profile', {}).get('require_reference_approval'):
                info = context.get('shot') or {}
                keys = list(info.get('character_keys') or [])
                if info.get('location_key'):
                    keys.append(info['location_key'])
                reference_bindings(context['entities'], keys, True)
            source = None
            if shot['selected_image_id'] and not shot['continue_from_previous']:
                source = conn.execute('SELECT output_path FROM studio_takes WHERE id=?',
                                      (shot['selected_image_id'],)).fetchone()[0]
        inputs = {
            'prompt': compile_video_prompt(
                shot | context.get('shot', {}), context,
                first_frame=bool(source or shot['continue_from_previous']),
                video_profile=settings.studio_video_profile,
            ),
            'duration_sec': shot['duration_sec'],
            'aspect_ratio': aspect,
            'audio_mode': shot['audio_mode'],
            'video_profile': settings.studio_video_profile,
            'image_path': source,
        }
        payload = await asyncio.to_thread(
            modal_h3.build_request, **(inputs | {'image_path': Path(source) if source else None}),
            seed=0,
        )
        prepared[shot['id']] = {
            'inputs': inputs, 'payload': payload,
            'source_sha256': await asyncio.to_thread(file_hash, source) if source else None,
        }
    if should_stop():
        raise asyncio.CancelledError()
    if not prepared:
        return None
    engine = modal_h3.ModalH3Engine(settings.studio_modal_app, settings.studio_modal_function)
    available = await asyncio.to_thread(engine.check_available)
    if not available['ok']:
        raise ValueError(available['message'])
    identity = await asyncio.to_thread(engine.identity)
    return {
        'shots': prepared, 'engine': engine, 'identity': identity,
        'modal_app': settings.studio_modal_app, 'modal_function': settings.studio_modal_function,
        'preparation_seconds': round(time.perf_counter() - started, 3),
    }


def prepared_request(request, prepared):
    if any(request.get(key) != value for key, value in prepared['inputs'].items()):
        raise ValueError('Le plan a changé après sa préparation. Reprenez la production.')
    if prepared['source_sha256'] and request.get('source_sha256') != prepared['source_sha256']:
        raise ValueError("L'image de départ a changé après sa préparation.")
    payload = prepared['payload'] | {'seed': request['seed']}
    if request.get('trace_id'):
        payload['trace_id'] = request['trace_id']
    if request.get('previous_video_path'):
        payload['image_base64'] = base64.b64encode(modal_h3._last_frame_png(
            Path(request['previous_video_path']), request.get('previous_video_time'),
        )).decode('ascii')
        payload['continuity_mode'] = 'last_frame'
    modal_h3.validate_request(payload)
    return payload
