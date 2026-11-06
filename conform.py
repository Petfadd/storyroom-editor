"""Local media conform: explicit source intervals and extracted-frame previews, not semantic video AI."""
import copy,hashlib,math,threading
from pathlib import Path
import cv2
from production import asset_matches,matching_assets
_SAMPLE_LOCK=threading.RLock()
def capture(path):
 if hasattr(cv2,'CAP_PROP_N_THREADS'):
  cap=cv2.VideoCapture(str(path),cv2.CAP_FFMPEG,[cv2.CAP_PROP_N_THREADS,2])
  if cap.isOpened():return cap
  cap.release()
 return cv2.VideoCapture(str(path))

def stills(p,root,rows):
 """Prepare every still beat for assembly.

 Two cases, both real:
 - the beat names an uploaded photograph, which is used as-is for its whole length;
 - the beat asks for a STILL moment inside an uploaded video, which is extracted once
   at exactly the requested time and registered as its own approved asset.

 Nothing is cropped to fake a longer shot and no extracted frame is treated as a
 moving picture. A still that cannot be prepared is reported on its row, never
 replaced by an unrelated clip.
 """
 out=copy.deepcopy(rows);root=Path(root)
 for row in out:
  matches=matching_assets(p,row.get('source_file',''))
  status=str(row.get('visual_status','')).upper()
  if len(matches)==1 and matches[0]['kind']=='image':
   # An uploaded photograph covers the whole beat at native length.
   row['source_in_ms']=0;row['source_out_ms']=row['timeline_out_ms']-row['timeline_in_ms']
   row['visual_status']='PHOTOGRAPH'
   continue
  if status!='STILL':continue
  if len(matches)!=1 or matches[0]['kind']!='video':continue
  source=matches[0];path=root/source['path'];ms=row['source_in_ms'];key=hashlib.sha256((source['id']+str(ms)).encode()).hexdigest()[:16];aid='frame-'+key
  existing=next((a for a in p['assets'] if a['id']==aid),None)
  try:
   if not path.is_file():raise ValueError('The local source file is missing.')
   if ms/1000>=source['duration']:raise ValueError('Requested still is beyond the source duration.')
   dest=root/'assets'/(aid+'.jpg')
   if not dest.exists():
    cap=capture(path)
    try:
     fps=cap.get(cv2.CAP_PROP_FPS)
     if not math.isfinite(fps) or fps<=0:raise ValueError('Cannot determine the source frame rate.')
     index=round(ms/1000*fps);cap.set(cv2.CAP_PROP_POS_FRAMES,index);ok,frame=cap.read()
     if not ok:raise ValueError('Cannot decode the requested still frame.')
     if not cv2.imwrite(str(dest),frame,[cv2.IMWRITE_JPEG_QUALITY,94]):raise ValueError('Cannot save extracted frame.')
    finally:cap.release()
   name='frame_'+Path(source.get('original_name',source['name'])).stem+'_'+str(ms)+'.jpg'
   if not existing:
    existing={'id':aid,'name':name,'original_name':name,'path':dest.relative_to(root).as_posix(),'kind':'image','duration':0,'source':source.get('source',''),'people':[],'category':'general','verified':False,'rights':source.get('rights','unreviewed'),'notes':'Frame extracted automatically at the document-requested time; content/identity not visually verified. '+str(row.get('coverage_review','')),'derived_from':{'asset_id':source['id'],'source_file':source.get('original_name',source['name']),'requested_time_ms':ms,'source_id':row.get('source_id','')},'editorial_source_id':row.get('source_id','')};p['assets'].append(existing)
    if source.get('verified') and (p.get('media_approval_policy') or {}).get('auto_upload'):
     from approval import approve_asset
     approve_asset(p,existing,'derived_frame_from_approved_local_upload')
   row['source_file']=name;row['source_in_ms']=0;row['source_out_ms']=row['timeline_out_ms']-row['timeline_in_ms'];row['visual_status']='STILL_EXTRACTED';row['extracted_from_file']=source.get('original_name',source['name']);row['extracted_frame_ms']=ms
  except Exception as e:row.setdefault('import_blockers',[]).append('Still extraction failed: '+str(e))
 return out

def samples(root,asset,cut):
 with _SAMPLE_LOCK:return _samples(root,asset,cut)

def _samples(root,asset,cut):
 """One contact strip of IN / middle / final frame. Never labels its content automatically.

 A photograph has no middle or last frame: it is shown once, labelled as a still, so the
 strip never implies movement that the source does not have.
 """
 from PIL import Image,ImageOps,ImageDraw
 root=Path(root);path=root/asset['path']
 if asset.get('kind')=='image':
  key=hashlib.sha256((str(path)+str(path.stat().st_mtime_ns)+'still').encode()).hexdigest()[:24]
  dest=root/'.cache/planned_samples'/(key+'.jpg');dest.parent.mkdir(parents=True,exist_ok=True)
  if not dest.exists():
   canvas=Image.new('RGB',(960,210),'#14212b');draw=ImageDraw.Draw(canvas)
   im=ImageOps.contain(ImageOps.exif_transpose(Image.open(path)).convert('RGB'),(632,178))
   canvas.paste(im,((960-im.width)//2,5+(178-im.height)//2))
   draw.text((8,190),'photograph | whole beat | no movement added',fill='white')
   canvas.save(dest,quality=85)
  return dest
 inside=cut['source_in_ms'];outside=cut['source_out_ms']
 if not path.is_file():raise ValueError('Source file not found.')
 key=hashlib.sha256((str(path)+str(path.stat().st_mtime_ns)+str(inside)+str(outside)).encode()).hexdigest()[:24]
 dest=root/'.cache/planned_samples'/(key+'.jpg');dest.parent.mkdir(parents=True,exist_ok=True)
 if not dest.exists():
  canvas=Image.new('RGB',(960,210),'#14212b');draw=ImageDraw.Draw(canvas);cap=capture(path)
  try:
   fps=cap.get(cv2.CAP_PROP_FPS) or 30
   points=[inside,(inside+outside)//2,max(inside,outside-round(1000/fps))]
   for i,ms in enumerate(points):
    cap.set(cv2.CAP_PROP_POS_MSEC,ms);ok,frame=cap.read()
    if not ok:raise ValueError('Could not decode a planned-cut sample at '+str(ms)+' ms.')
    im=Image.fromarray(cv2.cvtColor(frame,cv2.COLOR_BGR2RGB));im=ImageOps.contain(im,(316,178));canvas.paste(im,(i*320+(320-im.width)//2,5+(178-im.height)//2));draw.text((i*320+8,190),str(ms/1000)+'s | '+['IN','MIDDLE','LAST'][i],fill='white')
  finally:cap.release()
  canvas.save(dest,quality=85)
 return dest
