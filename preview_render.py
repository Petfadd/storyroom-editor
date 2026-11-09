"""Low-resolution, temporary playback proxy; never an export or an editorial change."""
import json, shutil, subprocess, threading, time, uuid
from pathlib import Path

import cv2
import imageio_ffmpeg

JOBS={}
GUARD=threading.Lock()


def status(root, pid, updated):
    with GUARD:
        job=JOBS.get(pid)
        if job and job['updated']==updated:
            result=dict(job)
        else:
            path=Path(root)/'.cache/continuous_preview/status.json'
            try:result=json.loads(path.read_text())
            except (OSError,ValueError):return {'status':'idle'}
    if result.get('updated')!=updated:return {'status':'stale'}
    if result.get('status')=='done' and not (Path(root)/'.cache/continuous_preview/preview.mp4').is_file():return {'status':'idle'}
    return result


def start(root, project, on_complete):
    root=Path(root);pid=project['id'];updated=project['updated']
    with GUARD:
        existing=JOBS.get(pid)
        if existing and existing['status'] in ('queued','running'):
            raise ValueError('A smooth preview is already being prepared. Please wait.')
        job={'status':'queued','progress':0,'updated':updated,'detail':'Preparing a temporary preview.'};JOBS[pid]=job
    threading.Thread(target=_run,args=(root,project,job,on_complete),daemon=True,name='preview-'+pid).start()
    return dict(job)


def _run(root,p,job,on_complete):
    ff=imageio_ffmpeg.get_ffmpeg_exe();base=root/'.cache/continuous_preview';base.mkdir(parents=True,exist_ok=True)
    work=base/('work-'+uuid.uuid4().hex);work.mkdir();segments=[]
    def update(**kw):
        with GUARD:job.update(kw)
    try:
        assets={a['id']:a for a in p['assets']};scenes=p['scenes'];last=0
        if not scenes:raise ValueError('Build a storyboard before preparing a preview.')
        for i,s in enumerate(scenes):
            a=assets.get(s.get('asset_id'));frames=s['frame_out']-s['frame_in']
            if not a or frames<=0 or s['frame_in']!=last:raise ValueError(f'Scene {i+1} has missing media or a timeline gap.')
            if s.get('transition')!='cut':raise ValueError('This temporary preview currently supports clean cuts only.')
            path=(root/a['path']).resolve()
            if not path.is_relative_to(root.resolve()) or not path.is_file():raise ValueError(f'Scene {i+1} media is missing.')
            if a['kind']=='video' and (s['in_s']<0 or s['in_s']+frames/30>a['duration']+.001):raise ValueError(f'Scene {i+1} footage is too short.')
            if a['kind'] not in ('video','image'):raise ValueError(f'Scene {i+1} has unsupported media.')
            last=s['frame_out']
        # Fixed light-weight geometry preserves project aspect without changing export settings.
        aspects={'landscape':(16,9),'portrait':(9,16),'square':(1,1),'vertical_4_5':(4,5),'cinema':(239,100)}
        aw,ah=aspects.get(p.get('format'),(16,9));w=max(2,int(640*aw/max(aw,ah))//2*2);h=max(2,int(640*ah/max(aw,ah))//2*2)
        update(status='running')
        for i,s in enumerate(scenes):
            a=assets[s['asset_id']];path=root/a['path'];frames=s['frame_out']-s['frame_in'];out=work/f'{i:05}.mp4'
            update(progress=round(90*i/len(scenes),1),detail=f'Preparing scene {i+1} of {len(scenes)}')
            cmd=[ff,'-nostdin','-y','-v','error','-threads','2']
            if a['kind']=='image':cmd+=['-loop','1','-framerate','30','-i',str(path)]
            else:cmd+=['-ss',str(s['in_s']),'-i',str(path)]
            scale=s.get('picture_scale',p.get('picture_scale',100))
            if isinstance(scale,bool) or not isinstance(scale,int) or not 100<=scale<=200:raise ValueError('Invalid picture scale.')
            sw=max(w,round(w*scale/200)*2);sh=max(h,round(h*scale/200)*2)
            vf=f'scale={w}:{h}:force_original_aspect_ratio=decrease,pad={w}:{h}:(ow-iw)/2:(oh-ih)/2'
            if scale!=100:vf+=f',scale={sw}:{sh},crop={w}:{h}'
            vf+=',fps=30,setsar=1,format=yuv420p'
            cmd+=['-vf',vf,'-frames:v',str(frames),'-an','-c:v','libx264','-preset','ultrafast','-crf','27','-threads','2','-video_track_timescale','90000',str(out)]
            result=subprocess.run(cmd,stdout=subprocess.DEVNULL,stderr=subprocess.PIPE,timeout=max(120,frames/30*20))
            if result.returncode:raise ValueError(f'Scene {i+1} could not be decoded: '+result.stderr.decode(errors='replace')[-400:])
            cap=cv2.VideoCapture(str(out));count=int(cap.get(cv2.CAP_PROP_FRAME_COUNT));cap.release()
            if count!=frames:raise ValueError(f'Scene {i+1} produced {count} of {frames} frames; preview stopped rather than freezing or shortening it.')
            segments.append(out)
        listing=work/'files.txt';listing.write_text(''.join("file '"+str(x.resolve()).replace("'", "'\\''")+"'\n" for x in segments))
        update(progress=92,detail='Joining clips and continuous narration')
        cmd=[ff,'-nostdin','-y','-v','error','-f','concat','-safe','0','-i',str(listing)]
        voice=p.get('voiceover')
        if voice:
            audio=(root/voice['path']).resolve()
            if not audio.is_relative_to(root.resolve()) or not audio.is_file():raise ValueError('Narration file is missing.')
            cmd+=['-i',str(audio),'-map','0:v:0','-map','1:a:0','-c:a','aac','-b:a','96k']
        else:cmd+=['-map','0:v:0','-an']
        temp=work/'preview.mp4';cmd+=['-t',str(last/30),'-c:v','copy','-movflags','+faststart',str(temp)]
        result=subprocess.run(cmd,stdout=subprocess.DEVNULL,stderr=subprocess.PIPE,timeout=max(120,last/30*4))
        if result.returncode:raise ValueError('Could not join preview: '+result.stderr.decode(errors='replace')[-400:])
        cap=cv2.VideoCapture(str(temp));count=int(cap.get(cv2.CAP_PROP_FRAME_COUNT));cap.release()
        if count!=last:raise ValueError(f'Preview has {count} frames instead of {last}.')
        # The caller checks the version and publishes under the project lock.
        if not on_complete(p['updated'],temp,base):update(status='stale',detail='The project changed; prepare a new preview.')
        else:update(status='done',progress=100,detail='Temporary smooth preview ready.')
    except Exception as e:update(status='error',detail=str(e)[:600])
    finally:shutil.rmtree(work,ignore_errors=True)
