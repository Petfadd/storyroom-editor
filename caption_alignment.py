"""Local, conservative narration-to-SRT phrase alignment. Never rewrites inputs."""
from difflib import SequenceMatcher
from pathlib import Path
import hashlib,json,re,threading,tempfile
from caption_layout import caption_phrases

_guard=threading.Lock()
_running=set()

def fingerprint(root,p):
    voice=p.get('voiceover') or {};path=Path(root)/voice.get('path','')
    if not voice or not path.is_file() or not (p.get('srt') or {}).get('blocks'):return None
    st=path.stat();data=[voice['path'],st.st_size,st.st_mtime_ns,p['srt']['blocks']]
    return hashlib.sha256(json.dumps(data,ensure_ascii=False,sort_keys=True).encode()).hexdigest()

def state(root,p):
    key=fingerprint(root,p)
    if not key:return {'status':'missing','detail':'Upload the original SRT and narration first.','phrases':[]}
    file=Path(root)/'.cache/caption_alignment'/f'{key}.json'
    if not file.exists():return {'status':'not_started','detail':'Match captions to narration before showing or burning them.','phrases':[]}
    try:data=json.loads(file.read_text(encoding='utf-8'))
    except (OSError,ValueError):return {'status':'error','detail':'Could not read alignment report. Run it again.','phrases':[]}
    with _guard:active=(str(root),key) in _running
    if data.get('status') in ('running','queued') and not active:data.update(status='interrupted',detail='Analysis was interrupted. Run it again.')
    return data

def _token(text):return ''.join(c for c in text.casefold() if c.isalnum())

def align(blocks, transcript):
    """Only accept cues where every spoken token matches within its SRT interval."""
    words=[]
    for segment in transcript.get('segments',[]):
        for w in segment.get('words') or []:
            token=_token(w.get('text',''));a=w.get('start_s');b=w.get('end_s')
            if token and a is not None and b is not None and 0<=a<b:words.append({'token':token,'start_ms':round(a*1000),'end_ms':round(b*1000)})
    phrases=caption_phrases(blocks);results=[];issues=[]
    for i,cue in enumerate(blocks):
        source=[_token(w) for w in str(cue.get('text','')).split()];source=[x for x in source if x]
        a,b=int(cue['start_ms']),int(cue['end_ms']);near=[w for w in words if w['start_ms']>=a-100 and w['end_ms']<=b+100 and w['end_ms']>a and w['start_ms']<b]
        matcher=SequenceMatcher(None,source,[w['token'] for w in near],autojunk=False)
        matched={x+k:y+k for x,y,n in matcher.get_matching_blocks() for k in range(n)}
        # Extra recognized words can signal an SRT omission: never silently omit speech.
        if not source or len(matched)!=len(source) or len(near)!=len(source):
            issues.append({'cue':cue.get('number',i+1),'reason':f'Narration and SRT words could not be matched confidently ({len(matched)}/{len(source)} words matched; {len(near)} heard).'});continue
        grouped=[p for p in phrases if p['cue_index']==i];offset=0;local=[]
        for ph in grouped:
            count=sum(bool(_token(x)) for x in ph['text'].split());last=offset+count-1
            if not count:continue
            start=max(a,near[matched[offset]]['start_ms']);end=min(b,near[matched[last]]['end_ms'])
            if start>=end:break
            local.append({'cue_index':i,'text':ph['text'],'start_ms':start,'end_ms':end});offset+=count
        if offset!=len(source) or len(local)!=len(grouped) or any(x['end_ms']>y['start_ms'] for x,y in zip(local,local[1:])):
            issues.append({'cue':cue.get('number',i+1),'reason':'Word timing overlaps or falls outside the SRT cue.'});continue
        results.extend(local)
    return {'status':'complete' if not issues else 'needs_review','phrases':results,'issues':issues,'detail':('Machine-matched audio and SRT; listen to verify timing. Visual content and rights are not verified.' if not issues else f'{len(issues)} SRT cue(s) did not match confidently. Captions for those cues are withheld; inspect the narration and SRT.')}

def start(root,p):
    root=Path(root);key=fingerprint(root,p)
    if not key:raise ValueError('Upload the original SRT and narration first.')
    ident=(str(root),key);dest=root/'.cache/caption_alignment'/f'{key}.json';dest.parent.mkdir(parents=True,exist_ok=True)
    with _guard:
        if ident in _running:return
        _running.add(ident)
    def save(data):
        tmp=dest.with_suffix('.tmp');tmp.write_text(json.dumps(data,ensure_ascii=False),encoding='utf-8');tmp.replace(dest)
    try:save({'status':'queued','detail':'Queued for local narration analysis.','phrases':[],'issues':[]})
    except Exception:
        with _guard:_running.discard(ident)
        raise
    blocks=json.loads(json.dumps(p['srt']['blocks']));source=root/p['voiceover']['path']
    def run():
        try:
            from local_understanding import _decode_audio,_transcribe,_WORKER
            with _WORKER:
                save({'status':'running','detail':'Listening to narration locally; this can take time on CPU.','phrases':[],'issues':[]})
                with tempfile.TemporaryDirectory(prefix='storyroom-align-') as d:
                    wav=Path(d)/'narration.wav';_decode_audio(source,wav);transcript=_transcribe(wav)
            save(align(blocks,transcript))
        except Exception as exc:save({'status':'error','detail':'Local alignment could not finish: '+str(exc)[:400],'phrases':[],'issues':[]})
        finally:
            with _guard:_running.discard(ident)
    threading.Thread(target=run,daemon=True).start()
