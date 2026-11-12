"""Prepare a documentary review storyboard from explicit beats and approved gaps.

This is a picture/audio PLAN only; it never claims source identity or rights, and
is not a final renderer. Existing scripts, original SRT and media are not changed.
"""
import copy, hashlib, math, uuid
from pathlib import Path
from strict_cuts import source_rate
from documentary import validate_bite, build_timeline
from documentary_beats import read_beats

# One output frame at 30 fps, the rounding unit for every picture slot.
FRAME_MS = 34


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
    # A script does not have to open or close on narration. The timeline is driven by
    # each beat's own pause point, so a leading bite simply starts at 0.0 and a
    # trailing one plays over the last of the audio; build_timeline handles both.
    # Requiring narration at both ends was a writing convention enforced as a hard
    # failure, which refused otherwise valid productions.
    if not beats:raise ValueError('The production script contains no Beats.')
    # A leading bite is anchored at 0.0 and a closing one at the end of the narration.
    # Those are checked in the bite loop below and rejected if the script says
    # otherwise: retiming a script here would silently move audio the user wrote at a
    # particular timecode, which this planner never does.
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
    # The narration duration comes from the uploaded file's container, which is rounded
    # to the nearest millisecond. A script whose final timecode lands a few milliseconds
    # past that is not longer than the audio -- it is the same audio, written out to a
    # third decimal. Measured on a real production: a 368.93s narration against a script
    # ending at 368.933s, a 3ms gap, refused the whole build. One output frame (33ms) is
    # the smallest meaningful difference here, and the end of the narration is snapped to
    # the script when it falls inside that tolerance so no phantom gap is invented.
    FRAME_MS=1000/30
    if cursor>voice_ms+FRAME_MS:
        raise ValueError(f'The script narration runs to {cursor/1000:.3f}s but the uploaded '
                         f'narration is only {voice_ms/1000:.3f}s long. Upload the full narration, '
                         f'or correct the final narration clock in the script.')
    if cursor>voice_ms:voice_ms=cursor
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
        # A bite may open or close the programme. At either edge there is no narration
        # neighbour to anchor it, so its own pause point is the anchor: 0.0 at the
        # start, and the end of the narration at the close. Inside the programme the
        # original rule still applies -- a bite must follow narration, or another bite
        # at the same pause when consecutive bites are approved.
        at_start=i==0;at_end=i+1>=len(beats)
        if at_start and at_end:raise ValueError(f"Beat {b['label']}: the script contains only one beat, and it is a sound bite. Add narration, or upload the full production script.")
        if not at_start and not (previous['kind']=='narration' or joined):raise ValueError(f"Beat {b['label']}: sound bite must follow narration unless consecutive bites are approved.")
        if not at_end and not (following['kind']=='narration' or joined):raise ValueError(f"Beat {b['label']}: sound bite must be followed by narration unless consecutive bites are approved.")
        if at_start:
            if b['pause_ms']!=0:raise ValueError(f"Beat {b['label']}: a sound bite before any narration must start at 00:00:00,000.")
        elif previous['kind']=='narration' and b['pause_ms']!=previous['narration_out_ms']:raise ValueError(f"Beat {b['label']}: pause does not match preceding narration.")
        elif previous['kind']=='sound_bite' and b['pause_ms']!=previous['pause_ms']:raise ValueError(f"Beat {b['label']}: consecutive bites must share one pause point.")
        if at_end and b['pause_ms']!=voice_ms:raise ValueError(f"Beat {b['label']}: a closing sound bite must start at the end of the narration ({voice_ms/1000:.3f}s).")
        bite=validate_bite(project,root,{'asset_id':files[b['source_file']]['id'],'pause_s':b['pause_ms']/1000,'source_in_s':b['source_in_ms']/1000,'source_out_s':b['source_out_ms']/1000},has_audio)
        bite.update(script_beat=b['label'],script_caption=b['caption'],script_direction=b['direction'],speaker_declared=b.get('speaker',''))
        bites.append(bite)
    if not bites:raise ValueError('The script has no sound bites.')
    plan={'version':1,'revision':1,'bites':bites};build_timeline(project,plan)
    # Sound bites are picture as well as sound. The bite plan places them on the
    # master clock; without a span here the renderer played the narration picture
    # straight through and cut the bite audio in over somebody else's face - whoever
    # is talking has to be visible, so the bite gets the frames that carry it.
    timeline=build_timeline(project,plan)
    bite_spans=[]
    for seg in timeline['segments']:
        if seg['kind']!='sound_bite':continue
        a=files.get(seg['asset_name'])
        if not a:raise ValueError(f"Sound bite source {seg['asset_name']} is not uploaded.")
        bite_spans.append({'start':round(seg['timeline_in_s']*1000),'end':round(seg['timeline_out_s']*1000),
                           'source_in':round(seg['source_in_s']*1000),'source_out':round(seg['source_out_s']*1000),
                           # The slot carries the speaker the script wrote for this
                           # bite, so the caption burned on the bite and the one the
                           # audit checks are the same string.
                           'filename':seg['asset_name'],'beat':'sound-bite','bite_id':seg['id'],
                           'caption':(next((x.get('script_caption') or x.get('caption') or '' for x in bites if x['id']==seg['id']),'') or 'None'),
                           'direction':f"Sound bite: {seg['asset_name']} on camera.",'gap':True})
    spans=[]
    for b in narratives:
        spans.append({'start':b['narration_in_ms'],'end':b['narration_out_ms'],'source_in':b['source_in_ms'],'source_out':b['source_out_ms'],'filename':b['source_file'],'beat':b['label'],'caption':b['caption'],'direction':b['direction'],'gap':False})
    for key,g in gaps.items():
        end=key[1]
        # The approved tail is picture only. Extend to the enclosing output frame.
        if key[1]==voice_ms and g['source_out_ms']-g['source_in_ms']>key[1]-key[0]:end=math.ceil(voice*30-1e-9)*1000/30
        spans.append({'start':key[0],'end':end,'source_in':g['source_in_ms'],'source_out':g['source_out_ms'],'filename':g['source_file'],'beat':'approved-gap','caption':'None','direction':'Approved proposed gap footage; content and rights require review.','gap':True})
    # The narration spans were built on the narration clock, but the picture runs on
    # the master clock the bites created. Rather than shifting coordinates, cut the
    # narration spans at every bite point and emit them segment by segment: each
    # piece keeps its own source range and lands where the timeline says it should.
    bite_by_start={round(b['start']):b for b in bite_spans}
    queue=sorted(spans,key=lambda x:x['start'])
    cutpoints=[]
    acc=0.0
    for seg in timeline['segments']:
        if seg['kind']=='sound_bite':
            cutpoints.append((round(seg['narration_resume_s']*1000), None,
                              round(seg['timeline_in_s']*1000)))
    out=[]
    # Walk the master timeline; within each narration segment, take the part of each
    # narration span that falls inside it.
    for seg in timeline['segments']:
        if seg['kind']=='sound_bite':
            out.append(bite_by_start[round(seg['timeline_in_s']*1000)])
            continue
        # `spans` are already laid out on the master clock: a narration span starts at
        # the script's own narration_in/out marks, which are master-clock marks when
        # the script is written on one continuous clock (every beat's pause point is
        # the previous narration's OUT). So the segment's own narration window is the
        # range to intersect with -- converting through source_in_s compares the voice
        # track's timeline against the edit's, which are two different clocks.
        lo=round(seg['source_in_s']*1000); hi=round(seg['source_out_s']*1000)
        base=round(seg['timeline_in_s']*1000)
        for sp in sorted(spans,key=lambda x:x['start']):
            a0=max(sp['start'],lo); a1=min(sp['end'],hi)
            if a1<=a0: continue
            piece=dict(sp)
            piece['start']=base+(a0-lo)
            piece['end']=base+(a1-lo)
            piece['source_in']=sp['source_in']+(a0-sp['start'])
            piece['source_out']=piece['source_in']+(piece['end']-piece['start'])
            out.append(piece)
    spans=out
    spans.sort(key=lambda x:(x['start'],x['end']));last=0;scenes=[]
    for span in spans:
        if abs(span['start']-last)>.001:raise ValueError('The picture plan contains a gap or overlap.')
        last=span['end']
        # A sound bite is one continuous piece of transmission: cutting it at a
        # subtitle boundary would splice somebody's sentence into two shots for no
        # editorial reason. Only the narration is indexed against the cues.
        points=([span['start'],span['end']] if span.get('beat')=='sound-bite'
                else [span['start']]+[b['start_ms'] for b in blocks if span['start']<b['start_ms']<span['end']]+[span['end']])
        for start,end in zip(points,points[1:]):
            fi,fo=round(start*.03),round(end*.03)
            if fo<=fi:raise ValueError('A script or subtitle boundary is shorter than one output frame.')
            # Scene times are on the master clock; the subtitle cues are on the
            # narration clock. Convert before looking the cue up, or a scene after a
            # sound bite is labelled with the wrong line of the script.
            # Master time to narration time: subtract only the bites that have
            # already finished by this point. Counting bites whose PAUSE sits before
            # this instant was wrong -- a bite's pause point is on the narration
            # clock, not the master clock -- and it swallowed the following line.
            # Only the part of a bite that has ALREADY played sits between the master
            # clock and the narration clock. Counting a whole bite the moment its slot
            # opened shifted every later scene back by the bite's remaining length and
            # labelled it with an earlier line of the script.
            bite_ms=sum(min(sp['end'],start)-sp['start'] for sp in bite_spans if sp['start']<start)
            narr_at=start-bite_ms
            cue=next((b for b in reversed(blocks) if b['start_ms']<=narr_at),blocks[0]);a=files[span['filename']]
            source_ms=span['source_in']+(start-span['start']);source_end_ms=span['source_in']+(end-span['start'])
            if source_ms<span['source_in']-.001 or source_end_ms>span['source_out']+1:raise ValueError('A picture slot exceeds its explicitly approved source range.')
            try:recorded_fps=str(source_rate(root/a['path']))
            except ValueError:recorded_fps=''
            row={'source_fps':recorded_fps,'shot_id':f"Srt{cue['number']:03d}",'source_file':span['filename'],**({'bite_id':span['bite_id']} if span.get('bite_id') else {}),'source_in_ms':round(source_ms),'source_out_ms':round(source_end_ms),'timeline_in_ms':round(start),'timeline_out_ms':round(end),'srt_blocks':[cue['number']],'required_caption':span['caption'],'continuity':f"Documentary beat {span['beat']}",'plain_cue':True}
            scenes.append({'id':uuid.uuid4().hex[:16],'srt_blocks':[cue['number'],cue['number']],'frame_in':fi,'frame_out':fo,'narration':cue['text'],'purpose':'documentary review','emotion':'neutral','focus':span['direction'] or 'Documentary footage','category':'general','person':'','asset_id':a['id'],'in_s':round(source_ms/1000,6),'motion':'auto','transition':'cut','context_only':False,'notes':'','approved':False,'profile':project.get('profile','documentary'),'focus_position':'center','motion_strength':'subtle','plan_row':row})
    if not scenes or scenes[0]['frame_in']!=0 or any(a['frame_out']!=b['frame_in'] for a,b in zip(scenes,scenes[1:])):raise ValueError('The frame-aligned picture plan has a gap or overlap.')
    staged=copy.deepcopy(project);staged['scenes']=scenes;staged['review_only_draft']=True;staged['script_moving_only']=True
    staged['script_application']={'recognized_instructions':len(beats),'placed_scenes':len(scenes),'issues':[],'mode':'documentary_beat_review','note':'Review storyboard only. On-screen captions, final fade and final export are not implemented.'}
    staged['documentary_beat_review']={'fitted_beats':[{'label':b['label'],'kind':b['kind'],'source_file':b['source_file'],
        'source_in_ms':b['source_in_ms'],'source_out_ms':b['source_out_ms'],
        'narration_in_ms':b.get('narration_in_ms'),'narration_out_ms':b.get('narration_out_ms'),
        'pause_ms':b.get('pause_ms'),'caption':b['caption']} for b in beats],
        'fitted_ranges':copy.deepcopy(fitted_ranges),
        # A sound bite's own timeline, saved with the storyboard, so the frame audit
        # can verify the bite's picture slot against the plan it was cut from.
        # The Beat each bite was cut from, and the caption that Beat asks for, have to
        # be saved with it. Without them the audit had no way to tell which scripted
        # caption a slot should carry, and the renderer had nothing to burn on screen;
        # both then reported the bite with an empty caption and silently dropped it.
        'sound_bites':[{'bite_id':seg['id'],
                        'script_beat':next((x['script_beat'] for x in bites if x['id']==seg['id']),None),
                        'source_file':seg['asset_name'],
                        'timeline_in_ms':round(seg['timeline_in_s']*1000),
                        'timeline_out_ms':round(seg['timeline_out_s']*1000),
                        'source_in_ms':round(seg['source_in_s']*1000),
                        'source_out_ms':round(seg['source_out_s']*1000),
                        'required_caption':(next((x.get('script_caption') or '' for x in bites if x['id']==seg['id']),'') or None)}
                       for seg in timeline['segments'] if seg['kind']=='sound_bite'],
        'sub_frame_gaps':[{'narration_in_ms':a,'narration_out_ms':b,'length_ms':b-a} for a,b in sub_frame],'script_sha256':hashlib.sha256(project['production_script']['text'].encode()).hexdigest(),'approved_gaps':copy.deepcopy(approved_gaps),'approved_overlaps':copy.deepcopy(approved_overlaps),'silent_tail_authorized':bool(allow_tail),'warning':'Review only: source content, identity, context and rights are unverified; script captions and final fade are not rendered.'}
    return staged,plan


def output_offset(segment):
    """Master-clock offset for a narration segment: the bites that precede it.

    build_timeline lays a narration segment at the sum of the narration played so
    far plus every bite already inserted. Its source_in_s is the narration mark,
    so the difference between the two is exactly the bites before it.
    """
    return segment['timeline_in_s'] - segment['source_in_s']


def verified_source_interval(project, scene, asset):
    """Exact plan range for an unchanged beat-review slot, otherwise None.

    Checking against both the original script and the explicit gap approval prevents
    frame-rounding noise being mistaken for an overlap without forgiving real edits.

    A storyboard is verified against the ranges recorded when it was built, not
    against the current text of the script. The build already fitted every Beat to
    its file and saved the result (`fitted_beats` / `fitted_ranges`), which is what
    the picture slots actually contain; requiring the script's hash to still match
    made a single edit anywhere invalidate every slot at once, so one timeline could
    serve only the one script it was born from. A changed script is reported as a
    stale plan to rebuild (see `stale_script_message`), not as a broken picture.
    """
    try:
        record=project.get('documentary_beat_review') or {}
        if not record:return None
        row=scene.get('plan_row') or {};filename=asset.get('original_name') or asset.get('name')
        if scene.get('transition')!='cut' or row.get('source_file')!=filename:return None
        a,b,si,so=(row[k] for k in ('timeline_in_ms','timeline_out_ms','source_in_ms','source_out_ms'))
        if any(type(x) is not int for x in (a,b,si,so)) or b<=a or so<=si:return None
        if scene.get('frame_in')!=round(a*.03) or scene.get('frame_out')!=round(b*.03) or abs(float(scene.get('in_s',-1))*1000-si)>.001:return None
        label=str(row.get('continuity') or '')
        if label=='Documentary beat sound-bite':
            # A sound bite is not a scripted Beat: its range comes from the bite
            # plan, and the plan is what the audio is cut from. Verify the slot
            # against the plan's own IN/OUT rather than against a narration Beat.
            candidates=[]
            for bit in (record.get('sound_bites') or []):
                if bit.get('source_file')!=filename:continue
                candidates.append((bit['timeline_in_ms'],bit['timeline_out_ms'],
                                   bit['source_in_ms'],bit['source_out_ms']))
        elif label=='Documentary beat approved-gap':
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
                fitted=[{'label':number,'kind':'narration','source_file':filename,
                         'narration_in_ms':saved.get('narration_in_ms'),'narration_out_ms':saved.get('narration_out_ms'),
                         'source_in_ms':saved['source_in_ms'],'source_out_ms':saved['source_out_ms']}]
            candidates=[(x['narration_in_ms'],x['narration_out_ms'],x['source_in_ms'],x['source_out_ms'])
                        for x in fitted if x.get('narration_in_ms') is not None and x.get('narration_out_ms') is not None]
        else:return None
        # A narration Beat interrupted by a sound bite appears twice: once before
        # the bite and once after it, both on the same source file. The second
        # piece starts at a source offset the original range never mentioned, so
        # the check has to allow a slot that begins INSIDE the approved range and
        # runs from the matching offset.
        # A slot boundary is rounded to a whole frame (1/30 s = 34 ms), and both
        # ends of every slot are rounded independently, so an interior slot can sit
        # up to one frame past its window without any frame being used twice. Allow
        # exactly that, and nothing more.
        for start,end,source_in,source_out in candidates:
            if a>=start and b<=end and abs(si-(source_in+a-start))<=1 and abs(so-(source_in+b-start))<=1 and so<=source_out+FRAME_MS:
                return si/1000,so/1000
            offset=si-source_in
            if 0<=offset and b>a and so-si==b-a and si+ (b-a) <= source_out+1:
                # Continuation: same Beat, source picked up where the bite left it.
                if source_in <= si and so<=source_out+1:
                    return si/1000,so/1000
    except (ValueError,KeyError,TypeError,OverflowError):pass
    return None
