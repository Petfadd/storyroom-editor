from __future__ import annotations
import copy, hashlib, json, math, re, sys, threading, time, uuid, subprocess, os
from pathlib import Path
from collections import defaultdict
from flask import Flask, request, jsonify, send_from_directory, render_template
from werkzeug.exceptions import BadRequest, NotFound, RequestEntityTooLarge
from types import SimpleNamespace
from PIL import Image, ImageOps, ImageFilter, ImageEnhance
import cv2, imageio_ffmpeg
BASE=Path(__file__).resolve().parent
from engine import RenderJob
PROJECTS=BASE/'projects';PROJECTS.mkdir(exist_ok=True)
MAX_VIDEO_BYTES=8*1024**3
app=Flask(__name__);app.config['TEMPLATES_AUTO_RELOAD']=True;app.config['MAX_CONTENT_LENGTH']=MAX_VIDEO_BYTES
LOCKS=defaultdict(threading.RLock);THUMBNAIL_SLOTS=threading.BoundedSemaphore(2);JOBS={};FFMPEG=imageio_ffmpeg.get_ffmpeg_exe()
PROFILES=json.loads((BASE/'blueprints/style_profiles.json').read_text())
# Output geometry is a project setting, not a constant. `format` picks an aspect; `size`
# picks the pixel target. Anything the engine can encode is allowed, so the same build
# serves a 720p review, a 1080p master, a vertical cut and a square one.
ASPECTS={'landscape':(16,9),'portrait':(9,16),'square':(1,1),'vertical_4_5':(4,5),'cinema':(239,100)}
DEFAULT_SIZE={'landscape':'1080p','portrait':'1080p','square':'1080p','vertical_4_5':'1080p','cinema':'1080p'}
SIZES={'480p':480,'540p':540,'720p':720,'1080p':1080,'1440p':1440,'4k':2160}
def valid_size(size):
 """True for a named target or an explicit WxH in a sane range."""
 size=str(size or '').strip()
 if size in SIZES:return True
 match=re.fullmatch(r'\s*(\d{2,5})\s*[x×]\s*(\d{2,5})\s*',size)
 if not match:return False
 w,h=int(match[1]),int(match[2])
 return 64<=w<=7680 and 64<=h<=7680

def output_size(fmt,size):
 """Pixel width and height for an aspect and a target, rounded to even numbers.

 A target names the SHORT side in every case, so "1080p" always means the number people
 mean by it: 1920x1080 landscape, 1080x1920 portrait, 1080x1080 square, 1080x1350 for 4:5.
 An explicit "1920x1080" is also accepted and used as written.
 """
 fmt=fmt if fmt in ASPECTS else 'landscape'
 if size in SIZES:short=SIZES[size]
 else:
  match=re.fullmatch(r'\s*(\d{2,5})\s*[x×]\s*(\d{2,5})\s*',str(size or ''))
  if match:return (max(2,int(match[1])//2*2),max(2,int(match[2])//2*2))
  short=SIZES['1080p']
 aw,ah=ASPECTS[fmt]
 if aw>=ah:w,h=int(round(short*aw/ah)),short       # landscape: short side is the height
 else:w,h=short,int(round(short*ah/aw))             # portrait: short side is the width
 return max(2,w//2*2),max(2,h//2*2)

# clips_only keeps native moving footage everywhere; mixed allows verified stills as well.
POLICIES={'clips_only':'Moving footage only','mixed':'Moving footage and photographs'}
STILL_LAYOUTS={'blurred_fit':'Photograph fitted over a blurred copy of itself','full_bleed':'Photograph cropped to fill the frame'}

def policy(p):return p.get('picture_policy') if p.get('picture_policy') in POLICIES else 'clips_only'

def is_still(p,asset):
 if not asset:return False
 if asset.get('kind')=='image':return True
 return str((asset.get('derived_from') or {}).get('kind') or '').lower()=='image'
def uid():return uuid.uuid4().hex[:16]
def write(path,data):
 t=path.with_name(path.name+'.'+uuid.uuid4().hex+'.tmp');t.write_text(json.dumps(data,indent=2),encoding='utf-8');t.replace(path)
def folder(pid):
 if not re.fullmatch('[a-f0-9]{16}',pid):raise NotFound('Production not found.')
 p=PROJECTS/pid
 if not (p/'project.json').exists():raise NotFound('Production not found.')
 return p
def load(pid):return json.loads((folder(pid)/'project.json').read_text())
def save(p):
 from approval import ensure_automatic_policy
 ensure_automatic_policy(p)
 from document_plan import synchronize
 synchronize(p)
 from production import reserve_id
 for a in p['assets']:
  a['reserve']=bool(reserve_id(a));a['source_label']=reserve_id(a) or Path(a.get('original_name',a['name'])).stem
 by_id={a['id']:a for a in p['assets']}
 for scene in p['scenes']:
  asset=by_id.get(scene.get('asset_id'));scene['source_label']=asset['source_label'] if asset else '';scene['source_role']='reserve' if asset and asset['reserve'] else 'primary' if asset else ''
 p['updated']=time.time();write(folder(p['id'])/'project.json',p)
def busy(pid):return pid in JOBS and JOBS[pid].snapshot()['status'] in ['queued','running']
def editable(pid):
 if busy(pid):raise BadRequest('This production is exporting. Wait or cancel before editing.')
def stamp(t):
 h,m,s,ms=re.split('[:,.]',t);ms=(ms+'000')[:3]
 return ((int(h)*60+int(m))*60+int(s))*1000+int(ms)

def parse_srt(text):
 blocks=[];last=0;nums=set()
 for part in re.split(r'\n\s*\n',text.strip().replace('\r\n','\n')):
  lines=part.splitlines()
  if len(lines)<3 or not lines[0].strip().isdigit():raise BadRequest('SRT blocks need a number, time range and narration text.')
  # Cue clocks are accepted as the tools actually write them: a one- or two-digit hour
  # field and one to three millisecond digits. A 47-minute programme written as
  # 0:00:00,000 used to be rejected outright, which is a length limit nobody asked for.
  match=re.fullmatch(r'\s*(\d{1,2}:\d{2}:\d{2}[,.]\d{1,3})\s*-->\s*(\d{1,2}:\d{2}:\d{2}[,.]\d{1,3})\s*',lines[1])
  if not match:raise BadRequest('Use standard SRT time ranges: 00:00:00,000 --> 00:00:05,000.')
  n=int(lines[0]);a,b=map(stamp,match.groups());txt=' '.join(lines[2:]).strip()
  if n in nums or a<last or b<=a or not txt:raise BadRequest('SRT numbers must be unique and time ranges must be ordered, positive and non-overlapping.')
  nums.add(n);last=b;blocks.append({'number':n,'start_ms':a,'end_ms':b,'text':txt})
 if not blocks:raise BadRequest('The SRT is empty.')
 return blocks

def srt_stamp(ms):
 h,rem=divmod(int(ms),3600000);m,rem=divmod(rem,60000);sec,frac=divmod(rem,1000)
 return '%02d:%02d:%02d,%03d'%(h,m,sec,frac)

def probe(path):
 try:out=subprocess.run([FFMPEG,'-nostdin','-hide_banner','-probesize','2097152','-analyzeduration','3000000','-threads','2','-i',str(path)],capture_output=True,text=True,encoding='utf-8',errors='replace',timeout=15).stderr
 except subprocess.TimeoutExpired:raise ValueError('Metadata check timed out after 15 seconds. No full-video analysis was attempted. Check that the file is fully downloaded and playable locally.')
 m=re.search(r'Duration: (\d+):(\d+):(\d+\.\d+)',out)
 return {'duration':int(m[1])*3600+int(m[2])*60+float(m[3]) if m else 0,'has_audio':'Audio:' in out,'has_video':'Video:' in out}

def intent(text,people,profile):
 """A coarse guess at a beat's tone, from wording alone.

 These word lists are hint dictionaries, not a taxonomy of subject matter: they exist to
 pick a movement family and an emotion label, and a production about any subject still
 resolves to neutral defaults when nothing matches. Add a production profile with its own
 lists instead of editing these for one programme.
 """
 found=next((x for x in people if any(re.search(r'\b'+re.escape(a)+r'\b',text,re.I) for a in [x['name']]+x.get('aliases',[]) if a)),None)
 lists=(PROFILES.get(profile) or {}).get('hints') or DEFAULT_HINTS
 for hint in lists:
  if re.search(hint['pattern'],text,re.I):
   purpose,emotion,category=hint['purpose'],hint['emotion'],hint['category'];break
 else:purpose,emotion,category='develop the account','attention / restraint','general'
 if found:category='person'
 return purpose,emotion,category,found['name'] if found else ''

def motion(scene,index):
 # Intent chooses the family of movement; the stable hash only varies its path.
 n=int(hashlib.sha256(scene['id'].encode()).hexdigest()[:8],16);v=(n%997)/997;duration=(scene['frame_out']-scene['frame_in'])/30
 if scene.get('script_camera'):
  request=scene['script_camera'];move=request['move'];amount=request['amount'];z0,z1=(1,1+amount) if move=='push' else (1+amount,1) if move=='pull' else (1,1) if move=='static' else (1.04,1.04)
  phase0=request.get('phase_start',0);phase1=request.get('phase_end',1);full0,full1=z0,z1;z0=full0+(full1-full0)*phase0;z1=full0+(full1-full0)*phase1
  return dict(id='motion-'+scene['id'],mode='photo_camera',move=move,emotion=scene['emotion'],focus=scene['focus'],duration_s=duration,zoom_start=z0,zoom_end=z1,x_start=.25+.5*phase0 if move=='pan-right' else .5,x_end=.25+.5*phase1 if move=='pan-right' else .5,y_start=.5,y_end=.5,easing='linear',rationale=request['basis'])
 tone=(scene['emotion']+' '+scene['purpose']).lower();mode=scene.get('motion','auto')
 if any(k in tone for k in ['grief','mourning','remembrance','loss']):suggestions=['pull','reveal'];reason='Give the memory breathing room rather than manufacture urgency.'
 elif any(k in tone for k in ['orientation','place','location']):suggestions=['pan-left','pan-right','reveal'];reason='Reveal the spatial context that the narration is explaining.'
 elif any(k in tone for k in ['scrutiny','investigation','uncertainty']):suggestions=['push','reveal'] if scene.get('person') else ['pan-left','pan-right'];reason='Direct attention toward the subject, or survey the context, without implying an unverified exhibit.'
 elif any(k in tone for k in ['legal','accountability','clarity']):suggestions=['reveal','push'];reason='Identify the legal subject with a composed, restrained movement.'
 else:suggestions=['push','reveal','rise'];reason='Bring attention to the declared visual focus without a decorative effect.'
 if mode not in ['push','pull','pan-left','pan-right','rise','reveal']:mode=suggestions[(index+n)%len(suggestions)]
 z0,z1=(1.008+v*.006,1.055+v*.025) if mode=='push' else (1.065+v*.02,1.005+v*.01) if mode in ['pull','reveal'] else (1.05+v*.025,1.05+v*.025)
 x0,x1=(.78,.18) if mode=='pan-left' else (.18,.78) if mode=='pan-right' else (.35+v*.15,.40+v*.14)
 if mode not in ['pan-left','pan-right']:
  shift={'left':-.20,'center':0,'right':.20}.get(scene.get('focus_position'),0);x0+=shift;x1+=shift
 y0,y1=(.46,.23) if mode=='rise' else (.36,.40)
 factor=min(1.12,max(.5,duration/4.5))*{'subtle':.65,'purposeful':1,'strong':1.18}.get(scene.get('motion_strength'),1)
 factor*=(PROFILES.get(scene.get('profile')) or {}).get('motion_scale',1)
 z0=min(1.095,1+(z0-1)*factor);z1=min(1.095,1+(z1-1)*factor)
 return dict(id='motion-'+scene['id'],mode='photo_camera',move=mode,emotion=scene['emotion'],focus=scene['focus'],duration_s=duration,zoom_start=round(z0,6),zoom_end=round(z1,6),x_start=round(x0,6),x_end=round(x1,6),y_start=y0,y_end=y1,easing='quick_settle' if mode in ['push','pull','reveal'] else 'linear',rationale=reason+f' Complete the movement over {duration:.3f}s; human review must confirm the actual composition.')

def scene_review(p,i,s,a,ranges,last,final=False):
 """Review one scene exactly as export readiness does. Bulk approval reuses this.

 Returns (errors, notes). `ranges` is the per-asset list of intervals already claimed
 by earlier scenes, so an overlap is caught the same way in both paths.
 """
 errors=[];notes=[];pre=f'Scene {i+1}: '
 if s['frame_in']!=last or s['frame_out']<=last:errors.append(pre+'timeline has a gap, overlap or empty scene.')
 if not a:return errors+[pre+'assign uploaded media.'],notes
 if not a['verified'] or not a['source'].strip():errors.append(pre+'confirm the asset’s identity/context and record its source.')
 if s['person'] and s['person'].casefold() not in [n.casefold().strip() for n in a['people']]:
  # Any subject category that is not a person can carry a disclosed wide/context picture;
  # the rule is that it is disclosed in notes, not that it belongs to a fixed list.
  if not(s['context_only'] and s['notes'].strip() and a['category']!='person' and not a['people']):errors.append(pre+'the named person is not verified in this asset. Select the correct person, or explicitly disclose genuine wide context with no substituted identity.')
  else:notes.append(pre+'disclosed context, not a verified portrait: '+s['notes'])
 lastscene=i==len(p['scenes'])-1
 if s['transition']=='dissolve' and lastscene:errors.append(pre+'the last scene cannot dissolve into a nonexistent next scene.')
 if s['transition']=='dissolve' and not lastscene and p['scenes'][i+1]['transition']=='dissolve':errors.append(pre+'adjacent dissolve pairs are unsupported; keep the next exit as a cut.')
 if s['transition']=='dissolve' and not lastscene and s.get('transition_handle_frames',4)>p['scenes'][i+1]['frame_out']-p['scenes'][i+1]['frame_in']:errors.append(pre+'the dissolve is longer than the incoming picture beat.')
 still=is_still(p,a)
 if still and policy(p)!='mixed':errors.append(pre+'this production is set to moving footage only. Switch the picture policy to allow photographs, or use a clip.')
 if still and not (a.get('approval_record') or {}).get('active',True):errors.append(pre+'the photograph’s use approval was revoked; re-approve it before exporting.')
 if still and a['kind']=='image' and a.get('derived_from') and not a.get('verified'):errors.append(pre+'the extracted frame was never approved for use.')
 if not (folder(p['id'])/a['path']).is_file():errors.append(pre+'source file is missing from disk.')
 if a['kind']=='video':
  end_s=s['in_s']+(s['frame_out']-s['frame_in']+(s.get('transition_handle_frames',4) if s['transition']=='dissolve' else 0))/30
  if s['in_s']<0 or end_s>a['duration']+.001:errors.append(pre+'video is too short, including transition handles. No loop or frozen tail will be generated.')
  for start,stop,number in ranges.get(a['id'],[]):
   if not p.get('allow_archive_reuse') and s['in_s']<stop and end_s>start:errors.append(pre+f'source interval overlaps scene {number}.')
 if p.get('transition_policy')=='clean_cuts_only' and s['transition']!='cut':errors.append(pre+'only clean cuts are enabled for this production.')
 if not s['focus'].strip():errors.append(pre+'set the story focus.')
 if final and not s['approved']:errors.append(pre+'editorial approval is pending.')
 if final and (a['rights']=='unreviewed' or not a['notes'].strip()):errors.append(pre+'document the rights/reuse decision and notes before final export.')
 return errors,notes

def check(p,final=False):
 errors=[];notes=[];assets={x['id']:x for x in p['assets']};ranges=defaultdict(list);last=0
 from automatic import repetition_errors,source_capacity_errors,_plan_coverage
 errors.extend(repetition_errors(p));errors.extend(source_capacity_errors(p))
 # A block map written for a different runtime leaves blocks unplaced. Say so as a plan
 # problem with the numbers, rather than letting the picture come up short at the end.
 uncovered=_plan_coverage(p)
 if uncovered and p['scenes']:
  errors.append(str(len(uncovered))+' SRT block(s) are not placed by the block map (first is block '+str(uncovered[0])+'). Extend the map over the whole narration, or download a fresh template for this SRT.')
 if not p['brief'].strip() and not p.get('srt'):errors.append('Import your SRT story, or add a case brief.')
 from production import document
 from edl import signature
 if p['scenes'] and (p.get('picture_plan') or p.get('assembly_signature')) and p.get('assembly_signature')!=signature(p):errors.append('Editing inputs changed. Rebuild the storyboard to apply the current SRT block map / production script.')
 if p.get('production_script') and p['scenes'] and p.get('edit_script_hash')!=hashlib.sha256(document(p).encode()).hexdigest():errors.append('Production script changed. Rebuild the storyboard to apply the new instructions.')
 # Narration is optional. A programme may be carried by its text alone; what is not
 # allowed is a narration track whose length disagrees with the picture it sits under.
 if not p['scenes']:errors.append('Import an SRT and build the storyboard.')
 if p.get('voiceover') and p['scenes'] and abs(p['voiceover']['duration']-p['scenes'][-1]['frame_out']/30)>.25:errors.append('Narration duration must match the SRT timeline within 0.25 seconds. Correct the timing; do not silently trim the voiceover.')
 for i,s in enumerate(p['scenes']):
  a=assets.get(s['asset_id'])
  scene_errors,scene_notes=scene_review(p,i,s,a,ranges,last,final)
  errors.extend(scene_errors);notes.extend(scene_notes)
  last=s['frame_out']
  if a and a['kind']=='video':
   end_s=s['in_s']+(s['frame_out']-s['frame_in']+(s.get('transition_handle_frames',4) if s['transition']=='dissolve' else 0))/30
   ranges[a['id']].append((s['in_s'],end_s,i+1))
 flow=p.get('picture_flow') or {}
 from picture_flow import outstanding_notes
 notes.extend(outstanding_notes(flow))
 if not final and any(not s['approved'] for s in p['scenes']):notes.append('Review export only. Scene approval and rights review are required for final delivery.')
 return {'errors':errors,'notes':notes,'can_export':not errors,'final':final}

def public(p):
 d=copy.deepcopy(p)
 from document_plan import match_report
 d['source_match_report']=match_report(d)
 d['validation']=check(p);d['final_validation']=check(p,True)
 saved=folder(p['id'])/'render_status.json'
 d['render']=JOBS[p['id']].snapshot() if p['id'] in JOBS else json.loads(saved.read_text()) if saved.exists() else {'status':'idle','progress':0,'stage':'No export yet'}
 if p['id'] not in JOBS and d['render']['status'] in ['running','queued']:d['render'].update(status='interrupted',stage='Previous export interrupted; start a new export',progress=0)
 if d['render'].get('output'):
  d['render']['url']=f'/api/projects/{p["id"]}/exports/{d["render"]["output"]}'
  record=folder(p['id'])/'reviews'/(Path(d['render']['output']).stem+'.json')
  d['render']['current']=record.exists() and json.loads(record.read_text()).get('project',{}).get('updated')==p['updated']
 for i,scene in enumerate(d['scenes']):scene['motion_plan']=motion(scene,i)
 from production import reserve_id
 for a in d['assets']:
  a['url']=f'/api/projects/{p["id"]}/assets/{a["id"]}/file';a['reserve']=bool(reserve_id(a));a['source_label']=reserve_id(a) or Path(a.get('original_name',a['name'])).stem
 if d['voiceover']:d['voiceover']['url']=f'/api/projects/{p["id"]}/narration'
 from automatic import automatic_readiness
 d['automatic_readiness']=automatic_readiness(p)
 status_path=folder(p['id'])/'automatic_status.json'
 try:d['automatic_status']=json.loads(status_path.read_text()) if status_path.exists() else {'status':'idle','detail':''}
 except (OSError,ValueError):d['automatic_status']={'status':'idle','detail':''}
 d['inputs_ready']=bool(p.get('srt') and p.get('production_script') and p.get('assets'))
 d['picture_flow']=p.get('picture_flow') or None
 d['flow_review']=[{'scene':b.get('scene'),'asset':b.get('asset'),'status':b.get('status'),'note':b.get('note'),'declared_in_s':b.get('declared_in_s'),'conformed_in_s':b.get('conformed_in_s'),'shot':b.get('shot'),'suggestions':b.get('suggestions') or []} for b in ((p.get('picture_flow') or {}).get('picture') or {}).get('beats',[]) if b.get('review')]
 d['flow_summary']=(p.get('picture_flow') or {}).get('summary') or {}
 d['rebuild_report']=p.get('rebuild_report')
 # What the next export will actually be, resolved once here so the studio never has to
 # guess and no two parts of the app can disagree about the size.
 width,height=output_size(p.get('format','landscape'),p.get('size'))
 d['output']={'format':p.get('format','landscape'),'size':p.get('size'),'width':width,'height':height,
              'label':str(width)+'×'+str(height),'aspect':ASPECTS.get(p.get('format','landscape'))}
 return d

@app.get('/')
def home():
 options=''.join('<option value="'+name+'"'+( ' selected' if name=='documentary' else '')+'>'+str(meta.get('name') or name)+'</option>' for name,meta in PROFILES.items())
 return render_template('index.html',asset_revision=max(p.stat().st_mtime_ns for p in (BASE/'static').glob('*') if p.is_file()),profile_options=options)
@app.after_request
def cache(response):
 if not request.path.endswith('/file'):response.headers['Cache-Control']='no-store'
 return response
@app.get('/api/projects')
def projects():
 result=[]
 for f in PROJECTS.glob('*/project.json'):
  p=json.loads(f.read_text());result.append({k:p[k] for k in ['id','title','profile','format','updated']}|{'scenes':len(p['scenes']),'approved':sum(s['approved'] for s in p['scenes']),'portable':bool(p.get('portable_from_project'))})
 return jsonify({'projects':sorted(result,key=lambda x:-x['updated']),'profiles':PROFILES})
@app.post('/api/projects')
def create():
 d=request.get_json() or {};title=str(d.get('title','')).strip()
 if not title or len(title)>100:raise BadRequest('Enter a production title of 1–100 characters.')
 profile=d.get('profile') or 'documentary';fmt=d.get('format','landscape')
 if profile not in PROFILES or fmt not in ASPECTS:raise BadRequest('Choose a supported profile and format.')
 requested_size=d.get('size') or DEFAULT_SIZE.get(fmt)
 if not isinstance(requested_size,str) or not valid_size(requested_size):raise BadRequest('Choose an export size: '+', '.join(SIZES)+', or a pixel size such as 1920x1080.')
 pid=uid();root=PROJECTS/pid;root.mkdir()
 for name in ['inputs','inputs/semantic','sources','assets','renders','reviews']: (root/name).mkdir(parents=True,exist_ok=True)
 requested=d.get('picture_policy')
 if requested is not None and requested not in POLICIES:raise BadRequest('Choose a picture policy: '+', '.join(POLICIES))
 p={'id':pid,'schema_version':1,'picture_policy':requested or 'clips_only','transition_policy':'clean_cuts_only','repetition_policy':{'cooldown_slots':6,'source_interval_overlap_forbidden_within_window':True},'auto_assemble_uploads':True,'title':title,'profile':profile,'format':fmt,'size':requested_size,'brief':str(d.get('brief','')),'people':[],'assets':[],'scenes':[],'voiceover':None,'srt':None,'updated':time.time()}
 from approval import ensure_automatic_policy
 ensure_automatic_policy(p)
 write(root/'project.json',p);write(root/'catalog.json',{'slots':{}});return jsonify(public(p)),201
@app.get('/api/projects/<pid>')
def get_project(pid):
 with LOCKS[pid]:
  p=load(pid)
  from approval import ensure_automatic_policy
  if ensure_automatic_policy(p):save(p)
  return jsonify(public(p))
@app.patch('/api/projects/<pid>')
def update_project(pid):
 with LOCKS[pid]:
  editable(pid);p=load(pid);d=request.get_json() or {};old_brief=p['brief'];old_people=copy.deepcopy(p['people'])
  for k in ['title','brief']:
   if k in d:p[k]=str(d[k])[:20000]
  if not p['title'].strip():raise BadRequest('A production title is required.')
  if 'people' in d:
   p['people']=[{'name':str(x.get('name','')).strip()[:120],'role':str(x.get('role',''))[:80],'aliases':[str(a).strip()[:120] for a in x.get('aliases',[]) if str(a).strip()]} for x in d['people'] if str(x.get('name','')).strip()]
  if 'picture_policy' in d:
   if d['picture_policy'] not in POLICIES:raise BadRequest('Choose a picture policy: '+', '.join(POLICIES))
   p['picture_policy']=d['picture_policy']
   for scene in p['scenes']:scene['approved']=False
  if 'image_layout' in d:
   if d['image_layout'] not in STILL_LAYOUTS:raise BadRequest('Choose a photograph layout: '+', '.join(STILL_LAYOUTS))
   p['image_layout']=d['image_layout']
  # Output geometry. Changing it does not change a single cut: it re-encodes the picture
  # at the new size, so the beats, wording, approvals and duration all stay as reviewed.
  if 'format' in d:
   if d['format'] not in ASPECTS:raise BadRequest('Choose a format: '+', '.join(ASPECTS))
   if d['format']!=p.get('format'):
    p['format']=d['format']
    p['size']=d.get('size') or DEFAULT_SIZE[d['format']]
    if not valid_size(p['size']):raise BadRequest('Choose an export size: '+', '.join(SIZES)+', or a pixel size such as 1920x1080.')
    p['picture_rebuilt']=False
  if 'size' in d:
   if not isinstance(d['size'],str) or not valid_size(d['size']):raise BadRequest('Choose an export size: '+', '.join(SIZES)+', or a pixel size such as 1920x1080.')
   if str(d['size'])!=str(p.get('size')):
    p['size']=str(d['size'])
    p['picture_rebuilt']=False
  if p['brief']!=old_brief or p['people']!=old_people:
   for scene in p['scenes']:scene['approved']=False
  save(p);return jsonify(public(p))
@app.get('/api/projects/<pid>/imports/<key>')
def import_status(pid,key):
 from import_status import token,get
 folder(pid)
 if not token(key):raise NotFound('Import not found.')
 return jsonify(get(pid,key))

@app.delete('/api/projects/<pid>/imports/<key>')
def cancel_import(pid,key):
 from import_status import token,cancel
 folder(pid)
 if not token(key):raise NotFound('Import not found.')
 return jsonify(cancel(pid,key))

@app.post('/api/projects/<pid>/media-stream')
@app.post('/api/projects/<pid>/upload')
def upload(pid):
 from import_status import token,update
 key=token(request.headers.get('X-Upload-ID',''));folder(pid);update(pid,key,'waiting_for_project')
 try:
  limit=MAX_VIDEO_BYTES if request.path.endswith('/media-stream') else 2*1024**3
  if request.content_length is not None and request.content_length>limit:raise RequestEntityTooLarge('Video streaming supports up to 8 GB per MP4/MOV; other uploads support up to 2 GB per request.')
  return perform_upload(pid,key)
 except Exception as e:
  update(pid,key,'error',detail=str(getattr(e,'description',e))[:500]);raise

def perform_upload(pid,key=''):
 from import_status import update,check,project_lock,save_local
 with project_lock(LOCKS[pid]):
  editable(pid);p=load(pid);root=folder(pid);check(pid,key);update(pid,key,'receiving')
  if request.path.endswith('/media-stream'):
   if request.mimetype!='application/octet-stream':raise BadRequest('Use the editor video-stream upload control.')
   name=request.args.get('name','')
   if Path(name).suffix.lower() not in ['.mp4','.mov']:raise BadRequest('Direct streaming accepts MP4 or MOV video.')
   f=SimpleNamespace(filename=name,stream=request.stream);kind='asset'
  else:f=request.files.get('file');kind=request.form.get('kind')
  check(pid,key)
  if not f:raise BadRequest('Choose a file.')
  ext=Path(f.filename or '').suffix.lower();update(pid,key,'reading_document',filename=f.filename)
  if kind=='srt':
   try:
    raw=f.read(4_000_001)
    if len(raw)>4_000_000:raise BadRequest('SRT files must be under 4 MB.')
    text=raw.decode('utf-8-sig')
   except UnicodeDecodeError:raise BadRequest('SRT must be UTF-8 text.')
   blocks=parse_srt(text)
   if p['scenes'] and request.form.get('replace')!='yes':raise BadRequest('Replacing the SRT invalidates the current storyboard. Confirm replacement first.')
   (root/'inputs/captions.srt').write_text(text);p['srt']={'blocks':blocks,'name':f.filename};p['scenes']=[];p.pop('srt_recovery',None)
  elif kind=='srt_block_map':
   from edl import parse,srt_signature
   try:
    from automatic import link_frame_csv
    payload=f.read(4_000_001);payload=link_frame_csv(payload,p) if ext=='.csv' else payload
    beats=parse(payload,ext)
   except (ValueError,UnicodeError) as e:raise BadRequest('SRT block map: '+str(e))
   if not all(row.get('map_type')=='srt_block' for row in beats):raise BadRequest('Choose a block-map CSV with block,start,end,beat,media_file,source_in,source_out columns.')
   numbers=[row['srt_blocks'][0] for row in beats]
   if numbers!=list(range(1,len(numbers)+1)):raise BadRequest('Block map must contain unique ordered SRT blocks 1 through its final block.')
   p['srt_block_map']={'name':f.filename,'beats':beats,'schema_version':1,'based_on_srt':srt_signature(p) if p.get('srt') else None,'import_notes':list(dict.fromkeys(note for row in beats for note in row.get('import_notes',[]))),'import_blockers':[{'beat_id':row.get('beat_id') or row.get('plan_row_id',''),'note':note} for row in beats for note in row.get('import_blockers',[])]}
   write(root/'inputs/srt_block_map.json',p['srt_block_map'])
   for scene in p['scenes']:scene['approved']=False
  elif kind=='production_script':
   if ext not in ['.txt','.md','.json','.docx']:raise BadRequest('Upload a TXT, Markdown, JSON or DOCX production script.')
   from production import read_document
   try:text=read_document(f,ext)
   except Exception as e:raise BadRequest('The production script could not be read: '+str(e)[:200])
   (root/'inputs/production_script.txt').write_text(text,encoding='utf-8');p['production_script']={'name':f.filename,'text':text}
   from document_plan import srt_outline_blocks
   recovered,outline_problems=srt_outline_blocks(text)
   if recovered and not p.get('srt'):
    p['srt']={'blocks':recovered,'name':f.filename+' · narration table','recovered_from':f.filename}
    (root/'inputs/captions.srt').write_text('\n\n'.join('%d\n%s --> %s\n%s'%(b['number'],srt_stamp(b['start_ms']),srt_stamp(b['end_ms']),b['text']) for b in recovered),encoding='utf-8')
    p['srt_recovery']={'source':f.filename,'blocks':len(recovered),'total_s':recovered[-1]['end_ms']/1000,'note':'Narration blocks were recovered verbatim from a Markdown block table in the production script. Wording, order and timing were copied, not rewritten; a separate .srt can still replace this.'}
   elif recovered and p.get('srt'):
    p['srt_recovery']={'source':f.filename,'blocks':len(recovered),'total_s':recovered[-1]['end_ms']/1000,'skipped':True,'note':'This production script also contains a narration block table. The separate .srt already uploaded is used; upload the table as .srt if you want it instead.'}
   elif outline_problems:
    p['srt_recovery']={'source':f.filename,'blocks':0,'problems':outline_problems[:5],'note':'A narration block table was found but not used; the reported problems would require altering timing.'}
   for scene in p['scenes']:scene['approved']=False
  elif kind=='voiceover':
   if ext not in ['.mp3','.wav','.m4a','.aac','.flac','.ogg']:raise BadRequest('Use MP3, WAV, M4A, AAC, FLAC or OGG narration.')
   dest=root/'inputs'/('pending'+ext);update(pid,key,'saving')
   try:
    save_local(f,dest,pid,key);check(pid,key);update(pid,key,'checking_metadata');info=probe(dest);check(pid,key)
   except Exception as e:
    dest.unlink(missing_ok=True)
    if isinstance(e,BadRequest):raise
    raise BadRequest(str(e))
   if not info['has_audio'] or not info['duration']:dest.unlink();raise BadRequest('The uploaded file has no decodable audio.')
   for old in (root/'inputs').glob('voiceover.*'):old.unlink()
   final=root/'inputs'/('voiceover'+ext);dest.replace(final);p['voiceover']={'name':f.filename,'path':final.relative_to(root).as_posix(),'duration':info['duration']}
   for scene in p['scenes']:scene['approved']=False
  elif kind=='asset':
   if ext not in ['.mp4','.mov','.jpg','.jpeg','.png','.webp']:raise BadRequest('Use MP4/MOV video or JPG/PNG/WebP photographs.')
   aid=uid();photo=ext in ['.jpg','.jpeg','.png','.webp'];dest=root/('assets' if photo else 'sources')/(aid+ext);update(pid,key,'saving')
   try:
    save_local(f,dest,pid,key);check(pid,key);update(pid,key,'checking_image' if photo else 'checking_metadata')
    if photo:
     with Image.open(dest) as im:im.verify()
     duration=0
    else:
     info=probe(dest)
     if not info['has_video'] or not info['duration']:raise ValueError('No decodable video')
     duration=info['duration']
    check(pid,key)
   except Exception as e:
    dest.unlink(missing_ok=True)
    if isinstance(e,BadRequest):raise
    raise BadRequest('Media check failed: '+str(e)[:350])
   p['assets'].append({'id':aid,'name':Path(f.filename).name,'original_name':Path(f.filename).name,'path':dest.relative_to(root).as_posix(),'kind':'image' if photo else 'video','duration':duration,'source':'','people':[],'category':'general','verified':False,'rights':'unreviewed','notes':''})
  else:raise BadRequest('Unknown import type.')
  if kind=='asset':
   from approval import auto_approve_upload
   auto_approve_upload(p,p['assets'][-1])
  if kind in ['srt','production_script']:
   from production import infer
   p['profile']=infer(p)['profile']
  update(pid,key,'indexing');save(p)
  from automatic import maybe_queue
  maybe_queue(sys.modules[__name__],p)
  result=jsonify(public(p));update(pid,key,'done');return result
@app.delete('/api/projects/<pid>/srt-block-map')
def remove_block_map(pid):
 with LOCKS[pid]:
  editable(pid);p=load(pid);p.pop('srt_block_map',None);(folder(pid)/'inputs/srt_block_map.json').unlink(missing_ok=True)
  for scene in p['scenes']:scene['approved']=False
  save(p);return jsonify(public(p))

@app.post('/api/projects/<pid>/assets/approve-all')
def approve_all_assets(pid):
 with LOCKS[pid]:
  editable(pid);p=load(pid);d=request.get_json(silent=True) or {}
  if d.get('attested') is not True:raise BadRequest('Confirm your review/reuse attestation before approving all files.')
  from approval import approve_asset,BASIS
  for a in p['assets']:approve_asset(p,a)
  for scene in p['scenes']:scene['approved']=False
  p.setdefault('media_approval_history',[]).append({'action':'approve_all','count':len(p['assets']),'at':time.time(),'basis':BASIS})
  save(p);return jsonify(public(p))

@app.patch('/api/projects/<pid>/assets/approval-policy')
def asset_approval_policy(pid):
 with LOCKS[pid]:
  editable(pid);p=load(pid);d=request.get_json(silent=True) or {};enabled=d.get('auto_upload') is True
  if enabled and d.get('attested') is not True:raise BadRequest('Confirm your review/reuse attestation before enabling automatic upload approval.')
  from approval import BASIS
  p['media_approval_policy']={'auto_upload':enabled,'basis':BASIS,'at':time.time(),'scope':'future_local_media_uploads_only'}
  save(p);return jsonify(public(p))

@app.post('/api/projects/<pid>/source-bindings')
def bind_script_source(pid):
 with LOCKS[pid]:
  editable(pid);p=load(pid);data=request.get_json() or {};name=str(data.get('source_file') or '').strip();aid=data.get('asset_id')
  if not name or len(name)>200 or '/' in name or chr(92) in name or Path(name).suffix.lower() not in ['.mp4','.mov','.jpg','.jpeg','.png','.webp']:raise BadRequest('Use a script filename such as v1.mp4, not a path or source label.')
  asset=next((a for a in p['assets'] if a['id']==aid),None)
  if not asset:raise BadRequest('Choose an existing upload from this project.')
  if asset.get('derived_from'):raise BadRequest('Choose the original source upload, not an extracted frame.')
  if Path(name).suffix.lower() in ['.mp4','.mov'] and asset['kind']!='video':raise BadRequest('A video filename must be linked to a video upload.')
  from production import reserve_id
  reserve=reserve_id(asset);logical=Path(name).stem.lower()
  if (reserve and logical!=reserve) or (re.fullmatch(r'r\d+',logical) and reserve!=logical):raise BadRequest('Reserve IDs cannot be renamed or reassigned. Keep the original r1, r2... source filename.')
  p['source_bindings']=dict(p.get('source_bindings') or {});p['source_bindings'][name.casefold()]=aid
  for scene in p['scenes']:scene['approved']=False
  save(p);return jsonify(public(p))

@app.patch('/api/projects/<pid>/assets/<aid>')
def update_asset(pid,aid):
 with LOCKS[pid]:
  editable(pid);p=load(pid);a=next((x for x in p['assets'] if x['id']==aid),None)
  if not a:raise NotFound('Asset not found.')
  d=request.get_json() or {}
  for k in ['name','source','notes']:
   if k in d:a[k]=str(d[k])[:5000]
  if 'people' in d:a['people']=[str(n).strip()[:120] for n in d['people'] if str(n).strip()]
  if 'verified' in d:
   a['verified']=d['verified'] is True
   if not a['verified'] and a.get('approval_record'):a['approval_record']['active']=False
  if 'category' in d:
   # Categories are a free label ("person", "place", "institution", "product", anything).
   # Only "person" is meaningful to the rules; the rest is the editor's own vocabulary.
   value=str(d.get('category') or '').strip()[:40]
   if value:a['category']=value
  if d.get('rights') in ['unreviewed','permission','licensed','public_domain','reviewed_other']:a['rights']=d['rights']
  for s in p['scenes']:
   if s['asset_id']==aid:s['approved']=False
  save(p);return jsonify(public(p))
def merge_scoped_rebuild(previous, rebuilt, blocks):
 """Splice a rebuilt block range back into the saved timeline. Returns scenes plus a note.

 A block can belong to several beats (a parent beat spread across blocks 9 and 10 is one
 row per block, and both rows carry the same parent), so "beats touching these blocks" is
 the unit of replacement, not single blocks. Those beats are dropped from the saved
 timeline, the rebuilt ones are shifted so the range starts where it used to start, and
 the range is spliced back where the first dropped beat sat.

 Re-basing a range onto frame 0 can leave a sub-frame residue at its far end (a 268-frame
 range rebuilt alone can measure 269 because each end was rounded independently). A
 residue of at most 1 frame is absorbed at the last rebuilt beat and reported; anything
 larger is left alone so the caller's continuity guard refuses the save.
 """
 if not previous or not rebuilt:
  return {'scenes': previous or rebuilt, 'rounding_frames': 0, 'of': blocks}
 inside = set(blocks)
 def overlaps(scene):
  return bool(set(scene.get('srt_blocks') or []) & inside)
 replaced = [s for s in previous if overlaps(s)]
 rounding = 0
 if replaced:
  offset = min(s['frame_in'] for s in replaced) - min(s['frame_in'] for s in rebuilt)
  for scene in rebuilt:
   scene['frame_in'] += offset
   scene['frame_out'] += offset
  old_span = max(s['frame_out'] for s in replaced) - min(s['frame_in'] for s in replaced)
  new_span = max(s['frame_out'] for s in rebuilt) - min(s['frame_in'] for s in rebuilt)
  residue = old_span - new_span
  if residue and abs(residue) <= 1:
   last = max(rebuilt, key=lambda s: s['frame_out'])
   last['frame_out'] += residue
   rounding = residue
 ordered = []
 placed = False
 for scene in previous:
  if overlaps(scene):
   if not placed:
    ordered.extend(rebuilt)
    placed = True
   continue
  ordered.append(scene)
 if not placed:
  ordered.extend(rebuilt)
 return {'scenes': ordered, 'rounding_frames': rounding, 'of': blocks}

@app.post('/api/projects/<pid>/storyboard')
def build(pid):
 with LOCKS[pid]:
  editable(pid);p=load(pid)
  if not p['srt']:raise BadRequest('Import the narration SRT first.')
  payload=request.get_json(silent=True) or {}
  if p['scenes'] and not payload.get('replace'):raise BadRequest('A storyboard already exists. Confirm rebuilding to discard scene edits.')
  # Optional block range: rebuild only the beats covering these SRT blocks and leave the
  # rest of the timeline exactly as saved, approvals included.
  scope=None;adopted_surfaces=0
  if payload.get('blocks'):
   try:scope=sorted({int(x) for x in payload['blocks']})
   except (TypeError,ValueError):raise BadRequest('blocks must be a list of SRT block numbers.')
   if not scope:raise BadRequest('blocks must name at least one SRT block.')
   known={b['number'] for b in p['srt']['blocks']}
   unknown=[n for n in scope if n not in known]
   if unknown:raise BadRequest('These SRT blocks do not exist: '+', '.join(map(str,unknown[:8]))+'.')
  from automatic import restore_reviewed,full_source_analysis,carry_previous_review
  previous=copy.deepcopy(p.get('scenes') or [])
  full_source_analysis(p,folder(pid))
  # A cached timeline may only stand in for a whole rebuild. On a block-range rebuild it
  # would replace the entire storyboard with the cached one, so a request that named three
  # blocks would silently rebuild (and resequence) all of them.
  if not scope and restore_reviewed(p,folder(pid)):
   save(p);return jsonify(public(p))
  from production import infer
  detected=infer(p);p['profile']=detected['profile']
  identity_people=copy.deepcopy(p['people'])
  for name in detected['people']:
   if name.casefold() not in [x['name'].casefold() for x in identity_people]:identity_people.append({'name':name,'aliases':[],'role':''})
  scenes=[];last=0;cursor=defaultdict(float);usage=defaultdict(int)
  for i,b in enumerate(p['srt']['blocks']):
   purpose,emotion,category,person=intent(b['text'],identity_people,p['profile']);end=round(b['end_ms']*.03)
   if end<=last:raise BadRequest('An SRT block is shorter than one output frame.')
   s=dict(id=uid(),srt_blocks=[b['number'],b['number']],frame_in=last,frame_out=end,narration=b['text'],purpose=purpose,emotion=emotion,focus=person or category,category=category,person=person,asset_id=None,in_s=0,motion='auto',transition='cut',context_only=False,notes='',approved=False)
   for a in sorted(p['assets'],key=lambda a:(a['id']==(scenes[-1]['asset_id'] if scenes else None),usage[a['id']])):
    if p.get('production_script') or p.get('srt_block_map'):break
    from production import reserve_id
    if reserve_id(a):continue
    matches=person.casefold() in [n.casefold() for n in a['people']] if person else a['category']==category
    if not a['verified'] or not matches:continue
    if a['kind']=='video' and cursor[a['id']]+(end-last)/30>a['duration']-.05:continue
    s['asset_id']=a['id'];s['in_s']=round(cursor[a['id']],3);usage[a['id']]+=1
    if a['kind']=='video':cursor[a['id']]+=(end-last)/30+.05
    break
   s['profile']=p['profile'];s['focus_position']='center';s['motion_strength']='purposeful';scenes.append(s);last=end
  p['scenes']=scenes
  from production import apply,document
  from document_plan import rows_for,synchronize
  try:decisions,plan_mode=rows_for(p)
  except ValueError as e:raise BadRequest('SRT block map: '+str(e) if p.get('srt_block_map') else 'Production table: '+str(e))
  if decisions:
   from automatic import native_rows
   from edl import assemble
   from conform import stills
   # Stills first, while the plan still says which beats are photographs or STILL moments:
   # extraction registers one approved frame per requested moment and rewrites those rows.
   # Only then are the remaining moving intervals bounded to their uploaded files.
   if scope:
    # Rebuild only the beats covering the requested blocks. The plan rows outside the
    # range are dropped, so assembling this subset cannot disturb them.
    inside=[r for r in decisions if set(r.get('srt_blocks') or []) & set(scope)]
    if not inside:raise BadRequest('No planned beat covers blocks '+', '.join(map(str,scope[:8]))+'.')
    timeline_start=min(r['timeline_in_ms'] for r in inside)
    decisions=[copy.deepcopy(r) for r in inside]
    for r in decisions:
     r['timeline_in_ms']-=timeline_start;r['timeline_out_ms']-=timeline_start
    # A cut can sit anywhere inside a beat's own wording and still be faithful to it: the
    # saved storyboard holds the surfaces the editor actually reviewed, block boundaries
    # nudged by up to 0.35s into narration breaths. When the range is one saved beat per
    # planned beat - the normal case - those exact surfaces are reused, so a rebuild
    # reproduces the reviewed picture instead of silently moving cuts and changing the
    # length of the range it was asked to preserve.
    saved=[s for s in previous if set(s.get('srt_blocks') or [])&set(scope)]
    if saved and len(saved)==len(decisions):
     first=min(s['frame_in'] for s in saved)
     for row,hit in zip(decisions,saved):
      row['timeline_in_ms']=round((hit['frame_in']-first)/30*1000)
      row['timeline_out_ms']=round((hit['frame_out']-first)/30*1000)
     adopted_surfaces=len(saved)
   still_plan=stills(p,folder(pid),copy.deepcopy(decisions))
   extracted=sum(bool(a.get('derived_from')) for a in p['assets'])
   still_plan=native_rows(p,still_plan)
   p['allow_archive_reuse']=True
   original=p.get('picture_plan')
   p['picture_plan']={'name':(p.get('srt_block_map') or {}).get('name','SRT block map'),'beats':still_plan,'schema_version':1,'partial':bool(scope)}
   try:
    # The plan decides the cuts. On a range rebuild the seed must also be limited to the
    # blocks being rebuilt: passing the whole-timeline seeds assembled every beat with
    # whole-timeline frame numbers, so a one-block rebuild moved the entire storyboard.
    assemble(p,[s for s in scenes if set(s['srt_blocks'])&set(scope)] if scope else scenes)
   except ValueError as e:raise BadRequest('SRT block map: '+str(e))
   finally:
    if original is None:p.pop('picture_plan',None)
    else:p['picture_plan']=original
   p['script_application']['mode']=plan_mode;p['script_application']['generated_stills']=extracted
  elif p.get('production_script'):apply(p)
  from edl import signature
  p['assembly_signature']=signature(p)
  p['edit_script_hash']=hashlib.sha256(document(p).encode()).hexdigest()
  from picture_flow import apply_flow
  p['picture_flow']=apply_flow(p,folder(pid))
  from automatic import validate_assembly
  decoded=validate_assembly(p,folder(pid));write(folder(pid)/'reviews/Automatic_frame_checks.json',decoded)
  if decoded['errors']:raise BadRequest('Automatic assembly stopped before saving an empty or undecodable timeline: '+' '.join(decoded['errors'][:3]))
  # An incremental rebuild: restore the review state of every beat whose picture the
  # rebuild reproduced exactly, and leave the rest unapproved. `scope` narrows this to a
  # block range, so editing one region does not disturb approvals elsewhere.
  if scope:
   if not decisions:raise BadRequest('An incremental rebuild needs a block map: the production script path has no per-block plan to rebuild by range.')
   spliced=merge_scoped_rebuild(previous,p['scenes'],scope);merged=spliced['scenes']
   # A range rebuild must reproduce the same timeline it replaces. If it does not, the
   # saved timeline would gain a gap or an overlap, so refuse rather than save a broken
   # storyboard and ask for a full rebuild instead.
   previous_last=0
   for i,scene in enumerate(merged):
    if scene['frame_in']!=previous_last:
     raise BadRequest('Rebuilding blocks '+', '.join(map(str,scope[:6]))+' would change the length of the surrounding timeline. Rebuild the whole storyboard instead.')
    previous_last=scene['frame_out']
   p['scenes']=merged
  carried=carry_previous_review({'scenes':previous},p['scenes'])
  p['rebuild_report']={'at':time.time(),'beats':len(p['scenes']),'approvals_carried':carried['carried'],'needs_review':carried['requiring_review'],'scope':scope,'previous_scenes':len(previous),'rounding_frames':(spliced['rounding_frames'] if scope else 0),'cut_surfaces_adopted':adopted_surfaces}
  save(p);return jsonify(public(p))
@app.patch('/api/projects/<pid>/scenes/<sid>')
def edit_scene(pid,sid):
 with LOCKS[pid]:
  editable(pid);p=load(pid);s=next((s for s in p['scenes'] if s['id']==sid),None)
  if not s:raise NotFound('Scene not found.')
  d=request.get_json() or {};p['manual_scene_edits']=True
  for k in ['purpose','emotion','focus','person','notes']:
   if k in d:s[k]=str(d[k])[:5000]
  if 'asset_id' in d:
   if d['asset_id'] is not None and not any(a['id']==d['asset_id'] for a in p['assets']):raise BadRequest('Asset does not belong to this production.')
   s['asset_id']=d['asset_id']
  moved_in_point=False
  if 'in_s' in d:
   try:n=float(d['in_s'])
   except (TypeError,ValueError):raise BadRequest('Source in-point must be a number.')
   if not math.isfinite(n) or n<0:raise BadRequest('Source in-point must be a nonnegative finite number.')
   moved_in_point=abs(n-float(s.get('in_s') or 0))>.001;s['in_s']=n
  if d.get('motion') in ['auto','push','pull','pan-left','pan-right','rise','reveal']:
   if d['motion']!='auto' and d['motion']!=s.get('motion'):s.pop('script_camera',None)
   s['motion']=d['motion']
  if d.get('focus_position') in ['left','center','right']:s['focus_position']=d['focus_position']
  if d.get('motion_strength') in ['subtle','purposeful','strong']:s['motion_strength']=d['motion_strength']
  if d.get('transition') in ['cut','dissolve']:
   s['transition']=d['transition'];s['transition_handle_frames']=(s.get('transition_handle_frames') or 4) if s['transition']=='dissolve' else 0
  if p.get('transition_policy')=='clean_cuts_only':s['transition']='cut';s['transition_handle_frames']=0
  if 'context_only' in d:s['context_only']=d['context_only'] is True
  s['approved']=d.get('approved',False) is True
  if moved_in_point:s.pop('picture_flow',None)  # an explicit human in-point overrides the automatic conform
  save(p);return jsonify(public(p))

@app.post('/api/projects/<pid>/scenes/approve')
def approve_scenes(pid):
 """Approve every scene that passes the same review export readiness applies.

 A scene that raises any export-readiness error is left unapproved and reported with
 its reasons. That keeps bulk approval honest: it cannot approve a beat whose media,
 timing or disclosure would block the export a moment later.
 """
 with LOCKS[pid]:
  editable(pid);p=load(pid);d=request.get_json(silent=True) or {}
  if d.get('attested') is not True:raise BadRequest('Confirm that you have reviewed the picture beats before approving them.')
  scope=str(d.get('scope') or 'ready')
  if scope not in ['ready','all']:raise BadRequest('Choose scope "ready" (default) or "all".')
  assets={x['id']:x for x in p['assets']};ranges=defaultdict(list);last=0
  approved=[];skipped=[]
  for i,scene in enumerate(p['scenes']):
   a=assets.get(scene['asset_id'])
   errors,_=scene_review(p,i,scene,a,ranges,last)
   if not errors:
    if not scene['approved']:
     scene['approved']=True;scene['approval_record']={'method':'bulk_scene_approval','scope':scope,'at':time.time(),'reasons':'media assigned, source recorded, timing valid, no blocking review item'}
     approved.append(i+1)
   else:
    scene['approved']=False;skipped.append({'scene':i+1,'srt_blocks':scene.get('srt_blocks'),'reasons':errors})
   last=scene['frame_out']
   if a and a['kind']=='video':
    end_s=scene['in_s']+(scene['frame_out']-scene['frame_in']+(scene.get('transition_handle_frames',4) if scene['transition']=='dissolve' else 0))/30
    ranges[a['id']].append((scene['in_s'],end_s,i+1))
  p.setdefault('scene_approval_history',[]).append({'action':'bulk_scene_approval','scope':scope,'approved':len(approved),'skipped':len(skipped),'at':time.time()})
  if scope=='all' and skipped:raise BadRequest(str(len(skipped))+' picture beat(s) cannot be approved yet. Nothing was approved; open the first one to see why: Scene '+str(skipped[0]['scene'])+' — '+skipped[0]['reasons'][0])
  save(p)
  result=public(p);result['bulk_approval']={'approved':approved,'skipped':skipped,'scope':scope}
  return jsonify(result)

@app.post('/api/projects/<pid>/scenes/<sid>/split')
def split_scene(pid,sid):
 with LOCKS[pid]:
  editable(pid);p=load(pid);i=next((i for i,s in enumerate(p['scenes']) if s['id']==sid),None)
  if i is None:raise NotFound('Scene not found.')
  a=p['scenes'][i];d=request.get_json() or {}
  try:offset=float(d.get('offset_s',0))
  except (ValueError,TypeError):raise BadRequest('Enter a valid split offset in seconds.')
  if not math.isfinite(offset):raise BadRequest('Enter a finite split offset.')
  frame=a['frame_in']+round(offset*30)
  if frame-a['frame_in']<15 or a['frame_out']-frame<15:raise BadRequest('Leave at least half a second on each side of a picture split.')
  b=copy.deepcopy(a);b['id']=uid();b['frame_in']=frame;a['frame_out']=frame
  words=a['narration'].split();at=max(1,min(len(words)-1,round(len(words)*offset/((b['frame_out']-a['frame_in'])/30))))
  a['narration']=' '.join(words[:at]);b['narration']=' '.join(words[at:]);a['approved']=b['approved']=False;a['transition']='cut';a['transition_handle_frames']=0;b['asset_id']=a['asset_id'];b['in_s']=a['in_s']+(frame-a['frame_in'])/30;p['manual_scene_edits']=True
  p['scenes'].insert(i+1,b);save(p);return jsonify(public(p))
@app.post('/api/projects/<pid>/scenes/<sid>/merge')
def merge_scene(pid,sid):
 with LOCKS[pid]:
  editable(pid);p=load(pid);i=next((i for i,s in enumerate(p['scenes']) if s['id']==sid),None)
  if i is None or i+1>=len(p['scenes']):raise BadRequest('Choose a scene with a following picture beat.')
  a,b=p['scenes'][i:i+2];a['frame_out']=b['frame_out'];a['narration']+=' '+b['narration'];a['srt_blocks']=[a['srt_blocks'][0],b['srt_blocks'][1]];a['transition']=b['transition'];a['transition_handle_frames']=b.get('transition_handle_frames',4 if b['transition']=='dissolve' else 0);a['approved']=False;a['notes']=' '.join(x for x in [a['notes'],b['notes']] if x)
  p['scenes'].pop(i+1);save(p);return jsonify(public(p))

def manifest(p):
 from production import reserve_id,asset_used
 root=folder(p['id']);w,h=output_size(p.get('format','landscape'),p.get('size'));assets={a['id']:a for a in p['assets']};shots=[];signatures=set()
 for i,s in enumerate(p['scenes']):
  a=assets[s['asset_id']];frames=s['frame_out']-s['frame_in'];m=motion(s,i);signature=tuple(m[k] for k in ['zoom_start','zoom_end','x_start','x_end','y_start','y_end'])
  while signature in signatures:m['x_end']+=.00001;signature=tuple(m[k] for k in ['zoom_start','zoom_end','x_start','x_end','y_start','y_end'])
  signatures.add(signature);extra=s.get('transition_handle_frames',4) if s['transition']=='dissolve' else 0
  shot={'shot':i+1,'kind':'narration','slot':None,'subject':s['focus'],'srt_blocks':s['srt_blocks'],'frame_in':s['frame_in'],'frame_out':s['frame_out'],'duration_frames':frames,'duration_ms':frames/30*1000,'timeline_in_ms':s['frame_in']/30*1000,'timeline_out_ms':s['frame_out']/30*1000,'voiceover_in_ms':s['frame_in']/30*1000,'voiceover_out_ms':s['frame_out']/30*1000,'narration_text':s['narration'],'source_id':reserve_id(a) or a.get('editorial_source_id') or ((a.get('document_plan') or {}).get('source_ids') or [a['id']])[0],'original_filename':a.get('original_name',a['name']),'source_role':'reserve' if reserve_id(a) else 'primary','source_file':Path(a['path']).name,'media_type':a['kind'],'video_in_ms':s['in_s']*1000,'video_out_ms':math.ceil((s['in_s']+(frames+extra)/30)*1000)+1,'motion_plan':m,'transition_handle_frames':extra,'image_approved':a['verified'],'match_note':s['notes'],'loop_required':False,'hold_last':False}
  if a['kind']=='image':
   out=root/'.cache/prepared_images'/f'{a["id"]}.jpg';out.parent.mkdir(parents=True,exist_ok=True)
   im=ImageOps.exif_transpose(Image.open(root/a['path'])).convert('RGB')
   if p.get('image_layout')=='full_bleed':
    ImageOps.fit(im,(w,h)).save(out,quality=95)
   else:
    bg=ImageEnhance.Brightness(ImageOps.fit(im,(w,h)).filter(ImageFilter.GaussianBlur(28))).enhance(.58);fg=ImageOps.contain(im,(int(w*.88),int(h*.86)));bg.paste(fg,((w-fg.width)//2,(h-fg.height)//2));bg.save(out,quality=95)
   shot['image']=out.relative_to(root).as_posix()
  if a['kind']=='video':shot['motion_plan'].update(mode='native_video',move='Preserve source camera and human action')
  shots.append(shot)
 result={'project':p['title'],'version':1,'editorial_approved':all(s['approved'] for s in p['scenes']),'captions_burned_in':False,'settings':{'width':w,'height':h,'fps':30,'caption_srt':'inputs/captions.srt','transition_frames':0},'shots':shots,'reserve_sources':[{'source_id':reserve_id(a),'original_filename':a.get('original_name',a['name']),'source_file':Path(a['path']).name,'status':'explicitly_selected' if asset_used(p,a) else 'held_in_reserve'} for a in p['assets'] if reserve_id(a) and not a.get('derived_from')]}
 write(root/'storyboard.json',result);return result
@app.post('/api/projects/<pid>/render')
def render(pid):
 with LOCKS[pid]:
  editable(pid);p=load(pid);final=(request.get_json(silent=True) or {}).get('final') is True;report=check(p,final)
  if report['errors']:return jsonify(report),400
  manifest(p);root=folder(pid);name=('final' if final else 'review')+'_'+time.strftime('%Y%m%d_%H%M%S')+'.mp4'
  job=RenderJob(root,on_change=lambda state:write(root/'render_status.json',state),story_path=root/'storyboard.json',catalog_path=root/'catalog.json',output_name=name);JOBS[pid]=job
  write(root/'reviews'/(Path(name).stem+'.json'),{'project':copy.deepcopy(p),'validation':report,'export_type':'final' if final else 'review','captions_burned_in':False,'created':time.time()})
  job.start(captions=False);return jsonify({'render':job.snapshot()}),202
@app.post('/api/projects/<pid>/cancel')
def cancel(pid):
 folder(pid)
 if pid in JOBS and busy(pid):JOBS[pid].cancel()
 return jsonify({'ok':True})
@app.get('/api/projects/<pid>/assets/<aid>/file')
def asset_file(pid,aid):
 p=load(pid);a=next((x for x in p['assets'] if x['id']==aid),None)
 if not a:raise NotFound('Asset not found.')
 return send_from_directory(folder(pid),a['path'],conditional=True)
@app.get('/api/projects/<pid>/narration')
def narration(pid):
 p=load(pid)
 if not p.get('voiceover'):raise NotFound('No narration imported.')
 return send_from_directory(folder(pid),p['voiceover']['path'],conditional=True)
@app.get('/api/projects/<pid>/exports/<name>')
def export_file(pid,name):
 if Path(name).name!=name:raise BadRequest('Invalid export name.')
 return send_from_directory(folder(pid)/'renders',name,conditional=True,as_attachment=request.args.get('download')=='1')
@app.get('/api/projects/<pid>/bundle.json')
def bundle(pid):
 response=jsonify(load(pid));response.headers['Content-Disposition']='attachment; filename="production_storyboard.json"';return response
@app.get('/api/projects/<pid>/assets/<aid>/planned-samples/<bid>')
def planned_samples(pid,aid,bid):
 from conform import samples
 p=load(pid);a=next((a for a in p['assets'] if a['id']==aid),None)
 if not a:raise NotFound('Media file not found.')
 cut=next((r for r in a.get('document_plan',{}).get('cuts',[]) if str(r['beat_id'])==bid),None)
 if not cut or a['kind'] not in ['video','image']:raise NotFound('Planned cut not found.')
 try:path=samples(folder(pid),a,cut)
 except Exception as e:raise BadRequest('Cannot inspect planned cut: '+str(e)[:250])
 return send_from_directory(path.parent,path.name,conditional=True)

@app.get('/api/projects/<pid>/cut-audit')
def download_cut_audit(pid):
 from cut_audit import audit
 import csv,io
 from flask import Response
 try:result=audit(load(pid))
 except ValueError as e:raise BadRequest('Cannot compare the SRT block map: '+str(e))
 if request.args.get('format')=='json':return jsonify(result)
 output=io.StringIO();fields=list(result['rows'][0]) if result['rows'] else ['scene','status'];writer=csv.DictWriter(output,fieldnames=fields);writer.writeheader();writer.writerows(result['rows'])
 return Response(output.getvalue(),mimetype='text/csv',headers={'Content-Disposition':'attachment; filename="cut-audit.csv"'})

def _shot_plan_prereqs(p):
 from shot_plan import _analysis
 if not p.get('srt'):raise BadRequest('Upload the authored SRT first; the plan is built from its blocks.')
 if not _analysis(folder(p['id'])) and any(a['kind']=='video' for a in p['assets']):raise BadRequest('The sources have not finished decoding yet. Run the build once, then open the plan builder.')

@app.get('/api/projects/<pid>/shot-index')
def shot_index_report(pid):
 from shot_plan import shots
 p=load(pid)
 if not any(a['kind']=='video' for a in p['assets']):raise BadRequest('Upload at least one video first.')
 index=shots(folder(pid),p['assets'])
 total=sum(len(e['shots']) for e in index)
 return jsonify({'sources':index,'shot_count':total,'scope':'Detected shot boundaries in decoded files. Nothing here says who or what a shot shows.'})

@app.get('/api/projects/<pid>/feasibility')
def feasibility_report(pid):
 from shot_plan import feasibility
 p=load(pid)
 blocks=request.args.get('blocks')
 scope=[int(x) for x in re.findall(r'\d+',blocks)] if blocks else None
 try:return jsonify(feasibility(p,folder(pid),scope))
 except ValueError as e:raise BadRequest('Feasibility: '+str(e))

@app.get('/api/projects/<pid>/candidates')
def candidate_report(pid):
 from shot_plan import candidates as candidate_list,shots_of_length
 p=load(pid)
 limit=min(int(request.args.get('limit',8) or 8),40)
 try:
  if request.args.get('length_s') is not None:
   length=float(request.args['length_s'])
   if not 0<length<3600:raise ValueError('Picture length must be between 0 and 3600 seconds.')
   handle=float(request.args.get('handle_s') or 0)
   if not 0<=handle<1.5:raise ValueError('Transition handle must be under 1.5 seconds.')
   return jsonify({'length_s':round(length,3),'needed_s':round(length+handle,3),'candidates':shots_of_length(folder(pid),p,length,handle,limit)})
  return jsonify(candidate_list(p,folder(pid),beat_id=request.args.get('beat'),limit=limit))
 except ValueError as e:raise BadRequest(str(e))

@app.get('/api/projects/<pid>/frame-qc')
def frame_qc_sheet(pid):
 from frame_qc import build
 p=load(pid)
 try:manifest=build(folder(pid),p,per_sheet=request.args.get('per_sheet',20),group=request.args.get('sheet',1))
 except ValueError as e:raise BadRequest(str(e))
 if request.args.get('format')=='json':
  return jsonify({k:v for k,v in manifest.items() if k!='file'}|{'url':f'/api/projects/{pid}/frame-qc/{manifest["file"]}'})
 return send_from_directory(folder(pid)/'.cache/frame_qc',manifest['file'],conditional=True)

@app.get('/api/projects/<pid>/frame-qc/<name>')
def frame_qc_file(pid,name):
 if not re.fullmatch(r'frame_qc_sheet_\d{2}_[0-9a-f]{24}\.jpg',name):raise NotFound('Sheet not found.')
 return send_from_directory(folder(pid)/'.cache/frame_qc',name,conditional=True)

@app.get('/api/projects/<pid>/block-map-template')
def block_map_template(pid):
 from shot_plan import prefill_csv,guidance
 from flask import Response
 p=load(pid)
 try:
  if request.args.get('format')=='json':return jsonify(guidance(p,folder(pid),parent_beat_min(1)))
  text=prefill_csv(p,parent_beat_min(1))
 except ValueError as e:raise BadRequest(str(e))
 return Response(text,mimetype='text/csv',headers={'Content-Disposition':'attachment; filename="block-map-template.csv"'})

def parent_beat_min(value):
 from edl import integer
 return integer(value,'beat')

@app.get('/api/projects/<pid>/flow-audit')
def download_flow_audit(pid):
 from picture_flow import csv_rows,apply_flow
 import csv,io
 from flask import Response
 p=load(pid);report=p.get('picture_flow') or apply_flow(copy.deepcopy(p),folder(pid),snap=False)
 if request.args.get('format')=='json':return jsonify(report)
 rows=csv_rows(report);output=io.StringIO();writer=csv.writer(output);writer.writerows(rows)
 return Response(output.getvalue(),mimetype='text/csv',headers={'Content-Disposition':'attachment; filename="flow-audit.csv"'})

def thumbnail_key(scene,asset,path):
 path=Path(path);stat=path.stat();key=hashlib.sha256(json.dumps([asset['id'],asset['path'],stat.st_size,stat.st_mtime_ns,round(scene['in_s']*1000000)]).encode()).hexdigest()[:24]
 return scene['id']+'_'+key+'.jpg'

@app.get('/api/projects/<pid>/scenes/<sid>/thumbnail')
def scene_thumbnail(pid,sid):
 p=load(pid);s=next((s for s in p['scenes'] if s['id']==sid),None)
 if not s:raise NotFound('Scene not found.')
 a=next((a for a in p['assets'] if a['id']==s['asset_id']),None)
 if not a:raise NotFound('Assign an asset first.')
 if a['kind']=='image':return send_from_directory(folder(pid),a['path'],conditional=True)
 cache=folder(pid)/'.cache/thumbnails';cache.mkdir(parents=True,exist_ok=True)
 source=folder(pid)/a['path'];out=cache/thumbnail_key(s,a,source)
 if not out.exists():
  from conform import capture
  with THUMBNAIL_SLOTS:
   if not out.exists():
    cap=capture(source)
    try:cap.set(cv2.CAP_PROP_POS_MSEC,s['in_s']*1000);ok,img=cap.read()
    finally:cap.release()
    if not ok:raise NotFound('This source frame could not be decoded.')
    tmp=out.with_name(out.stem+'.'+uid()+'.jpg');cv2.imwrite(str(tmp),cv2.resize(img,(320,180)));tmp.replace(out)
 return send_from_directory(cache,out.name,conditional=True)
@app.errorhandler(400)
@app.errorhandler(404)
@app.errorhandler(413)
def friendly(e):return jsonify({'error':str(e.description)}),e.code
@app.errorhandler(500)
def internal(e):return jsonify({'error':'The operation could not finish. Your saved project remains on disk. Check server diagnostics.'}),500
if __name__=='__main__':app.run(host=os.environ.get('STORYROOM_HOST','0.0.0.0'),port=int(os.environ.get('PORT','8003')),threaded=True,debug=False)
