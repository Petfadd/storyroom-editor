"""Read explicit documentary Beat blocks without creating or altering a timeline.

This is a preflight reader, not an editor or renderer. No source, timing, speaker or
rights are inferred from text or filenames; callers must not regard output as approval.
"""
import re
from pathlib import Path

HEAD = re.compile(r'^Beat:\s*(\d+)\s*$', re.M | re.I)
CLOCK = r'\d{2}:\d{2}:\d{2}[,.]\d{3}'

def time_ms(value):
    if not re.fullmatch(CLOCK, value.strip()):
        raise ValueError('Expected HH:MM:SS,mmm timecode.')
    h, m, s, ms = map(int, re.split(r'[:,.]', value.strip()))
    if m >= 60 or s >= 60:
        raise ValueError('Invalid timecode.')
    return ((h * 60 + m) * 60 + s) * 1000 + ms

def read_beats(text):
    heads = list(HEAD.finditer(text or ''))
    if not heads:
        raise ValueError('No numbered Beat blocks found.')
    beats, seen = [], set()
    for i, head in enumerate(heads):
        label = head.group(1)
        number = int(label)
        if number in seen:
            raise ValueError(f'Beat {label}: duplicate beat number.')
        if beats and number <= beats[-1]['number']:
            raise ValueError(f'Beat {label}: beat numbers must be in order.')
        seen.add(number)
        body = text[head.end():heads[i + 1].start() if i + 1 < len(heads) else len(text)]
        def field(name, required=True):
            found = re.findall(r'^' + re.escape(name) + r':\s*([^\r\n]*)$', body, re.I | re.M)
            if len(found) > 1 or (required and (not found or not found[0].strip())):
                raise ValueError(f'Beat {label}: provide exactly one {name} field.')
            return found[0].strip() if found else ''
        typ = field('Type').upper()
        if typ in ('NARRATION (VOICE OVER)', 'VOICE OVER'): kind = 'narration'
        elif typ == 'DOCUMENTARY SOUND BITE': kind = 'sound_bite'
        else: raise ValueError(f'Beat {label}: unsupported beat Type {typ!r}.')
        # Either label names the one scripted source. Never guess from the file list.
        filename = field('Video File') if field('Video File', False) else field('Full Source')
        if Path(filename).name != filename or '\\' in filename or not re.search(r'\.(mp4|mov)$', filename, re.I):
            raise ValueError(f'Beat {label}: use an exact video filename, not a path.')
        try:
            si, so = time_ms(field('Source IN')), time_ms(field('Source OUT'))
            if so <= si: raise ValueError('Source OUT must be after IN.')
            beat = {'number': number, 'label': label, 'kind': kind, 'source_file': filename,
                    'source_in_ms': si, 'source_out_ms': so,
                    'caption': field('On-screen Caption', False),
                    'direction': field('Visual Direction', False)}
            # Same fact under either label used by the supplied scripts.
            state = field('Narration State') if field('Narration State', False) else field('VO State')
            audio = field('Footage Audio')
            if kind == 'narration':
                clock = field('Narration Clock') if field('Narration Clock', False) else field('VO Clock')
                clock = re.sub(r'^\s*(?:RESUMES|RESUMING|PLAYING|CONTINUES)\s+(?:at\s+)?', '', clock, flags=re.I)
                span = re.fullmatch(r'\s*('+CLOCK+r')\s*(?:-->|→|->)\s*('+CLOCK+r')\s*', clock)
                if not span: raise ValueError('Narration Clock must have IN and OUT.')
                ni, no = map(time_ms, span.groups())
                if no <= ni: raise ValueError('Narration Clock must have a positive duration.')
                if no-ni != so-si:
                    # Report the mismatch instead of refusing to read on. Nothing is
                    # retimed here; documentary_beat_plan fits the picture range and
                    # records every change for review.
                    beat['duration_mismatch_ms'] = (so-si)-(no-ni)
                if not re.match(r'^(PLAYING|RESUMING)\b',state,re.I) or not re.match(r'^MUTED\b',audio,re.I):
                    raise ValueError('Narration must be playing and ordinary footage muted.')
                beat.update(narration_in_ms=ni, narration_out_ms=no)
            else:
                match = re.search(r'PAUSED\s*\(\s*Suspended at\s+('+CLOCK+r')\s*\)', state, re.I)
                if match: pause = match.group(1)
                else:
                    # Other scripts keep the pause on the voice-over clock instead.
                    found = re.search(r'PAUSED\s+at\s+('+CLOCK+r')', field('Narration Clock') if field('Narration Clock', False) else field('VO Clock'), re.I)
                    if not found: raise ValueError('Sound bite must give PAUSED (Suspended at <timecode>) or a paused VO Clock.')
                    pause = found.group(1)
                if not re.match(r'^UNMUTED\b',audio,re.I): raise ValueError('Sound-bite source audio must be explicitly unmuted.')
                if field('Duration', False): duration = time_ms(field('Duration'))
                else:
                    # Duration may be written on the master timeline line instead.
                    found = re.search(r'\(\s*Duration:\s*'+CLOCK+r'\s*\)', field('Master Timeline', False))
                    if found: duration = time_ms(found.group(1))
                    else:
                        secs = re.search(r'\(\s*Duration:\s*(?:([\d.]+)\s*sec|(\d+)\s*min\s*(\d+)\s*sec)\s*\)', field('Master Timeline', False), re.I)
                        if not secs: raise ValueError('Sound bite must give a Duration.')
                        duration = round(float(secs.group(1))*1000) if secs.group(1) else (int(secs.group(2))*60+int(secs.group(3)))*1000
                if duration != so-si: raise ValueError('Sound-bite Duration does not match source IN–OUT.')
                # Either label names the declared speaker; it is a claim to review,
                # never a verified identity.
                beat.update(pause_ms=time_ms(pause), duration_ms=duration,
                            speaker=field('Speaker',False) or field('Speaker & Role',False))
        except ValueError as e:
            raise ValueError(f'Beat {label}: {e}') from e
        beats.append(beat)
    return beats

def audit_beats(beats, project, root):
    """Validate explicit source identities and audio continuity. Return all issues."""
    root=Path(root).resolve();issues=[];cursor=0;used={};assets=project.get('assets') or [];short=[]
    if not beats or beats[0]['kind']!='narration' or beats[-1]['kind']!='narration':
        issues.append('Documentary must start and end with narration.')
    for i,beat in enumerate(beats):
        label=beat['label'];matches=[a for a in assets if (a.get('original_name') or a.get('name'))==beat['source_file']]
        if len(matches)!=1:
            # One line per filename: read_beats and the beat reader both reach this,
            # and repeating it once per beat is the wall of errors this audit replaced.
            if beat['source_file'] not in [x.split('exact source ')[1].split(' is ')[0] for x in issues if 'exact source ' in x]:
                issues.append(f"Beat {label}: exact source {beat['source_file']} is missing or ambiguous "
                              f"(every beat naming it is affected; no substitution was made).")
        else:
            a=matches[0];path=(root/a.get('path','')).resolve()
            if a.get('kind')!='video' or not path.is_relative_to(root/'sources') or not path.is_file():
                issues.append(f'Beat {label}: source is not an available uploaded video.')
            elif beat['source_out_ms']>float(a.get('duration') or 0)*1000+.001:
                # Reported once, with the real numbers, at the end of the audit.
                short.append((label,a,beat['source_out_ms']))
            # A shot may legitimately come back under new narration, so reuse is a
            # review note, not a blocker. It is reported once per file below.
            used.setdefault(a.get('id'),[]).append((beat['source_in_ms'],beat['source_out_ms'],label,beat['kind']))
        if beat['kind']=='narration':
            start,end=beat['narration_in_ms'],beat['narration_out_ms']
            if start!=cursor:issues.append(f'Beat {label}: narration gap or overlap from {cursor/1000:.3f}s to {start/1000:.3f}s.')
            cursor=end
        else:
            # Two bites at the same pause play back to back, which the storyboard
            # supports with one explicit approval. Only a real fault blocks here.
            if i==0 or i==len(beats)-1 or (beats[i-1]['kind']!='narration' and beats[i-1].get('pause_ms')!=beat['pause_ms']) \
                    or (beats[i+1]['kind']!='narration' and beats[i+1].get('pause_ms')!=beat['pause_ms']):
                issues.append(f'Beat {label}: sound bite must be between narration blocks (or directly after another '
                              f'bite at the same pause).')
            if beat['pause_ms']!=cursor:
                previous=next((b for b in reversed(beats[:i]) if b['kind']=='narration'),None)
                if previous is None or beat['pause_ms']!=previous['narration_out_ms']:
                    issues.append(f'Beat {label}: pause {beat["pause_ms"]/1000:.3f}s does not match the preceding narration endpoint.')
        # On-screen captions used to block here because no renderer burned them in.
        # They do now (documentary_final_renderer drawtext + `burn_captions`), so this
        # is a review reminder, not a build blocker.

    if short:
        # One root-cause message instead of one line per beat. The user cannot act
        # on "OUT exceeds the video" eleven times; they can act on "your script
        # asks for 232 s of a 173.8 s file".
        by_asset={}
        for label,a,out_ms in short:
            entry=by_asset.setdefault(a.get('id'),{'asset':a,'labels':[],'highest':0})
            entry['labels'].append(label);entry['highest']=max(entry['highest'],out_ms)
        for entry in by_asset.values():
            a=entry['asset'];have=float(a.get('duration') or 0)
            name=a.get('original_name') or a.get('name')
            asked=max(b['source_out_ms'] for b in beats if b['source_file']==(a.get('original_name') or a.get('name')))/1000
            labels=entry['labels']
            span=f'Beat {labels[0]}' if len(labels)==1 else f'Beats {labels[0]}-{labels[-1]} ({len(labels)} beats)'
            issues.append(
                f'{name} is {have:.3f}s long, but the script asks for footage up to {asked:.3f}s, '
                f'so {span} run past the end of the uploaded file. The uploaded file is shorter than the '
                f'one this script was written against; re-export or re-upload the full-length source, '
                f'or edit those Source IN/OUT values. Nothing was trimmed automatically.')
    # Reusing a shot is normal documentary practice, so this is one review note
    # per file rather than one blocker per overlapping pair.
    for a in assets:
        spans=used.get(a.get('id'),[])
        pairs=[(v[2],u[2]) for i,u in enumerate(spans) for v in spans[:i] if u[0]<v[1] and u[1]>v[0]]
        if pairs:
            names=', '.join(sorted({x for pair in pairs for x in pair},key=lambda x:int(x)))
            issues.append(f'{a.get("original_name") or a.get("name")} source ranges overlap between Beats {names}. '
                          f'Reusing picture under new narration is allowed, so this is a note to check, not a fault: '
                          f'the storyboard asks you to approve each repeated range before it is built.')
    issues.extend([])
    voice=(project.get('voiceover') or {}).get('duration')
    if voice is None:issues.append('Upload the original narration.')
    elif abs(cursor-float(voice)*1000)>250:
        extra=(float(voice)*1000-cursor)/1000
        issues.append(
            f'The script ends its narration at {cursor/1000:.3f}s, but the uploaded narration '
            f'file lasts {float(voice):.3f}s ({extra:+.3f}s). The audio is not the timeline the '
            f'script describes: either the narration file includes words the script does not '
            f'account for, or the script is missing the closing narration block. No audio was '
            f'trimmed and no narration was retimed to hide this.')
    return issues
