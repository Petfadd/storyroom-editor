"""Non-destructive sound-bite plans. Existing edits, media and SRT are never rewritten."""
from pathlib import Path
import json, math, time, uuid


def load_plan(root):
    path=Path(root)/'documentary_plan.json'
    if not path.exists():return {'version':1,'revision':0,'bites':[]}
    data=json.loads(path.read_text(encoding='utf-8'))
    if data.get('version')!=1 or not isinstance(data.get('bites'),list):raise ValueError('The documentary plan could not be read. It was not changed.')
    return data


def _number(value,label):
    if isinstance(value,bool):raise ValueError(f'{label} must be a time in seconds.')
    try:value=float(value)
    except (TypeError,ValueError):raise ValueError(f'{label} must be a time in seconds.')
    if not math.isfinite(value):raise ValueError(f'{label} must be a finite time.')
    return round(value,3)


def validate_bite(project, root, data, has_audio):
    voice=project.get('voiceover') or {}; duration=voice.get('duration') or 0
    if not duration or not (Path(root)/voice.get('path','')).is_file():raise ValueError('Upload narration before adding a sound bite.')
    assets={a['id']:a for a in project.get('assets',[])};asset=assets.get(data.get('asset_id'))
    if not asset or asset.get('kind')!='video' or asset.get('derived_from'):raise ValueError('Choose an uploaded source video.')
    path=(Path(root)/asset.get('path','')).resolve();base=Path(root).resolve()
    if not path.is_relative_to(base/'sources') or not path.is_file():raise ValueError('The selected source video is missing.')
    pause=_number(data.get('pause_s'),'Narration pause');start=_number(data.get('source_in_s'),'Source IN');end=_number(data.get('source_out_s'),'Source OUT')
    if not 0<=pause<=duration:raise ValueError('Narration pause is outside the narration.')
    if start<0 or end<=start or end>float(asset.get('duration') or 0):raise ValueError('Choose a source IN and OUT within the video, with OUT after IN.')
    if not has_audio(path):raise ValueError('The selected source video has no audio stream. Choose footage with audio.')
    voice_path=Path(root)/voice['path'];stat=voice_path.stat()
    return {'id':uuid.uuid4().hex[:16], 'pause_s':pause,'asset_id':asset['id'],'asset_name':asset.get('original_name') or asset.get('name'),'asset_revision':asset.get('revision'), 'source_in_s':start,'source_out_s':end,'narration_name':voice.get('name'),'narration_size':stat.st_size,'narration_mtime_ns':stat.st_mtime_ns,'created':time.time()}


def annotate_plan(project,root,plan):
    assets={a['id']:a for a in project.get('assets',[])};voice=project.get('voiceover') or {};path=Path(root)/voice.get('path','')
    for bite in plan['bites']:
        asset=assets.get(bite['asset_id']);reasons=[]
        if not asset or asset.get('revision')!=bite.get('asset_revision') or not (Path(root)/asset.get('path','')).is_file():reasons.append('Source video was removed or replaced; review this bite again.')
        if not path.is_file() or path.stat().st_size!=bite.get('narration_size') or path.stat().st_mtime_ns!=bite.get('narration_mtime_ns'):reasons.append('Narration changed; review the pause point again.')
        bite['stale_reasons']=reasons
    return plan
