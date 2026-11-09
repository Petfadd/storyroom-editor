"""Build a lossless narration/sound-bite audio preview; never modify source media.

This deliberately does not build a video timeline or subtitles.
"""
from pathlib import Path
import copy, json, subprocess, tempfile, threading, time, uuid
import av
import imageio_ffmpeg
from documentary import annotate_plan, build_timeline

JOBS={}
GUARD=threading.RLock()


def _run_command(cmd):
    result=subprocess.run(cmd,stdout=subprocess.DEVNULL,stderr=subprocess.PIPE,text=True,timeout=1800)
    if result.returncode:raise ValueError('Audio preparation failed: '+result.stderr[-1000:])


def build_audio(root, project, plan, destination, progress=lambda *a:None):
    root=Path(root).resolve();destination=Path(destination);timeline=build_timeline(project,annotate_plan(project,root,copy.deepcopy(plan)))
    if not any(s['kind']=='sound_bite' for s in timeline['segments']):raise ValueError('Add at least one sound bite before preparing documentary audio.')
    assets={a['id']:a for a in project.get('assets',[])};voice=project.get('voiceover') or {};narration=(root/voice.get('path','')).resolve()
    if not narration.is_relative_to(root/'inputs') or not narration.is_file():raise ValueError('The original narration file is missing.')
    ff=imageio_ffmpeg.get_ffmpeg_exe();destination.parent.mkdir(parents=True,exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='audio-',dir=destination.parent) as temp:
        work=Path(temp);parts=[]
        for i,segment in enumerate(timeline['segments']):
            progress(i,len(timeline['segments']))
            if segment['kind']=='narration':source=narration
            else:
                a=assets.get(segment['asset_id'])
                if not a or a.get('kind')!='video':raise ValueError('A sound-bite video is missing.')
                source=(root/a.get('path','')).resolve()
                if not source.is_relative_to(root/'sources') or not source.is_file():raise ValueError('A sound-bite video is missing.')
            with av.open(str(source)) as media:
                if not media.streams.audio:raise ValueError('A selected sound-bite video has no audio stream.')
            start=segment['source_in_s'];end=segment['source_out_s'];part=work/f'{i:04d}.wav'
            filt=f'atrim=start={start:.3f}:end={end:.3f},asetpts=PTS-STARTPTS,aresample=48000,aformat=sample_fmts=s16:sample_rates=48000:channel_layouts=stereo'
            _run_command([ff,'-hide_banner','-loglevel','error','-y','-i',str(source),'-map','0:a:0','-vn','-af',filt,'-c:a','pcm_s16le',str(part)])
            # Refuse to silently extend a short or missing clip with silence.
            with av.open(str(part)) as media:
                stream=media.streams.audio[0];length=float(stream.duration*stream.time_base) if stream.duration is not None else 0
            expected=end-start
            if abs(length-expected)>.025:raise ValueError(f'Audio segment {i+1} decoded as {length:.3f}s instead of {expected:.3f}s. Check the source audio and selected range.')
            parts.append(part)
        listing=work/'concat.txt';listing.write_text(''.join("file '"+str(p).replace("'","'\\''")+"'\n" for p in parts),encoding='utf-8')
        out=work/'result.wav';_run_command([ff,'-hide_banner','-loglevel','error','-y','-f','concat','-safe','0','-i',str(listing),'-c:a','pcm_s16le',str(out)])
        with av.open(str(out)) as media:
            stream=media.streams.audio[0];duration=float(stream.duration*stream.time_base)
        if abs(duration-timeline['duration_s'])>max(.025,len(parts)*.005):raise ValueError('The assembled audio length does not match the planned timeline.')
        out.replace(destination)
    progress(len(timeline['segments']),len(timeline['segments']))
    return {'duration_s':duration,'planned_duration_s':timeline['duration_s'],'segments':timeline['segments']}


def start(root, project, plan, is_current):
    root=Path(root);pid=project['id']
    with GUARD:
        old=JOBS.get(pid)
        if old and old['status'] in ('queued','running'):raise ValueError('Documentary audio is already being prepared.')
        job={'status':'queued','completed':0,'total':0,'detail':'Preparing documentary audio preview.','project_version':project.get('updated'),'plan_revision':plan['revision']};JOBS[pid]=job
    def work():
        output=root/'renders'/('documentary_audio_'+uuid.uuid4().hex+'.wav')
        try:
            with GUARD:job['status']='running'
            def progress(n,total):
                with GUARD:job.update(completed=n,total=total,detail=f'Preparing audio segment {min(n+1,total)} of {total}.')
            result=build_audio(root,project,plan,output,progress)
            if not is_current(project,plan):
                output.unlink(missing_ok=True)
                raise ValueError('The project or sound-bite plan changed during preparation. Prepare audio again.')
            with GUARD:
                job.update(status='done',detail='Documentary audio preview ready. Video and adjusted SRT are not yet built.',output=output.name,**result)
                record=root/'reviews'/'documentary_audio_status.json';record.parent.mkdir(parents=True,exist_ok=True)
                temp=record.with_name(record.name+'.'+uuid.uuid4().hex+'.tmp')
                try:
                    temp.write_text(json.dumps(job,indent=2),encoding='utf-8');temp.replace(record)
                finally:temp.unlink(missing_ok=True)
        except Exception as e:
            output.unlink(missing_ok=True)
            with GUARD:job.update(status='error',detail=str(e))
    threading.Thread(target=work,daemon=True,name='documentary-audio-'+pid).start()
    return dict(job)


def status(pid, root=None):
    with GUARD:
        if pid in JOBS:return dict(JOBS[pid])
        if root:
            try:
                saved=json.loads((Path(root)/'reviews'/'documentary_audio_status.json').read_text(encoding='utf-8'))
                if saved.get('status')=='done' and (Path(root)/'renders'/Path(saved.get('output','')).name).is_file():return saved
            except (OSError,ValueError,TypeError):pass
        return {'status':'idle'}
