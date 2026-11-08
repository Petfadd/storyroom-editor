"""Create individually named scene clips from a current, already rendered timeline."""
import re
import json
import shutil
import subprocess
import threading
import time
import uuid
import zipfile
from pathlib import Path

import cv2
import imageio_ffmpeg

JOBS = {}
GUARD = threading.Lock()


def scene_name(index):
    return f'C{index:02d}.mp4'


def clip_filename(scene, index):
    row=scene.get('plan_row') or {}
    return row['clip_id'] + '.mp4' if row.get('strict_production') and row.get('clip_id') else scene_name(index)


def status(root, pid, updated):
    with GUARD:
        job = JOBS.get(pid)
        if job and job['updated'] == updated:
            result = dict(job)
        else:
            try:
                result = json.loads((Path(root) / 'scene_exports_status.json').read_text())
            except (OSError, ValueError):
                return {'status': 'idle'}
    if result.get('updated') != updated:
        return {'status': 'stale'}
    if result.get('status') == 'done' and not (Path(root) / 'renders' / 'scene_exports' / result['output']).is_file():
        return {'status': 'idle'}
    return result


def start(root, project, source, source_kind, publish):
    pid = project['id']
    with GUARD:
        current = JOBS.get(pid)
        if current and current['status'] in ('queued', 'running'):
            raise ValueError('Scene clips are already being prepared.')
        state = {'status': 'queued', 'progress': 0, 'updated': project['updated'], 'source_kind': source_kind, 'naming': 'C', 'detail': 'Preparing scene clips.'}
        JOBS[pid] = state
    thread = threading.Thread(target=_run, args=(Path(root), project, Path(source), state, publish), name='scene-export-' + pid, daemon=True)
    thread.start()
    return dict(state)


def _run(root, project, source, state, publish):
    work = root / '.cache' / ('scene-export-' + uuid.uuid4().hex)
    work.mkdir(parents=True, exist_ok=True)
    def update(**kwargs):
        with GUARD:
            state.update(kwargs)
    try:
        scenes = project['scenes']
        if not scenes or not source.is_file():
            raise ValueError('The selected timeline video is unavailable.')
        names=[clip_filename(scene,i) for i,scene in enumerate(scenes,1)]
        if any(not re.fullmatch(r'C\d+\.mp4',name) for name in names) or len(set(name.casefold() for name in names))!=len(names):
            raise ValueError('The script reuses a clip reference across shots. Separate exports cannot safely use the same C filename twice; use the full master until those clips are defined uniquely.')
        last = 0
        for i, scene in enumerate(scenes):
            if scene['frame_in'] != last or scene['frame_out'] <= last:
                raise ValueError(f'Scene {i+1} has an invalid timeline interval.')
            last = scene['frame_out']
        cap = cv2.VideoCapture(str(source))
        total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT));fps = cap.get(cv2.CAP_PROP_FPS);cap.release()
        if total != last or abs(fps - 30) > .01:
            raise ValueError('The selected timeline video does not match the current storyboard frame count or frame rate.')
        output_name = 'scenes_' + time.strftime('%Y%m%d_%H%M%S') + '_' + uuid.uuid4().hex[:8] + '.zip'
        archive = work / output_name
        ff = imageio_ffmpeg.get_ffmpeg_exe()
        update(status='running')
        with zipfile.ZipFile(archive, 'w', compression=zipfile.ZIP_STORED, allowZip64=True) as z:
            for i, scene in enumerate(scenes, 1):
                update(progress=round((i-1)*100/len(scenes), 1), detail=f'Preparing {names[i-1]} ({i} of {len(scenes)})')
                frames = scene['frame_out'] - scene['frame_in'];duration = frames / 30
                path = work / names[i-1]
                cmd = [ff, '-nostdin', '-y', '-v', 'error', '-ss', f"{scene['frame_in']/30:.9f}", '-i', str(source),
                       '-t', f'{duration:.9f}', '-map', '0:v:0', '-map', '0:a:0?', '-c:v', 'libx264',
                       '-preset', 'ultrafast', '-crf', '18', '-pix_fmt', 'yuv420p', '-threads', '2',
                       '-c:a', 'aac', '-b:a', '192k', '-movflags', '+faststart', str(path)]
                completed = subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, timeout=max(120, duration*20))
                if completed.returncode:
                    raise ValueError(f'{names[i-1]} could not be created: ' + completed.stderr.decode(errors='replace')[-400:])
                cap = cv2.VideoCapture(str(path));actual = int(cap.get(cv2.CAP_PROP_FRAME_COUNT));cap.release()
                if actual != frames:
                    raise ValueError(f'{names[i-1]} has {actual} frames instead of {frames}; no incomplete archive was saved.')
                z.write(path, names[i-1]);path.unlink()
        if publish(project['updated'], archive, output_name, state['source_kind']):
            update(status='done', progress=100, detail=f'{len(scenes)} scene clips ready.', output=output_name)
        else:
            update(status='stale', detail='The project changed while the scene clips were being prepared. Start again for the current edit.')
    except Exception as exc:
        update(status='error', detail=str(exc)[:600])
    finally:
        shutil.rmtree(work, ignore_errors=True)
