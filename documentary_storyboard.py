"""Prepare a documentary review storyboard from explicit beats and approved gaps.

This is a picture/audio PLAN only; it never claims source identity or rights, and
is not a final renderer. Existing scripts, original SRT and media are not changed.
"""
import copy, hashlib, math, uuid
from pathlib import Path
from strict_cuts import source_rate
from documentary import validate_bite, build_timeline
from documentary_beats import read_beats


def prepare(project, root, approved_gaps, approved_overlaps, allow_tail, has_audio, beats=None, allow_consecutive_bites=False, fitted_ranges=None):
    root=Path(root).resolve();beats=beats if beats is not None else read_beats((project.get('production_script') or {}).get('text',''))
    blocks=(project.get('srt') or {}).get('blocks') or []
    if not blocks or not project.get('voiceover'):raise ValueError('Upload original SRT and narration first.')
    if project.get('scenes'):raise ValueError('An existing storyboard must not be replaced automatically.')
    assets=project.get('assets') or [];files={}
    for b in beats:
        matches=[a for a in assets if (a.get('original_name') or a.get('name'))==b['source_file']]
        if len(matches)!=1:raise ValueError(f"Beat {b['label']}: source {b['source_file']} is missing or ambiguous.")
        a=matches[0];path=(root/a.get('path','')).resolve()
        if a.get('kind')!='video' or not path.is_relative_to(root/'sources') or not path.is_file():raise ValueError(f"Beat {b['label']}: uploaded video is missing.")
        duration_ms=float(a.get('duration') or 0)*1000
        if b['source_out_ms']>duration_ms+.001:
            # Name the numbers and the fix; "source OUT exceeds video duration"
            # alone left no clue which range to change.
            raise ValueError(f"Beat {b['label']}: source OUT {b['source_out_ms']/1000:.3f}s is past the end of {b['source_file']} "
                             f"({duration_ms/1000:.3f}s). Review the Beat picture plan so the range is fitted before building, "
                             f"or set Source OUT to {duration_ms/1000:.3f}s or less in the script.")
        files[b['source_file']]=a
    if not beats or beats[0]['kind']!='narration' or beats[-1]['kind']!='narration':raise ValueError('The script must begin and end with narration.')
    if not isinstance(approved_gaps,list) or not isinstance(approved_overlaps,list):raise ValueError('Provide explicitly approved gap ranges and source overlaps.')
    pairs=set()
    for pair in approved_overlaps:
        if not isinstance(pair,list) or len(pair)!=2 or not all(isinstance(x,int) for x in pair):raise ValueError('Invalid approved overlap.')
        pairs.add(tuple(sorted(pair)))
    found=set()
    for i,b in enumerate(beats):
        for a in beats[:i]:
            if a['source_file']==b['source_file'] and b['source_in_ms']<a['source_out_ms'] and b['source_out_ms']>a['source_in_ms']:
                pair=tuple(sorted((a['number'],b['number'])));found.add(pair)
                if pair not in pairs:raise ValueError(f"Beats {a['label']} and {b['label']} overlap without explicit approval.")
    if found!=pairs:raise ValueError('Approved overlap list does not match the current script.')
    narratives=[b for b in beats if b['kind']=='narration'];cursor=0;uncovered=[]
    for b in narratives:
        start,end=b['narration_in_ms'],b['narration_out_ms']
        if start<cursor:raise ValueError('Narration blocks overlap.')
        if start>cursor:uncovered.append((cursor,start))
        cursor=end
    voice=float(project['voiceover'].get('duration') or 0);voice_ms=round(voice*1000)
    if cursor>voice_ms:raise ValueError('Scripted narration exceeds original audio.')
    if cursor<voice_ms:uncovered.append((cursor,voice_ms))
    # An uncovered sliver narrower than one output frame cannot become a picture
    # slot; rounding would give it zero frames. Record it instead of inventing one.
    sub_frame=[(a,b) for a,b in uncovered if round(b*.03)-round(a*.03)<1]
    uncovered=[(a,b) for a,b in uncovered if (a,b) not in sub_frame]
    gaps={}
    for g in approved_gaps:
        if not isinstance(g,dict) or any(type(g.get(x)) is not int for x in ('narration_in_ms','narration_out_ms','source_in_ms','source_out_ms')):raise ValueError('Gap timing must be integer milliseconds.')
        key=(g['narration_in_ms'],g['narration_out_ms'])
        if key in gaps:raise ValueError('A gap was approved more than once.')
        filename=g.get('source_file');a=files.get(filename)
        if not a:raise ValueError('Gap footage must be an exact uploaded file already named in the script.')
        if g['source_in_ms']<0 or g['source_out_ms']<=g['source_in_ms'] or g['source_out_ms']>float(a['duration'])*1000+.001:raise ValueError('Approved gap footage is outside the source.')
        want=key[1]-key[0];actual=g['source_out_ms']-g['source_in_ms'];last=key[1]==voice_ms
        if abs(actual-want)>1 and not(last and allow_tail is True and 0<=actual-want<=34):raise ValueError('Gap picture duration differs from narration; no speed change or unapproved tail allowed.')
        for b in beats:
            if filename==b['source_file'] and g['source_in_ms']<b['source_out_ms'] and g['source_out_ms']>b['source_in_ms']:raise ValueError('A proposed gap range repeats footage from a script beat.')
        for old in gaps.values():
            if filename==old['source_file'] and g['source_in_ms']<old['source_out_ms'] and g['source_out_ms']>old['source_in_ms']:raise ValueError('Proposed gap footage overlaps another gap.')
        gaps[key]=copy.deepcopy(g)
    if set(gaps)!=set(uncovered):raise ValueError('Explicit gap decisions do not match every uncovered narration interval.')
    bites=[]
    for i,b in enumerate(beats):
        if b['kind']!='sound_bite':continue
        previous=beats[i-1] if i else None;following=beats[i+1] if i+1<len(beats) else None
        joined=allow_consecutive_bites and ((previous and previous['kind']=='sound_bite' and previous['pause_ms']==b['pause_ms']) or (following and following['kind']=='sound_bite' and following['pause_ms']==b['pause_ms']))
        if i==0 or i+1>=len(beats) or (previous['kind']!='narration' and not joined) or (following['kind']!='narration' and not joined):raise ValueError(f"Beat {b['label']}: sound bite must be between narration blocks unless consecutive bites are approved.")
        if previous['kind']=='narration' and b['pause_ms']!=previous['narration_out_ms']:raise ValueError(f"Beat {b['label']}: pause does not match preceding narration.")
        if previous['kind']=='sound_bite' and b['pause_ms']!=previous['pause_ms']:raise ValueError(f"Beat {b['label']}: consecutive bites must share one pause point.")
        bite=validate_bite(project,root,{'asset_id':files[b['source_file']]['id'],'pause_s':b['pause_ms']/1000,'source_in_s':b['source_in_ms']/1000,'source_out_s':b['source_out_ms']/1000},has_audio)
        bite.update(script_beat=b['label'],script_caption=b['caption'],script_direction=b['direction'],speaker_declared=b.get('speaker',''))
        bites.append(bite)
    if not bites:raise ValueError('The script has no sound bites.')
    plan={'version':1,'revision':1,'bites':bites};build_timeline(project,plan)
    spans=[]
    for b in narratives:
        spans.append({'start':b['narration_in_ms'],'end':b['narration_out_ms'],'source_in':b['source_in_ms'],'source_out':b['source_out_ms'],'filename':b['source_file'],'beat':b['label'],'caption':b['caption'],'direction':b['direction'],'gap':False})
    for key,g in gaps.items():
        end=key[1]
        # The approved tail is picture only. Extend to the enclosing output frame.
        if key[1]==voice_ms and g['source_out_ms']-g['source_in_ms']>key[1]-key[0]:end=math.ceil(voice*30-1e-9)*1000/30
        spans.append({'start':key[0],'end':end,'source_in':g['source_in_ms'],'source_out':g['source_out_ms'],'filename':g['source_file'],'beat':'approved-gap','caption':'None','direction':'Approved proposed gap footage; content and rights require review.','gap':True})
    spans.sort(key=lambda x:x['start']);last=0;scenes=[]
    for span in spans:
        if abs(span['start']-last)>.001:raise ValueError('The picture plan contains a gap or overlap.')
        last=span['end']
        points=[span['start']]+[b['start_ms'] for b in blocks if span['start']<b['start_ms']<span['end']]+[span['end']]
        for start,end in zip(points,points[1:]):
            fi,fo=round(start*.03),round(end*.03)
            if fo<=fi:raise ValueError('A script or subtitle boundary is shorter than one output frame.')
            cue=next((b for b in reversed(blocks) if b['start_ms']<=start),blocks[0]);a=files[span['filename']]
            source_ms=span['source_in']+(start-span['start']);source_end_ms=span['source_in']+(end-span['start'])
            if source_ms<span['source_in']-.001 or source_end_ms>span['source_out']+1:raise ValueError('A picture slot exceeds its explicitly approved source range.')
            try:recorded_fps=str(source_rate(root/a['path']))
            except ValueError:recorded_fps=''
            row={'source_fps':recorded_fps,'shot_id':f"Srt{cue['number']:03d}",'source_file':span['filename'],'source_in_ms':round(source_ms),'source_out_ms':round(source_end_ms),'timeline_in_ms':round(start),'timeline_out_ms':round(end),'srt_blocks':[cue['number']],'required_caption':span['caption'],'continuity':f"Documentary beat {span['beat']}",'plain_cue':True}
            scenes.append({'id':uuid.uuid4().hex[:16],'srt_blocks':[cue['number'],cue['number']],'frame_in':fi,'frame_out':fo,'narration':cue['text'],'purpose':'documentary review','emotion':'neutral','focus':span['direction'] or 'Documentary footage','category':'general','person':'','asset_id':a['id'],'in_s':round(source_ms/1000,6),'motion':'auto','transition':'cut','context_only':False,'notes':'','approved':False,'profile':project.get('profile','documentary'),'focus_position':'center','motion_strength':'subtle','plan_row':row})
    if not scenes or scenes[0]['frame_in']!=0 or any(a['frame_out']!=b['frame_in'] for a,b in zip(scenes,scenes[1:])):raise ValueError('The frame-aligned picture plan has a gap or overlap.')
    staged=copy.deepcopy(project);staged['scenes']=scenes;staged['review_only_draft']=True;staged['script_moving_only']=True
    staged['script_application']={'recognized_instructions':len(beats),'placed_scenes':len(scenes),'issues':[],'mode':'documentary_beat_review','note':'Review storyboard only. On-screen captions, final fade and final export are not implemented.'}
    staged['documentary_beat_review']={'fitted_beats':[{'label':b['label'],'kind':b['kind'],'source_file':b['source_file'],
        'source_in_ms':b['source_in_ms'],'source_out_ms':b['source_out_ms'],
        'narration_in_ms':b.get('narration_in_ms'),'narration_out_ms':b.get('narration_out_ms'),
        'pause_ms':b.get('pause_ms'),'caption':b['caption']} for b in beats],
        'fitted_ranges':copy.deepcopy(fitted_ranges),'sub_frame_gaps':[{'narration_in_ms':a,'narration_out_ms':b,'length_ms':b-a} for a,b in sub_frame],'script_sha256':hashlib.sha256(project['production_script']['text'].encode()).hexdigest(),'approved_gaps':copy.deepcopy(approved_gaps),'approved_overlaps':copy.deepcopy(approved_overlaps),'silent_tail_authorized':bool(allow_tail),'warning':'Review only: source content, identity, context and rights are unverified; script captions and final fade are not rendered.'}
    return staged,plan


def verified_source_interval(project, scene, asset):
    """Exact plan range for an unchanged beat-review slot, otherwise None.

    Checking against both the original script and the explicit gap approval prevents
    frame-rounding noise being mistaken for an overlap without forgiving real edits.
    """
    try:
        record=project.get('documentary_beat_review') or {};script=(project.get('production_script') or {}).get('text','')
        if not record or record.get('script_sha256')!=hashlib.sha256(script.encode()).hexdigest():return None
        row=scene.get('plan_row') or {};filename=asset.get('original_name') or asset.get('name')
        if scene.get('transition')!='cut' or row.get('source_file')!=filename:return None
        a,b,si,so=(row[k] for k in ('timeline_in_ms','timeline_out_ms','source_in_ms','source_out_ms'))
        if any(type(x) is not int for x in (a,b,si,so)) or b<=a or so<=si:return None
        if scene.get('frame_in')!=round(a*.03) or scene.get('frame_out')!=round(b*.03) or abs(float(scene.get('in_s',-1))*1000-si)>.001:return None
        label=str(row.get('continuity') or '')
        if label=='Documentary beat approved-gap':
            candidates=[]
            for g in record.get('approved_gaps',[]):
                if g.get('source_file')!=filename:continue
                end=g['narration_out_ms']
                if end==round(float(project['voiceover']['duration'])*1000) and record.get('silent_tail_authorized') and g['source_out_ms']-g['source_in_ms']>end-g['narration_in_ms']:
                    end=round(math.ceil(float(project['voiceover']['duration'])*30-1e-9)*1000/30)
                candidates.append((g['narration_in_ms'],end,g['source_in_ms'],g['source_out_ms']))
        elif label.startswith('Documentary beat '):
            number=label[len('Documentary beat '):]
            # Prefer the ranges saved with this storyboard: fitting trims or extends
            # a scripted range without changing the script file itself.
            fitted=[x for x in (record.get('fitted_beats') or []) if x.get('kind')=='narration' and x.get('label')==number and x.get('source_file')==filename]
            saved=(record.get('fitted_ranges') or {}).get(number)
            if not fitted and saved and saved.get('source_file')==filename:
                # A storyboard saved before the fitted ranges were embedded can still
                # be verified against the range recorded when it was built.
                scripted=next((x for x in read_beats(script) if x['kind']=='narration' and x['label']==number),None)
                if scripted:
                    fitted=[dict(scripted,source_in_ms=saved['source_in_ms'],source_out_ms=saved['source_out_ms'])]
            candidates=[(x['narration_in_ms'],x['narration_out_ms'],x['source_in_ms'],x['source_out_ms']) for x in fitted]
        else:return None
        for start,end,source_in,source_out in candidates:
            if a>=start and b<=end and abs(si-(source_in+a-start))<=1 and abs(so-(source_in+b-start))<=1 and so<=source_out+1:
                return si/1000,so/1000
    except (ValueError,KeyError,TypeError,OverflowError):pass
    return None
