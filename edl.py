"""Optional one-row-per-picture-beat CSV/JSON plan (SRT block map or beat table).

A plan supplies explicit source intervals only; it never certifies media, identity or rights.
"""
import csv,io,json,math,re,hashlib,copy,uuid
from decimal import Decimal,InvalidOperation
from pathlib import Path
from production import asset_matches,matching_assets,match_problem

def srt_signature(p):return hashlib.sha256(json.dumps((p.get('srt') or {}).get('blocks',[]),sort_keys=True).encode()).hexdigest()

def plan(p):return p.get('picture_plan') or p.get('edit_decision_list') or {}

def row_id(row,index=''):
 return str(row.get('beat_id') or row.get('plan_row_id') or index)

def signature(p):
 return hashlib.sha256(json.dumps([plan(p),(p.get('production_script') or {}).get('text','')]+([p['source_bindings']] if p.get('source_bindings') else [])+([p['srt_block_map']] if p.get('srt_block_map') else []),sort_keys=True).encode()).hexdigest()
def integer(value,label):
 if isinstance(value,bool):raise ValueError(label+' must be an integer number of milliseconds.')
 try:
  number=float(value)
  if not math.isfinite(number) or number!=int(number) or not 0<=number<1e9:raise ValueError()
  return int(number)
 except (ValueError,TypeError,OverflowError):raise ValueError(label+' must be a non-negative integer.')
def timecode_ms(value,label):
 text=str(value).strip().replace(',','.')
 if not re.fullmatch(r'\d+:[0-5]\d(?::[0-5]\d)?(?:\.\d{1,3})?',text):raise ValueError(label+': expected HH:MM:SS.mmm or MM:SS.mmm, not an unlabelled seconds value.')
 total=Decimal(0)
 for part in text.split(':'):total=total*60+Decimal(part)
 return int(total*1000)

def normalize_rows(rows):
 aliases={'beat':'beat_id','scene':'beat_id','local_file':'source_file','media_filename':'source_file','media':'media_file','file':'media_file','source':'media_file','source_file_name':'media_file','media_file_name':'media_file','in':'source_in','out':'source_out','source_in_time':'source_in','source_out_time':'source_out','timeline_out_ms_excl':'timeline_out_ms','source_in_ms_planned':'source_in_ms','source_out_ms_excl_planned':'source_out_ms','narration':'narration_text','required_visual':'visible_content','match_rationale':'match_reason'}
 converted=[]
 for i,raw in enumerate(rows,1):
  if not isinstance(raw,dict):raise ValueError(f'Row {i}: expected a beat object.')
  if None in raw:raise ValueError(f'Row {i}: too many CSV cells. Quote any field that contains a comma (timecodes may be written 00:00:05.400 instead).')
  row={str(k).strip().lower().replace(' ','_'):v for k,v in raw.items()}
  for old,new in aliases.items():
   if old in row:
    if new in row and row[new] not in [None,'',row[old]]:raise ValueError(f'Row {i}: conflicting {old} / {new} columns.')
    row.setdefault(new,row[old])
  if all(k in row for k in ['block','start','end','beat','source_in','source_out']) and ('media_file' in row or 'source_file' in row):
   row.update(map_type='srt_block',parent_beat_id=str(integer(row['beat'],f'Row {i}: parent beat')),beat_id='SRT-'+str(row['block']).zfill(3),srt_blocks=[integer(row['block'],f'Row {i}: block')],timeline_in=row['start'],timeline_out=row['end'],source_file=row.get('media_file') or row.get('source_file'),source_out_excl=row['source_out'],visual_status=row.get('status',''))
   row.pop('beat',None) # parent beat is deliberately not a unique picture-row identifier.
   if i==1:row.setdefault('import_notes',[]).append('SRT block map: one scene per block; parent_beat_id links production instructions. Source times come from this map, not inferred filename order.')
  for key in ['import_notes','import_blockers']:
   if isinstance(row.get(key),str):
    value=row[key].strip()
    try:row[key]=json.loads(value) if value else []
    except ValueError:row[key]=[value]
   if key in row and (not isinstance(row[key],list) or any(not isinstance(v,str) for v in row[key])):raise ValueError(f'Row {i}: {key} must be a list of notes.')
  for old,new in [('timeline_in','timeline_in_ms'),('timeline_out','timeline_out_ms'),('source_in','source_in_ms'),('source_out_excl','source_out_ms'),('source_out','source_out_ms')]:
   if old in row and row[old] not in [None,'']:
    number=timecode_ms(row[old],f'Row {i}: {old}')
    if new in row and row[new] not in [None,''] and integer(row[new],new)!=number:raise ValueError(f'Row {i}: conflicting {old} / {new} values.')
    row[new]=number
  if 'transition_out' not in row and row.get('transition'):
   description=str(row['transition']).strip();row['transition_requested_text']=description
   if re.match(r'^(?:clean\s+)?cut\b',description,re.I) or 'cut to black' in description.lower():row['transition_out']='cut'
   elif re.match(r'^(?:soft|short)?\s*(?:\d+[- ]frame\s+)?dissolve\b',description,re.I):
    count=re.search(r'(\d+)[- ]frame',description,re.I)
    row['transition_out']='dissolve'
    if count:
     frames=int(count[1]);handle_frames=round(frames*30/23.976) if 'frames_23976' in row else frames
     if not 1<=handle_frames<=30:raise ValueError(f'Row {i}: dissolve must be at most one second in this renderer.')
     row['transition_handle_frames']=handle_frames;row['transition_handle_ms']=round(handle_frames/30*1000)
     if 'frames_23976' in row:row.setdefault('import_notes',[]).append(f'Beat {row.get("beat_id",i)}: {frames} frames at 23.976 fps mapped to {handle_frames} frames at 30 fps to preserve approximate dissolve duration.')
   else:raise ValueError(f'Row {i}: transition instruction is not recognized; use cut/dissolve or provide an explicit supported instruction.')
  if 'frames_23976' in row:
   row['declared_frame_rate']=23.976
   if i==1:row.setdefault('import_notes',[]).append('Input includes 23.976-fps frame counts. This editor renders at 30 fps: timecodes drive placement, frame counts are retained as reference and not treated as 30-fps counts.')
  if row.get('import_notes'):row['import_notes']=list(dict.fromkeys(row['import_notes']))
  converted.append(row)
 return converted

TIMECODE=re.compile(r'(?<![\w"”])(\d{1,2}:\d{2}(?::\d{2})?[,.](\d{1,3}))(?![\w"”])')

def quote_timecodes(text):
 """Wrap unquoted CSV/SRT-style clocks so a decimal comma does not split a cell.

 `00:00:05,400` is a normal way to write a clock, but in a CSV it produces two
 cells and the whole map is rejected. Timecodes are quoted before the file is
 read; nothing is rewritten, so `00:00:05,400` and `00:00:05.400` both parse to
 the same millisecond.
 """
 def wrap(match):
  token=match[0]
  if ';' in token or '\t' in token:return token
  return '"'+token+'"'
 return TIMECODE.sub(wrap,text)

def parse(raw,ext):
 if len(raw)>4_000_000:raise ValueError('Picture plans must be under 4 MB.')
 text=raw.decode('utf-8-sig')
 if ext=='.csv':
  text=quote_timecodes(text)
  try:
   sample=text[:16384]
   try:dialect=csv.Sniffer().sniff(sample,delimiters=',;\t')
   except csv.Error:dialect=csv.excel
   rows=list(csv.DictReader(io.StringIO(text),dialect=dialect))
  except csv.Error as e:raise ValueError('CSV could not be read: '+str(e))
 elif ext=='.json':
  data=json.loads(text);rows=data.get('beats') if isinstance(data,dict) else data
 else:raise ValueError('Choose a .csv or .json picture plan: a JSON list, or an object with a beats array.')
 if not isinstance(rows,list) or not rows:raise ValueError('Provide at least one beat row.')
 if len(rows)>20000:raise ValueError('Provide at most 20,000 beat rows in one plan.')
 rows=normalize_rows(rows)
 result=[];seen=set();last=0
 for i,rawrow in enumerate(rows,1):
  if not isinstance(rawrow,dict):raise ValueError(f'Row {i}: expected a beat object.')
  row=copy.deepcopy(rawrow);bid=row_id(row,i)
  if bid in seen:raise ValueError('Duplicate beat_id: '+bid)
  seen.add(bid);row['beat_id']=bid
  for key in ['timeline_in_ms','timeline_out_ms']:row[key]=integer(row.get(key),f'Row {i}: {key}')
  if row['timeline_in_ms']!=last or row['timeline_out_ms']<=last:raise ValueError(f'Row {i}: timeline must start at zero and remain ordered, contiguous and non-overlapping.')
  last=row['timeline_out_ms'];blocks=row.get('srt_blocks')
  if isinstance(blocks,str):
   try:blocks=json.loads(blocks)
   except ValueError:
    if re.fullmatch(r'\s*\d+\s*-\s*\d+\s*',blocks):
     a,b=map(int,re.findall(r'\d+',blocks));blocks=list(range(a,b+1))
    else:blocks=[x.strip() for x in re.split('[;,/]',blocks) if x.strip()]
  if isinstance(blocks,int):blocks=[blocks]
  if not isinstance(blocks,list) or not blocks:raise ValueError(f'Row {i}: provide srt_blocks as an explicit list, e.g. [2,3].')
  row['srt_blocks']=[integer(x,f'Row {i}: SRT block') for x in blocks]
  if row['srt_blocks']!=sorted(row['srt_blocks']):raise ValueError(f'Row {i}: SRT references must be in narration order.')
  if len(set(row['srt_blocks']))!=len(row['srt_blocks']):raise ValueError(f'Row {i}: duplicate SRT blocks.')
  row['source_file']=str(row.get('source_file') or '').strip()
  if row['source_file'] and (Path(row['source_file']).name!=row['source_file'] or '\\' in row['source_file']):raise ValueError(f'Row {i}: use an original filename, not a path.')
  row['transition_out']=str(row.get('transition_out') or 'cut').strip().lower()
  if row['transition_out'] not in ['cut','dissolve']:raise ValueError(f'Row {i}: transition_out must be cut or dissolve.')
  handle=integer(row.get('transition_handle_ms') or 0,f'Row {i}: transition_handle_ms')
  if handle>1000:raise ValueError(f'Row {i}: dissolve handles must be at most 1000 ms.')
  if row['transition_out']=='cut' and handle:raise ValueError(f'Row {i}: a clean cut has no transition handle.')
  frames=integer(row.get('transition_handle_frames') or (round(handle*.03) if handle else 4),f'Row {i}: transition frames') if row['transition_out']=='dissolve' else 0
  if row['transition_out']=='dissolve' and not 1<=frames<=30:raise ValueError(f'Row {i}: invalid dissolve frame count.')
  row['transition_handle_frames']=frames;row['transition_handle_ms']=round(frames/30*1000)
  for key in ['source_in_ms','source_out_ms']:
   row[key]=None if row.get(key) in [None,''] else integer(row[key],f'Row {i}: {key}')
  if row['source_file'] and (row['source_in_ms'] is None or row['source_out_ms'] is None):raise ValueError(f'Row {i}: a selected source file needs both source_in_ms and source_out_ms.')
  if row['source_in_ms'] is not None and row['source_out_ms'] is not None and row['source_out_ms']<=row['source_in_ms']:raise ValueError(f'Row {i}: source OUT must be greater than IN (OUT is exclusive).')
  if not row['source_file'] and not str(row.get('gap_note') or '').strip():raise ValueError(f'Row {i}: record gap_note when no source file is selected.')
  result.append(row)
 if result[-1]['transition_out']=='dissolve':raise ValueError('The final beat must end with a cut, not a dissolve into a nonexistent shot.')
 for a,b in zip(result,result[1:]):
  if a['transition_out']==b['transition_out']=='dissolve':raise ValueError('Adjacent dissolve pairs are unsupported; use a clean cut between them.')
 return result

def assemble(p,seed):
 bound=plan(p).get('based_on_srt')
 if bound and bound!=srt_signature(p):raise ValueError('The SRT changed since this block map was uploaded. Review and re-upload the matching map, or remove it to use the production script.')
 rows=plan(p)['beats'];blocks=p['srt']['blocks'];expected=round(blocks[-1]['end_ms']*.03)
 # A rebuild of one block range deliberately assembles a subset; a whole-map upload must
 # still cover the complete timeline, so nothing is silently dropped from a normal build.
 partial=bool(plan(p).get('partial'))
 if not partial and round(rows[-1]['timeline_out_ms']*.03)!=expected:raise ValueError('The SRT block map must cover the complete SRT timeline. No partial list or silent fallback is used.')
 scenes=[];issues=[];used={};by_number={b['number']:b for b in blocks};by_srt={s['srt_blocks'][0]:s for s in seed}
 for row in rows:
  bid=row_id(row);start=round(row['timeline_in_ms']*.03);end=round(row['timeline_out_ms']*.03)
  if end<=start:raise ValueError('Beat '+bid+': timing is shorter than one output frame.')
  refs=row['srt_blocks']
  if any(n not in by_number for n in refs):raise ValueError('Beat '+bid+': SRT reference does not exist.')
  # A single-block-range rebuild re-bases its subset to start at zero, so the comparison
  # is made against SRT timing shifted by the same amount. A whole-map build is unaffected.
  shift=0 if not partial else by_number.get(refs[0],{}).get('start_ms',0)-row['timeline_in_ms']
  actual=[b['number'] for b in blocks if b['start_ms']<row['timeline_out_ms']+shift and b['end_ms']>row['timeline_in_ms']+shift]
  if set(actual)!=set(refs):
   # A range rebuild replays the surfaces the editor reviewed, and those are block
   # boundaries nudged by up to 0.35s into a narration breath. Such a cut can legitimately
   # reach into the neighbouring block, so the tolerance there is one immediate neighbour
   # - never a distant block, and never a declared block that went missing.
   near=set()
   for n in refs:near.update([n-1,n,n+1])
   if not partial or not set(refs)<=set(actual) or not set(actual)<=near:
    raise ValueError('Beat '+bid+': SRT references do not match its timeline interval.')
  s=copy.deepcopy(by_srt[refs[0]]);s.update(id=uuid.uuid4().hex[:16],srt_blocks=[min(refs),max(refs)],frame_in=start,frame_out=end,narration=' '.join(by_number[n]['text'] for n in refs),asset_id=None,in_s=0,transition=row['transition_out'],transition_handle_frames=row.get('transition_handle_frames',4 if row['transition_out']=='dissolve' else 0),approved=False,plan_row_id=bid,plan_row=copy.deepcopy(row))
  # A name in the narration is not a claim that the source shows that face.
  s['mentioned_person']=s.get('person','');s['person']=str(row.get('shown_person') or '');s['focus']=str(row.get('visible_content') or s.get('category') or 'Case footage')
  s['notes']='Optional picture-plan instruction. '+str(row.get('match_reason') or '')+' '+str(row.get('gap_note') or '')+' Review: '+str(row.get('coverage_review') or '')+' Treatment: '+str(row.get('picture_treatment') or '')+' Transition requested: '+str(row.get('transition_requested_text') or '')+' Limit on implication: '+str(row.get('limit_on_implication') or '')
  if str(row.get('visual_status','')).upper()=='CTX':s['context_only']=True
  if row.get('visible_content'):s['focus']=str(row['visible_content'])[:5000]
  treatment=str(row.get('picture_treatment','')).lower();amount=re.search(r'(\d+(?:\.\d+)?)%',treatment)
  if amount and float(amount[1])<=10:
   direction='static' if 'hold static' in treatment else 'push' if 'push' in treatment else 'pull' if 'pull' in treatment else 'pan-right' if 'drift to the right' in treatment else ''
   if direction:s['script_camera']={'move':direction,'amount':float(amount[1])/100,'basis':'explicit percentage/move in production treatment; image center, not face tracking','phase_start':row.get('camera_phase_start',0),'phase_end':row.get('camera_phase_end',1)}
  matches=matching_assets(p,row['source_file'])
  problem=''
  if row.get('import_blockers'):problem=' '.join(row['import_blockers'])
  elif not row['source_file']:problem=str(row['gap_note'])
  elif len(matches)!=1:problem=match_problem(p,row['source_file'])
  elif str(row.get('rights_status','')).lower()=='incompatible':problem='This mapped source has incompatible reuse rights.'
  else:
   a=matches[0];source_start=row['source_in_ms']/1000;source_end=row['source_out_ms']/1000;duration=(end-start)/30;handle=s.get('transition_handle_frames',4)/30 if s['transition']=='dissolve' else 0
   if row.get('map_type')=='srt_block' and scenes:
    previous=scenes[-1];prior=previous.get('plan_row',{})
    if previous.get('asset_id')==a['id'] and prior.get('parent_beat_id')==row.get('parent_beat_id') and prior.get('source_out_ms')==row['source_in_ms'] and previous['transition']=='cut':
     snapped=previous['in_s']+(previous['frame_out']-previous['frame_in'])/30
     if abs(snapped-source_start)<=.001:
      s['notes']+=' Contiguous split-source boundary adjusted by '+str(round((snapped-source_start)*1000,6))+' ms for 30-fps rounding.';source_start=snapped
   if a['kind']=='video':
    if str(row.get('visual_status','')).upper()=='STILL':problem='A still frame was requested from this video but no extracted frame is registered. Set picture policy to photographs and stills so the frame can be extracted, or choose a moving interval.'
    elif abs((source_end-source_start)-duration)>1/30+.001:problem='Source interval and picture duration differ; no automatic trimming, stretching, looping or freezing.'
    elif max(source_end,source_start+duration)+handle>a['duration']+.001:problem='Source file is too short, including transition handles.'
    elif not p.get('allow_archive_reuse') and any(source_start<out and source_start+duration+handle>inside for inside,out in used.get(a['id'],[])):problem='Selected source interval overlaps an earlier beat.'
   if not problem:
    s.update(asset_id=a['id'],in_s=source_start)
    # What the cooldown actually has to protect: not the byte offsets, but the SHOT. Two
    # beats can play the very same stretch of film from different in-points - shifted by a
    # second, or trimmed at either end - and an overlap test on intervals will not notice,
    # because the intervals do not have to overlap. A group is therefore either declared by
    # the plan (footage_group) or derived from what the plan already states about the shot:
    # the file, its change points, and the script's own wording for the picture. Beats that
    # share all three are the same shot and now fall under the same cooldown slot rule.
    declared=str(row.get('footage_group') or '').strip()
    described=str(row.get('visible_content') or '').strip().casefold()
    if declared:s['footage_group']=declared
    elif described:
     # Only a described picture can be grouped automatically. The key is the file plus the
     # plan's own wording for what should be on screen - deliberately NOT the row or parent
     # beat, which is unique per row by construction and would make every group unique and
     # the rule unreachable. A plan that says nothing about the picture gets no derived
     # group, because there is no evidence two rows are the same shot and inventing one
     # would refuse a legitimate build.
     s['footage_group']='shot:'+hashlib.sha256((a.get('original_name','')+'|'+described).encode()).hexdigest()[:16]
    if a['kind']=='video':used.setdefault(a['id'],[]).append((source_start,source_start+duration+handle))
  if problem:issues.append('Beat '+bid+': '+problem);s['notes']+=' Unresolved: '+problem
  scenes.append(s)
 p['scenes']=scenes;p['script_application']={'recognized_instructions':len(rows),'placed_scenes':sum(bool(s['asset_id']) for s in scenes),'issues':issues,'mode':'picture_plan','note':'Parsed picture plan controls picture timing/source placement; production script supplies context. Status fields do not certify media or grant approval. All beats remain unapproved.'}
 return p
