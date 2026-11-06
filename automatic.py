"""Automatic picture conform. Document claims never become face identifications.
A visually reviewed assembly is reused only while its documents AND local media remain unchanged.
Frame decoding verifies availability, not semantic identity or copyright.
"""
from pathlib import Path
import copy,hashlib,json,math

def fingerprint(p,root):
 root=Path(root)
 media=[(a['id'],a['path'],(root/a['path']).stat().st_size,(root/a['path']).stat().st_mtime_ns) for a in p['assets'] if a['kind']=='video' and (root/a['path']).is_file()]
 if p.get('portable_from_project'):
  media=[(a['id'],a['path'],(root/a['path']).stat().st_size,hashlib.sha256((root/a['path']).read_bytes()).hexdigest()) for a in p['assets'] if a['kind']=='video' and (root/a['path']).is_file()]
 data=[p.get('production_script'),p.get('srt'),p.get('srt_block_map'),p.get('source_bindings'),media,p.get('picture_policy'),p.get('repetition_policy'),p.get('transition_policy')]
 return hashlib.sha256(json.dumps(data,sort_keys=True).encode()).hexdigest()

def restore_reviewed(p,root):
 record=p.get('automatic_timeline') or {}
 if not record.get('scenes') or record.get('input_fingerprint')!=fingerprint(p,root):return False
 p['scenes']=copy.deepcopy(record['scenes']);p['allow_archive_reuse']=True
 p['script_application']={'recognized_instructions':len(p['srt']['blocks']),'placed_scenes':len(p['scenes']),'issues':[],'mode':'reviewed_mp4_only_assembly','note':'Automatic restoration of frame-reviewed case-specific cuts; input fingerprint matched. Unavailable exact evidence is recorded separately, never fabricated.'}
 return True

def native_rows(p,rows):
 from production import matching_assets
 out=copy.deepcopy(rows)
 for row in out:
  row['transition_out']='cut';row['transition_handle_frames']=0;row['transition_handle_ms']=0
  row['visual_status']='VIDEO';row['picture_treatment']='Native MP4 action; no extracted still, loop, frozen tail or imposed camera effect.'
  row['import_blockers']=[n for n in row.get('import_blockers',[]) if 'transition' not in str(n).lower()]
  a=matching_assets(p,row.get('source_file',''))
  if len(a)==1 and a[0]['kind']=='video':
   duration=(round(row['timeline_out_ms']*.03)-round(row['timeline_in_ms']*.03))/30
   if duration>a[0]['duration']:continue
   original=row['source_in_ms']/1000;inside=max(0,min(original,a[0]['duration']-duration-.07));row['source_in_ms']=round(inside*1000);row['source_out_ms']=round((inside+duration)*1000)
   if abs(inside-original)>.001:row.setdefault('import_notes',[]).append('Nominal source IN was outside playable coverage; bounded to this same uploaded MP4. This is not semantic verification.')
 return out

def inspect_interval(root,asset,inside,frames,fps=30):
 from conform import capture
 import cv2,numpy as np
 cap=capture(Path(root)/asset['path']);samples=[]
 try:
  source_fps=cap.get(cv2.CAP_PROP_FPS)
  if not math.isfinite(source_fps) or source_fps<=0:raise ValueError('Source has no valid frame rate.')
  for offset in [0,max(0,(frames-1)/fps/2),max(0,(frames-1)/fps)]:
   t=inside+offset;cap.set(cv2.CAP_PROP_POS_MSEC,t*1000);ok,f=cap.read()
   if not ok:raise ValueError(f'Cannot decode {asset["original_name"]} at {t:.3f}s.')
   gray=cv2.cvtColor(f,cv2.COLOR_BGR2GRAY);black=float(np.mean(gray<20));samples.append({'time':t,'black_fraction':black,'luma_mean':float(gray.mean())})
   if black>.99:raise ValueError(f'Black picture in {asset["original_name"]} at {t:.3f}s.')
 finally:cap.release()
 return samples

def validate_assembly(p,root):
 assets={a['id']:a for a in p['assets']};report={'scope':'decoded IN/middle/LAST frame availability; not face recognition','scenes':[],'errors':[]}
 for i,s in enumerate(p['scenes'],1):
  a=assets.get(s.get('asset_id'))
  if not a or a['kind']!='video':report['errors'].append(f'Scene {i}: no native MP4 interval.');continue
  try:report['scenes'].append({'scene':i,'samples':inspect_interval(root,a,s['in_s'],s['frame_out']-s['frame_in'])})
  except Exception as e:report['errors'].append(f'Scene {i}: '+str(e))
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
def maybe_queue(studio,p):
 if not p.get('auto_assemble_uploads') or p.get('manual_scene_edits'):return
 if not all(p.get(k) for k in ['srt','voiceover','production_script']):return
 pid=p['id'];root=studio.folder(pid)
 from document_plan import rows_for
 from production import matching_assets
 from reviewed_library import queue_ready
 try:
  ready=queue_ready(p,root)
  if not ready:
   rows,_=rows_for(p)
   ready=bool(rows) and all(len(matching_assets(p,r.get('source_file','')))==1 for r in rows)
 except (ValueError,OSError):return
 if not ready:return
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
  def status_only(state,detail):studio.write(root/'automatic_status.json',{'status':state,'detail':detail,'at':time.time()})
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
 def status(state,detail):
  studio.write(root/'automatic_status.json',{'status':state,'detail':detail,'at':time.time()})
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
  r=subprocess.run([ff,'-hide_banner','-threads','2','-i',str(path),'-map','0:v:0','-vf',"scale=320:-2,blackdetect=d=0.05:pic_th=0.99:pix_th=0.05,select='gt(scene,0.32)',showinfo",'-an','-f','null','-'],capture_output=True,text=True,timeout=600)
  if r.returncode:raise RuntimeError('Full source decode failed: '+a['original_name']+' '+r.stderr[-400:])
  record={'asset_id':a['id'],'file':a['original_name'],'file_signature':key,'sha256':hashlib.sha256(path.read_bytes()).hexdigest(),'duration_s':a['duration'],'complete_decode_success':True,'scene_change_times_s':[float(x) for x in re.findall(r'pts_time:\s*([\d.]+)',r.stderr)],'black_intervals':[dict(start=float(x),end=float(y)) for x,y in re.findall(r'black_start:([\d.]+) black_end:([\d.]+)',r.stderr)],'scope':'Every video frame decoded; shot changes/black intervals indexed. Semantic identity is NOT inferred by this scan.'}
  return record
 videos=[a for a in p['assets'] if a['kind']=='video'];results=[]
 with concurrent.futures.ThreadPoolExecutor(max_workers=3) as pool:
  for result in pool.map(scan,videos):results.append(result);dest.write_text(json.dumps({'sources':results,'complete':False,'source_count':len(videos),'before_timeline_creation':True},indent=2));print('Full video analysis:',len(results),'/',len(videos),result['file'],flush=True)
 report={'sources':results,'complete':len(results)==len(videos),'source_count':len(videos),'before_timeline_creation':True};dest.write_text(json.dumps(report,indent=2));return report

def repetition_errors(p):
 policy=p.get('repetition_policy') or {};window=int(policy.get('cooldown_slots',0))
 if not window:return []
 errors=[];recent=[];assets={a['id']:a for a in p['assets']}
 for i,s in enumerate(p['scenes'],1):
  a=assets.get(s.get('asset_id'));m=s.get('automatic_match') or {};group=m.get('footage_group') or (a or {}).get('footage_group');source=m.get('original_source_file') or ((a or {}).get('original_full_source') or {}).get('file') or (a or {}).get('original_name');start=m.get('original_source_in_s',((a or {}).get('original_full_source') or {}).get('source_in_s',0)+s.get('in_s',0));end=start+(s['frame_out']-s['frame_in'])/30
  for j,g,src,inside,outside,prior_hashes in recent[-window:]:
   if (group and group==g) or (src==source and start<outside-.001 and end>inside+.001):errors.append(f'Scene {i}: repeats footage from scene {j} inside the {window}-slot cooldown.');break
   hs=m.get('perceptual_hashes') or []
   if hs and prior_hashes and min((int(x,16)^int(y,16)).bit_count() for x in hs for y in prior_hashes)<=int(policy.get('visual_hash_max_distance',4)):errors.append(f'Scene {i}: visually matching source footage recurs from scene {j} inside the {window}-slot cooldown.');break
  recent.append((i,group,source,start,end,m.get('perceptual_hashes') or []))
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
