from __future__ import annotations
import copy, hashlib, json, math, re, sys, threading, time, uuid, subprocess, os, shutil
from pathlib import Path
from collections import defaultdict
from functools import wraps
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
TRANSITION_POLICIES=['clean_cuts_only','allow_dissolves']
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
def project_io_lock(func):
 @wraps(func)
 def locked(pid,*args,**kwargs):
  with LOCKS[pid]:return func(pid,*args,**kwargs)
 return locked

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

def source_overlap_interval(scene, asset):
 """Use exact script boundaries for an unmodified strict shot; otherwise actual edit timing.

 The renderer's frame conformance still needs separate review. This avoids treating
 contiguous millisecond script ranges as overlaps solely due to 30-fps rounding.
 """
 start=float(scene.get('in_s') or 0)
 end=start+(scene['frame_out']-scene['frame_in']+(scene.get('transition_handle_frames',4) if scene.get('transition')=='dissolve' else 0))/30
 row=scene.get('plan_row') or {}
 if (row.get('strict_production') and scene.get('transition')=='cut' and
     asset.get('original_name',asset.get('name','')).casefold()==str(row.get('source_file','')).casefold() and
     row.get('source_in_ms') is not None and row.get('source_out_ms') is not None and
     abs(start-row['source_in_ms']/1000)<.001 and
     scene['frame_in']==round(row['timeline_in_ms']*.03) and scene['frame_out']==round(row['timeline_out_ms']*.03)):
  return start,row['source_out_ms']/1000
 return start,end

def scene_review(p,i,s,a,ranges,last,final=False):
 """Review one scene exactly as export readiness does. Bulk approval reuses this.

 Returns (errors, notes). `ranges` is the per-asset list of intervals already claimed
 by earlier scenes, so an overlap is caught the same way in both paths.
 """
 errors=[];notes=[];pre=f'Scene {i+1}: '
 if s['frame_in']!=last or s['frame_out']<=last:errors.append(pre+'timeline has a gap, overlap or empty scene.')
 if not a:return errors+[pre+'assign uploaded media.'],notes
 if not a['verified'] or not a['source'].strip():errors.append(pre+'confirm the asset’s identity/context and record its source.')
 person=s['person']
 # Earlier review drafts stored the whole Shows instruction as the person's name.
 # Interpret the explicit role/basis syntax without changing the saved draft or
 # granting identity verification; the photo still needs the user's label.
 if (s.get('plan_row') or {}).get('uploaded_photo') and person==(s.get('plan_row') or {}).get('shown_person'):
  from plain_script import shown_person_name
  person=shown_person_name(person)
 if person and person.casefold() not in [n.casefold().strip() for n in a['people']]:
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
 if a.get('derived_from'):errors.append(pre+'extracted frames are not permitted; use original uploaded media.')
 if not (folder(p['id'])/a['path']).is_file():errors.append(pre+'source file is missing from disk.')
 if a['kind']=='video':
  end_s=s['in_s']+(s['frame_out']-s['frame_in']+(s.get('transition_handle_frames',4) if s['transition']=='dissolve' else 0))/30
  if s['in_s']<0 or end_s>a['duration']+.001:errors.append(pre+'video is too short, including transition handles. No loop or frozen tail will be generated.')
  compare_in,compare_out=source_overlap_interval(s,a)
  for start,stop,number in ranges.get(a['id'],[]):
   if ((s.get('plan_row') or {}).get('strict_production') or not p.get('allow_archive_reuse')) and (s.get('plan_row') or {}).get('requested_treatment')!='ARCHIVAL REUSE' and compare_in<stop and compare_out>start:errors.append(pre+f'source interval overlaps scene {number}.')
 if p.get('transition_policy')=='clean_cuts_only' and s['transition']!='cut':errors.append(pre+'only clean cuts are enabled for this production.')
 if not s['focus'].strip():errors.append(pre+'set the story focus.')
 if final and not s['approved']:errors.append(pre+'editorial approval is pending.')
 if final and ((a.get('reuse_review') or {}).get('decision')!='approved' or not (a.get('reuse_review') or {}).get('details','').strip()):errors.append(pre+'record an explicit reuse-rights approval and supporting details for this source before final export.')
 return errors,notes

def check(p,final=False):
 errors=[];notes=[];assets={x['id']:x for x in p['assets']};ranges=defaultdict(list);last=0
 if p.get('review_only_draft'):
  rows=[s.get('plan_row') or {} for s in p['scenes']]
  supported=bool(rows) and all(r.get('plain_cue') for r in rows) and p.get('script_moving_only') and script_reading(p).get('ready_to_build') is True
  if final or not supported:errors.append('This is a script review draft, not a finished edit. Final export is blocked; complete the script and source checks before rendering.')
  else:
   notes.append('Review render only: script captions and SRT subtitles are burned in only if selected. Verify visual content, identity, still eligibility and reuse rights; final export remains blocked.')
   if p.get('voiceover'):
    audio_duration=p['voiceover'].get('duration',0);timeline_duration=p['scenes'][-1]['frame_out']/30
    if audio_duration>timeline_duration+.001:errors.append('Narration is longer than the picture timeline. Use matching audio; the review render must not trim narration.')
    elif audio_duration<timeline_duration-.001:notes.append(f'Narration is {timeline_duration-audio_duration:.3f} seconds shorter than the timeline; the review render pads the end with silence without stretching it.')
 from automatic import repetition_errors,source_capacity_errors,_plan_coverage
 errors.extend(repetition_errors(p));errors.extend(source_capacity_errors(p))
 # A block map written for a different runtime leaves blocks unplaced. Say so as a plan
 # problem with the numbers, rather than letting the picture come up short at the end.
 uncovered=_plan_coverage(p)
 if uncovered and p['scenes']:
  errors.append(str(len(uncovered))+' SRT block(s) are not placed by the block map (first is block '+str(uncovered[0])+'). Extend the map over the whole narration, or download a fresh template for this SRT.')
 if not p['brief'].strip() and not p.get('srt'):errors.append('Import your SRT story, or add a case brief.')
 if p.get('srt_removed') and p['scenes'] and not p.get('srt'):errors.append('The SRT was removed. Import an SRT and rebuild before exporting.')
 from production import document
 from edl import signature
 if p['scenes'] and (p.get('picture_plan') or p.get('assembly_signature')) and p.get('assembly_signature')!=signature(p):errors.append('Editing inputs changed. Rebuild the storyboard to apply the current SRT block map / production script.')
 if p.get('production_script') and p['scenes'] and p.get('edit_script_hash')!=hashlib.sha256(document(p).encode()).hexdigest():errors.append('Production script changed. Rebuild the storyboard to apply the new instructions.')
 # Narration is optional. A programme may be carried by its text alone; what is not
 # allowed is a narration track whose length disagrees with the picture it sits under.
 if not p['scenes']:errors.append('Build the storyboard from the imported SRT.' if p.get('srt') else 'Import an SRT and build the storyboard.')
 if p.get('voiceover') and p['scenes'] and abs(p['voiceover']['duration']-p['scenes'][-1]['frame_out']/30)>.25:errors.append('Narration duration must match the SRT timeline within 0.25 seconds. Correct the timing; do not silently trim the voiceover.')
 for i,s in enumerate(p['scenes']):
  a=assets.get(s['asset_id'])
  scene_errors,scene_notes=scene_review(p,i,s,a,ranges,last,final)
  row=s.get('plan_row') or {}
  if s.get('source_replacement_pending'):scene_errors.append(f'Scene {i+1}: review and approve the replaced source {s["source_replacement_pending"]} before export.')
  if row.get('strict_production'):
   if not s.get('approved'):scene_errors.append(f'Scene {i+1}: inspect the picture, identity, context and official source provenance before approval.')
   if s['frame_in']!=round(row['timeline_in_ms']*.03) or s['frame_out']!=round(row['timeline_out_ms']*.03) or s.get('transition')!='cut' or (a and abs(s.get('in_s',0)-row['source_in_ms']/1000)>.001):
    scene_errors.append(f'Scene {i+1}: edited timing or transition differs from the production script.')
   if a:
    from production import asset_matches
    if not asset_matches(a,row['source_file']):scene_errors.append(f'Scene {i+1}: selected source differs from the production script.')
   if row.get('requested_treatment') in ('STILL HOLD','EDITORIAL FREEZE-FRAME') and (s.get('script_camera') or {}).get('move')!='static':scene_errors.append(f'Scene {i+1}: still must remain a static hold.')
   if row.get('requested_treatment')=='CONTEXT VIDEO' and not s.get('context_only'):scene_errors.append(f'Scene {i+1}: context footage must remain labelled as context.')
  errors.extend(scene_errors);notes.extend(scene_notes)
  last=s['frame_out']
  if a and a['kind']=='video':
   end_s=s['in_s']+(s['frame_out']-s['frame_in']+(s.get('transition_handle_frames',4) if s['transition']=='dissolve' else 0))/30
   compare_in,compare_out=source_overlap_interval(s,a)
   ranges[a['id']].append((compare_in,compare_out,i+1))
 if any((s.get('plan_row') or {}).get('strict_production') for s in p['scenes']) and p['scenes']:
  still_frames=sum(s['frame_out']-s['frame_in'] for s in p['scenes'] if (assets.get(s.get('asset_id')) and (is_still(p,assets[s['asset_id']]) or (s.get('plan_row') or {}).get('native_static_interval'))))
  total=p['scenes'][-1]['frame_out']
  if total and still_frames/total>.1+1e-9:errors.append(f'Scheduled stills occupy {still_frames/total*100:.2f}% of the timeline, above the 10% limit. Review eligibility and replace only with appropriate footage.')
 flow=p.get('picture_flow') or {}
 from picture_flow import outstanding_notes
 notes.extend(outstanding_notes(flow))
 if not final and any(not s['approved'] for s in p['scenes']):notes.append('Review export only. Scene approval and rights review are required for final delivery.')
 return {'errors':errors,'notes':notes,'can_export':not errors,'final':final}

def is_plain_summary(text):
 return bool(re.search(r'^##\s+Storyboard Production Script\s*$',text,re.M|re.I))

def script_reading(p, include_shots=False):
 from document_plan import rows_for
 from production import asset_matches
 try:rows,mode=rows_for(p)
 except ValueError as e:return {'status':'error','detail':str(e)}
 if mode!='detailed_production_script':return {'status':'not_detailed'}
 missing=[];short=[];overlaps=[];used={}
 for row in rows:
  matches=[a for a in p['assets'] if asset_matches(a,row['source_file'])]
  if len(matches)!=1 or not (folder(p['id'])/matches[0]['path']).is_file():missing.append(row['source_file']);continue
  asset=matches[0]
  if asset.get('kind')=='video' and (row['source_in_ms']/1000>=float(asset.get('duration') or 0) or ((row['visual_status']=='VIDEO' or row.get('native_static_interval')) and row['source_out_ms']/1000>float(asset.get('duration') or 0)+.001)):short.append(row['shot_id'])
  if asset.get('kind')=='video' and row.get('source_in_ms') is not None and row.get('source_out_ms') is not None:
   for start,end,shot in used.get(asset['id'],[]):
    if row['source_in_ms']<end and row['source_out_ms']>start:
     overlaps.append({'shot':row['shot_id'],'earlier_shot':shot});break
   used.setdefault(asset['id'],[]).append((row['source_in_ms'],row['source_out_ms'],row['shot_id']))
 mismatches=sorted({b for row in rows if row.get('narration_mismatch') for b in row['srt_blocks']})
 build_issues=[]
 if missing:build_issues.append('Missing or ambiguous source files: '+', '.join(sorted(set(missing)))+'.')
 if short:build_issues.append('Source intervals exceed uploaded file duration: '+', '.join(short)+'.')
 if overlaps:build_issues.append('Source intervals overlap earlier shots: '+', '.join(x['shot']+' / '+x['earlier_shot'] for x in overlaps)+'.')
 if mismatches:build_issues.append('Script narration differs from the SRT in '+str(len(mismatches))+' cue(s).')
 if not p.get('voiceover'):build_issues.append('Upload the narration audio.')
 elif not p['voiceover'].get('path') or not (folder(p['id'])/p['voiceover']['path']).is_file():build_issues.append('The narration audio file is missing from disk.')
 elif abs(float(p['voiceover'].get('duration') or 0)-rows[-1]['timeline_out_ms']/1000)>.25:build_issues.append('Narration duration does not match the SRT timeline.')
 unsupported=[r['shot_id'] for r in rows if r.get('requested_treatment') in ('STILL HOLD','EDITORIAL FREEZE-FRAME') and not r.get('native_static_interval') and not r.get('uploaded_photo')]
 if unsupported:build_issues.append('Unsupported still or freeze instructions: '+', '.join(unsupported)+'. No frames will be extracted.')
 if policy(p)!='mixed' and any(r.get('uploaded_photo') for r in rows):build_issues.append('Enable moving footage and photographs for this project.')
 still_ms=sum(r['timeline_out_ms']-r['timeline_in_ms'] for r in rows if r.get('native_static_interval') or r.get('uploaded_photo'))
 if rows[-1]['timeline_out_ms'] and still_ms/rows[-1]['timeline_out_ms']>.1+1e-9:build_issues.append('Script-declared stills exceed the 10% limit.')

 result={'status':'read','shots':len(rows),'blocks':len({n for row in rows for n in row['srt_blocks']}),'duration_ms':rows[-1]['timeline_out_ms'],
         'source_count':len({r['source_file'] for r in rows}),'missing_files':sorted(set(missing)), 'short_shots':short,'overlap_shots':overlaps,'build_issues':build_issues,'ready_to_build':not build_issues,
         'native_static_ms':sum(r['timeline_out_ms']-r['timeline_in_ms'] for r in rows if r.get('native_static_interval')), 'photo_ms':sum(r['timeline_out_ms']-r['timeline_in_ms'] for r in rows if r.get('uploaded_photo')), 'photo_shots':sum(bool(r.get('uploaded_photo')) for r in rows), 'still_shots':sum(r.get('requested_treatment')=='STILL HOLD' for r in rows),'freeze_shots':sum(r.get('requested_treatment')=='EDITORIAL FREEZE-FRAME' for r in rows),
         'video_shots':sum(r.get('requested_treatment') in ('VIDEO','CONTEXT VIDEO','ARCHIVAL REUSE') for r in rows),
         'caption_shots':sum(bool(r.get('required_caption')) for r in rows),'narration_mismatch_blocks':mismatches,
         'blocker_count':sum(bool(r.get('import_blockers')) for r in rows)}
 if include_shots:
  result['shots_detail']=rows
  text=(p.get('production_script') or {}).get('text','')
  for key,title in (('project_instructions','Editor Assembly Instructions' if is_plain_summary(text) else 'Project instructions'),('final_checks','Final Coverage Summary' if is_plain_summary(text) else 'Final production checks')):
   section=re.search(r'^##\s+'+re.escape(title)+r'\s*$\n(.*?)(?=^##\s|\Z)',text,re.M|re.S|re.I)
   result[key]=section[1].strip() if section else ''
 return result

def public(p):
 d=copy.deepcopy(p)
 from caption_layout import caption_phrases
 d['caption_phrases']=caption_phrases((p.get('srt') or {}).get('blocks',[]))
 from document_plan import match_report
 d['source_match_report']=match_report(d)
 from footage_requirements import footage_requirements,photo_requirements
 d['footage_requirements']=footage_requirements(p);d['photo_requirements']=photo_requirements(p)
 if p.get('production_script'):
  from cue_script import is_cue_script
  from plain_script import is_plain_script
  if is_plain_script(p['production_script'].get('text','')) or is_cue_script(p['production_script'].get('text','')) or re.search(r'^###\s+Srt\d+-[A-Za-z]+\s*$',p['production_script'].get('text',''),re.M|re.I):d['script_reading']=script_reading(p)
 d['validation']=check(p);d['final_validation']=check(p,True)
 saved=folder(p['id'])/'render_status.json'
 d['render']=JOBS[p['id']].snapshot() if p['id'] in JOBS else json.loads(saved.read_text()) if saved.exists() else {'status':'idle','progress':0,'stage':'No export yet'}
 if p['id'] not in JOBS and d['render']['status'] in ['running','queued']:d['render'].update(status='interrupted',stage='Previous export interrupted; start a new export',progress=0)
 if d['render'].get('output'):
  d['render']['url']=f'/api/projects/{p["id"]}/exports/{d["render"]["output"]}'
  record=folder(p['id'])/'reviews'/(Path(d['render']['output']).stem+'.json')
  saved_record=json.loads(record.read_text()) if record.exists() else {}
  d['render']['current']=bool(saved_record) and saved_record.get('project',{}).get('updated')==p['updated']
  if saved_record:d['render']['options']={'script_captions':saved_record.get('script_captions_requested',saved_record.get('captions_burned_in',False)), 'srt_subtitles':saved_record.get('srt_subtitles_burned_in',False), 'enhance_picture':saved_record.get('picture_enhancement',False)}
 for i,scene in enumerate(d['scenes']):scene['motion_plan']=motion(scene,i)
 from production import reserve_id
 for a in d['assets']:
  a['url']=f'/api/projects/{p["id"]}/assets/{a["id"]}/file'+(f'?v={a["revision"]}' if a.get('revision') else '');a['reserve']=bool(reserve_id(a));a['source_label']=reserve_id(a) or Path(a.get('original_name',a['name'])).stem
 if d['voiceover']:
  voice_path=folder(p['id'])/d['voiceover']['path']
  revision=str(voice_path.stat().st_mtime_ns) if voice_path.is_file() else 'missing'
  d['voiceover']['url']=f'/api/projects/{p["id"]}/narration?v={revision}'
 from automatic import automatic_readiness
 d['automatic_readiness']=automatic_readiness(p)
 status_path=folder(p['id'])/'automatic_status.json'
 try:d['automatic_status']=json.loads(status_path.read_text()) if status_path.exists() else {'status':'idle','detail':''}
 except (OSError,ValueError):d['automatic_status']={'status':'idle','detail':''}
 # Persisted progress is not proof of a live thread after a server restart or crash.
 from automatic import _QUEUE_LOCK, _PENDING
 with _QUEUE_LOCK:auto_active=p['id'] in _PENDING
 if d['automatic_status'].get('status') in ('queued','working','studying','storyboard_ready','rendering') and not auto_active and not busy(p['id']):
  d['automatic_status']={'status':'interrupted','detail':'The previous automatic job stopped. Check the storyboard and export validation before retrying.'}
 from preview_render import status as preview_status
 d['preview']=preview_status(folder(p['id']),p['id'],p['updated'])
 if d['preview'].get('status')=='done':d['preview']['url']=f'/api/projects/{p["id"]}/continuous-preview?version={p["updated"]}'
 from scene_exports import status as scene_export_status
 d['scene_exports']=scene_export_status(folder(p['id']),p['id'],p['updated'])
 if d['scene_exports'].get('status')=='done':d['scene_exports']['url']=f'/api/projects/{p["id"]}/scene-exports/{d["scene_exports"]["output"]}'
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
  try:p=json.loads(f.read_text())
  except FileNotFoundError:continue  # A production may have been deleted while listing.
  result.append({k:p[k] for k in ['id','title','profile','format','updated']}|{'scenes':len(p['scenes']),'approved':sum(s['approved'] for s in p['scenes']),'portable':bool(p.get('portable_from_project')),'edit_mode':p.get('edit_mode','review')})
 return jsonify({'projects':sorted(result,key=lambda x:-x['updated']),'profiles':PROFILES})
@app.post('/api/projects')
def create():
 d=request.get_json() or {};title=str(d.get('title','')).strip()
 if not title or len(title)>100:raise BadRequest('Enter a production title of 1–100 characters.')
 mode=d.get('edit_mode','review')
 if mode not in ('review','documentary'):raise BadRequest('Choose either script review or documentary edit.')
 profile=d.get('profile') or 'documentary';fmt=d.get('format','landscape')
 if profile not in PROFILES or fmt not in ASPECTS:raise BadRequest('Choose a supported profile and format.')
 requested_size=d.get('size') or DEFAULT_SIZE.get(fmt)
 if not isinstance(requested_size,str) or not valid_size(requested_size):raise BadRequest('Choose an export size: '+', '.join(SIZES)+', or a pixel size such as 1920x1080.')
 pid=uid();root=PROJECTS/pid;root.mkdir()
 for name in ['inputs','inputs/semantic','sources','assets','renders','reviews']: (root/name).mkdir(parents=True,exist_ok=True)
 requested=d.get('picture_policy')
 if requested is not None and requested not in POLICIES:raise BadRequest('Choose a picture policy: '+', '.join(POLICIES))
 p={'id':pid,'schema_version':1,'edit_mode':mode,'picture_policy':requested or 'clips_only','transition_policy':'clean_cuts_only','repetition_policy':{'cooldown_slots':6,'source_interval_overlap_forbidden_within_window':True},'auto_assemble_uploads':True,'title':title,'profile':profile,'format':fmt,'size':requested_size,'brief':str(d.get('brief','')),'people':[],'assets':[],'scenes':[],'voiceover':None,'srt':None,'updated':time.time()}
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
@app.delete('/api/projects/<pid>')
def delete_project(pid):
 # The ID is validated by folder(), and all project writers/start operations use this lock.
 with LOCKS[pid]:
  root=folder(pid)
  if (request.get_json(silent=True) or {}).get('confirm') is not True:
   raise BadRequest('Confirm permanent deletion of the production and its files.')
  if busy(pid):raise BadRequest('This production is rendering. Wait for it to finish or cancel the render before deleting.')
  import automatic, preview_render, scene_exports, local_understanding
  with automatic._QUEUE_LOCK:
   if pid in automatic._PENDING:raise BadRequest('This production is assembling. Wait for it to finish before deleting.')
  for module,label in ((preview_render,'preview'),(scene_exports,'scene-clip export')):
   with module.GUARD:
    job=module.JOBS.get(pid)
    if job and job.get('status') in ('queued','running'):raise BadRequest('A '+label+' is being prepared. Wait for it to finish before deleting.')
  with local_understanding._GUARD:
   if any(Path(project)==root for project,target in local_understanding._RUNNING):raise BadRequest('Local media analysis is running. Wait for it to finish before deleting.')
  if root.is_symlink():raise BadRequest('Cannot delete a linked project folder. Remove it manually after checking its location.')
  try:shutil.rmtree(root)
  except OSError as e:
   app.logger.exception('Could not completely delete production %s',pid)
   raise BadRequest('Deletion could not finish; some project files may remain. Check the project folder and file permissions or close programs using them. '+str(e))
  JOBS.pop(pid,None)
  with preview_render.GUARD:preview_render.JOBS.pop(pid,None)
  with scene_exports.GUARD:scene_exports.JOBS.pop(pid,None)
  return jsonify({'deleted':True,'id':pid})

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
  response_extra={}
  if 'transition_policy' in d:
   if d['transition_policy'] not in TRANSITION_POLICIES:raise BadRequest('Choose a transition policy: '+', '.join(TRANSITION_POLICIES))
   if d['transition_policy']!=p.get('transition_policy'):
    p['transition_policy']=d['transition_policy']
    cleared=0
    if d['transition_policy']=='clean_cuts_only':
     # Turning dissolves off has to mean something for the scenes that already carry them.
     # Clearing them is the only consistent reading of "clean cuts only", and the response
     # says how many so a caller is never left guessing where its dissolves went. It is
     # reported rather than saved: it describes this request, not the production.
     for scene in p['scenes']:
      if scene.get('transition')!='cut':scene['transition']='cut';scene['transition_handle_frames']=0;cleared+=1
    p.pop('dissolves_cleared',None)
    response_extra={'dissolves_cleared':cleared}
  if 'picture_scale' in d:
   scale=d['picture_scale']
   if isinstance(scale,bool) or not isinstance(scale,int) or not 100<=scale<=200:raise BadRequest('Picture scale must be a whole percentage from 100 to 200.')
   if scale!=p.get('picture_scale',100):p['picture_scale']=scale;p['picture_rebuilt']=False
  if 'size' in d:
   if not isinstance(d['size'],str) or not valid_size(d['size']):raise BadRequest('Choose an export size: '+', '.join(SIZES)+', or a pixel size such as 1920x1080.')
   if str(d['size'])!=str(p.get('size')):
    p['size']=str(d['size'])
    p['picture_rebuilt']=False
  if p['brief']!=old_brief or p['people']!=old_people:
   for scene in p['scenes']:scene['approved']=False
  save(p);body=public(p)
  if response_extra:body.update(response_extra)
  return jsonify(body)
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
   (root/'inputs/captions.srt').write_text(text);p['srt']={'blocks':blocks,'name':f.filename};p['scenes']=[];p.pop('srt_recovery',None);p.pop('srt_removed',None)
  elif kind=='srt_block_map':
   from edl import parse,srt_signature
   expected_srt=request.form.get('expected_srt_signature')
   if expected_srt and (not p.get('srt') or expected_srt!=srt_signature(p)):
    raise BadRequest('The SRT changed while you were editing the block map. Reopen the editor to review the current SRT.')
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
    p['srt']={'blocks':recovered,'name':f.filename+' · narration table','recovered_from':f.filename};p.pop('srt_removed',None)
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
   photo=ext in ['.jpg','.jpeg','.png','.webp'];name=Path(f.filename).name
   # Only identical video filenames replace each other; source numbers alone are not identities.
   matches=[] if photo else [a for a in p['assets'] if a.get('kind')=='video' and not a.get('derived_from') and str(a.get('original_name') or a.get('name') or '').casefold()==name.casefold()]
   replacement=None;old_paths=[]
   if matches:
    ids={a['id'] for a in matches};related=[a for a in p['assets'] if (a.get('derived_from') or {}).get('asset_id') in ids]
    removable(pid,ids|{a['id'] for a in related})
    import preview_render,scene_exports
    # A job may be rendering an older project revision but still reading these paths.
    with preview_render.GUARD: preview_busy=(preview_render.JOBS.get(pid) or {}).get('status') in ('running','queued')
    with scene_exports.GUARD: scene_export_busy=(scene_exports.JOBS.get(pid) or {}).get('status') in ('running','queued')
    if preview_busy or scene_export_busy:raise BadRequest('Wait for the current preview or scene export to finish before replacing footage.')
   aid=matches[0]['id'] if matches else uid();dest=root/('assets' if photo else 'sources')/(uid()+ext);update(pid,key,'saving')
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
   asset={'id':aid,'name':name,'original_name':name,'path':dest.relative_to(root).as_posix(),'kind':'image' if photo else 'video','duration':duration,'source':'','people':[],'category':'general','verified':False,'rights':'unreviewed','notes':'','revision':uid()}
   if matches:
    duplicate_ids={a['id'] for a in matches};derived_ids={a['id'] for a in related};affected=0;unassigned=0
    # Derived images came from the old bytes: preserve them, but never keep them on the timeline as if they came from the replacement.
    for item in related:
     item['verified']=False;item['derived_from']['source_replaced']=True
     if item.get('approval_record'):item['approval_record']['active']=False
    for scene in p['scenes']:
     old_id=scene.get('asset_id')
     if old_id not in duplicate_ids|derived_ids:continue
     affected+=1;scene['approved']=False;scene['source_replacement_pending']=name;scene.pop('thumbnail',None)
     length=(scene['frame_out']-scene['frame_in']+(scene.get('transition_handle_frames',0) if scene.get('transition')=='dissolve' else 0))/30
     if old_id in derived_ids or float(scene.get('in_s') or 0)+length>duration+.001:
      scene['review_previous_asset_id']=old_id;scene['asset_id']=None;unassigned+=1
      scene['replacement_issue']='The replacement video is too short for this shot, or this shot used a frame derived from the previous upload.'
     else:scene['asset_id']=aid;scene.pop('replacement_issue',None)
    p['assets']=[a for a in p['assets'] if a['id'] not in duplicate_ids]+[asset]
    p['source_bindings']={key:(aid if value in duplicate_ids else value) for key,value in (p.get('source_bindings') or {}).items()}
    kept={a.get('path') for a in p['assets']};old_paths=[root/a['path'] for a in matches if a.get('path') and a['path'] not in kept]
    from local_understanding import result_path
    old_paths += [result_path(root,x) for x in duplicate_ids|derived_ids]
    replacement={'name':name,'affected_scenes':affected,'unassigned_scenes':unassigned,'replaced_count':len(matches)}
   else:p['assets'].append(asset)
  else:raise BadRequest('Unknown import type.')
  if kind=='asset':
   from approval import auto_approve_upload
   auto_approve_upload(p,p['assets'][-1])
  if kind in ['srt','production_script']:
   from production import infer
   p['profile']=infer(p)['profile']
  update(pid,key,'indexing')
  try:save(p)
  except Exception:
   if kind=='asset':dest.unlink(missing_ok=True)
   raise
  replacement_warnings=remove_local_files(root,old_paths) if kind=='asset' and old_paths else []
  from automatic import maybe_queue
  maybe_queue(sys.modules[__name__],p)
  # Automatic analysis only when explicitly enabled; it never delays the upload or
  # changes the authored SRT/storyboard. The manual control works independently.
  if os.environ.get('STORYROOM_UNDERSTANDING_AUTO','0')=='1':
   try:
    from local_understanding import enqueue
    if kind=='voiceover':enqueue(root,'narration',root/p['voiceover']['path'],'audio',p['voiceover']['duration'])
    elif kind=='asset' and not photo:enqueue(root,aid,dest,'video',duration)
   except Exception as e:app.logger.warning('Could not queue local understanding: %s',e)
  result=public(p)
  if kind=='asset' and replacement:result['replaced_video']=replacement;result['replacement_warnings']=replacement_warnings
  result=jsonify(result);update(pid,key,'done');return result
@app.delete('/api/projects/<pid>/srt-block-map')
def remove_block_map(pid):
 with LOCKS[pid]:
  editable(pid);p=load(pid);p.pop('srt_block_map',None);(folder(pid)/'inputs/srt_block_map.json').unlink(missing_ok=True)
  for scene in p['scenes']:scene['approved']=False
  save(p);return jsonify(public(p))

def removable(pid, targets=()):
 editable(pid)
 from automatic import _QUEUE_LOCK, _PENDING
 with _QUEUE_LOCK:
  if pid in _PENDING:raise BadRequest('Wait for automatic assembly or rendering to finish before removing files.')
 from local_understanding import _GUARD, _RUNNING
 with _GUARD:
  if any((str(folder(pid)),target) in _RUNNING for target in targets):
   raise BadRequest('Wait for local analysis of this file to finish before removing it.')

def remove_local_files(root, paths):
 warnings=[]
 for path in paths:
  try:
   path=Path(path).resolve()
   if not path.is_relative_to(root.resolve()):raise ValueError('File is outside this project')
   path.unlink(missing_ok=True)
  except (OSError,ValueError) as e:warnings.append('Could not remove a local file: '+str(e))
 return warnings

@app.delete('/api/projects/<pid>/assets/<aid>')
def delete_asset(pid,aid):
 with LOCKS[pid]:
  p=load(pid);root=folder(pid);asset=next((a for a in p['assets'] if a['id']==aid),None)
  if not asset:raise NotFound('Media not found.')
  related=[asset]+[a for a in p['assets'] if (a.get('derived_from') or {}).get('asset_id')==aid]
  ids={a['id'] for a in related};removable(pid,ids)
  d=request.get_json(silent=True) or {}
  if d.get('confirm') is not True:raise BadRequest('Confirm removal of this file.')
  affected=[s for s in p['scenes'] if s.get('asset_id') in ids]
  if (affected or len(related)>1) and d.get('confirm_used') is not True:
   raise BadRequest('Confirm removal of the source and any extracted frames; affected storyboard scenes will become unassigned.')
  remaining=[a for a in p['assets'] if a['id'] not in ids]
  remaining_paths={a.get('path') for a in remaining}
  paths=[root/a['path'] for a in related if a.get('path') and a['path'] not in remaining_paths]
  from local_understanding import result_path
  paths += [result_path(root, a['id']) for a in related]
  p['assets']=remaining
  p['source_bindings']={name:asset_id for name,asset_id in (p.get('source_bindings') or {}).items() if asset_id not in ids}
  for scene in affected:scene['asset_id']=None;scene['approved']=False
  save(p)
  warnings=remove_local_files(root,paths)
  result=public(p);result['removal_warnings']=warnings
  return jsonify(result)

@app.delete('/api/projects/<pid>/inputs/<kind>')
def delete_input(pid,kind):
 if kind not in ('voiceover','srt','production_script'):raise NotFound('Input not found.')
 with LOCKS[pid]:
  p=load(pid);root=folder(pid);removable(pid,['narration'] if kind=='voiceover' else [])
  d=request.get_json(silent=True) or {}
  if d.get('confirm') is not True:raise BadRequest('Confirm removal of this file.')
  if not p.get(kind):raise NotFound('Input not found.')
  if p['scenes'] and d.get('confirm_used') is not True:raise BadRequest('Confirm removal: the existing storyboard will need review or rebuilding.')
  paths=[]
  if kind=='voiceover':
   paths.append(root/p['voiceover']['path'])
   from local_understanding import result_path
   paths.append(result_path(root,'narration'))
  elif kind=='srt':
   paths.append(root/'inputs/captions.srt');p.pop('srt_recovery',None);p['srt_removed']=True
  else:paths.append(root/'inputs/production_script.txt')
  p[kind]=None
  for scene in p['scenes']:scene['approved']=False
  save(p)
  warnings=remove_local_files(root,paths)
  result=public(p);result['removal_warnings']=warnings
  return jsonify(result)

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
  if not name or len(name)>200 or '/' in name or chr(92) in name or Path(name).suffix.lower() not in ['.mp4','.mov','.jpg','.jpeg','.png','.webp']:raise BadRequest('Use a script filename such as S01.mp4, not a path or source label.')
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

@app.put('/api/projects/<pid>/assets/<aid>/reuse-rights')
def set_asset_reuse_rights(pid,aid):
 """Record the user's specific decision; never infer ownership or legal clearance."""
 with LOCKS[pid]:
  editable(pid);p=load(pid);a=next((x for x in p['assets'] if x['id']==aid),None)
  if not a:raise NotFound('Asset not found.')
  d=request.get_json(silent=True) or {};decision=d.get('decision');basis=d.get('basis','');details=str(d.get('details') or '').strip()
  if decision not in ('unreviewed','approved','not_approved'):raise BadRequest('Choose a reuse-rights decision.')
  if len(details)>5000:raise BadRequest('Rights details must be at most 5,000 characters.')
  if decision=='approved':
   if d.get('attested') is not True:raise BadRequest('Confirm your reuse-rights decision for this file.')
   if basis not in ('permission','license','public_domain','other'):raise BadRequest('Choose the basis for your reuse decision.')
   if not details:raise BadRequest('Add the source, permission, license or other basis and any limits for this specific file.')
  else:basis=''
  if a.get('reuse_review'):a.setdefault('reuse_review_history',[]).append(copy.deepcopy(a['reuse_review']))
  a['reuse_review']={'decision':decision,'basis':basis,'details':details,'at':time.time(),'attested':decision=='approved','independent_verification':False}
  for scene in p['scenes']:
   if scene.get('asset_id')==aid:scene['approved']=False
  save(p);return jsonify(public(p))

@app.put('/api/projects/<pid>/assets/reuse-rights')
def set_all_asset_reuse_rights(pid):
 """Apply one expressly attested decision to the exact set shown to the user."""
 with LOCKS[pid]:
  editable(pid);p=load(pid);d=request.get_json(silent=True) or {};assets=p['assets'];ids=d.get('asset_ids')
  if not assets:raise BadRequest('There are no uploaded sources to review.')
  if not isinstance(ids,list) or len(ids)!=len(assets) or any(not isinstance(i,str) for i in ids) or set(ids)!={a['id'] for a in assets}:raise BadRequest('The uploaded sources changed. Refresh and review the list before approving.')
  basis=d.get('basis','');details=str(d.get('details') or '').strip()
  if d.get('attested') is not True:raise BadRequest('Confirm that your decision applies to every listed file.')
  if basis not in ('permission','license','public_domain','other'):raise BadRequest('Choose the basis for your reuse decision.')
  if not details or len(details)>5000:raise BadRequest('Provide supporting details and limits, up to 5,000 characters.')
  now=time.time()
  for a in assets:
   if a.get('reuse_review'):a.setdefault('reuse_review_history',[]).append(copy.deepcopy(a['reuse_review']))
   a['reuse_review']={'decision':'approved','basis':basis,'details':details,'at':now,'attested':True,'independent_verification':False}
  for scene in p['scenes']:
   if scene.get('asset_id') in ids:scene['approved']=False
  save(p);return jsonify(public(p))

@app.patch('/api/projects/<pid>/assets/<aid>')
def update_asset(pid,aid):
 with LOCKS[pid]:
  editable(pid);p=load(pid);a=next((x for x in p['assets'] if x['id']==aid),None)
  if not a:raise NotFound('Asset not found.')
  d=request.get_json() or {}
  if 'source' in d and str(d['source'])[:5000]!=a.get('source','') and a.get('reuse_review'):
   a.setdefault('reuse_review_history',[]).append(copy.deepcopy(a['reuse_review']))
   a['reuse_review']={'decision':'unreviewed','basis':'','details':'Source provenance changed; review reuse again.','at':time.time(),'attested':False,'independent_verification':False}
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

@app.post('/api/projects/<pid>/script-draft')
def script_draft(pid):
 """Display script shots for review; never claim unsupported instructions are rendered."""
 from import_status import token,update
 key=token(request.headers.get('X-Upload-ID',''))
 update(pid,key,'reading_script',detail='Reading the production script and matching each shot to its SRT cue.')
 with LOCKS[pid]:
  editable(pid);p=load(pid)
  payload=request.get_json(silent=True) or {}
  if p.get('scenes') and not (payload.get('replace_review') is True and p.get('review_only_draft') and not p.get('manual_scene_edits')):
   raise BadRequest('Existing storyboard was not replaced. Only an unmodified review draft can be rebuilt; manual edits must be preserved.')
  from document_plan import rows_for
  from edl import assemble,signature
  from conform import stills
  try:rows,mode=rows_for(p)
  except ValueError as e:raise BadRequest('Production script: '+str(e))
  if mode!='detailed_production_script' or not rows:raise BadRequest('A recognized detailed production script and matching SRT are required.')
  update(pid,key,'checking_sources',detail=f'Checked {len(rows)} cue instructions. Matching filenames and source ranges to uploaded footage.')
  review=script_reading(p)
  if payload.get('require_ready') is True and not review.get('ready_to_build'):
   raise BadRequest('Cannot build until all input checks pass: '+(' '.join(review.get('build_issues') or [review.get('detail') or 'Read the production script and SRT first.'])))
  moving_only=(request.get_json(silent=True) or {}).get('moving_only') is True
  if (review.get('missing_files') or review.get('short_shots')) and not moving_only:raise BadRequest('The review draft cannot be made: source files are missing, ambiguous or too short. No footage was substituted.')
  seeds=[];last=0
  for block in p['srt']['blocks']:
   end=round(block['end_ms']*.03)
   seeds.append(dict(id=uid(),srt_blocks=[block['number'],block['number']],frame_in=last,frame_out=end,narration=block['text'],purpose='script review',emotion='neutral',focus='Script review',category='general',person='',asset_id=None,in_s=0,motion='auto',transition='cut',context_only=False,notes='',approved=False,profile=p['profile'],focus_position='center',motion_strength='subtle'))
   last=end
  draft_rows=copy.deepcopy(rows)
  for row in draft_rows:row['review_warnings']=row.pop('import_blockers',[])
  moving_only=(request.get_json(silent=True) or {}).get('moving_only') is True
  # Never extract still frames: an explicit uploaded photo is a separate static source.
  prepared=draft_rows
  for row in prepared:
   if row.get('native_static_interval'):
    row['native_static_review']=True
   elif row.get('uploaded_photo'):
    pass
   elif row.get('requested_treatment') in ('STILL HOLD','EDITORIAL FREEZE-FRAME'):
    row['import_blockers']=['Only explicitly named uploaded photographs or native static intervals can be shown; no frame was extracted or substituted.']
  original_plan=p.get('picture_plan');had_plan='picture_plan' in p;reuse=p.get('allow_archive_reuse');had_reuse='allow_archive_reuse' in p
  p['picture_plan']={'beats':prepared};p['allow_archive_reuse']=False;p['_review_root']=str(folder(pid))
  update(pid,key,'assembling',detail=f'Preparing {len(rows)} timeline shots from the explicit source ranges; unresolved shots stay empty.')
  try:assemble(p,seeds)
  except ValueError as e:raise BadRequest('Could not prepare review draft: '+str(e))
  finally:
   p.pop('_review_root',None)
   if had_plan:p['picture_plan']=original_plan
   else:p.pop('picture_plan',None)
   if had_reuse:p['allow_archive_reuse']=reuse
   else:p.pop('allow_archive_reuse',None)
  p['review_only_draft']=True
  if moving_only:
   p['script_moving_only']=True
   for scene in p['scenes']:
    row=scene.get('plan_row') or {}
    if row.get('requested_treatment') in ('STILL HOLD','EDITORIAL FREEZE-FRAME') and not row.get('native_static_interval') and not row.get('uploaded_photo'):scene['missing_video_out']=True
  p['script_application']['mode']='moving_video_review' if moving_only else 'script_review_draft'
  p['script_application']['note']='Review draft only: caption layout, framing instructions and exact freeze frames are not verified or rendered. Names, uploaded SRT and script remain unchanged. Export is blocked.'
  p['edit_script_hash']=hashlib.sha256((p.get('production_script') or {}).get('text','').encode()).hexdigest()
  p['assembly_signature']=signature(p)
  update(pid,key,'saving_storyboard',detail='Saving the review timeline and its unresolved-shot report.')
  save(p);result=jsonify(public(p));update(pid,key,'done',detail=f'Review timeline saved: {p["script_application"]["placed_scenes"]} of {len(p["scenes"])} shots assigned.');return result

@app.post('/api/projects/<pid>/native-static-review')
def update_native_static_review(pid):
 """Enable explicit native intervals in an existing untouched review, without rebuilding it."""
 with LOCKS[pid]:
  editable(pid);p=load(pid)
  if not p.get('review_only_draft') or not p.get('script_moving_only') or p.get('manual_scene_edits'):
   raise BadRequest('An unmodified moving-video review is required; existing edits were not changed.')
  if p.get('edit_script_hash')!=hashlib.sha256((p.get('production_script') or {}).get('text','').encode()).hexdigest():
   raise BadRequest('The production script changed; existing shots were not changed.')
  from document_plan import rows_for
  from production import matching_assets
  try:rows,mode=rows_for(p)
  except ValueError as e:raise BadRequest('Production script: '+str(e))
  if mode!='detailed_production_script':raise BadRequest('The script cannot be safely read.')
  by_shot={r['shot_id']:r for r in rows};placed=[];unresolved=[]
  for scene in p['scenes']:
   old=scene.get('plan_row') or {};row=by_shot.get(old.get('shot_id'))
   if not row or not row.get('native_static_interval'):continue
   if scene.get('asset_id') or not scene.get('missing_video_out') or any(old.get(k)!=row.get(k) for k in ('shot_id','source_file','source_in_ms','source_out_ms','timeline_in_ms','timeline_out_ms')):
    unresolved.append(row['shot_id']+': existing shot differs or is already assigned; left unchanged.');continue
   matches=matching_assets(dict(p,source_bindings={}),row['source_file'])
   if len(matches)!=1 or matches[0].get('kind')!='video' or not (folder(pid)/matches[0]['path']).is_file():
    unresolved.append(row['shot_id']+': exact source missing or ambiguous.');continue
   asset=matches[0];start=row['source_in_ms']/1000;end=row['source_out_ms']/1000
   if end>float(asset.get('duration') or 0)+.001:
    unresolved.append(row['shot_id']+': source video is too short.');continue
   if any(other is not scene and other.get('asset_id')==asset['id'] and start < float((other.get('plan_row') or {}).get('source_out_ms',0))/1000 and end > float((other.get('plan_row') or {}).get('source_in_ms',0))/1000 for other in p['scenes']):
    unresolved.append(row['shot_id']+': source interval overlaps another assigned shot.');continue
   old.update(native_static_interval=True,native_static_review=True)
   old['review_warnings']=[x for x in old.get('review_warnings',[]) if 'omitted from moving-only' not in x]+['Native static interval: count toward still allowance; identity, eligibility and rights need review.']
   scene['asset_id']=asset['id'];scene['in_s']=start;scene['missing_video_out']=False;scene['approved']=False
   scene['notes']+=' Native static interval is played directly for review; still eligibility, budget, identity and rights are unverified.'
   placed.append(row['shot_id'])
  info=p.get('script_application') or {};info['placed_scenes']=sum(bool(s.get('asset_id')) for s in p['scenes'])
  info['issues']=[issue for issue in info.get('issues',[]) if not any(issue.startswith('Beat '+sid+':') for sid in placed)]
  p['script_application']=info
  if placed:save(p)
  result=public(p);result['native_static_update']={'placed':placed,'unresolved':unresolved,'note':'Native intervals remain unverified still candidates, not genuine motion.'}
  return jsonify(result)

@app.post('/api/projects/<pid>/moving-review')
def moving_review(pid):
 """Remove stills from the review timeline; never invent missing source OUTs."""
 with LOCKS[pid]:
  editable(pid);p=load(pid)
  if not p.get('review_only_draft') or not p.get('scenes'):
   raise BadRequest('Create a detailed script review timeline first; this action cannot replace an existing edit.')
  if p.get('manual_scene_edits'):
   raise BadRequest('This review timeline has manual edits. They were not replaced; review the source ranges first.')
  assets={a['id']:a for a in p['assets']};missing=[];moving=0
  for scene in p['scenes']:
   row=scene.get('plan_row') or {};asset=assets.get(scene.get('asset_id'))
   if row.get('requested_treatment') in ('STILL HOLD','EDITORIAL FREEZE-FRAME') and not row.get('native_static_interval'):
    if scene.get('asset_id'):scene['review_previous_asset_id']=scene['asset_id']
    scene['asset_id']=None;scene['in_s']=0;scene['approved']=False;scene.pop('script_camera',None)
    scene['missing_video_out']=True;missing.append(row.get('shot_id') or scene.get('plan_row_id'))
   elif (row.get('requested_treatment')=='VIDEO' or row.get('native_static_interval')) and asset and asset.get('kind')=='video':
    scene['missing_video_out']=False;moving+=1
   else:
    scene['asset_id']=None;scene['approved']=False;scene['missing_video_out']=True;missing.append(row.get('shot_id') or scene.get('plan_row_id'))
  p['script_moving_only']=True
  p['script_application'].update(mode='moving_video_review',placed_scenes=moving,
   issues=[str(len(missing))+' shots specify a single still/frame, not a video IN/OUT range. Moving-video IN and OUT times are needed; no video segment was guessed.'],
   note='Moving-video review only: no loops, no still holds and no invented source ranges. The original script, SRT, filenames and narration are unchanged; captions/layout remain unimplemented and export is blocked.')
  save(p);return jsonify(public(p))

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
  from document_plan import rows_for
  try:preflight_rows,preflight_mode=rows_for(p)
  except ValueError as e:raise BadRequest('Production plan: '+str(e))
  if preflight_mode=='detailed_production_script':
   blockers=[f"{r['shot_id']}: {issue}" for r in preflight_rows for issue in r.get('import_blockers',[])]
   if blockers:raise BadRequest('Detailed script needs attention: '+' '.join(blockers[:8]))
  from automatic import restore_reviewed,full_source_analysis,carry_previous_review
  previous=copy.deepcopy(p.get('scenes') or [])
  full_source_analysis(p,folder(pid))
  # A cached timeline may only stand in for a whole rebuild. On a block-range rebuild it
  # would replace the entire storyboard with the cached one, so a request that named three
  # blocks would silently rebuild (and resequence) all of them.
  if not scope and preflight_mode!='detailed_production_script' and restore_reviewed(p,folder(pid)):
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
   # Prepare original photographs only. Never create a still from a video frame.
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
   if plan_mode=='detailed_production_script' and scope:raise BadRequest('Detailed shot scripts require a whole-timeline rebuild; partial rebuilding could change shot order.')
   if plan_mode=='detailed_production_script' and policy(p)!='mixed' and any(r.get('visual_status')=='STILL' for r in decisions):raise BadRequest('This script requests still holds or freeze-frames. Allow photographs and stills, or revise the script; no substitute was chosen.')
   still_plan=stills(p,folder(pid),copy.deepcopy(decisions))
   extracted=sum(bool(a.get('derived_from')) for a in p['assets'])
   if plan_mode!='detailed_production_script':
    still_plan=native_rows(p,still_plan)
    p['allow_archive_reuse']=True
   else:p['allow_archive_reuse']=False
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
  if plan_mode=='detailed_production_script':
   p['picture_flow']={'note':'Exact script timings retained; no automatic source conform or breath snapping.'}
  else:p['picture_flow']=apply_flow(p,folder(pid))
  from automatic import validate_assembly
  decoded=validate_assembly(p,folder(pid));write(folder(pid)/'reviews/Automatic_frame_checks.json',decoded)
  if decoded['errors']:raise BadRequest('Automatic assembly stopped before saving an empty or undecodable timeline: '+' '.join((p.get('script_application') or {}).get('issues',[])[:3]+decoded['errors'][:3]))
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
  if plan_mode=='detailed_production_script':p.pop('review_only_draft',None)
  carried=carry_previous_review({'scenes':previous},p['scenes'])
  p['rebuild_report']={'at':time.time(),'beats':len(p['scenes']),'approvals_carried':carried['carried'],'needs_review':carried['requiring_review'],'scope':scope,'previous_scenes':len(previous),'rounding_frames':(spliced['rounding_frames'] if scope else 0),'cut_surfaces_adopted':adopted_surfaces}
  save(p);return jsonify(public(p))
@app.patch('/api/projects/<pid>/scenes/<sid>')
def edit_scene(pid,sid):
 with LOCKS[pid]:
  editable(pid);p=load(pid);s=next((s for s in p['scenes'] if s['id']==sid),None)
  if not s:raise NotFound('Scene not found.')
  d=request.get_json() or {}
  if p.get('script_moving_only'):
   if d.get('asset_id',s.get('asset_id'))!=s.get('asset_id'):
    raise BadRequest('The moving-video review uses only explicit script source intervals. Change the production script with an explicit IN and OUT, then rebuild; no substitute or timing guess was applied.')
  p['manual_scene_edits']=True
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
   moved_in_point=abs(n-float(s.get('in_s') or 0))>.001
   if p.get('script_moving_only') and moved_in_point:raise BadRequest('The moving-video review uses the script source IN; edit the script and rebuild instead.')
   s['in_s']=n
  if d.get('motion') in ['auto','push','pull','pan-left','pan-right','rise','reveal']:
   if d['motion']!='auto' and d['motion']!=s.get('motion'):s.pop('script_camera',None)
   s['motion']=d['motion']
  if d.get('focus_position') in ['left','center','right']:s['focus_position']=d['focus_position']
  if d.get('motion_strength') in ['subtle','purposeful','strong']:s['motion_strength']=d['motion_strength']
  if d.get('transition') in ['cut','dissolve']:
   # A dissolve is refused while the production is clean-cuts-only rather than silently
   # rewritten to a cut. Silently forcing it meant the request appeared to succeed and the
   # scene then validated as a cut, so the policy error it should have produced could never
   # be reached and there was nothing to explain why the dissolve never appeared.
   if d['transition']=='dissolve' and p.get('transition_policy')=='clean_cuts_only':
    raise BadRequest('This production uses clean cuts only. Allow dissolves for it before setting a dissolve on a scene.')
   s['transition']=d['transition'];s['transition_handle_frames']=(s.get('transition_handle_frames') or 4) if s['transition']=='dissolve' else 0
  if 'context_only' in d:s['context_only']=d['context_only'] is True
  s['approved']=d.get('approved',False) is True
  if s['approved'] and s.get('asset_id'):
   s.pop('source_replacement_pending',None);s.pop('replacement_issue',None)
  if moved_in_point:s.pop('picture_flow',None)  # an explicit human in-point overrides the automatic conform
  save(p);return jsonify(public(p))

@app.patch('/api/projects/<pid>/scenes/<sid>/picture-scale')
def edit_scene_picture_scale(pid,sid):
 with LOCKS[pid]:
  editable(pid);p=load(pid);scene=next((s for s in p['scenes'] if s['id']==sid),None)
  if scene is None:raise NotFound('Scene not found.')
  data=request.get_json(silent=True) or {}
  if 'picture_scale' not in data:raise BadRequest('Specify a picture scale or null to use the project scale.')
  value=data['picture_scale']
  if value is not None and (isinstance(value,bool) or not isinstance(value,int) or not 100<=value<=200):raise BadRequest('Picture scale must be a whole percentage from 100 to 200, or null to use the project scale.')
  if value is None:scene.pop('picture_scale',None)
  else:scene['picture_scale']=value
  save(p);return jsonify(public(p))

@app.patch('/api/projects/<pid>/scenes/picture-scale')
def edit_selected_picture_scale(pid):
 with LOCKS[pid]:
  editable(pid);p=load(pid);data=request.get_json(silent=True) or {};ids=data.get('scene_ids');value=data.get('picture_scale')
  if not isinstance(ids,list) or not ids or any(not isinstance(x,str) for x in ids) or len(ids)!=len(set(ids)):raise BadRequest('Select one or more distinct timeline slots.')
  if 'picture_scale' not in data or (value is not None and (isinstance(value,bool) or not isinstance(value,int) or not 100<=value<=200)):raise BadRequest('Picture scale must be a whole percentage from 100 to 200, or null to use the project scale.')
  by_id={s['id']:s for s in p['scenes']}
  if any(x not in by_id for x in ids):raise BadRequest('A selected slot is no longer in this production; no changes were made.')
  for sid in ids:
   if value is None:by_id[sid].pop('picture_scale',None)
   else:by_id[sid]['picture_scale']=value
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
    ranges[a['id']].append((*source_overlap_interval(scene,a),i+1))
  p.setdefault('scene_approval_history',[]).append({'action':'bulk_scene_approval','scope':scope,'approved':len(approved),'skipped':len(skipped),'at':time.time()})
  if scope=='all' and skipped:raise BadRequest(str(len(skipped))+' picture beat(s) cannot be approved yet. Nothing was approved; open the first one to see why: Scene '+str(skipped[0]['scene'])+' — '+skipped[0]['reasons'][0])
  save(p)
  result=public(p);result['bulk_approval']={'approved':approved,'skipped':skipped,'scope':scope}
  return jsonify(result)

@app.post('/api/projects/<pid>/scenes/<sid>/split')
def split_scene(pid,sid):
 raise BadRequest('Splitting a cue is disabled: use exactly one shot per SRT cue.')

@app.post('/api/projects/<pid>/scenes/<sid>/merge')
def merge_scene(pid,sid):
 raise BadRequest('Merging cues is disabled: use exactly one shot per SRT cue.')

def manifest(p, strict=None, enhance_picture=False):
 from production import reserve_id,asset_used
 root=folder(p['id']);w,h=output_size(p.get('format','landscape'),p.get('size'));assets={a['id']:a for a in p['assets']};shots=[];signatures=set()
 for i,s in enumerate(p['scenes']):
  a=assets[s['asset_id']];frames=s['frame_out']-s['frame_in'];m=motion(s,i);signature=tuple(m[k] for k in ['zoom_start','zoom_end','x_start','x_end','y_start','y_end'])
  while signature in signatures and m.get('move')!='static':m['x_end']+=.00001;signature=tuple(m[k] for k in ['zoom_start','zoom_end','x_start','x_end','y_start','y_end'])
  signatures.add(signature);extra=s.get('transition_handle_frames',4) if s['transition']=='dissolve' else 0
  shot={'shot':i+1,'kind':'narration','slot':None,'subject':s['focus'],'srt_blocks':s['srt_blocks'],'frame_in':s['frame_in'],'frame_out':s['frame_out'],'duration_frames':frames,'duration_ms':frames/30*1000,'timeline_in_ms':s['frame_in']/30*1000,'timeline_out_ms':s['frame_out']/30*1000,'voiceover_in_ms':s['frame_in']/30*1000,'voiceover_out_ms':s['frame_out']/30*1000,'narration_text':s['narration'],'source_id':reserve_id(a) or a.get('editorial_source_id') or ((a.get('document_plan') or {}).get('source_ids') or [a['id']])[0],'original_filename':a.get('original_name',a['name']),'source_role':'reserve' if reserve_id(a) else 'primary','source_file':Path(a['path']).name,'media_type':a['kind'],'video_in_ms':s['in_s']*1000,'video_out_ms':math.ceil((s['in_s']+(frames+extra)/30)*1000)+1,'motion_plan':m,'transition_handle_frames':extra,'image_approved':a['verified'],'match_note':s['notes'],'loop_required':False,'hold_last':False}
  if a['kind']=='image':
   out=root/'.cache/prepared_images'/f'{a["id"]}.png';out.parent.mkdir(parents=True,exist_ok=True)
   im=ImageOps.exif_transpose(Image.open(root/a['path'])).convert('RGB')
   if p.get('image_layout')=='full_bleed':
    ImageOps.fit(im,(w,h)).save(out)
   else:
    bg=ImageEnhance.Brightness(ImageOps.fit(im,(w,h)).filter(ImageFilter.GaussianBlur(28))).enhance(.58);fg=ImageOps.contain(im,(int(w*.88),int(h*.86)));bg.paste(fg,((w-fg.width)//2,(h-fg.height)//2));bg.save(out)
   shot['image']=out.relative_to(root).as_posix()
  if strict and strict.get('required') and a['kind']=='video':
   timing=strict['shots'][i]
   shot['strict_source_frames']=[timing['source_start_frame'],timing['source_end_frame']]
   shot['video_in_ms']=timing['placed_in_ms'];shot['video_out_ms']=timing['placed_out_ms']
  shot['picture_scale']=s.get('picture_scale',p.get('picture_scale',100))
  if a['kind']=='video':shot['motion_plan'].update(mode='native_video',move='Preserve source camera and human action')
  shots.append(shot)
 result={'project':p['title'],'version':1,'editorial_approved':all(s['approved'] for s in p['scenes']),'captions_burned_in':False,'settings':{'width':w,'height':h,'fps':30,'caption_srt':'inputs/captions.srt','transition_frames':0,'picture_scale':p.get('picture_scale',100),'enhance_picture':enhance_picture},'shots':shots,'reserve_sources':[{'source_id':reserve_id(a),'original_filename':a.get('original_name',a['name']),'source_file':Path(a['path']).name,'status':'explicitly_selected' if asset_used(p,a) else 'held_in_reserve'} for a in p['assets'] if reserve_id(a) and not a.get('derived_from')]}
 write(root/'storyboard.json',result);return result
@app.post('/api/projects/<pid>/continuous-preview')
def prepare_continuous_preview(pid):
 from preview_render import start
 with LOCKS[pid]:
  p=load(pid);root=folder(pid)
  if p.get('edit_mode','review')=='documentary':raise BadRequest('Documentary smooth preview is not implemented yet.')
  if p.get('review_only_draft'):raise BadRequest('This review draft cannot be rendered as a smooth preview until its caption, framing and freeze-frame instructions are implemented.')
  if not p['scenes']:raise BadRequest('Build a storyboard first.')
  def publish(updated,temp,base):
   with LOCKS[pid]:
    if load(pid)['updated']!=updated:return False
    temp.replace(base/'preview.mp4')
    write(base/'status.json',{'status':'done','updated':updated,'progress':100,'detail':'Temporary smooth preview ready.'})
    return True
  try:result=start(root,p,publish)
  except ValueError as e:raise BadRequest(str(e))
  return jsonify(result),202

@app.get('/api/projects/<pid>/continuous-preview')
def continuous_preview_file(pid):
 from preview_render import status as preview_status
 p=load(pid);root=folder(pid);state=preview_status(root,pid,p['updated'])
 if state.get('status')!='done' or request.args.get('version')!=str(p['updated']):raise NotFound('Preview is not current.')
 return send_from_directory(root/'.cache/continuous_preview','preview.mp4',conditional=True)

@app.post('/api/projects/<pid>/scene-exports')
def prepare_scene_exports(pid):
 from scene_exports import start
 with LOCKS[pid]:
  p=load(pid);root=folder(pid);data=request.get_json(silent=True) or {};kind=data.get('source')
  if p.get('edit_mode','review')=='documentary':raise BadRequest('Documentary scene export is not implemented yet.')
  if p.get('review_only_draft'):raise BadRequest('A review draft cannot be exported as finished scene clips.')
  if kind not in ('preview','render'):raise BadRequest('Choose the smooth preview or a rendered master.')
  d=public(p)
  if kind=='preview':
   if d['preview'].get('status')!='done':raise BadRequest('Prepare a current smooth preview first.')
   source=root/'.cache/continuous_preview/preview.mp4'
  else:
   render_info=d['render']
   if render_info.get('status')!='done' or not render_info.get('current'):raise BadRequest('Render the current storyboard first.')
   name=render_info.get('output','')
   if Path(name).name!=name:raise BadRequest('Invalid rendered video name.')
   source=root/'renders'/name
  def publish(updated,archive,name,source_kind):
   with LOCKS[pid]:
    if load(pid)['updated']!=updated:return False
    dest=root/'renders/scene_exports';dest.mkdir(parents=True,exist_ok=True);archive.replace(dest/name)
    write(root/'scene_exports_status.json',{'status':'done','updated':updated,'source_kind':source_kind,'naming':'C','output':name,'progress':100,'detail':str(len(p['scenes']))+' scene clips ready.'})
    return True
  try:result=start(root,p,source,kind,publish)
  except ValueError as e:raise BadRequest(str(e))
  return jsonify(result),202

@app.get('/api/projects/<pid>/scene-exports/<name>')
def download_scene_exports(pid,name):
 if not re.fullmatch(r'scenes_[0-9]{8}_[0-9]{6}_[a-f0-9]{8}\.zip',name):raise NotFound('Scene archive not found.')
 return send_from_directory(folder(pid)/'renders/scene_exports',name,conditional=True,as_attachment=True)

@app.get('/api/projects/<pid>/documentary-bites')
def get_documentary_bites(pid):
 from documentary import load_plan,annotate_plan
 root=folder(pid)
 with LOCKS[pid]:
  try:return jsonify(annotate_plan(load(pid),root,load_plan(root)))
  except ValueError as e:raise BadRequest(str(e))

@app.post('/api/projects/<pid>/documentary-bites')
def add_documentary_bite(pid):
 from documentary import load_plan,validate_bite,annotate_plan
 root=folder(pid);data=request.get_json(silent=True) or {}
 with LOCKS[pid]:
  plan=load_plan(root)
  if data.get('revision')!=plan['revision']:raise BadRequest('The sound-bite plan changed in another window. Reopen it before saving.')
  if len(plan['bites'])>=200:raise BadRequest('The plan is limited to 200 sound bites.')
  try:bite=validate_bite(load(pid),root,data,lambda path:probe(path)['has_audio'])
  except (ValueError,OSError) as e:raise BadRequest(str(e))
  plan['bites'].append(bite);plan['bites'].sort(key=lambda x:(x['pause_s'],x['created']))
  plan['revision']+=1;write(root/'documentary_plan.json',plan)
  return jsonify(annotate_plan(load(pid),root,plan))

@app.delete('/api/projects/<pid>/documentary-bites/<bid>')
def remove_documentary_bite(pid,bid):
 from documentary import load_plan,annotate_plan
 root=folder(pid);data=request.get_json(silent=True) or {}
 with LOCKS[pid]:
  plan=load_plan(root)
  if data.get('revision')!=plan['revision']:raise BadRequest('The sound-bite plan changed in another window. Reopen it before removing anything.')
  new=[x for x in plan['bites'] if x['id']!=bid]
  if len(new)==len(plan['bites']):raise NotFound('Sound bite not found.')
  plan['bites']=new;plan['revision']+=1;write(root/'documentary_plan.json',plan)
  return jsonify(annotate_plan(load(pid),root,plan))

@app.post('/api/projects/<pid>/documentary-audio')
def start_documentary_audio(pid):
 import documentary_audio
 from documentary import load_plan,annotate_plan,build_timeline
 with LOCKS[pid]:
  p=load(pid);root=folder(pid)
  if p.get('edit_mode','review')!='documentary':raise BadRequest('Documentary audio is available only in a documentary project.')
  plan=load_plan(root)
  try:
   timeline=build_timeline(p,annotate_plan(p,root,copy.deepcopy(plan)))
   if not any(s['kind']=='sound_bite' for s in timeline['segments']):raise ValueError('Add a sound bite first.')
   def current(original,source_plan):
    with LOCKS[pid]:
     current_p=load(pid);current_plan=load_plan(root)
     if current_p.get('updated')!=original.get('updated') or current_plan['revision']!=source_plan['revision']:return False
     return not any(x.get('stale_reasons') for x in annotate_plan(current_p,root,current_plan)['bites'])
   return jsonify(documentary_audio.start(root,copy.deepcopy(p),copy.deepcopy(plan),current)),202
  except (ValueError,OSError) as e:raise BadRequest(str(e))

@app.get('/api/projects/<pid>/documentary-audio')
def documentary_audio_status(pid):
 import documentary_audio
 from documentary import load_plan,annotate_plan
 root=folder(pid)
 with LOCKS[pid]:
  p=load(pid);plan=load_plan(root);state=documentary_audio.status(pid,root)
  if p.get('edit_mode','review')!='documentary':raise BadRequest('Documentary audio is available only in a documentary project.')
  if state.get('status')=='done':
   stale=state.get('project_version')!=p.get('updated') or state.get('plan_revision')!=plan['revision'] or any(b.get('stale_reasons') for b in annotate_plan(p,root,plan)['bites'])
   if stale:state={'status':'stale','detail':'Project or plan changed; prepare documentary audio again.'}
   else:state['url']=f'/api/projects/{pid}/documentary-audio/{state["output"]}'
  return jsonify(state)

@app.get('/api/projects/<pid>/documentary-audio/<name>')
def documentary_audio_file(pid,name):
 import documentary_audio
 from documentary import load_plan,annotate_plan
 if not re.fullmatch(r'documentary_audio_[a-f0-9]{32}\.wav',name):raise NotFound('Documentary audio not found.')
 root=folder(pid)
 with LOCKS[pid]:
  p=load(pid);plan=load_plan(root);job=documentary_audio.status(pid,root)
  if p.get('edit_mode','review')!='documentary' or job.get('status')!='done' or job.get('output')!=name or job.get('project_version')!=p.get('updated') or job.get('plan_revision')!=plan['revision'] or any(b.get('stale_reasons') for b in annotate_plan(p,root,plan)['bites']):raise NotFound('Documentary audio is not current.')
  return send_from_directory(root/'renders',name,conditional=True,as_attachment=request.args.get('download')=='1')

@app.get('/api/projects/<pid>/documentary-timeline')
def documentary_timeline(pid):
 from documentary import load_plan,annotate_plan,build_timeline
 with LOCKS[pid]:
  p=load(pid)
  if p.get('edit_mode','review')!='documentary':raise BadRequest('Documentary timeline is available only in a documentary project.')
  try:return jsonify(build_timeline(p,annotate_plan(p,folder(pid),load_plan(folder(pid)))))
  except ValueError as e:raise BadRequest(str(e))

@app.get('/api/projects/<pid>/strict-cut-check')
def strict_cut_check(pid):
 from strict_cuts import report
 p=load(pid);return jsonify(report(p,folder(pid)))

# Strict decoding may exceed a proxy's request timeout. Run it once per saved
# project version in the background; polling never starts a render or edits data.
STRICT_JOBS={}
STRICT_JOBS_LOCK=threading.Lock()

@app.get('/api/projects/<pid>/strict-cut-status')
def strict_cut_status(pid):
 from strict_cuts import report
 p=load(pid);version=p.get('updated');key=(pid,version)
 with STRICT_JOBS_LOCK:
  job=STRICT_JOBS.get(key)
  if job is None:
   job={'state':'checking','version':version,'completed':0,'total':len(p.get('scenes') or [])}
   STRICT_JOBS[key]=job
   # Keep only the newest non-running status for this project.
   for old in list(STRICT_JOBS):
    if old!=key and old[0]==pid and STRICT_JOBS[old]['state']!='checking':STRICT_JOBS.pop(old,None)
   def work():
    try:
     def progress(completed,total):
      with STRICT_JOBS_LOCK:job.update(completed=completed,total=total)
     result=report(p,folder(pid),progress=progress)
     with STRICT_JOBS_LOCK:job.update(state='done',report=result,completed=len(p.get('scenes') or []))
    except Exception as exc:
     app.logger.exception('Strict cut check failed')
     with STRICT_JOBS_LOCK:job.update(state='error',error=str(exc)[:300] or 'Strict check failed.')
   threading.Thread(target=work,daemon=True,name='strict-cut-check').start()
  return jsonify(dict(job))

@app.post('/api/projects/<pid>/strict-cut-status/retry')
def retry_strict_cut_status(pid):
 p=load(pid);key=(pid,p.get('updated'))
 with STRICT_JOBS_LOCK:
  job=STRICT_JOBS.get(key)
  if job and job['state']=='error':STRICT_JOBS.pop(key,None)
 return jsonify({'ok':True})

@app.post('/api/projects/<pid>/render')
def render(pid):
 with LOCKS[pid]:
  editable(pid);p=load(pid);data=request.get_json(silent=True) or {};final=data.get('final') is True
  if p.get('edit_mode','review')=='documentary':raise BadRequest('Documentary export is not implemented yet. The review renderer cannot include your sound bites.')
  report=check(p,final)
  from strict_cuts import report as strict_report
  strict=strict_report(p,folder(pid))
  if strict['errors']:report['errors'].extend(strict['errors']);report['can_export']=False
  if strict['required'] and data.get('strict_checked_version')!=p.get('updated'):
   report['errors'].append('Review and confirm the current per-shot strict cut check before export.');report['can_export']=False
  if report['errors']:return jsonify(report),400
  enhance_picture=data.get('enhance_picture') is True
  manifest(p,strict,enhance_picture);root=folder(pid);name=('final' if final else 'review')+'_'+time.strftime('%Y%m%d_%H%M%S')+'.mp4'
  job=RenderJob(root,on_change=lambda state:write(root/'render_status.json',state),story_path=root/'storyboard.json',catalog_path=root/'catalog.json',output_name=name);JOBS[pid]=job
  burn_script=data.get('script_captions') is True;burn_srt=data.get('srt_subtitles') is True
  caption_entries=[]
  if burn_script:
   for scene in p['scenes']:
    row=scene.get('plan_row') or {}
    # Older combined fields are ambiguous editing instructions, not caption text.
    caption='' if row.get('plain_cue') and 'continuity' not in row else str(row.get('required_caption') or '').strip()
    if caption and caption.casefold() not in ('none','n/a','na','—','-'):
     caption_entries.append((scene['frame_in'],scene['frame_out'],caption))
  caption_file=None
  if caption_entries:
   caption_file=root/'reviews'/(Path(name).stem+'_script_captions.srt')
   caption_file.write_text('\n\n'.join(f'{i}\n{srt_stamp(round(a/30*1000))} --> {srt_stamp(round(b/30*1000))}\n{re.sub(chr(10)+chr(10), chr(10), text)}' for i,(a,b,text) in enumerate(caption_entries,1))+'\n',encoding='utf-8')
  write(root/'reviews'/(Path(name).stem+'.json'),{'project':copy.deepcopy(p),'validation':report,'strict_cut_report':strict,'export_type':'final' if final else 'review','captions_burned_in':bool(caption_entries),'script_captions_requested':burn_script,'srt_subtitles_burned_in':burn_srt,'picture_enhancement':enhance_picture,'created':time.time()})
  one_line_file=None
  if burn_srt:
   from caption_layout import make_single_line_ass
   if not (p.get('srt') or {}).get('blocks'):raise BadRequest('Import an SRT before burning subtitles.')
   width,height=output_size(p.get('format','landscape'),p.get('size'))
   try:ass=make_single_line_ass(p['srt']['blocks'],width,height)
   except ValueError as e:raise BadRequest(str(e))
   one_line_file=root/'reviews'/(Path(name).stem+'_srt_display.ass');one_line_file.write_text(ass,encoding='utf-8')
  job.start(captions=burn_srt,script_caption_srt=caption_file,narration_caption_file=one_line_file);return jsonify({'render':job.snapshot()}),202
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
@project_io_lock
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

@app.get('/api/projects/<pid>/script-reading')
@project_io_lock
def script_reading_report(pid):
 return jsonify(script_reading(load(pid),include_shots=True))

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

@app.get('/api/projects/<pid>/coverage-summary')
def coverage_summary(pid):
 p=load(pid);assets={a['id']:a for a in p['assets']};video=still=unassigned=0;used=set()
 for scene in p.get('scenes',[]):
  seconds=(scene['frame_out']-scene['frame_in'])/30
  asset=assets.get(scene.get('asset_id'))
  if not asset:unassigned+=seconds;continue
  used.add(asset['id'])
  if is_still(p,asset) or (scene.get('plan_row') or {}).get('native_static_interval'):still+=seconds
  else:video+=seconds
 total=video+still+unassigned
 result={'narration_duration_seconds':(p.get('voiceover') or {}).get('duration'), 'cue_count':len((p.get('srt') or {}).get('blocks',[])), 'source_count':len(used), 'timeline_seconds':round(total,3), 'video_seconds':round(video,3), 'still_seconds':round(still,3), 'unassigned_seconds':round(unassigned,3), 'video_percent':round(100*video/total,2) if total else 0, 'still_percent':round(100*still/total,2) if total else 0, 'unresolved_issues':check(p,True)['errors'], 'note':'Declared native static intervals count as stills; source identity, eligibility and rights require review.'}
 response=jsonify(result);response.headers['Content-Disposition']='attachment; filename="coverage-summary.json"';return response

@app.get('/api/projects/<pid>/frame-qc')
@project_io_lock
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
 if not re.fullmatch(r'frame_qc_sheet_\d{2,5}_[0-9a-f]{24}\.jpg',name):raise NotFound('Sheet not found.')
 return send_from_directory(folder(pid)/'.cache/frame_qc',name,conditional=True)

@app.get('/api/local-understanding/capabilities')
def local_understanding_capabilities():
 from local_understanding import capabilities
 return jsonify(capabilities())

def understanding_source(pid,target):
 p=load(pid);root=folder(pid)
 if target=='narration':
  a=p.get('voiceover');kind='audio'
 else:
  if not re.fullmatch('[a-f0-9]{16}',target):raise NotFound('Media not found.')
  a=next((a for a in p['assets'] if a['id']==target and a['kind']=='video'),None);kind='video'
 if not a:raise NotFound('Media not found.')
 path=(root/a['path']).resolve()
 if not path.is_relative_to(root.resolve()) or not path.is_file():raise NotFound('Media file not found.')
 return root,path,kind,float(a['duration'])

@app.route('/api/projects/<pid>/understanding/<target>',methods=['GET','POST'])
def local_understanding_report(pid,target):
 from local_understanding import enqueue,read_result
 with LOCKS[pid]:
  root,path,kind,duration=understanding_source(pid,target)
  if request.method=='POST':enqueue(root,target,path,kind,duration)
  return jsonify(read_result(root,target,path))

@app.post('/api/projects/<pid>/understanding/<target>/sound-events')
def reanalyse_sound_events(pid,target):
 from local_understanding import enqueue_sound_events
 with LOCKS[pid]:
  root,path,_,_=understanding_source(pid,target)
  try: enqueue_sound_events(root,target,path)
  except ValueError as e: raise BadRequest(str(e))
  return jsonify({'status':'queued'})


@app.post('/api/projects/<pid>/understanding/<target>/visual')
def reanalyse_visual(pid,target):
 from local_understanding import enqueue_visual
 with LOCKS[pid]:
  root,path,kind,_=understanding_source(pid,target)
  if kind != 'video': raise BadRequest('Visual analysis requires a video source.')
  try: enqueue_visual(root,target,path)
  except ValueError as e: raise BadRequest(str(e))
  return jsonify({'status':'queued'})


def transcript_srt(report, require_complete=False):
 if report.get('status') in ('stale','not_started','error','interrupted'):raise BadRequest('No current transcript is available for this source.')
 stage=(report.get('stages') or {}).get('transcript') or {}
 if require_complete and stage.get('status')!='complete':raise BadRequest('Wait for transcription to finish before using it as the project SRT.')
 segments=(stage.get('result') or {}).get('segments') or []
 if not segments:raise BadRequest('No transcript segments are available yet.')
 lines=[];last=0
 for i,segment in enumerate(segments,1):
  try:a=round(float(segment['start_s'])*1000);b=round(float(segment['end_s'])*1000)
  except (ValueError,TypeError,KeyError,OverflowError):raise BadRequest('Transcript has invalid timestamps; review it in the analysis report.')
  text=' '.join(str(segment.get('text') or '').split())
  if a<last or a<0 or b<=a or not text:raise BadRequest('Transcript timestamps overlap or are invalid; review the analysis report before making an SRT.')
  lines.append('%d\n%s --> %s\n%s'%(i,srt_stamp(a),srt_stamp(b),text));last=b
 return '\n\n'.join(lines)+'\n', stage.get('status')=='complete'

@app.get('/api/projects/<pid>/understanding/<target>/transcript.srt')
def local_transcript_srt(pid,target):
 from flask import Response
 from local_understanding import read_result
 with LOCKS[pid]:
  root,path,_,_=understanding_source(pid,target)
  text,complete=transcript_srt(read_result(root,target,path))
  suffix='' if complete else '-partial'
  return Response(text,mimetype='text/plain; charset=utf-8',headers={'Content-Disposition':'attachment; filename="transcript'+suffix+'.srt"'})

@app.post('/api/projects/<pid>/understanding/narration/apply-srt')
def apply_narration_transcript(pid):
 from local_understanding import read_result
 with LOCKS[pid]:
  editable(pid);p=load(pid);d=request.get_json(silent=True) or {}
  if d.get('confirm_generated') is not True:raise BadRequest('Confirm that this is an unverified machine transcript before applying it.')
  if (p.get('srt') or p.get('scenes')) and d.get('confirm_replace') is not True:
   raise BadRequest('Confirm replacement: this replaces the current SRT and clears the storyboard.')
  root,path,_,_=understanding_source(pid,'narration')
  text,_=transcript_srt(read_result(root,'narration',path),require_complete=True)
  blocks=parse_srt(text)
  # Match normal SRT upload semantics, but only after the user confirms. No timestamps
  # are stretched to match narration and old block maps remain detectably stale.
  (root/'inputs/captions.srt').write_text(text,encoding='utf-8')
  p['srt']={'blocks':blocks,'name':'Machine transcript · review required.srt'};p.pop('srt_removed',None)
  p['scenes']=[];p.pop('srt_recovery',None)
  save(p)
  from automatic import maybe_queue
  maybe_queue(sys.modules[__name__],p)
  return jsonify(public(p))

@app.get('/api/projects/<pid>/scenes/<sid>/understanding')
def scene_understanding_report(pid,sid):
 from local_understanding import read_result,scene_context
 with LOCKS[pid]:
  p=load(pid)
  scene=next((s for s in p['scenes'] if s['id']==sid),None)
  if not scene:raise NotFound('Scene not found.')
  target=scene.get('asset_id')
  if not target:raise NotFound('This scene has no video source.')
  root,path,kind,_=understanding_source(pid,target)
  return jsonify(scene_context(read_result(root,target,path),scene))

@app.get('/api/projects/<pid>/block-map-editor')
def block_map_editor(pid):
 from edl import srt_signature
 from shot_plan import prefill
 p=load(pid)
 try: rows=prefill(p,parent_beat_min(1))
 except ValueError as e:raise BadRequest(str(e))
 existing=p.get('srt_block_map')
 if existing:
  if existing.get('based_on_srt')!=srt_signature(p):
   raise BadRequest('The SRT changed since this map was imported. Review and re-upload a matching map, or remove it before starting a new one.')
  rows=existing['beats']
 return jsonify(rows=rows,srt_signature=srt_signature(p),existing=bool(existing))

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
@project_io_lock
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
@project_io_lock
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
