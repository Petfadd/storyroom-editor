"""Document-led planning for any programme. Deterministic parsing, never factual/visual verification."""
import json as _json
from pathlib import Path as _Path
_PROFILE_FILE=_Path(__file__).with_name('blueprints')/'style_profiles.json'
try:PROFILES=_json.loads(_PROFILE_FILE.read_text())
except (OSError,ValueError):PROFILES={}
import collections,json,re,zipfile,xml.etree.ElementTree as ET
from pathlib import Path

def reserve_id(asset):
 name=Path(str((asset.get('derived_from') or {}).get('source_file') or asset.get('original_name') or asset.get('name',''))).stem.lower()
 return name if re.fullmatch(r'r\d+',name) else ''
def asset_used(p,a):
 selected={s.get('asset_id') for s in p['scenes']}
 return a['id'] in selected or any(c['id'] in selected and (c.get('derived_from') or {}).get('asset_id')==a['id'] for c in p['assets'])

def asset_matches(asset,name):
 original=str(asset.get('original_name') or asset.get('name',''));name=str(name)
 # Uploaded JPEG/PNG photos may use different extensions for the same exact stem.
 # Count every matching upload (even an exact-extension match), so duplicates are
 # ambiguous rather than silently selecting one. Never apply this to derived frames.
 photo_extensions={'.jpg','.jpeg','.png'}
 if asset.get('kind')=='image' and not asset.get('derived_from') and _Path(original).suffix.lower() in photo_extensions and _Path(name).suffix.lower() in photo_extensions:
  return _Path(original).stem==_Path(name).stem
 return original.casefold()==name.casefold() or bool(not asset.get('derived_from') and reserve_id(asset) and reserve_id(asset)==name.casefold())

def matching_assets(p,name):
 key=str(name).strip().casefold();binding=(p.get('source_bindings') or {}).get(key)
 if binding:return [a for a in p['assets'] if a['id']==binding]
 return [a for a in p['assets'] if asset_matches(a,str(name))]

def match_problem(p,name):
 matches=matching_assets(p,name)
 if len(matches)==1:return ''
 if (p.get('source_bindings') or {}).get(str(name).strip().casefold()):return 'Selected upload is no longer present for '+str(name)+'. Select another copy in Import media.'
 if not matches:return 'No uploaded file matches '+str(name)+'. If it is uploaded under another filename, link that upload in Import media.'
 return str(len(matches))+' uploaded files match '+str(name)+'. Choose one using Use this upload for script matching in Import media.'

def read_document(file,ext):
 raw=file.read(4_000_001)
 if len(raw)>4_000_000:raise ValueError('Production scripts must be under 4 MB.')
 if ext=='.docx':
  import io
  with zipfile.ZipFile(io.BytesIO(raw)) as z:
   info=z.getinfo('word/document.xml')
   if info.file_size>8_000_000:raise ValueError('The Word document is too large.')
   root=ET.fromstring(z.read(info))
   text='\n'.join(''.join(p.itertext()) for p in root.iter('{http://schemas.openxmlformats.org/wordprocessingml/2006/main}p'))
 else:text=raw.decode('utf-8-sig')
 if not text.strip():raise ValueError('The production script is empty.')
 # Normalise line endings once, here. A script written on Windows (CRLF) used to be
 # unreadable: the beat reader's field patterns anchor on '^...$' and exclude \r,
 # so every field failed and the only message was "provide exactly one Type field",
 # which names neither Windows nor line endings.
 return text.replace('\r\n','\n').replace('\r','\n')

def document(p):return (p.get('production_script') or {}).get('text','')
def structured(p):
 try:
  d=json.loads(document(p));return d if isinstance(d,dict) else {}
 except ValueError:return {}

def infer(p):
 production=document(p);d=structured(p);narration='\n'.join(b['text'] for b in (p.get('srt') or {}).get('blocks',[]));text=production+'\n'+narration
 ignore=set('the a an this that when after before then police officer detective detectives district attorney judge court monday tuesday wednesday thursday friday saturday sunday january february march april may june july august september october november december video scene srt source original production script case homicide murder chapter part he she his her they their in on at but now and was found missing footage convicted sentenced guilty not'.split())
 names=[]
 for m in re.finditer(r"\b[A-Z][a-z]+(?:[-'][A-Za-z]+)?(?:[ \t]+[A-Z][a-z]+(?:[-'][A-Za-z]+)?){1,2}\b",text):
  name=' '.join(m[0].split())
  if any(w.casefold() in ignore for w in name.split()):continue
  names.append(name)
 counts=collections.Counter(names)
 explicit=d.get('case_name') or d.get('case')
 if isinstance(explicit,dict):explicit=explicit.get('name')
 label=re.search(r'(?im)^\s*(?:case(?: name)?|victim)\s*:\s*([^\n|]+)',production)
 anchor=str(explicit or (label[1].strip() if label else ''))[:160]
 if not anchor and counts:
  def score(name):
   contexts=[text[max(0,m.start()-70):m.end()+90].lower() for m in re.finditer(re.escape(name),text)]
   victim=sum(bool(re.search(r'victim|murdered|killed|missing|body of|death of|homicide of',c)) for c in contexts)
   return victim*3+min(counts[name],8)
  ranked=sorted(counts,key=lambda n:(-score(n),-counts[n],text.index(n)))
  anchor=ranked[0]
  if len(ranked)>1 and score(ranked[0])==score(ranked[1]) and counts[ranked[0]]==counts[ranked[1]]:
   # Same-case actors can be searched together; do not assert which one is the victim.
   ambiguity='Several actors have equal prominence; selected the first named actor as a search anchor, not a victim identification.'
  else:ambiguity='Name roles are document-derived proposals, not independently verified identities.'
 else:ambiguity='Case identifier comes from the uploaded production script.'
 people=d.get('people',[])
 if isinstance(people,str):people=[x.strip() for x in people.split(',') if x.strip()]
 if isinstance(people,list):people=[x.get('name','') if isinstance(x,dict) else str(x) for x in people]
 else:people=[]
 people=list(dict.fromkeys(people+list(counts)))[:40]
 if anchor and anchor not in people:people.insert(0,anchor)
  # Profile is inferred from whichever registered profile best matches the narration's
 # wording. Nothing here names a genre of programme: a script about a market, a product,
 # a process or a person resolves to the profile whose hints actually fire, and to the
 # neutral default when none do.
 profile='documentary';best=0
 blocks=(p.get('srt') or {}).get('blocks',[])
 for name,meta in (PROFILES or {}).items():
  hits=sum(len(re.findall(h.get('pattern',''),narration,re.I)) for h in meta.get('hints') or [])
  if hits>best and hits>max(2,len(blocks)*.25):profile,best=name,hits
 return {'anchor':anchor,'people':people,'aliases':d.get('case_aliases',[]),'locations':d.get('locations',[]),'confirmed':True,'profile':profile,'basis':ambiguity,'origin':'uploaded_production_script_and_srt','text_characters':len(text)}

def seconds(value):
 if isinstance(value,(int,float)):return float(value)
 parts=str(value).strip().replace(',','.').split(':');total=0
 for part in parts:total=total*60+float(part)
 return total

def instructions(p):
 d=structured(p);rows=d.get('scenes',[]);issues=[];result=[]
 if not isinstance(rows,list):issues.append('The script scenes field must be a list.');rows=[]
 if not rows:
  # Recognize explicit references only; never infer a scene from a source number.
  for line in document(p).splitlines():
   asset=re.search(r'\b((?:s|v)\d+\.(?:mp4|mov)|r\d+(?:\.(?:mp4|mov))?)\b',line,re.I);block=re.search(r'\b(?:SRT|Scene)\s*#?\s*(\d+)(?:\s*[-–]\s*(\d+))?',line,re.I)
   if not asset or not block:continue
   start=re.search(r'\b(?:in(?:_s)?|from|start)\s*[:=]?\s*(\d+(?::\d+){0,2}(?:[.,]\d+)?)',line,re.I)
   end=re.search(r'\b(?:out(?:_s)?|to|end)\s*[:=]?\s*(\d+(?::\d+){0,2}(?:[.,]\d+)?)',line,re.I)
   rows.append({'srt_blocks':[int(block[1]),int(block[2] or block[1])],'asset':asset[1],'in_s':seconds(start[1]) if start else None,'out_s':seconds(end[1]) if end else None,'transition':'dissolve' if re.search(r'\bdissolve\b',line,re.I) else 'cut','note':line})
 if document(p).strip() and not rows:issues.append('No explicit SRT/file/source-time edit instructions were recognized. No footage placement was guessed; check the uploaded script format.')
 for row in rows:
  if not isinstance(row,dict):issues.append('Ignored a non-object scene instruction.');continue
  blocks=row.get('srt_blocks',row.get('srt',row.get('scene')))
  if isinstance(blocks,int):blocks=[blocks,blocks]
  if isinstance(blocks,str):
   nums=re.findall(r'\d+',blocks);blocks=[int(nums[0]),int(nums[-1])] if nums else None
  if not isinstance(blocks,list) or len(blocks)!=2:issues.append('Scene instruction has no explicit SRT block range.');continue
  try:
   start=None if row.get('in_s') is None else seconds(row['in_s']);end=None if row.get('out_s') is None else seconds(row['out_s'])
   if start is not None and (not 0<=start<1e8):raise ValueError()
   if end is not None and (not 0<end<1e8):raise ValueError()
   result.append({**row,'srt_blocks':[int(x) for x in blocks],'asset':Path(str(row.get('asset') or row.get('file') or '')).name,'in_s':start,'out_s':end})
  except (ValueError,TypeError):issues.append('Ignored an instruction with invalid source timing or SRT numbers.')
 return result,issues

def apply(p):
 rows,issues=instructions(p);used=collections.defaultdict(list);scenes=p['scenes'];applied=0
 for row in rows:
  selected=[s for s in scenes if row['srt_blocks'][0]<=s['srt_blocks'][0] and s['srt_blocks'][1]<=row['srt_blocks'][1]]
  matches=matching_assets(p,row['asset'])
  if not selected:issues.append(f"{row['asset']}: no matching SRT scenes.");continue
  if len(matches)!=1:issues.append(match_problem(p,row['asset']));continue
  a=matches[0]
  if a['kind']=='video' and row['in_s'] is None:issues.append(f"{row['asset']}: script does not specify a source in-point; no interval was guessed.");continue
  cursor=row['in_s'] or 0;proposals=[];problem=''
  for s in selected:
   transition='dissolve' if row.get('transition')=='dissolve' and s is selected[-1] and s is not scenes[-1] else 'cut'
   dur=(s['frame_out']-s['frame_in']+(4 if transition=='dissolve' else 0))/30;stop=cursor+dur
   if a['kind']=='video':
    if stop>min(a['duration'],row['out_s'] if row['out_s'] is not None else a['duration'])+.001:problem='requested interval is too short; no loop/freeze/slowdown was added';break
    if any(cursor<end and stop>start for start,end in used[a['id']]):problem='source interval would repeat another scripted scene';break
   if s.get('script_instruction'):problem='two script instructions target the same scene';break
   proposals.append((s,cursor,stop,transition));cursor=stop
  if problem:issues.append(f"{row['asset']}: {problem}.");continue
  for s,start,stop,transition in proposals:
   s.update(asset_id=a['id'],in_s=start,transition=transition,approved=False,script_instruction=True,notes='Production-script edit instruction; actual file identity/context still require verification. '+str(row.get('note','')))
   for key in ['focus','emotion','person','purpose']:
    if row.get(key):s[key]=str(row[key])[:5000]
   move=row.get('motion')
   if move in ['auto','push','pull','pan-left','pan-right','rise','reveal']:s['motion']=move
   if a['kind']=='video':used[a['id']].append((start,stop))
   applied+=1
 # Never leave invalid dissolve pairs; prefer clean narration-led cuts.
 for i,s in enumerate(scenes):
  if s['transition']=='dissolve' and (i==len(scenes)-1 or scenes[i+1]['transition']=='dissolve'):s['transition']='cut'
 p['script_application']={'recognized_instructions':len(rows),'placed_scenes':applied,'issues':issues,'note':'Scripted placement is not visual/identity verification. Unrecognized prose is not silently treated as an edit decision.'}
 return p
