"""Automatic picture conform. Document claims never become face identifications.
A visually reviewed assembly is reused only while its documents AND local media remain unchanged.
Frame decoding verifies availability, not semantic identity or copyright.
"""
from pathlib import Path
import copy,hashlib,json,math

def file_sha256(path,chunk=1024*1024):
 """Hash a file by streaming it. Uploads may be 8 GB; reading one into memory to hash it
 would either swap the machine or fail outright, and it is only ever compared, not stored."""
 digest=hashlib.sha256()
 with open(path,'rb') as handle:
  for block in iter(lambda:handle.read(chunk),b''):digest.update(block)
 return digest.hexdigest()

def fingerprint(p,root):
 root=Path(root)
 media=[(a['id'],a['path'],(root/a['path']).stat().st_size,(root/a['path']).stat().st_mtime_ns) for a in p['assets'] if a['kind']=='video' and (root/a['path']).is_file()]
 if p.get('portable_from_project'):
  media=[(a['id'],a['path'],(root/a['path']).stat().st_size,file_sha256(root/a['path'])) for a in p['assets'] if a['kind']=='video' and (root/a['path']).is_file()]
 data=[p.get('production_script'),p.get('srt'),p.get('srt_block_map'),p.get('source_bindings'),media,p.get('picture_policy'),p.get('repetition_policy'),p.get('transition_policy')]
 return hashlib.sha256(json.dumps(data,sort_keys=True).encode()).hexdigest()

# A beat survives a rebuild only when the decision behind its picture is unchanged.
# Everything that could make the same picture mean something different is in this list.
SCENE_IDENTITY_FIELDS = ['srt_blocks', 'frame_in', 'frame_out', 'asset_id', 'in_s',
                         'transition', 'transition_handle_frames', 'narration', 'person',
                         'context_only', 'focus']


def scene_identity(scene):
    """The part of a scene a rebuild must reproduce for its approval to still stand."""
    return {field: scene.get(field) for field in SCENE_IDENTITY_FIELDS}


def carry_previous_review(p, scenes):
    """Keep approvals on beats whose picture did not change, drop them on beats that did.

    A rebuild starts every scene unapproved, which is right for new pictures and wrong for
    the ninety-nine beats an editor already reviewed. This compares the rebuilt scene with
    the previously saved one on the fields that decide what the picture is; a match
    carries the approval and its record forward, a difference leaves the beat unapproved
    so it is reviewed again. It never approves anything a human did not approve before.
    """
    previous = {str(s.get('plan_row_id') or s.get('srt_blocks')): s for s in p.get('scenes') or []}
    kept, reset = 0, 0
    for scene in scenes:
        key = str(scene.get('plan_row_id') or scene.get('srt_blocks'))
        old = previous.get(key)
        if old and old.get('approved') and scene_identity(old) == scene_identity(scene):
            scene['approved'] = True
            if old.get('approval_record'):
                scene['approval_record'] = dict(old['approval_record'], carried_from_rebuild=True)
            kept += 1
        else:
            scene['approved'] = False
            reset += 1
    return {'carried': kept, 'requiring_review': reset}


def restore_reviewed(p,root):
 record=p.get('automatic_timeline') or {}
 if not record.get('scenes') or record.get('input_fingerprint')!=fingerprint(p,root):return False
 staged=copy.deepcopy(record['scenes'])
 carry_previous_review(p,staged)
 p['scenes']=staged;p['allow_archive_reuse']=True
 p['script_application']={'recognized_instructions':len(p['srt']['blocks']),'placed_scenes':len(p['scenes']),'issues':[],'mode':'reviewed_mp4_only_assembly','note':'Automatic restoration of frame-reviewed cuts; input fingerprint matched. Unavailable exact evidence is recorded separately, never fabricated.'}
 return True

def native_rows(p,rows):
 from production import matching_assets
 out=copy.deepcopy(rows)
 for row in out:
  row['transition_out']='cut';row['transition_handle_frames']=0;row['transition_handle_ms']=0
  if str(row.get('visual_status','')).upper() in ('PHOTOGRAPH','STILL_EXTRACTED'):continue  # already a prepared still
  row['visual_status']='VIDEO';row['picture_treatment']='Native MP4 action; no extracted still, loop, frozen tail or imposed camera effect.'
  row['import_blockers']=[n for n in row.get('import_blockers',[]) if 'transition' not in str(n).lower()]
  a=matching_assets(p,row.get('source_file',''))
  if len(a)==1 and a[0]['kind']=='video':
   duration=(round(row['timeline_out_ms']*.03)-round(row['timeline_in_ms']*.03))/30
   if duration>a[0]['duration']:continue
   original=row['source_in_ms']/1000;inside=max(0,min(original,a[0]['duration']-duration-.07));row['source_in_ms']=round(inside*1000);row['source_out_ms']=round((inside+duration)*1000)
   if abs(inside-original)>.001:row.setdefault('import_notes',[]).append('Nominal source IN was outside playable coverage; bounded to this same uploaded MP4. This is not semantic verification.')
 return out

def inspect_interval(root,asset,inside,frames,fps=30,cap=None):
 from conform import capture
 import cv2,numpy as np
 owned=cap is None
 cap=capture(Path(root)/asset['path']) if owned else cap;samples=[]
 try:
  source_fps=cap.get(cv2.CAP_PROP_FPS)
  if not math.isfinite(source_fps) or source_fps<=0:raise ValueError('Source has no valid frame rate.')
  for offset in [0,max(0,(frames-1)/fps/2),max(0,(frames-1)/fps)]:
   t=inside+offset
   # Seek by frame number, not timestamp: on a long programme timestamp seeking walks
   # from the previous position and cost ~46 ms per seek even on a 3-minute source.
   cap.set(cv2.CAP_PROP_POS_FRAMES,max(0,int(round(t*source_fps))))
   ok,f=cap.read()
   if not ok:raise ValueError(f'Cannot decode {asset["original_name"]} at {t:.3f}s.')
   gray=cv2.cvtColor(f,cv2.COLOR_BGR2GRAY);black=float(np.mean(gray<20));samples.append({'time':t,'black_fraction':black,'luma_mean':float(gray.mean())})
   if black>.99:raise ValueError(f'Black picture in {asset["original_name"]} at {t:.3f}s.')
 finally:
  if owned:cap.release()
 return samples

def still_kind(asset):
 if not asset:return ''
 if asset.get('kind')=='image':return 'photograph'
 return 'extracted frame' if (asset.get('derived_from') or {}).get('kind')=='image' else ''

def inspect_still(root,asset):
 """A still must decode and must not be a black frame. It is not a moving interval."""
 from PIL import Image,ImageOps,ImageStat
 path=Path(root)/asset['path']
 if not path.is_file():raise ValueError('The picture file is missing from disk.')
 with Image.open(path) as im:
  im=ImageOps.exif_transpose(im).convert('L')
  if min(im.size)<64:raise ValueError('The picture is too small to fill a frame.')
  mean=ImageStat.Stat(im).mean[0];size=im.size
 if mean<3:raise ValueError('The picture is almost black; choose a usable image.')
 return {'kind':still_kind(asset),'size':size,'luma_mean':round(mean,2)}

def validate_assembly(p,root):
 assets={a['id']:a for a in p['assets']};report={'scope':'decoded IN/middle/LAST frame availability and still decode; not face recognition','scenes':[],'errors':[]}
 from conform import capture
 decoders={}
 try:
  for i,s in enumerate(p['scenes'],1):
   a=assets.get(s.get('asset_id'))
   if not a:report['errors'].append(f'Scene {i}: no media assigned.');continue
   kind=still_kind(a)
   try:
    if kind:
     report['scenes'].append({'scene':i,'still':inspect_still(root,a)})
     continue
    if a['kind']!='video':report['errors'].append(f'Scene {i}: no native MP4 interval.');continue
    # One decoder per source file, reused for every beat taken from it: opening a long
    # file once per beat is pure overhead on a programme with hundreds of beats.
    if a['id'] not in decoders:decoders[a['id']]=capture(Path(root)/a['path'])
    report['scenes'].append({'scene':i,'samples':inspect_interval(root,a,s['in_s'],s['frame_out']-s['frame_in'],cap=decoders[a['id']])})
   except Exception as e:report['errors'].append(f'Scene {i}: '+str(e))
 finally:
  for cap in decoders.values():
   try:cap.release()
   except Exception:pass
 return report

# Coalesce sequential uploads into one assembly after all mapped sources are present.
import threading,time
_QUEUE_LOCK=threading.Lock()
_PENDING=set()
def monitor_render(studio,pid,status):
 """Keep automatic_status in sync while the continuous master is encoded."""
 def watch():
  terminal={'done','cancelled','error'}
  for _ in range(5400):
   try:
    with studio.app.test_client() as client:
     r=client.get('/api/projects/'+pid).get_json() or {}
     state=r.get('render') or {}
    st=state.get('status') or 'running'
    status('rendering',state.get('detail') or ('Continuous master render: '+str(state.get('progress',0))+'%'))
    if st in terminal:
     status('done','Continuous master ready. Program monitor can play the rendered MP4.' if st=='done' else 'Render finished: '+str(state.get('stage') or st))
     return
   except Exception:
    pass
   time.sleep(2)
 threading.Thread(target=watch,daemon=True,name='automatic-render-monitor-'+pid).start()
def _plan_coverage(p):
 """Which SRT blocks the block map never places, and where the plan stops."""
 blocks=(p.get('srt') or {}).get('blocks') or []
 rows=(p.get('srt_block_map') or {}).get('beats') or []
 if not blocks or not rows:return []
 covered={n for row in rows for n in row.get('srt_blocks') or []}
 return [b['number'] for b in blocks if b['number'] not in covered]


def _plan_does_not_cover_srt(p):
 return bool(_plan_coverage(p))


def _coverage_detail(p):
 missing=_plan_coverage(p)
 plan_s=((p.get('srt_block_map') or {}).get('beats') or [{}])[-1].get('timeline_out_ms',0)/1000
 srt_s=((p.get('srt') or {}).get('blocks') or [{}])[-1].get('end_ms',0)/1000
 return (str(len(missing))+' SRT block(s) are not placed by this block map: it covers '+str(round(plan_s,1))+'s of a '
         +str(round(srt_s,1))+'s narration (first unplaced block '+str(missing[0])+'). A map written for a different '
         'runtime needs its rows extended, or download a fresh template for this SRT. Nothing is left silently unplanned.')


def automatic_readiness(p):
 """Why automatic assembly will or will not run, in words an editor can act on.

 The app used to fail this silently: with inputs present but no plan, or with the SRT
 missing, nothing happened and nothing said why. This is the single place that answers
 the question, used by the status line and by maybe_queue.
 """
 have_srt=bool(p.get('srt'))
 have_voice=bool(p.get('voiceover'))
 have_media=bool(p.get('assets'))
 have_script=bool(p.get('production_script'))
 have_map=bool(p.get('srt_block_map'))
 missing=[]
 if not have_media:missing.append('footage')
 if not have_srt:missing.append('SRT')
 # Narration is recorded, never required: a programme may be carried by its text alone.
 if have_map:
  plan='block map'
 elif have_script:
  plan='production script'
 else:
  plan=''
 state={'inputs':{'srt':have_srt,'voiceover':have_voice,'assets':have_media,'production_script':have_script,'srt_block_map':have_map},
         'has_plan':bool(plan),'plan':plan,'missing':missing,
         'auto_enabled':bool(p.get('auto_assemble_uploads')),
         'manual_edits':bool(p.get('manual_scene_edits'))}
 if not have_srt:
  state.update(status='blocked',detail='Automatic assembly is waiting for the narration SRT, which supplies every beat timing.')
 elif not have_media:
  state.update(status='blocked',detail='Automatic assembly is waiting for footage. Upload the source videos (and photographs if the picture policy allows them).')
 elif have_map and _plan_does_not_cover_srt(p):
  state.update(status='blocked',detail=_coverage_detail(p))
 elif not plan:
  state.update(status='needs_plan',detail='All required inputs are present, but there is no plan saying which picture goes where. Upload a block map (download the template first), or a production script with per-scene instructions. Without one, nothing is placed - the app will not choose pictures by guesswork.')
 elif p.get('scenes'):
  state.update(status='built',detail=str(len(p['scenes']))+' picture beats are built. Edit, approve and render.')
 elif state['manual_edits']:
  state.update(status='manual',detail='This production has manual scene edits, so automatic assembly will not overwrite them. Use Build storyboard to rebuild deliberately.')
 elif not state['auto_enabled']:
  state.update(status='paused',detail='Automatic assembly is switched off for this production. Use Build storyboard to assemble now.')
 else:
  state.update(status='ready',detail='Every input is present and a plan is defined. Automatic assembly will run; if it has not started, use Build storyboard.')
 return state


def maybe_queue(studio,p):
 state=automatic_readiness(p)
 pid=p['id'];root=studio.folder(pid)
 def publish(payload):
  studio.write(root/'automatic_status.json',dict(payload,at=time.time()))
 def status_only(state_,detail):
  publish({'status':state_,'detail':detail})
 if state['status']!='ready':
  # Say why, loudly, instead of sitting idle.
  publish({'status':state['status'],'detail':state['detail'],'missing':state['missing'],'plan':state['plan']})
  return
 from document_plan import rows_for
 from production import matching_assets
 from reviewed_library import queue_ready
 try:
  ready=queue_ready(p,root)
  if not ready:
   rows,_=rows_for(p)
   ready=bool(rows) and all(len(matching_assets(p,r.get('source_file','')))==1 for r in rows)
 except (ValueError,OSError) as e:
  publish({'status':'blocked','detail':'The plan could not be read: '+str(e)[:300]})
  return
 if not ready:
  publish({'status':'blocked','detail':'A plan exists but some beats name a source file that does not match exactly one upload. Resolve those filenames, then build.'})
  return
 render_status=root/'render_status.json'
 def render_busy():
  if not render_status.is_file():return False
  try:return json.loads(render_status.read_text()).get('status') in ['running','queued']
  except (OSError,ValueError):return False
 if p.get('scenes'):
  current=(p.get('render') or {}).get('output') and (p.get('render') or {}).get('current')
  if render_busy() or current:return
  with _QUEUE_LOCK:
   if pid in _PENDING:return
   _PENDING.add(pid)
  def render_only():
   try:
    status_only('rendering','Storyboard is ready. Starting the continuous MP4 master automatically.')
    with studio.app.test_client() as client:
     result=client.post('/api/projects/'+pid+'/render',json={'final':False})
     if result.status_code>=400:raise RuntimeError((result.get_json() or {}).get('error','Automatic rendering failed.'))
     monitor_render(studio,pid,status_only)
     status_only('done','Continuous master rendering is active. The Program monitor will switch to the rendered MP4 when ready.')
   except Exception as e:status_only('error',str(e))
   finally:
    with _QUEUE_LOCK:_PENDING.discard(pid)
  threading.Thread(target=render_only,daemon=True,name='automatic-render-'+pid).start()
  return
 with _QUEUE_LOCK:
  if pid in _PENDING:return
  _PENDING.add(pid)
 def status(state_,detail):status_only(state_,detail)
 def work():
  try:
   status('working','All inputs are ready. Allowing the machine time to study every source before assembly.')
   time.sleep(1.5);status('studying','Decoding every source MP4 and checking the production plan.')
   with studio.app.test_client() as client:
    result=client.post('/api/projects/'+pid+'/storyboard',json={'replace':True})
    if result.status_code>=400:raise RuntimeError((result.get_json() or {}).get('error','Automatic assembly failed.'))
    count=len((result.get_json() or {}).get('scenes',[]))
    status('storyboard_ready',str(count)+' clean-cut picture slots are ready. Starting continuous MP4 render automatically.')
    time.sleep(.5);status('rendering','Building the continuous MP4 master for smooth Program-monitor playback.')
    rendered=client.post('/api/projects/'+pid+'/render',json={'final':False})
    if rendered.status_code>=400:raise RuntimeError((rendered.get_json() or {}).get('error','Automatic rendering failed.'))
    monitor_render(studio,pid,status)
    status('done','Automatic storyboard and continuous-master rendering are active. The Program monitor will switch to the rendered master when it is ready.')
  except Exception as e:status('error',str(e))
  finally:
   with _QUEUE_LOCK:_PENDING.discard(pid)
 status('queued','Automatic source study and storyboard assembly will begin shortly.')
 threading.Thread(target=work,daemon=True,name='automatic-assembly-'+pid).start()

def full_source_analysis(p,root):
 """Decode every uploaded video before conforming. Scene/black detection is not identity recognition."""
 import subprocess,re,concurrent.futures,imageio_ffmpeg
 root=Path(root);dest=root/'reviews/Full_source_analysis.json';old=json.loads(dest.read_text()) if dest.exists() else {};cached={x['asset_id']:x for x in old.get('sources',[])};ff=imageio_ffmpeg.get_ffmpeg_exe()
 def scan(a):
  path=root/a['path'];st=path.stat();key=[st.st_size,st.st_mtime_ns];prev=cached.get(a['id'])
  if prev and prev.get('file_signature')==key and prev.get('complete_decode_success'):return prev
  # The budget scales with the picture instead of capping at a fixed 600 s. A scan runs at
  # roughly 50x realtime on two cores, so a ten-minute file takes about 12 s; the allowance
  # is deliberately loose (6 s of decoding per minute of footage, minimum 10 minutes) and
  # exists only to stop a pathological file hanging the whole assembly forever. A fixed
  # ceiling would have failed a long enough file on a slow enough disk with no explanation.
  budget=max(600,int(float(a.get('duration') or 0)*6)+60)
  try:
   r=subprocess.run([ff,'-hide_banner','-threads','2','-i',str(path),'-map','0:v:0','-vf',"scale=320:-2,blackdetect=d=0.05:pic_th=0.99:pix_th=0.05,select='gt(scene,0.32)',showinfo",'-an','-f','null','-'],capture_output=True,text=True,timeout=budget)
  except subprocess.TimeoutExpired:
   raise RuntimeError('Source study timed out after %d seconds on %s. The file may be on slow storage; retry, or re-encode it at a lower bitrate.'%(budget,a['original_name']))
  if r.returncode:raise RuntimeError('Full source decode failed: '+a['original_name']+' '+r.stderr[-400:])
  record={'asset_id':a['id'],'file':a['original_name'],'file_signature':key,'sha256':file_sha256(path),'duration_s':a['duration'],'complete_decode_success':True,'scene_change_times_s':[float(x) for x in re.findall(r'pts_time:\s*([\d.]+)',r.stderr)],'black_intervals':[dict(start=float(x),end=float(y)) for x,y in re.findall(r'black_start:([\d.]+) black_end:([\d.]+)',r.stderr)],'scope':'Every video frame decoded; shot changes/black intervals indexed. Semantic identity is NOT inferred by this scan.'}
  return record
 videos=[a for a in p['assets'] if a['kind']=='video'];results=[]
 def checkpoint():
  dest.write_text(json.dumps({'sources':results,'complete':False,'source_count':len(videos),'before_timeline_creation':True},indent=2))
 with concurrent.futures.ThreadPoolExecutor(max_workers=3) as pool:
  # Each file reports for itself. pool.map raises on the first failure and would discard
  # the completed scans of every other file, so a single unreadable upload cancelled the
  # whole study and lost the work already done.
  futures={pool.submit(scan,a):a for a in videos}
  failures=[]
  try:
   for done in concurrent.futures.as_completed(futures):
    try:
     record=done.result()
    except Exception as e:
     failures.append(str(e)[:400]);continue
    results.append(record);checkpoint()
    print('Full video analysis:',len(results),'/',len(videos),record['file'],flush=True)
  finally:
   # The scans that did succeed are written first, whatever happens next. Studying sources
   # is the most expensive part of assembly on long footage, and one unreadable upload used
   # to throw the finished studies away and start over on the retry.
   checkpoint()
  if failures:raise RuntimeError(failures[0]+('' if len(failures)==1 else ' (%d sources failed.)'%len(failures)))
 report={'sources':results,'complete':len(results)==len(videos),'source_count':len(videos),'before_timeline_creation':True};dest.write_text(json.dumps(report,indent=2));return report

# Beats are laid on whole output frames, so where an editor split one continuous take
# the two half-frames can appear to overlap by up to a frame. Anything larger is real
# reuse of the same source interval.
CONTINUATION_TOLERANCE = 1.5 / 30


def repetition_errors(p):
 """Pictures that genuinely repeat, never pictures that merely continue.

 The measurement is the source interval each beat actually plays. Two beats from the
 same file are a repeat only when those intervals genuinely overlap. Splitting one take
 across consecutive beats - the normal way to cut a long take into beats - produces
 intervals that touch or overlap by under a frame after frame rounding, and that is the
 same continuous shot, not a repeat. Adjacent beats holding the same photograph are the
 still equivalent. Everything else is reported.
 """
 policy=p.get('repetition_policy') or {};window=int(policy.get('cooldown_slots',0))
 if not window:return []
 errors=[];recent=[];assets={a['id']:a for a in p['assets']};tolerance=CONTINUATION_TOLERANCE
 for i,s in enumerate(p['scenes'],1):
  a=assets.get(s.get('asset_id'));m=s.get('automatic_match') or {}
  group=m.get('footage_group') or s.get('footage_group') or (a or {}).get('footage_group')
  source=m.get('original_source_file') or ((a or {}).get('original_full_source') or {}).get('file') or (a or {}).get('original_name')
  duration=(s['frame_out']-s['frame_in'])/30
  start=m.get('original_source_in_s',((a or {}).get('original_full_source') or {}).get('source_in_s',0)+s.get('in_s',0));end=start+duration
  still=a is not None and a.get('kind')=='image'
  previous=recent[-1] if recent else None
  continues=bool(previous and previous[0]==i-1 and previous[2]==source and
                 (abs(previous[4]-start)<=tolerance or (still and previous[6]==a['id'])))
  if continues:
   recent.append((i,group,source,start,end,m.get('perceptual_hashes') or [],a['id'] if a else None))
   continue
  for j,g,src,inside,outside,prior_hashes,twin in recent[-window:]:
   if group and group==g:
    errors.append(f'Scene {i}: repeats the same shot as scene {j} inside the {window}-slot cooldown.')
    break
   overlap=min(end,outside)-max(start,inside)
   if src==source and overlap>tolerance:
    errors.append(f'Scene {i}: replays source footage from scene {j} inside the {window}-slot cooldown '
                  f'(the intervals overlap by {overlap:.2f}s).')
    break
   hs=m.get('perceptual_hashes') or []
   if hs and prior_hashes and min((int(x,16)^int(y,16)).bit_count() for x in hs for y in prior_hashes)<=int(policy.get('visual_hash_max_distance',4)):
    errors.append(f'Scene {i}: visually matching source footage recurs from scene {j} inside the {window}-slot cooldown.')
    break
  recent.append((i,group,source,start,end,m.get('perceptual_hashes') or [],a['id'] if a else None))
 return errors

def source_capacity_errors(p):
 """A plan cannot demand more picture from a file than the file holds.

 Reusing one interval across many beats would pass the cooldown check while making the
 same shot repeat over and over, so the arithmetic is stated plainly before a long
 render. Photographs are exempt: holding one across beats is a legitimate choice.
 """
 assets={a['id']:a for a in p['assets']};demand={}
 for i,s in enumerate(p['scenes'],1):
  a=assets.get(s.get('asset_id'))
  if not a or a.get('kind')!='video':continue
  handle=(s.get('transition_handle_frames') or 0)/30 if s.get('transition')=='dissolve' else 0
  record=demand.setdefault(a['id'],{'name':a.get('original_name') or a.get('name'),'needed':0.0,'beats':0,'duration':float(a.get('duration') or 0)})
  record['needed']+=(s['frame_out']-s['frame_in'])/30+handle;record['beats']+=1
 errors=[]
 for record in demand.values():
  if record['duration'] and record['needed']>record['duration']+1e-6:
   errors.append(str(record['beats'])+' picture beats take '+str(round(record['needed'],3))+'s from '+str(record['name'])+', which is only '+str(round(record['duration'],3))+'s long. Fit cannot come from repeating the same footage; use more sources or shorter beats.')
 return errors

def link_frame_csv(payload,p):
 """Link the supplied frame-cut CSV to actual SRT blocks; preserve planned frame columns."""
 import csv,io
 try:rows=list(csv.DictReader(io.StringIO(payload.decode('utf-8-sig'))))
 except (UnicodeError,csv.Error):return payload
 if not rows or not {'scene','media_filename','timeline_in_ms','timeline_out_ms_excl'}<=set(rows[0]):return payload
 blocks=(p.get('srt') or {}).get('blocks',[])
 if not blocks:raise ValueError('The frame-cut CSV needs the original SRT to resolve block references; no block numbers will be guessed.')
 fields=list(rows[0]);fields += [k for k in ['srt_blocks','import_notes'] if k not in fields]
 for r in rows:
  start,end=int(r['timeline_in_ms']),int(r['timeline_out_ms_excl']);r['srt_blocks']=json.dumps([b['number'] for b in blocks if b['start_ms']<end and b['end_ms']>start]);r['import_notes']='Provided frame columns are a planning grid; explicit millisecond timing is conformed to the project frame rate. A supplied verified YES/NO flag does not independently verify identity or source content.'
 out=io.StringIO();w=csv.DictWriter(out,fieldnames=fields);w.writeheader();w.writerows(rows);return out.getvalue().encode()
