"""Strict reader for one-shot-per-cue, labelled plain-text production scripts."""
import json
import re
from edl import parse, timecode_ms
from section_script import norm

HEAD = re.compile(r'^Shot:\s*(Srt\d+)\s*$', re.M | re.I)
FIELDS = {'Narration timeline', 'Exact narration', 'Full source', 'Source IN', 'Source OUT', 'Treatment', 'Visual direction', 'Continuity/caption'}

def is_plain_script(text):
    return bool(re.search(r'^Shot:\s*Srt\d+', text, re.M | re.I))

def parse_plain_script(text, blocks):
    if not blocks: raise ValueError('Import the original SRT before reading the script.')
    # The authored summary and source audit are preserved in the document and remain
    # unverified review instructions. Only the explicitly headed shot section is parsed.
    if re.search(r'^##\s+Storyboard Production Script\s*$',text,re.M|re.I):
        sections=list(re.finditer(r'^##\s+Storyboard Production Script\s*$',text,re.M|re.I))
        if len(sections)!=1:raise ValueError('The script has multiple storyboard sections.')
        section=sections[0];end=re.search(r'^##\s+',text[section.end():],re.M)
        body=text[section.end():section.end()+end.start()] if end else text[section.end():]
        body=re.sub(r'\n\s*---\s*$', '', body).strip()
        if HEAD.search(text[:section.start()]) or (end and HEAD.search(text[section.end()+end.start():])):
            raise ValueError('Shot instructions occur outside the storyboard section.')
        text=body
    heads=list(HEAD.finditer(text))
    if not heads or text[:heads[0].start()].strip(): raise ValueError('Unsupported text before the first shot.')
    if [int(re.search(r'\d+',h[1])[0]) for h in heads] != [int(b['number']) for b in blocks]:
        raise ValueError('The script must have exactly one shot for each SRT cue, in order.')
    rows=[]
    for i,h in enumerate(heads):
        sid=h[1]; cue=blocks[i]; content=text[h.end():heads[i+1].start() if i+1<len(heads) else len(text)]
        fields={}
        for line in content.splitlines():
            if not line.strip(): continue
            k,sep,v=line.partition(':')
            if not sep or k not in FIELDS or k in fields: raise ValueError(sid+': duplicate or unsupported instruction: '+line[:120])
            fields[k]=v.strip()
        if set(fields)!=FIELDS or any(not v for v in fields.values()):raise ValueError(sid+': missing required labelled instruction.')
        clock=lambda v:timecode_ms(v.strip(),sid)
        parts=re.split(r'\s*(?:-->|→|->)\s*',fields['Narration timeline'])
        if len(parts)!=2:raise ValueError(sid+': narration timeline must have IN and OUT.')
        tin,tout=map(clock,parts)
        if (tin,tout)!=(cue['start_ms'],cue['end_ms']):raise ValueError(sid+': narration timeline does not match the SRT cue.')
        source=fields['Full source'];proposed=bool(re.match(r'^PROPOSED:\s*',source,re.I));source=re.sub(r'^PROPOSED:\s*','',source,flags=re.I)
        if not re.fullmatch(r'[^/\\\r\n]+\.(?:mp4|mov|jpg|jpeg|png|webp)',source,re.I):raise ValueError(sid+': an exact full video or photograph filename is required.')
        treatment=fields['Treatment'];photo=bool(re.fullmatch(r'(?:PHOTO|PHOTOGRAPH|STILL PHOTO|STILL HOLD)',treatment,re.I))
        if photo:
            if not re.search(r'\.(?:jpg|jpeg|png|webp)$',source,re.I):raise ValueError(sid+': a photo treatment requires an uploaded image filename, not a video frame.')
            if any(v.strip().upper() not in ('N/A','NA','NONE','—','-') for v in (fields['Source IN'],fields['Source OUT'])):raise ValueError(sid+': photos have no source IN/OUT; use N/A for both.')
            sin=0;sout=tout-tin;typ='STILL';requested='STILL HOLD'
        else:
            if not re.search(r'\.(?:mp4|mov)$',source,re.I):raise ValueError(sid+': video and native static treatments require a video filename.')
            sin=clock(fields['Source IN']);sout=clock(fields['Source OUT'])
            if sout<=sin or sout-sin!=tout-tin:raise ValueError(sid+': source range must match the cue duration at 1.0×.')
            if re.fullmatch(r'VIDEO\s*,\s*1\.0[×x]\s*,\s*loop\s+OFF',treatment,re.I):typ='VIDEO';requested='VIDEO'
            elif re.fullmatch(r'STILL\s*(?:\(native static interval\)|[—–-]\s*native static interval in source video)',treatment,re.I):typ='STILL';requested='STILL HOLD'
            else:raise ValueError(sid+': unsupported treatment; no playback changes were assumed.')
        mismatch=norm(fields['Exact narration'])!=norm(cue.get('text',''))
        warnings=['Visual content, identity, source version and rights are not verified.','Caption and continuity instructions require review before rendering.']
        if proposed:warnings.append('Source is marked PROPOSED in the script; verify before approval.')
        if mismatch:warnings.append('Script narration differs from the uploaded SRT; neither was changed.')
        if typ=='STILL':warnings.append(('Uploaded photograph held static' if photo else 'Native static interval played from the source')+' for review only; eligibility, still budget, identity and rights remain unverified.')
        rows.append(dict(beat_id=sid,shot_id=sid,clip_id='',srt_blocks=[cue['number']],timeline_in_ms=tin,timeline_out_ms=tout,source_file=source,source_in_ms=sin,source_out_ms=sout,visual_status=typ,requested_treatment=requested,visible_content=fields['Visual direction'],picture_treatment=treatment,required_caption=fields['Continuity/caption'],match_reason=fields['Continuity/caption'],limit_on_implication='Declared visual is not independently verified.',script_narration=fields['Exact narration'],srt_narration=cue.get('text',''),narration_mismatch=mismatch,register_window='',register_visible='',register_restrictions='',coverage_review='Requires visual inspection and rights review.',import_blockers=warnings,strict_production=True,transition_out='cut',source_proposed=proposed,plain_cue=True,uploaded_photo=photo,native_static_interval=(typ=='STILL' and not photo)))
    return parse(json.dumps(rows).encode(),'.json')
