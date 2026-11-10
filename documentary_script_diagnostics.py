"""Report documentary beat-script problems without converting or editing a plan."""
import re
from pathlib import Path

BEAT = re.compile(r'^Beat:\s*(\d+)\s*$', re.I | re.M)
CLOCK = r'(\d{2}:\d{2}:\d{2}[,.]\d{3})'
# A resumed beat writes "RESUMES at 00:00:32,000 --> ...". documentary_beats
# already ignores that prefix; this reader has to as well, or the beat that
# carries the closing words is skipped and the narration looks short.
_PREFIX = re.compile(r'^\s*(?:RESUMES|RESUMING|PLAYING|CONTINUES|CONTINUING)\s+(?:at\s+)?', re.I)


def _strip_prefix(line):
    # A plain replacement string here would interpret the arrow group in
    # "00:00:32,000 --> 00:00:38,000"; a function cannot.
    return _PREFIX.sub('', line)

def _ms(value):
    h,m,s,ms=map(int,re.split(r'[:,.]',value))
    if m>=60 or s>=60: raise ValueError('Invalid timecode')
    return ((h*60+m)*60+s)*1000+ms

def diagnose(project,root):
    text=(project.get('production_script') or {}).get('text') or ''
    heads=list(BEAT.finditer(text)); issues=[]
    if not heads:
        return {'status':'not_detailed','ready_to_build':False,'build_issues':['This production script format is not supported for storyboard building. Use the labelled one-shot-per-SRT-cue format; documentary sound-bite automation is not implemented.']}
    # This used to read "not implemented", which sounded like a broken deployment
    # even when the only real problem was a script/file mismatch. Beat scripts go
    # through the explicit review path instead, so say that.
    issues.append(f'This script uses {len(heads)} labelled Beat blocks. Beat scripts are reviewed explicitly ("Review the Beat picture plan") rather than built automatically, so every source range can be checked before anything is assembled.')
    assets=project.get('assets',[]); intervals=[]; narration=[]
    for i,h in enumerate(heads):
        body=text[h.end():heads[i+1].start() if i+1<len(heads) else len(text)]
        # Scripts name the file under Video File or Full Source; the beat reader
        # accepts both, so this check has to as well or it reports a false miss.
        name=re.search(r'^(?:Video File|Full Source):\s*(\S.*?)\s*$',body,re.M|re.I)
        if name:
            filename=name.group(1).strip();matches=[a for a in assets if (a.get('original_name') or a.get('name'))==filename and (Path(root)/a.get('path','')).is_file()]
            if len(matches)!=1 and filename not in intervals:intervals.append(filename)
        # Match the whole stated line before splitting on the arrow. Searching for
        # a clock pair anywhere in the line made "PAUSED at 00:00:32,000" read as
        # the start of a narration span, which corrupted the beat before it.
        stated=re.search(r'^Narration Clock:\s*([^\r\n]*)$',body,re.M|re.I)
        if stated:
            span=re.fullmatch(r'\s*'+CLOCK+r'\s*(?:-->|→|->)\s*'+CLOCK+r'\s*',_strip_prefix(stated.group(1)))
            if span:
                try:narration.append((h.group(1),_ms(span.group(1)),_ms(span.group(2))))
                except ValueError:issues.append(f'Beat {h.group(1)} contains an invalid narration timecode.')
    if intervals:issues.append('Script names missing or ambiguous source files: '+', '.join(intervals)+'. Filenames must match uploaded files exactly; no substitution was made.')
    if narration:
        if narration[0][1]>0:issues.append(f'Narration before Beat {narration[0][0]} is not assigned: 0.000–{narration[0][1]/1000:.3f}s.')
        for previous,current in zip(narration,narration[1:]):
            if current[1]!=previous[2]:
                kind='gap' if current[1]>previous[2] else 'overlap'
                issues.append(f'Narration {kind} between Beat {previous[0]} and Beat {current[0]}: {previous[2]/1000:.3f}s to {current[1]/1000:.3f}s. No narration was trimmed or retimed.')
        duration=(project.get('voiceover') or {}).get('duration')
        # A few milliseconds is encoder rounding between the narration file and
        # the SRT it came from, not an unaccounted passage of speech.
        if duration is not None and abs(float(duration)-narration[-1][2]/1000)>.25:
            issues.append(f'Last scripted narration ends at {narration[-1][2]/1000:.3f}s; uploaded narration lasts {float(duration):.3f}s. Review the difference; no audio was trimmed.')
    # Extract and audit only the explicitly labelled beat fields. This does not
    # create a storyboard or certify the source, its speaker or reuse rights.
    from documentary_beats import read_beats
    found=0;plans=[]
    try:
        beats=read_beats(text);found=len(beats)
        # Report what the Beat picture plan will actually do, not a demand the
        # plan then resolves by itself. The old audit said "re-upload the
        # full-length source" while the plan quietly clamped and extended the
        # same range; the panel contradicted the button sitting under it.
        plans=plan_notes(beats,project,root)
    except ValueError as exc:
        beats=[];issues.append('Beat reader: '+str(exc))
        # Count the blocks the reader could open so the summary is not "0 beats"
        # just because a later beat conflicts. This is a count, not approval.
        found=len(re.findall(r'^\s*Beat:\s*\d+\s*$',text,re.I|re.M))
    for note in plans:
        if note['blocking']:issues.append(note['text'])
    return {'status':'not_detailed','ready_to_build':False,'build_issues':list(dict.fromkeys(issues)),
            'build_notes':[x for x in plans if not x['blocking']],
            'documentary_beat_count':len(beats),'documentary_beat_blocks':found,'documentary_beats':beats}


def plan_notes(beats,project,root):
    """What the Beat picture plan will change, in the words the review uses.

    `blocking` notes are the ones the plan refuses to build without; the rest
    are changes it makes mechanically and records for review.
    """
    from documentary_beat_plan import frame_limits,proposal
    notes=[]
    # The narration file and the scripted narration must describe the same film.
    # This is a fact about the uploads, not a range decision, so it stays here.
    voice=(project.get('voiceover') or {}).get('duration')
    if voice is None:
        notes.append({'blocking':True,'text':'Upload the original narration.'})
    else:
        scripted=beats[-1].get('narration_out_ms') if beats[-1]['kind']=='narration' else None
        if scripted is not None and abs(float(voice)*1000-scripted)>250:
            notes.append({'blocking':True,'text':
                f'The script ends its narration at {scripted/1000:.3f}s, but the uploaded narration file '
                f'lasts {float(voice):.3f}s ({(float(voice)*1000-scripted)/1000:+.3f}s). The audio is not '
                f'the timeline the script describes: either the narration file includes words the script '
                f'does not account for, or the script is missing the closing narration block. No audio was '
                f'trimmed and no narration was retimed to hide this.'})
    try:
        data=proposal(project,beats,None,None,frame_limits(root,project.get('assets') or []))
    except (ValueError,KeyError,TypeError,OSError) as exc:
        notes.append({'blocking':True,'text':'Beat plan: '+str(exc)})
        return notes
    notes.extend({'blocking':True,'text':x} for x in data.get('issues') or [])
    notes.extend({'blocking':False,'text':x} for x in data.get('adjustments') or [])
    # Reusing a shot is normal documentary practice. Say the plan asks for
    # approval instead of calling it a fault. One line per file, as the review
    # panel does, rather than one line for every overlapping pair.
    by_file={}
    labels={b['number']:b['label'] for b in beats}
    for first,second in data.get('repeated_ranges') or []:
        source=next((b['source_file'] for b in data.get('beats') or [] if b['number']==second),'')
        by_file.setdefault(source,set()).update({str(labels.get(first,first)),str(labels.get(second,second))})
    for source,names in by_file.items():
        joined=', '.join(sorted(names,key=lambda x:int(x)))
        notes.append({'blocking':False,'text':f'{source} source ranges overlap between Beats {joined}. '
                                               f'Reusing picture under new narration is allowed, so this is a note '
                                               f'to check, not a fault: the storyboard asks you to approve each '
                                               f'repeated range before it is built.'})
    return notes
