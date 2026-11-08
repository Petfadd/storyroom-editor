"""Read explicit Markdown source/beat tables; never equate a plan with visual proof."""
import json,re,hashlib,copy
from production import document,asset_matches,reserve_id,matching_assets

def clean(s):return re.sub(r'[*`]', '',str(s)).strip()
def tables(text):
 lines=text.splitlines();i=0
 while i<len(lines):
  if not lines[i].lstrip().startswith('|'):i+=1;continue
  rows=[]
  while i<len(lines) and lines[i].lstrip().startswith('|'):
   line=lines[i].strip()
   if not re.fullmatch(r'[|\s:–-]+',line):rows.append([clean(c) for c in re.split(r'(?<!\\)\|',line.strip('|'))])
   i+=1
  if len(rows)>1:yield [dict(zip(rows[0],row)) for row in rows[1:] if len(row)==len(rows[0])]

def _clock_ms(text):
 """HH:MM:SS.mmm, HH:MM:SS,mmm or MM:SS.mmm -> milliseconds, else None."""
 m=re.fullmatch(r'(\d{1,2}):(\d{2})(?::(\d{2}))?(?:[.,](\d{1,3}))?',str(text).strip())
 if not m:return None
 a,b,c,frac=m.groups()
 hours,minutes,seconds=(0,int(a),int(b)) if c is None else (int(a),int(b),int(c))
 if minutes>59 or seconds>59:return None
 return (hours*3600+minutes*60+seconds)*1000+int(((frac or '0')+'000')[:3])

def _clock_span(cell):
 """'IN → OUT' -> (in_ms, out_ms) when both sides read as clocks."""
 parts=[p.strip() for p in re.split(r'\s*(?:→|->|-->|–|—)\s*',str(cell)) if p.strip()]
 vals=[_clock_ms(p) for p in parts]
 vals=[v for v in vals if v is not None]
 return (vals[0],vals[1]) if len(vals)>=2 and vals[0]<vals[1] else None

def raw_tables(text):
 lines=text.splitlines();i=0
 while i<len(lines):
  if not lines[i].lstrip().startswith('|'):i+=1;continue
  rows=[];header=None
  while i<len(lines) and lines[i].lstrip().startswith('|'):
   line=lines[i].strip()
   if not re.fullmatch(r'[|\s:–-]+',line):
    cells=[clean(c) for c in re.split(r'(?<!\\)\|',line.strip('|'))]
    header=cells if header is None else header
    if len(cells)==len(header):rows.append(cells)
   i+=1
  if rows:
   body=rows[1:] if len(rows)>1 and rows[0]==header and not re.fullmatch(r'\d{1,4}',rows[0][0]) else rows
   if body:yield header,body

def srt_outline(text):
 """Recover a narration SRT that exists only as a Markdown block table.

 Columns are identified by content, never by header wording: a block-number column,
 a document clock (IN -> OUT) column, and the longest remaining column as the wording.
 Returns [(number,in_ms,out_ms,wording)] or [] when no such table is present. The
 recovery is structural: no wording, order or timing is altered or invented.
 """
 for header,rows in raw_tables(text):
  width=len(header);columns=[[row[i] for row in rows] for i in range(width)]
  number_i=next((i for i,col in enumerate(columns) if col and all(re.fullmatch(r'\d{1,4}',c) for c in col)),None)
  if number_i is None or len(rows)<2:continue
  clock_i=next((i for i,col in enumerate(columns) if i!=number_i and all(_clock_span(c) for c in col)),None)
  if clock_i is None:continue
  others=[(sum(len(c) for c in columns[i]),i) for i in range(width) if i not in (number_i,clock_i)]
  if not others:continue
  wording_i=max(others)[1];out=[]
  for row in rows:
   span=_clock_span(row[clock_i]);wording=' '.join(row[wording_i].split())
   if not re.fullmatch(r'\d{1,4}',row[number_i]) or not span or not wording:out=[];break
   out.append((int(row[number_i]),span[0],span[1],wording))
  if len(out)>=2 and all(a[0]<b[0] for a,b in zip(out,out[1:])):return out
 return []

def srt_outline_blocks(text):
 """Validate a recovered outline as SRT blocks; returns (blocks,problems)."""
 rows=srt_outline(text)
 if not rows:return [],[]
 problems=[];last=0
 if [r[0] for r in rows]!=list(range(1,len(rows)+1)):problems.append('Block numbers in the table are not 1 through '+str(len(rows))+' in order.')
 for number,start,end,_ in rows:
  if start!=last:problems.append('Block '+str(number)+' starts at '+str(start)+' ms while the previous block ends at '+str(last)+' ms; recovered timing must be contiguous.')
  if end<=start:problems.append('Block '+str(number)+' has a non-positive interval.')
  last=end
 if problems:return [],problems
 return [{'number':n,'start_ms':a,'end_ms':b,'text':t} for n,a,b,t in rows],[]

def script_beats(p):
 from plain_script import is_plain_script,parse_plain_script
 if is_plain_script(document(p)):return parse_plain_script(document(p),(p.get('srt') or {}).get('blocks',[]))
 from cue_script import is_cue_script,parse_cue_script
 if is_cue_script(document(p)):return parse_cue_script(document(p),(p.get('srt') or {}).get('blocks',[]))
 from detailed_script import parse_detailed
 detailed=parse_detailed(document(p),(p.get('srt') or {}).get('blocks',[]))
 if detailed:return detailed
 from edl import parse
 for table in tables(document(p)):
  if 'Timeline in → out' not in table[0] or 'Src IN → OUT' not in table[0]:continue
  rows=[]
  for row in table:
   files=re.findall(r'\b(?:s|v|r)\d+\.(?:mp4|mov)\b',row['Source → file'],re.I)
   times=[s.strip() for s in row['Timeline in → out'].split('→')];source=[s.strip() for s in row['Src IN → OUT'].split('→')]
   if len(files)!=1 or len(times)!=2 or len(source)!=2:raise ValueError('A Markdown beat has an ambiguous file or IN/OUT range.')
   treatment=row.get('Treatment · transition','');parts=treatment.rsplit(' · ',1);transition=parts[-1]
   status=row.get('Status','');visual='STILL' if 'still' in status.lower() else 'CTX' if 'context' in status.lower() else 'PERSON'
   rows.append({'beat':row['#'],'srt_blocks':row['SRT'],'timeline_in':times[0],'timeline_out':times[1],'local_file':files[0],'source_id':row['Source → file'].split('→')[0].strip(),'source_in':source[0],'source_out_excl':source[1],'visual_status':visual,'required_visual':row.get('Visual required',''),'match_rationale':row.get('Match rationale · limit on implication',''),'picture_treatment':parts[0],'transition':transition,'coverage_review':row.get('Review',''),'frames_23976':row.get('Dur / frames',''),'verification_status':'provisional_document_plan'})
  return parse(json.dumps(rows).encode(),'.json')
 return legacy_beat_table(p)

def legacy_beat_table(p):
 """Read the original 102-beat v2 Markdown script without treating it as visual proof.

The original table's nominal SRT labels are reconciled against its explicit
millisecond clocks. Source OUT is set to the exact timeline duration so that a
native 30 fps interval is neither stretched nor frozen.
 """
 from edl import parse,timecode_ms
 blocks=(p.get('srt') or {}).get('blocks',[])
 if not blocks:return []
 required={'Beat','SRT blocks','Timeline IN → OUT','Media','Source IN/OUT (planned)'}
 for table in tables(document(p)):
  if not required<=set(table[0]):continue
  rows=[];reconciled=0
  for i,row in enumerate(table,1):
   timeline=[x.strip() for x in str(row.get('Timeline IN → OUT','')).split('→')]
   source=[x.strip() for x in str(row.get('Source IN/OUT (planned)','')).split('→')]
   file=re.search(r'\b(?:s|v|r)\d+\.(?:mp4|mov)\b',str(row.get('Media','')),re.I)
   if len(timeline)!=2 or len(source)!=2 or not file:raise ValueError('Beat '+str(i)+' has an ambiguous media file or IN/OUT range.')
   start=timecode_ms(timeline[0],'Beat '+str(i)+' timeline IN')
   end=timecode_ms(timeline[1],'Beat '+str(i)+' timeline OUT')
   source_start=timecode_ms(source[0],'Beat '+str(i)+' source IN')
   duration=end-start
   if duration<=0:raise ValueError('Beat '+str(i)+' has a non-positive timeline duration.')
   refs=[b['number'] for b in blocks if b['start_ms']<end and b['end_ms']>start]
   if not refs:raise ValueError('Beat '+str(i)+' does not overlap any original SRT block.')
   nominal=[int(x) for x in re.findall(r'\d+',str(row.get('SRT blocks','')))]
   if nominal and nominal!=refs:reconciled+=1
   klass=str(row.get('Class',''))
   visual='STILL' if 'photograph' in klass.lower() or 'document' in klass.lower() else 'PERSON' if 'verified person' in klass.lower() else 'CTX'
   rows.append({
    'beat_id':str(row.get('Beat') or i).zfill(3),
    'srt_blocks':refs,
    'timeline_in_ms':start,'timeline_out_ms':end,
    'source_file':file[0],'source_in_ms':source_start,'source_out_ms':source_start+duration,
    'visible_content':row.get('Visible content (planned)',''),
    'visual_status':visual,'match_reason':row.get('Match rationale',''),
    'coverage_review':row.get('Review status',''),
    'picture_treatment':row.get('Transition',''),
    'limit_on_implication':row.get('Limitation',''),
    'transition_out':'cut','transition_requested_text':row.get('Treatment',''),
    'verification_status':'provisional_document_plan',
    'import_notes':[] if i>1 else ['Original Markdown beat table supplies document-declared source times, not visual verification. Explicit millisecond clocks were reconciled with the uploaded SRT; requested flags do not certify people or exact exhibits.']
   })
  if reconciled and rows:rows[0]['import_notes'].append(str(reconciled)+' nominal SRT labels were reconciled against explicit timeline milliseconds; original SRT wording and timing remain unchanged.')
  return parse(json.dumps(rows).encode(),'.json')
 return []

def source_register(p):
 result=[]
 for table in tables(document(p)):
  if 'Local file' in table[0] and any('YouTube video' in k for k in table[0]):
   for row in table:
    text=next(row[k] for k in row if 'YouTube video' in k);url=re.search(r'https?://(?:www\.)?(?:youtube\.com/watch\?v=[\w-]+|youtu\.be/[\w-]+)',text)
    result.append({'source_id':row.get('Source ID',''),'source_file':row['Local file'],'youtube_url':url[0] if url else '', 'publisher':row.get('Publisher',''),'planned_content':row.get('Role in this cut',''),'rights_state':row.get('Rights state','pending'),'basis':'production_script_declaration_not_visual_verification'})
 for table in tables(document(p)):
  if 'Reserve' in table[0] and 'Video' in table[0]:
   for i,row in enumerate(table,1):
    url=re.search(r'https?://(?:www\.)?(?:youtube\.com/watch\?v=[\w-]+|youtu\.be/[\w-]+)',row['Video'])
    label=row['Reserve'].lower();label=label if re.fullmatch(r'r\d+',label) else 'r'+str(i)
    result.append({'source_id':label,'original_script_label':row['Reserve'],'source_file':label+'.mp4','youtube_url':url[0] if url else '', 'publisher':row.get('Publisher',''),'planned_content':row.get('Why not counted',''),'rights_state':'pending','reserve':True,'basis':'production_script_reserve_register'})
 return result

def base_rows_for(p):
 return_rows=script_beats(p)
 return return_rows,('detailed_production_script' if return_rows and return_rows[0].get('strict_production') else 'production_script_table') if return_rows else ''

def require_one_shot_per_cue(rows, blocks):
 """Do not silently merge or split authored instructions to fit cue boundaries."""
 if len(rows)!=len(blocks):
  raise ValueError('The production script must contain exactly one shot per SRT cue. Review the script; existing instructions were not changed.')
 for row, cue in zip(rows, blocks):
  if row.get('srt_blocks') != [int(cue['number'])] or (row.get('timeline_in_ms'),row.get('timeline_out_ms')) != (cue['start_ms'],cue['end_ms']):
   raise ValueError('Each shot must match exactly one SRT cue and its complete timeline IN/OUT. Review the script; existing instructions were not changed.')

def rows_for(p):
 if p.get('srt_block_map'):
  from detailed_script import parse_detailed
  from cue_script import is_cue_script
  from plain_script import is_plain_script
  if is_plain_script(document(p)) or is_cue_script(document(p)) or parse_detailed(document(p),(p.get('srt') or {}).get('blocks',[])):raise ValueError('A detailed production script and SRT block map are both present. Remove the block map before building; the script must control its own shots.')
 if p.get('srt_block_map'):
  rows,mode=[],'srt_block_map'
 else:
  rows,mode=base_rows_for(p)
 mapping=p.get('srt_block_map')
 if not mapping:
  if rows:require_one_shot_per_cue(rows,(p.get('srt') or {}).get('blocks',[]))
  return rows,mode
 from edl import srt_signature
 if mapping.get('based_on_srt') and mapping['based_on_srt']!=srt_signature(p):raise ValueError('The SRT changed since this block map was uploaded. Review and re-upload the matching map.')
 parents={str(int(r.get('beat_id') or r.get('plan_row_id'))):r for r in rows if str(r.get('beat_id') or r.get('plan_row_id','')).isdigit()};out=[]
 mapped=mapping['beats']
 for i,raw in enumerate(mapped):
  row=copy.deepcopy(raw);parent=parents.get(str(int(row['parent_beat_id'])));last=i+1==len(mapped) or mapped[i+1]['parent_beat_id']!=row['parent_beat_id']
  if parent:
   if not parent['timeline_in_ms']<=row['timeline_in_ms']<row['timeline_out_ms']<=parent['timeline_out_ms']:raise ValueError('Block '+str(row['block'])+' falls outside its parent production beat. Review the map/production version.')
   for key in ['visible_content','match_reason','coverage_review','picture_treatment','limit_on_implication','youtube_url','source_id']:
    if parent.get(key) and not row.get(key):row[key]=parent[key]
   duration=parent['timeline_out_ms']-parent['timeline_in_ms'];row['camera_phase_start']=(row['timeline_in_ms']-parent['timeline_in_ms'])/duration;row['camera_phase_end']=(row['timeline_out_ms']-parent['timeline_in_ms'])/duration
   row['transition_out']=parent['transition_out'] if last else 'cut';row['transition_handle_frames']=parent.get('transition_handle_frames',4) if last and row['transition_out']=='dissolve' else 0
   row['transition_handle_ms']=round(row['transition_handle_frames']/30*1000)
   if last and parent.get('transition_requested_text'):row['transition_requested_text']=parent['transition_requested_text']
   # Do not carry publisher provenance to a different mapped file silently.
   if row['source_file'].casefold()!=parent['source_file'].casefold():
    for key in ['youtube_url','source_id']:row.pop(key,None)
    row.setdefault('import_notes',[]).append('Mapped file differs from parent beat; parent source URL/ID were not copied.')
  out.append(row)
 require_one_shot_per_cue(out,(p.get('srt') or {}).get('blocks',[]))
 return out,'srt_block_map'

def synchronize(p):
 try:rows,mode=rows_for(p);register=source_register(p);error=''
 except ValueError as e:rows=[];register=[];mode='';error=str(e)
 files={a['id']:a for a in p['assets']};matched=0
 for a in p['assets']:
  planned=[r for r in rows if any(m['id']==a['id'] for m in matching_assets(dict(p,source_bindings={}) if r.get('strict_production') else p,str(r.get('source_file') or '')))]
  records=[r for r in register if any(m['id']==a['id'] for m in matching_assets(p,r['source_file']))]
  links=list(dict.fromkeys([str(r.get('youtube_url') or '') for r in planned+records if r.get('youtube_url')]))
  labels=list(dict.fromkeys(str(r.get('source_id') or '') for r in planned+records if r.get('source_id')))
  a['document_plan']={'cuts':[{'beat_id':r['beat_id'],'timeline_in_ms':r['timeline_in_ms'],'timeline_out_ms':r['timeline_out_ms'],'source_in_ms':r['source_in_ms'],'source_out_ms':r['source_out_ms'],'visible_content':r.get('visible_content',''),'visual_status':r.get('visual_status',''),'review':r.get('coverage_review',''),'picture_treatment':r.get('picture_treatment','')} for r in planned], 'source_ids':labels,'youtube_urls':links,'register':records,'visual_verification':'not_established_by_documents'}
  if (not a.get('source','').strip() or (a.get('approval_record') and a.get('source','').startswith('User-provided file: '))) and len(links)==1:a['source']=links[0]
  if planned:matched+=1
 missing=[r['source_file'] for r in rows if r.get('source_file') and not matching_assets(dict(p,source_bindings={}) if r.get('strict_production') else p,r['source_file'])]
 p['document_plan_summary']={'mode':mode,'beats':len(rows),'registered_sources':len(register),'matched_media':matched,'missing_files':list(dict.fromkeys(missing)),'error':error,'provisional':bool(re.search(r'could not watch|could not view|every source.*provisional|nominal allocation',document(p),re.I)),'note':'Document-declared content and provisional times are not visual verification. Inspect planned-cut samples before approving.'}
 return p

def match_report(p):
 try:rows,mode=rows_for(p)
 except ValueError:return []
 names=list(dict.fromkeys(r['source_file'] for r in rows if r.get('source_file')));result=[]
 for name in names:
  exact=[a for a in p['assets'] if asset_matches(a,name)];effective=matching_assets(dict(p,source_bindings={}) if mode=='detailed_production_script' else p,name);binding=None if mode=='detailed_production_script' else (p.get('source_bindings') or {}).get(name.strip().casefold())
  result.append({'source_file':name,'original_name_match_count':len(exact),'effective_match_count':len(effective),'selected_asset_id':binding or (effective[0]['id'] if len(effective)==1 else None),'explicit':bool(binding),'status':'selected' if binding and len(effective)==1 else 'matched' if len(effective)==1 else 'missing' if not effective else 'ambiguous','candidate_ids':[a['id'] for a in exact],'beats':sum(r.get('source_file')==name for r in rows)})
 return result
