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
        try:
            from documentary_speech import for_bite
            bite['speech']=for_bite(root,plan,bite,asset) if asset else []
        except ValueError:
            # Unreadable saved speech must never silently change the on-screen words.
            bite['speech']=[];reasons.append('Saved sound-bite speech cannot be read; spoken captions are not shown.')
        bite['stale_reasons']=reasons
    return plan


def _bite_order(plan):
    """Bites in narrative order.

    Two bites may share one pause point (a back-to-back statement with no
    narration between). Those keep the order they were saved in; everything
    else is ordered by its pause.
    """
    bites=list(plan.get('bites',[]))
    for b in bites:_number(b['pause_s'],'Narration pause')
    return sorted(bites,key=lambda b:(b['pause_s'],b.get('created') if b.get('created') is not None else 0))


def _spans(bites):
    """Group bites into runs that share one pause point."""
    runs=[]
    for b in bites:
        if runs and abs(runs[-1][0]['pause_s']-b['pause_s'])<.0005:runs[-1].append(b)
        else:runs.append([b])
    return runs


def build_timeline(project, plan):
    """Map narration and inserted footage onto a separate, immutable edit clock.

    This is a planning representation only, not a rendered MP4 or subtitle file.
    """
    voice=project.get('voiceover') or {};duration=_number(voice.get('duration'),'Narration duration')
    if duration<=0:raise ValueError('Upload narration to build a documentary timeline.')
    bites=_bite_order(plan)
    if any(b.get('stale_reasons') for b in bites):raise ValueError('A sound bite uses replaced or missing media. Review it before building the documentary timeline.')
    cursor=0;output=0;segments=[]
    for run in _spans(bites):
        at=_number(run[0]['pause_s'],'Narration pause')
        if at<cursor or at>duration:raise ValueError('A planned sound bite has invalid timings. Review it before continuing.')
        if at>cursor:
            span=at-cursor;segments.append({'kind':'narration','source_in_s':round(cursor,3),'source_out_s':round(at,3),'timeline_in_s':round(output,3),'timeline_out_s':round(output+span,3)});output+=span
        for b in run:
            start=_number(b['source_in_s'],'Source IN');end=_number(b['source_out_s'],'Source OUT')
            if start<0 or end<=start:raise ValueError('A planned sound bite has invalid timings. Review it before continuing.')
            # Spoken words are bite-relative, exactly as saved and reviewed; the
            # preview adds them to this segment's own start on the edit clock.
            span=end-start;segments.append({'kind':'sound_bite','id':b['id'],'asset_id':b['asset_id'],'asset_name':b['asset_name'],'source_in_s':start,'source_out_s':end,'timeline_in_s':round(output,3),'timeline_out_s':round(output+span,3),'narration_resume_s':at,'speech':list(b.get('speech') or [])});output+=span
        cursor=at
    if cursor<duration:
        span=duration-cursor;segments.append({'kind':'narration','source_in_s':round(cursor,3),'source_out_s':duration,'timeline_in_s':round(output,3),'timeline_out_s':round(output+span,3)});output+=span
    return {'status':'planning_only','duration_s':round(output,3),'narration_duration_s':duration,'segments':segments,'subtitles':'not_generated','export_available':False}


def adjusted_srt(original, project, plan, splits=None):
    """Shift a separate SRT; a crossing cue requires an explicit word split.

    Split points are editorial input, not automatic word alignment. Every word
    remains verbatim and ordered. The original file is never changed.
    """
    import re
    timeline=build_timeline(project,plan)
    source=original.replace('\r\n','\n').replace('\r','\n').strip()
    parts=re.split(r'\n\s*\n',source);blocks=(project.get('srt') or {}).get('blocks') or []
    if not blocks or len(parts)!=len(blocks):raise ValueError('The original uploaded SRT does not match this project. Upload it again before creating adjusted subtitles.')
    def clock(ms):
        h,rem=divmod(ms,3600000);m,rem=divmod(rem,60000);sec,ms=divmod(rem,1000)
        return f'{h:02}:{m:02}:{sec:02},{ms:03}'
    bites=[(round(s['narration_resume_s']*1000),round((s['timeline_out_s']-s['timeline_in_s'])*1000)) for s in timeline['segments'] if s['kind']=='sound_bite']
    rules={}
    if not isinstance(splits or [],list):raise ValueError('Subtitle split choices must be a list.')
    for rule in splits or []:
        if not isinstance(rule,dict) or type(rule.get('cue')) is not int or type(rule.get('pause_ms')) is not int or type(rule.get('words_before')) is not int:raise ValueError('Invalid subtitle split choice.')
        key=(rule['cue'],rule['pause_ms'])
        if key in rules:raise ValueError('Duplicate subtitle split choice.')
        rules[key]=rule['words_before']
    used=set();entries=[];any_split=False
    for part,block in zip(parts,blocks):
        lines=part.split('\n')
        if len(lines)<3 or lines[0].strip()!=str(block['number']) or ' '.join(lines[2:]).strip()!=block['text']:raise ValueError('The original SRT contents do not match the project. Upload the original SRT again.')
        text='\n'.join(lines[2:]);start,end=block['start_ms'],block['end_ms']
        pauses=sorted({pause for pause,_ in bites if start<pause<end})
        if len(pauses)>1:raise ValueError(f"Cue {block['number']} spans multiple pause points; split it manually in a separate subtitle editor.")
        sections=[(start,end,text)]
        if pauses:
            pause=pauses[0];key=(block['number'],pause)
            if key not in rules:raise ValueError(f"Sound bite falls inside SRT cue {block['number']}. Choose and review where its original words split before downloading.")
            if '<' in text or '>' in text:raise ValueError(f"Cue {block['number']} contains formatting; split it manually in a separate subtitle editor.")
            words=list(re.finditer(r'\S+',text));count=rules[key]
            if count<1 or count>=len(words):raise ValueError(f"Choose a word boundary inside SRT cue {block['number']}.")
            left=text[:words[count-1].end()].rstrip();right=text[words[count].start():].lstrip()
            if not left or not right:raise ValueError('A split subtitle cannot be empty.')
            sections=[(start,pause,left),(pause,end,right)];used.add(key);any_split=True
        for a,b,content in sections:
            shift_start=sum(length for pause,length in bites if pause<=a)
            shift_end=sum(length for pause,length in bites if pause<b)
            if b+shift_end<=a+shift_start:raise ValueError('A subtitle segment has no duration.')
            entries.append((block['number'],a+shift_start,b+shift_end,content))
    if set(rules)!=used:raise ValueError('A subtitle split choice no longer matches the current plan. Review it again.')
    return '\n\n'.join(f'{i if any_split else number}\n{clock(a)} --> {clock(b)}\n{text}' for i,(number,a,b,text) in enumerate(entries,1))+'\n'
