"""Strict parser for explicitly structured SrtNN-letter production shots.

No source identity, visual content or official provenance is inferred. Unsupported
instructions are retained as blockers rather than silently omitted.
"""
import json
import re
from edl import parse, timecode_ms

SHOT = re.compile(r'^srt0*(\d+)-([a-z]+)$', re.I)
CLIP = re.compile(r'^C\d+$', re.I)
SOURCE = re.compile(r'^S\d+\.(?:mp4|mov|jpg|jpeg|png)$', re.I)
TREATMENTS = {'VIDEO': 'VIDEO', 'CONTEXT VIDEO': 'CTX', 'ARCHIVAL REUSE': 'ARCHIVAL',
              'STILL HOLD': 'STILL', 'EDITORIAL FREEZE-FRAME': 'STILL'}


def key(text):
    return re.sub(r'[^a-z0-9]+', '_', str(text).strip().lower()).strip('_')


def value(row, *names):
    return next((str(row[n]).strip() for n in names if n in row and str(row[n]).strip()), '')


def clock(value_, label):
    if not value_: raise ValueError(label+' is required.')
    return timecode_ms(value_, label)


def span(row, whole, start, end, label, end_optional=False):
    text=value(row,*whole)
    if text:
        parts=re.split(r'\s*(?:→|-->|->|–|—)\s*',text)
        if len(parts)==1 and end_optional:return clock(parts[0],label+' IN'),None
        if len(parts)!=2:raise ValueError(label+' must contain exactly one IN → OUT range.')
        return clock(parts[0],label+' IN'),clock(parts[1],label+' OUT')
    a=value(row,*start);b=value(row,*end)
    return clock(a,label+' IN'),clock(b,label+' OUT') if b else (None if end_optional else clock(b,label+' OUT'))


def parse_detailed(text, blocks):
    """Return strict rows, [] for a legacy script, or fail closed on unsupported shot structure."""
    from document_plan import tables
    if re.search(r'^##\s+Srt\d+\s*$',text,re.M|re.I) and re.search(r'^###\s+Srt\d+-[A-Za-z]+\s*$',text,re.M|re.I):
        from section_script import parse_section_script
        return parse_section_script(text,blocks)
    found=[]
    for table in tables(text):
        if not table:continue
        header={key(k) for k in table[0]}
        if header & {'shot','shot_id','shot_reference','shot_ref'}:
            found.extend({key(k):v for k,v in row.items()} for row in table)
    if not found:
        if re.search(r'\bSrt\s*0*\d+\s*-\s*[A-Za-z]+\b',text,re.I):
            raise ValueError('Detailed SrtNN-A shots were found, but no supported shot table was recognized. Provide explicit shot, clip, source, timeline, source time and treatment columns; no fallback was applied.')
        return []
    prose=[line.strip() for line in text.splitlines() if line.strip() and not line.strip().startswith(('|','#')) and not re.fullmatch(r'[-*_ ]+',line.strip())]
    if prose:raise ValueError('The detailed script contains text outside its shot tables. Its instructions cannot be safely interpreted automatically; review and adapt the parser before assembly.')
    for table in tables(text):
        if table and not ({key(k) for k in table[0]} & {'shot','shot_id','shot_reference','shot_ref'}):
            raise ValueError('The detailed script contains an additional unsupported table; its instructions cannot be silently ignored.')
    if re.search(r'https?://(?:www\.)?(?:youtube\.com|youtu\.be)/', text,re.I):
        raise ValueError('Remove YouTube URLs from the production script; use uploaded filenames and clip references.')
    if not blocks:raise ValueError('Import the authored SRT before reading detailed shot times.')
    normalized=[];last_block=0;last_suffix=0;seen=set();last_t=0
    for i,row in enumerate(found,1):
        label=f'Shot row {i}'
        known={'shot','shot_id','shot_reference','shot_ref','clip','clip_id','clip_reference','clip_ref','source_file','source','source_video','filename','file','source_filename','narration_timeline_in_out','timeline_in_out','narration_in_out','timeline','narration_timeline_in','timeline_in','narration_in','narration_timeline_out','timeline_out','narration_out','treatment','shot_treatment','source_in_out','source_time','source_range','source_in','source_time_in','source_frame','frame_time','source_out','source_time_out','visual_description','visual','visible_content','description','restrictions','restriction','limitations','limit_on_implication','contextual_caption','caption','required_caption','source_audio','news_audio','audio_instruction','notes','editorial_notes'}
        unknown=[k for k,v in row.items() if k not in known and str(v).strip() not in ('','-','N/A','n/a')]
        if unknown:raise ValueError(label+': unsupported column(s) '+', '.join(unknown)+'; instructions cannot be silently ignored.')
        sid=value(row,'shot','shot_id','shot_reference','shot_ref');match=SHOT.fullmatch(sid)
        if not match:raise ValueError(label+': use a shot ID such as Srt01-A.')
        block=int(match[1]);suffix=0
        for letter in match[2].upper():suffix=suffix*26+ord(letter)-ord('A')+1
        if block<last_block or (block==last_block and suffix<=last_suffix):raise ValueError(label+': shots must be listed in SRT and letter order.')
        if sid.casefold() in seen:raise ValueError(label+': duplicate shot ID '+sid+'.')
        seen.add(sid.casefold());last_block,last_suffix=block,suffix
        clip=value(row,'clip','clip_id','clip_reference','clip_ref')
        src=value(row,'source_file','source','source_video','filename','file','source_filename')
        if not CLIP.fullmatch(clip):raise ValueError(label+': provide an explicit clip reference such as C01.')
        if not SOURCE.fullmatch(src):raise ValueError(label+': provide an exact source filename such as S01.mp4.')
        tin,tout=span(row,('narration_timeline_in_out','timeline_in_out','narration_in_out','timeline'),('narration_timeline_in','timeline_in','narration_in'),('narration_timeline_out','timeline_out','narration_out'),'Narration timeline')
        if tin!=last_t or tout<=tin:raise ValueError(label+': timeline must be ordered, contiguous, non-overlapping and start at zero.')
        last_t=tout
        treatment=value(row,'treatment','shot_treatment').upper().replace('–','-').replace('—','-')
        if treatment not in TREATMENTS:raise ValueError(label+': unsupported or missing treatment '+repr(treatment)+'.')
        still=TREATMENTS[treatment]=='STILL'
        sin,sout=span(row,('source_in_out','source_time','source_range'),('source_in','source_time_in','source_frame','frame_time'),('source_out','source_time_out'), 'Source time',end_optional=still)
        if sout is not None and sout<=sin:raise ValueError(label+': source OUT must be later than IN (OUT is exclusive).')
        if not still and sout-sin != tout-tin:raise ValueError(label+': source and narration durations differ; no speed change or hold was assumed.')
        touching=[b['number'] for b in blocks if b['start_ms']<tout and b['end_ms']>tin]
        if block not in touching:raise ValueError(label+': '+sid+' does not overlap its named SRT block.')
        visual=value(row,'visual_description','visual','visible_content','description')
        limitations=value(row,'restrictions','restriction','limitations','limit_on_implication')
        context=value(row,'contextual_caption','caption','required_caption')
        audio=value(row,'source_audio','news_audio','audio_instruction')
        notes=value(row,'notes','editorial_notes')
        blockers=[]
        if not visual: blockers.append('Visual description missing; inspect and specify it.')
        if re.search(r'\b(?:caption|on.screen text|lower.third|subtitle)\b',visual+' '+notes+' '+limitations,re.I):blockers.append('A caption or on-screen text instruction needs manual implementation and review.')
        if re.search(r'\b(?:use|include|keep|play|unmute|retain)\b.{0,30}\b(?:source|news|original)\s+(?:audio|sound|voice|voiceover)\b',notes+' '+limitations,re.I):blockers.append('Source audio instruction needs manual implementation; source audio is muted by default.')
        if re.search(r'\b(?:dissolve|crossfade|fade to black|loop|replay|reverse)\b',notes+' '+limitations,re.I):blockers.append('A transition or playback instruction needs manual implementation and review.')
        if context and context.casefold() not in ('none','no','n/a','-'):blockers.append('Required contextual caption needs manual implementation and review; automatic caption placement is not supported.')
        if audio and not re.fullmatch(r'(?:mute|muted|none|no|n/a|-)',audio,re.I):blockers.append('Source audio instruction needs manual implementation; source audio is muted by default.')
        if re.search(r'\b(?:slow motion|speed up|sped up|time.?lapse|\d+(?:\.\d+)?\s*[x×])\b',notes+' '+limitations,re.I):blockers.append('A speed-change instruction needs manual implementation; normal speed was not substituted.')
        normalized.append({'beat_id':sid,'shot_id':sid,'clip_id':clip.upper(),'srt_blocks':touching,'timeline_in_ms':tin,'timeline_out_ms':tout,
                           'source_file':src,'source_in_ms':sin,'source_out_ms':sout if sout is not None else sin+1,
                           'original_source_out_ms':sout,'visual_status':TREATMENTS[treatment], 'requested_treatment':treatment,
                           'visible_content':visual,'picture_treatment':treatment,'limit_on_implication':limitations,
                           'required_caption':context,'source_audio_instruction':audio,'match_reason':notes,
                           'coverage_review':'Requires human inspection; filename is not visual or source-provenance verification.',
                           'import_blockers':blockers,'strict_production':True,'transition_out':'cut'})
    if last_t < max(int(b['end_ms']) for b in blocks):raise ValueError('The shot table ends before the final narration block.')
    return parse(json.dumps(normalized).encode(),'.json')
