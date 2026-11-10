"""Explicitly review-only documentary timeline preview. Never approves an export."""
import copy, json, subprocess, tempfile, threading, uuid
from pathlib import Path
import av, imageio_ffmpeg
from documentary import annotate_plan, build_timeline
from documentary_audio import build_audio, AUDIO_PROCESSING_VERSION

JOBS={};GUARD=threading.RLock()


def picture_plan(project, plan, allow_tail=False):
    timeline=build_timeline(project,plan)
    scenes=project.get('scenes') or []
    if not scenes:raise ValueError('Build the base picture timeline first.')
    cursor=0
    assets={a['id']:a for a in project.get('assets',[])}
    for s in scenes:
        if s['frame_in']!=cursor or s['frame_out']<=cursor:raise ValueError('The base picture timeline has a gap or overlap; review it first.')
        a=assets.get(s.get('asset_id'))
        if not a or a.get('kind')!='video':raise ValueError('This review preview requires an assigned video in every base slot; no picture was substituted.')
        cursor=s['frame_out']
    narration=timeline['narration_duration_s'];tail=cursor/30-narration
    if tail < -.001:raise ValueError('The base picture ends before the narration. Fix the mismatch before previewing.')
    if tail > .001 and (tail>.251 or not allow_tail):raise ValueError(f'The base picture ends {tail:.3f}s after the narration. Confirm this silent picture tail before preparing the review preview.')
    # Each inserted sound bite must land on output frame boundaries. Never
    # silently round a user's selected pause or alter the audio duration.
    bites=[]
    for b in sorted(plan['bites'],key=lambda b:(b['pause_s'],b['created'])):
        pause=round(b['pause_s']*30);frames=round((b['source_out_s']-b['source_in_s'])*30)
        if abs(pause/30-b['pause_s'])>.001 or abs(frames/30-(b['source_out_s']-b['source_in_s']))>.001 or frames<1:
            raise ValueError('The sound-bite pause and duration must align to 30 fps output frames. Choose frame-aligned times; no timing was changed.')
        if b['asset_id'] not in assets:raise ValueError('Sound-bite source is missing.')
        bites.append((pause,frames,b))
    parts=[];pos=0
    def base_until(end):
        nonlocal pos
        while pos<end:
            scene=next((s for s in scenes if s['frame_in']<=pos<s['frame_out']),None)
            if not scene:raise ValueError('A base picture interval has no assigned shot.')
            last=min(end,scene['frame_out']);a=assets[scene['asset_id']]
            start=float(scene.get('in_s') or 0)+(pos-scene['frame_in'])/30
            parts.append({'asset_id':a['id'],'start':start,'frames':last-pos,'kind':'base','scale':int(scene.get('picture_scale') or project.get('picture_scale') or 100)})
            pos=last
    for pause,frames,b in bites:
        base_until(pause);parts.append({'asset_id':b['asset_id'],'start':b['source_in_s'],'frames':frames,'kind':'sound_bite'})
    base_until(cursor)
    for part in parts:
        a=assets[part['asset_id']]
        if part['start']<0 or part['start']+part['frames']/30>float(a.get('duration') or 0)+.001:raise ValueError('A source interval exceeds its uploaded video; no extension or substitute was used.')
    return parts,tail,timeline


def _run(args):
    r=subprocess.run(args,stdout=subprocess.DEVNULL,stderr=subprocess.PIPE,text=True,timeout=1800)
    if r.returncode:raise ValueError('Review preview preparation failed: '+r.stderr[-700:])


def build(root,project,plan,dest,allow_tail=False,progress=lambda *x:None):
    root=Path(root).resolve();dest=Path(dest);plan=annotate_plan(project,root,copy.deepcopy(plan));parts,tail,timeline=picture_plan(project,plan,allow_tail)
    if not plan['bites']:raise ValueError('Add a sound bite before preparing this documentary preview.')
    assets={a['id']:a for a in project['assets']};ff=imageio_ffmpeg.get_ffmpeg_exe();dest.parent.mkdir(parents=True,exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='documentary-review-',dir=dest.parent) as tmp:
        work=Path(tmp);audio=work/'audio.wav'
        from documentary_music import music_path,settings as music_settings
        music=music_settings(project);audio_result=build_audio(root,project,plan,audio,music=bool(music['enabled'] and music['present'] and music_path(root).is_file()))
        clips=[]
        for i,part in enumerate(parts):
            progress(i,len(parts));a=assets[part['asset_id']];src=(root/a.get('path','')).resolve()
            if not src.is_relative_to(root/'sources') or not src.is_file():raise ValueError('A required video source is missing.')
            clip=work/f'{i:04d}.mp4';scale=part.get('scale',100)
            if not 100<=scale<=200:raise ValueError('The picture scale must be between 100 and 200 percent.')
            filt='fps=30,scale=640:360:force_original_aspect_ratio=decrease,pad=640:360:(ow-iw)/2:(oh-ih)/2:black,setsar=1'
            if scale!=100:filt+=f',scale={round(640*scale/100/2)*2}:{round(360*scale/100/2)*2},crop=640:360'
            filt+=',format=yuv420p'
            _run([ff,'-hide_banner','-loglevel','error','-y','-ss',f'{part["start"]:.6f}','-i',str(src),'-an','-vf',filt,'-frames:v',str(part['frames']),'-c:v','libx264','-preset','ultrafast','-crf','23','-threads','2',str(clip)])
            with av.open(str(clip)) as media:
                if not media.streams.video or media.streams.video[0].frames!=part['frames']:raise ValueError('A source did not decode enough frames; preview stopped rather than freezing or looping.')
            clips.append(clip)
        listing=work/'clips.txt';listing.write_text(''.join("file '"+str(p).replace("'","'\\''")+"'\n" for p in clips),encoding='utf-8')
        picture=work/'picture.mp4';_run([ff,'-hide_banner','-loglevel','error','-y','-f','concat','-safe','0','-i',str(listing),'-c','copy',str(picture)])
        duration=sum(x['frames'] for x in parts)/30;result=work/'result.mp4';cmd=[ff,'-hide_banner','-loglevel','error','-y','-i',str(picture),'-i',str(audio),'-map','0:v:0','-map','1:a:0','-c:v','copy']
        if tail>.001:cmd+=['-af',f'apad=pad_dur={tail:.6f}']
        cmd+=['-c:a','aac','-b:a','160k','-t',f'{duration:.6f}','-movflags','+faststart',str(result)];_run(cmd)
        with av.open(str(result)) as media:
            actual=media.streams.video[0].frames
            if actual!=sum(x['frames'] for x in parts):raise ValueError('The review preview frame count is incorrect.')
        result.replace(dest)
    progress(len(parts),len(parts));return {'duration_s':duration,'picture_tail_s':max(0,tail),'audio_processing_version':AUDIO_PROCESSING_VERSION,'narration_lufs':audio_result['narration_lufs'],'sound_bite_levels':audio_result['sound_bite_levels']}


def start(root,project,plan,allow_tail,is_current):
    root=Path(root);pid=project['id']
    with GUARD:
        if JOBS.get(pid,{}).get('status') in ('queued','running'):raise ValueError('A documentary preview is already being prepared.')
        job={'status':'queued','completed':0,'total':0,'detail':'Preparing documentary timeline preview.'};JOBS[pid]=job
    def run():
        output=root/'renders'/('documentary_review_'+uuid.uuid4().hex+'.mp4')
        try:
            with GUARD:job['status']='running'
            def report(n,total):
                with GUARD:job.update(completed=n,total=total,detail=f'Preparing picture {min(n+1,total)} of {total}.')
            result=build(root,project,plan,output,allow_tail,report)
            if not is_current(project,plan):raise ValueError('Project or sound-bite plan changed during preparation. Prepare again.')
            record={'output':output.name,'project_version':project.get('updated'),'plan_revision':plan['revision'],**result}
            with GUARD:
                dest=root/'reviews'/'documentary_review_preview.json';dest.parent.mkdir(parents=True,exist_ok=True);temp=dest.with_name(dest.name+'.'+uuid.uuid4().hex+'.tmp')
                try:temp.write_text(json.dumps(record,indent=2),encoding='utf-8');temp.replace(dest)
                finally:temp.unlink(missing_ok=True)
                job.update(status='done',detail='Documentary timeline review preview ready.',**result)
        except Exception as e:
            output.unlink(missing_ok=True)
            with GUARD:job.update(status='error',detail=str(e))
    threading.Thread(target=run,daemon=True,name='documentary-preview-'+pid).start();return dict(job)


def status(pid):
    with GUARD:return dict(JOBS.get(pid,{}))
