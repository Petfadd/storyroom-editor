"""Read section-per-SRT production scripts without claiming visual verification."""
import json
import re
from edl import parse, timecode_ms

CLOCK = r'\d{1,2}:\d{2}:\d{2}[.,]\d{3}'
SHOT_FIELDS = {'Narration placement', 'Source', 'Extraction', 'Actually visible', 'Edit and continuity', 'Editorial caption', 'Audio', 'Meaning and limits'}


def clock(text):return timecode_ms(text, 'Production script clock')

def norm(text):return re.sub(r'[^a-z0-9]+', '', text.casefold())

def parse_section_script(text, blocks):
    from document_plan import tables
    if not blocks:raise ValueError('Import the original SRT before interpreting this script.')
    if re.search(r'https?://(?:www\.)?(?:youtube\.com|youtu\.be)/', text, re.I):raise ValueError('YouTube URLs are not permitted in the production script.')
    headings = list(re.finditer(r'^##\s+Srt(\d+)\s*$', text, re.M|re.I))
    if not headings:raise ValueError('No SRT sections found.')
    by_number={int(b['number']):b for b in blocks}
    if [int(m[1]) for m in headings]!=[int(b['number']) for b in blocks]:raise ValueError('Script SRT sections must match the uploaded SRT, once each and in order.')
    register={}
    for table in tables(text):
        if table and {'Clip','Source','Original window or extraction time','Visible content','Treatment and restrictions'}<=set(table[0]):
            for item in table:
                clip=item['Clip'].strip().upper()
                if clip in register:raise ValueError('Duplicate source-register clip '+clip+'.')
                register[clip]=item
    if not register:raise ValueError('This section-based script needs its source clip register.')
    rows=[];last=0
    for i,section in enumerate(headings):
        number=int(section[1]);end=headings[i+1].start() if i+1<len(headings) else len(text)
        content=text[section.end():end]
        content=re.split(r'^##\s+(?!Srt\d+)',content,maxsplit=1,flags=re.M|re.I)[0]
        cue=by_number[number]
        timeline=re.search(r'\*\*Narration timeline\*\*\s*('+CLOCK+r')\s*[–—-]\s*('+CLOCK+r')',content)
        if not timeline or (clock(timeline[1]),clock(timeline[2]))!=(cue['start_ms'],cue['end_ms']):raise ValueError(f'Srt{number:02}: script and SRT cue clocks do not agree.')
        narration=re.search(r'\*\*Exact narration\*\*\s*\n\s*\n((?:>[^\n]*\n?)+)',content)
        if not narration:raise ValueError(f'Srt{number:02}: approved narration is missing.')
        authored=' '.join(re.sub(r'^>\s*','',line).strip() for line in narration[1].splitlines())
        mismatch=norm(authored)!=norm(cue.get('text',''))
        shot_headers=list(re.finditer(r'^###\s+(Srt\d+-[A-Za-z]+)\s*$',content,re.M|re.I))
        if not shot_headers:raise ValueError(f'Srt{number:02}: no shots found.')
        expected=1
        for j,sh in enumerate(shot_headers):
            sid=sh[1];m=re.fullmatch(r'Srt0*(\d+)-([A-Za-z]+)',sid,re.I);suffix=0
            for letter in m[2].upper():suffix=suffix*26+ord(letter)-64
            if int(m[1])!=number or suffix!=expected:raise ValueError(sid+': shots must be in block and letter order.')
            expected+=1
            shot=content[sh.end():(shot_headers[j+1].start() if j+1<len(shot_headers) else len(content))]
            fields={}
            for field in re.finditer(r'^- \*\*([^*]+)\*\*\s*(.*)$',shot,re.M):
                if field[1] in fields:raise ValueError(sid+': duplicate '+field[1]+'.')
                fields[field[1]]=field[2].strip()
            if set(fields)!=SHOT_FIELDS:raise ValueError(sid+': missing or unsupported shot instructions: '+', '.join(sorted(set(fields)^SHOT_FIELDS)))
            placement=re.search(r'('+CLOCK+r')\s*[–—-]\s*('+CLOCK+r')',fields['Narration placement'])
            if not placement:raise ValueError(sid+': narration placement is missing.')
            tin,tout=clock(placement[1]),clock(placement[2])
            if tin!=last or tout<=tin or tin<cue['start_ms'] or tout>cue['end_ms']:raise ValueError(sid+': timeline gap, overlap or out-of-block shot.')
            last=tout
            src=re.search(r'`(S\d+\.(?:mp4|mov))`\s*·\s*\*\*Clip\*\*\s*(C\d+)\s*·\s*\*\*Treatment\*\*\s*(.*)',fields['Source'],re.I)
            if not src:raise ValueError(sid+': source, clip or treatment is missing.')
            file,clip=src[1],src[2].upper();entry=register.get(clip)
            if not entry or entry['Source'].strip().casefold()!=file.casefold():raise ValueError(sid+': clip reference does not match the source register.')
            treatment_text=src[3].strip().rstrip('.');treatment=re.sub(r'\s*·\s*intentional archival reuse\s*$','',treatment_text,flags=re.I).upper()
            if treatment=='NORMAL-SPEED VIDEO':treatment='VIDEO'
            if treatment not in ('VIDEO','STILL HOLD','EDITORIAL FREEZE-FRAME'):raise ValueError(sid+': unsupported treatment '+treatment_text)
            ext=fields['Extraction'];frame=re.search(r'Source frame\s+('+CLOCK+r')',ext,re.I);rng=re.search(r'Source IN\s+('+CLOCK+r')\s*·\s*source OUT\s+('+CLOCK+r')\s*\(exclusive\)',ext,re.I)
            if treatment=='VIDEO':
                if not rng:raise ValueError(sid+': explicit source IN and exclusive OUT are required.')
                source_in,source_out=clock(rng[1]),clock(rng[2])
                if source_out-source_in!=tout-tin:raise ValueError(sid+': source and timeline durations differ.')
            else:
                if not frame:raise ValueError(sid+': explicit source frame is required.')
                source_in=clock(frame[1]);source_out=source_in+1
            window=entry['Original window or extraction time'];times=re.findall(CLOCK,window)
            if not times:raise ValueError(sid+': source register has no clock.')
            if treatment=='VIDEO' and (len(times)!=2 or source_in<clock(times[0]) or source_out>clock(times[1])):raise ValueError(sid+': segment is outside the registered source window.')
            if treatment=='STILL HOLD' and ('Frame' not in window or source_in!=clock(times[0])):raise ValueError(sid+': still frame differs from its registered extraction.')
            if treatment=='EDITORIAL FREEZE-FRAME' and (len(times)!=2 or source_in!=clock(times[1])-1):raise ValueError(sid+': freeze must be the last displayed frame before registered exclusive OUT.')
            if not re.fullmatch(r'Mute source\. Continue narration\.',fields['Audio'],re.I):raise ValueError(sid+': unsupported audio instruction; no audio was substituted.')
            warnings=['Editorial caption and its layout must be implemented and reviewed before rendering.']
            if mismatch:warnings.append('Approved script narration differs from uploaded SRT wording; compare without changing either file.')
            if treatment=='EDITORIAL FREEZE-FRAME':warnings.append('Freeze must use the final displayed frame before the previous exclusive OUT; exact frame selection needs implementation and verification.')
            if re.search(r'\b(?:crop|inset|plain field|plain dark field|names under|text beside)',fields['Edit and continuity'],re.I):warnings.append('Requested crop, inset or text placement requires explicit implementation and review.')
            rows.append({'beat_id':sid,'shot_id':sid,'clip_id':clip,'srt_blocks':[number],'timeline_in_ms':tin,'timeline_out_ms':tout,'source_file':file,'source_in_ms':source_in,'source_out_ms':source_out,'visual_status':'VIDEO' if treatment=='VIDEO' else 'STILL','requested_treatment':treatment,'intentional_archival_reuse':'archival reuse' in treatment_text.lower(),'visible_content':fields['Actually visible'],'picture_treatment':treatment,'limit_on_implication':fields['Meaning and limits'],'required_caption':fields['Editorial caption'],'source_audio_instruction':fields['Audio'],'match_reason':fields['Edit and continuity'],'script_narration':authored,'srt_narration':cue.get('text',''),'narration_mismatch':mismatch,'register_window':window,'register_visible':entry['Visible content'],'register_restrictions':entry['Treatment and restrictions'],'coverage_review':'Declared in script; source content, identity, version and rights require review.','import_blockers':warnings,'strict_production':True,'transition_out':'cut'})
        if last!=cue['end_ms']:raise ValueError(f'Srt{number:02}: shots do not cover the complete cue.')
    return parse(json.dumps(rows).encode(),'.json')
