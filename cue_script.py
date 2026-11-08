"""Strict reader for one-picture-per-cue scripts; declarations are not verification."""
import json
import re
from edl import parse, timecode_ms
from section_script import norm

CLOCK = r'\d{1,2}:\d{2}:\d{2}[.,]\d{3}'

def is_cue_script(text):
    return bool(re.search(r'^\*\*Picture placement\*\*', text, re.M))

def parse_cue_script(text, blocks):
    from document_plan import tables, _clock_span
    text=text.replace('\r\n','\n')
    if not blocks: raise ValueError('Import the original SRT before reading this script.')
    if re.search(r'https?://(?:www\.)?(?:youtube\.com|youtu\.be)/',text,re.I): raise ValueError('YouTube URLs are not permitted in the production script.')
    headings=list(re.finditer(r'^##\s+Srt(\d+)\s*$',text,re.M|re.I))
    if [int(h[1]) for h in headings]!=[int(b['number']) for b in blocks]: raise ValueError('Script cue sections must match the uploaded SRT in order.')
    register={}
    for table in tables(text):
        if table and {'Asset','Full source','Source IN → exclusive OUT','Type','Identification / context'}<=set(table[0]):
            for r in table:
                key=r['Asset'].upper()
                if key in register:raise ValueError('Duplicate clip in source register: '+key)
                register[key]=r
    if not register:raise ValueError('Selected asset register is missing.')
    result=[]
    for i,h in enumerate(headings):
        n=int(h[1]);cue=blocks[i];sid=h[0].strip()[3:];content=text[h.end():headings[i+1].start() if i+1<len(headings) else len(text)]
        content=re.split(r'^##\s+(?!Srt\d+)',content,maxsplit=1,flags=re.M|re.I)[0]
        original=re.search(r'Original SRT cue\s*\*\*(\d+)\*\*\s*·\s*Original time\s*\*\*('+CLOCK+r')\s*-->\s*('+CLOCK+r')\*\*',content,re.I)
        placement=re.search(r'\*\*Picture placement\*\*\s*('+CLOCK+r')\s*→\s*('+CLOCK+r')',content,re.I)
        shot=re.search(r'\*\*(VIDEO|STILL)\s*·\s*(C\d+)\s*·\s*(S\d+\.(?:mp4|mov))\*\*\s*·\s*Source IN\s*('+CLOCK+r')\s*·\s*OUT\s*('+CLOCK+r')\s+exclusive\s*·\s*[\d.]+\s*s\s*·\s*1\.0×\s*·\s*loop OFF',content,re.I)
        narration=re.search(r'\*\*Exact narration\*\*\s*\n\s*\n(.*?)\n\s*\n',content,re.S|re.I)
        if not original or not placement or not shot or not narration:raise ValueError(sid+': cue timing, narration or video instruction is missing or unsupported.')
        ms=lambda x:timecode_ms(x,sid)
        tin,tout=ms(placement[1]),ms(placement[2]);sin,sout=ms(shot[4]),ms(shot[5])
        if int(original[1])!=n or (ms(original[2]),ms(original[3]))!=(cue['start_ms'],cue['end_ms']) or (tin,tout)!=(cue['start_ms'],cue['end_ms']):raise ValueError(sid+': SRT and picture clocks differ.')
        if sout-sin!=tout-tin:raise ValueError(sid+': source and picture durations differ.')
        typ,clip,file=shot[1].upper(),shot[2].upper(),shot[3];entry=register.get(clip)
        if not entry or entry['Full source'].casefold()!=file.casefold() or entry['Type'].upper()!=typ or _clock_span(entry['Source IN → exclusive OUT'])!=(sin,sout):raise ValueError(sid+': source interval differs from selected asset register.')
        def field(name):
            m=re.search(r'^\*\*'+re.escape(name)+r'\.\*\*\s*(.*)$',content,re.M|re.I)
            if not m:raise ValueError(sid+': '+name+' is missing.')
            return m[1].strip()
        visible,caption,direction=field('Picture'),field('Editorial caption'),field('Direction')
        authored=' '.join(narration[1].split());mismatch=norm(authored)!=norm(cue.get('text',''))
        warnings=['Editorial caption and treatment require implementation and review before rendering.']
        if mismatch:warnings.append('Script narration differs from the uploaded SRT; neither is changed.')
        if typ=='STILL':warnings.append('Static broadcast interval requires still-policy and rights review; omitted from moving-only review.')
        result.append(dict(beat_id=sid,shot_id=sid,clip_id=clip,srt_blocks=[n],timeline_in_ms=tin,timeline_out_ms=tout,source_file=file,source_in_ms=sin,source_out_ms=sout,visual_status=typ,requested_treatment='VIDEO' if typ=='VIDEO' else 'STILL HOLD',visible_content=visible,picture_treatment=typ,required_caption=caption,match_reason=direction,limit_on_implication='Source content, identity, rights and full editorial instructions require review.',script_narration=authored,srt_narration=cue.get('text',''),narration_mismatch=mismatch,register_window=entry['Source IN → exclusive OUT'],register_visible=entry['Identification / context'],register_restrictions=entry['Type'],coverage_review='Declared in script; not independently verified.',import_blockers=warnings,strict_production=True,transition_out='cut'))
    return parse(json.dumps(result).encode(),'.json')
