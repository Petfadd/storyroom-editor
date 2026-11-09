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
 """Prepare original photographs only; never turn video frames into assets."""
 out=copy.deepcopy(rows)
 for row in out:
  matches=matching_assets(dict(p,source_bindings={}) if row.get('strict_production') else p,row.get('source_file',''))
  status=str(row.get('visual_status','')).upper();treatment=row.get('requested_treatment')
  if treatment=='EDITORIAL FREEZE-FRAME':
   row.setdefault('import_blockers',[]).append('Freeze-frames are prohibited; use an original uploaded photograph or a normal video interval.')
  elif len(matches)==1 and matches[0].get('kind')=='image':
   if matches[0].get('derived_from'):
    row.setdefault('import_blockers',[]).append('Extracted video frames are prohibited; use an original uploaded photograph.')
   elif row.get('strict_production') and status!='STILL' and not row.get('uploaded_photo'):
    row.setdefault('import_blockers',[]).append('A video treatment cannot use a photograph.')
   else:
    row['source_in_ms']=0;row['source_out_ms']=row['timeline_out_ms']-row['timeline_in_ms'];row['visual_status']='PHOTOGRAPH'
  elif status=='STILL' and not row.get('native_static_interval'):
   row.setdefault('import_blockers',[]).append('A still cannot be extracted from video. Supply a named original photograph or a normal video interval.')
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
