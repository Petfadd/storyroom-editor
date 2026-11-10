"""Build a lossless narration/sound-bite audio preview; never modify source media.

This deliberately does not build a video timeline or subtitles.
"""
from pathlib import Path
import copy, json, math, re, shutil, subprocess, tempfile, threading, uuid
import av
import imageio_ffmpeg
from documentary import annotate_plan, build_timeline

JOBS={}
GUARD=threading.RLock()
# v2: the preview mix no longer bakes in background music; the bed is a live
# layer in the browser. A v1 mix still sounds like the old bed, so it is rejected.
AUDIO_PROCESSING_VERSION='narration-loudness-match-v2'


def _run_command(cmd):
    result=subprocess.run(cmd,stdout=subprocess.DEVNULL,stderr=subprocess.PIPE,text=True,timeout=1800)
    if result.returncode:raise ValueError('Audio preparation failed: '+result.stderr[-1000:])


def measure_loudness(ff, path):
    """EBU R128 integrated loudness/true peak. Unmeasurable speech fails closed."""
    result=subprocess.run([ff,'-hide_banner','-nostats','-i',str(path),'-vn','-af','loudnorm=I=-16:TP=-1:LRA=11:print_format=json','-f','null','-'],stdout=subprocess.DEVNULL,stderr=subprocess.PIPE,text=True,timeout=1800)
    match=re.search(r'\{\s*"input_i".*?\}',result.stderr,re.S)
    if result.returncode or not match:raise ValueError('Could not measure audio loudness. No automatic level change was applied.')
    try:
        data=json.loads(match.group());integrated=float(data['input_i']);peak=float(data['input_tp'])
        if not math.isfinite(integrated) or not math.isfinite(peak):raise ValueError()
    except (ValueError,KeyError,TypeError):raise ValueError('A narration or sound-bite segment is silent or too short to measure safely. Review its audio.')
    return {'integrated_lufs':integrated,'true_peak_db':peak}


def build_audio(root, project, plan, destination, progress=lambda *a:None, strict_handoffs=False, music=False, compact_preview=False):
    root=Path(root).resolve();destination=Path(destination).resolve();timeline=build_timeline(project,annotate_plan(project,root,copy.deepcopy(plan)))
    if not any(s['kind']=='sound_bite' for s in timeline['segments']):raise ValueError('Add at least one sound bite before preparing documentary audio.')
    assets={a['id']:a for a in project.get('assets',[])};voice=project.get('voiceover') or {};narration=(root/voice.get('path','')).resolve()
    if not narration.is_relative_to(root/'inputs') or not narration.is_file():raise ValueError('The original narration file is missing.')
    ff=imageio_ffmpeg.get_ffmpeg_exe();destination.parent.mkdir(parents=True,exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='audio-',dir=destination.parent) as temp:
        work=Path(temp);parts=[];levels=[];segment_lengths=[]
        # Measure the original narration without changing its samples. Convert it
        # to the same stereo format as the extracted sound bites for comparison.
        reference=work/'narration-reference.wav'
        _run_command([ff,'-hide_banner','-loglevel','error','-y','-i',str(narration),'-map','0:a:0','-vn','-af',f'atrim=start=0:end={timeline["narration_duration_s"]:.3f},asetpts=PTS-STARTPTS,aresample=48000,aformat=sample_fmts=s16:sample_rates=48000:channel_layouts=stereo','-c:a','pcm_s16le',str(reference)])
        narration_level=measure_loudness(ff,reference)
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
            if strict_handoffs and i < len(timeline['segments'])-1 and abs(length-expected)>2/48000+.00000001:
                raise ValueError(f'Audio segment {i+1} does not end on the planned sample boundary; final assembly stopped.')
            if segment['kind']=='sound_bite':
                measured=measure_loudness(ff,part)
                # Limit extreme corrections and protect peaks. The only audio being
                # processed is this selected source interval; narration stays untouched.
                requested=narration_level['integrated_lufs']-measured['integrated_lufs']
                gain=max(-12.0,min(24.0,requested));adjusted=work/f'{i:04d}-levelled.wav'
                _run_command([ff,'-hide_banner','-loglevel','error','-y','-i',str(part),'-af',f'volume={gain:.4f}dB,alimiter=limit=0.794328:level=0:latency=1','-c:a','pcm_s16le',str(adjusted)])
                with av.open(str(adjusted)) as media:
                    stream=media.streams.audio[0];adjusted_length=float(stream.duration*stream.time_base) if stream.duration is not None else 0
                if abs(adjusted_length-expected)>.025:raise ValueError('Level matching changed the selected sound-bite duration; preview stopped.')
                if strict_handoffs and abs(adjusted_length-expected)>2/48000+.00000001:raise ValueError('Level matching changed the sound-bite sample boundary; final assembly stopped.')
                final=measure_loudness(ff,adjusted);levels.append({'asset_name':segment.get('asset_name'),'original_lufs':round(measured['integrated_lufs'],2),'gain_db':round(gain,2),'result_lufs':round(final['integrated_lufs'],2),'result_true_peak_db':round(final['true_peak_db'],2),'gain_capped':abs(gain-requested)>.01})
                part=adjusted
            segment_lengths.append(adjusted_length if segment['kind']=='sound_bite' else length);parts.append(part)
        listing=work/'concat.txt';listing.write_text(''.join("file '"+str(p).replace("'","'\\''")+"'\n" for p in parts),encoding='utf-8')
        speech=work/'speech.wav';_run_command([ff,'-hide_banner','-loglevel','error','-y','-f','concat','-safe','0','-i',str(listing),'-c:a','pcm_s16le',str(speech)])
        music_result=None
        out=work/'result.wav'
        if music:
            from documentary_music import build_bed, music_path
            bed=work/'music-bed.wav'
            music_result=build_bed(project,music_path(root),bed,timeline['duration_s'],timeline['segments'])
            # Narration and bites stay at their reviewed level; only the bed is ducked.
            _run_command([ff,'-hide_banner','-loglevel','error','-y','-i',str(speech),'-i',str(bed),'-filter_complex','[0:a][1:a]amix=inputs=2:duration=first:dropout_transition=0:normalize=0,alimiter=limit=0.891251:level=0:latency=1','-ar','48000','-c:a','pcm_s16le',str(out)])
        else:
            shutil.copyfile(speech,out)
        with av.open(str(out)) as media:
            stream=media.streams.audio[0];duration=float(stream.duration*stream.time_base)
        if abs(duration-timeline['duration_s'])>max(.025,len(parts)*.005):raise ValueError('The assembled audio length does not match the planned timeline.')
        # The assembly is lossless WAV, but delivering 150MB of PCM made the
        # browser buffer for a long time before playback could start. The review
        # preview is encoded to a small streamable file; the lossless assembly is
        # still what was measured, and nothing upstream is re-encoded.
        # The browser review wants a small, streamable file; the assembly and the
        # bit-exact narration handoff want the lossless one. Only encode when the
        # caller explicitly asked for a preview.
        preview=encode_preview(ff,out) if compact_preview and str(destination).lower().endswith('.m4a') else None
        if preview:
            preview.replace(destination)
            out.unlink(missing_ok=True)
        else:
            out.replace(destination)
    progress(len(timeline['segments']),len(timeline['segments']))
    return {'duration_s':duration,'planned_duration_s':timeline['duration_s'],'segments':timeline['segments'],'audio_processing_version':AUDIO_PROCESSING_VERSION,'narration_lufs':round(narration_level['integrated_lufs'],2),'sound_bite_levels':levels,'segment_lengths_s':segment_lengths,'music':music_result}


def encode_preview(ff, source):
    """Encode the assembled preview compactly so it starts playing quickly.

    Returns the encoded file, or None when encoding is unavailable, in which case
    the lossless WAV is delivered unchanged.
    """
    target=source.with_suffix('.m4a')
    if target==source:return None
    try:
        _run_command([ff,'-hide_banner','-loglevel','error','-y','-i',str(source),
                      '-map','0:a:0','-c:a','aac','-b:a','192k','-ar','48000',
                      '-movflags','+faststart',str(target)])
        return target if target.is_file() and target.stat().st_size>0 else None
    except Exception:
        target.unlink(missing_ok=True)
        return None


def signature(project, plan):
    """What the audio mix actually depends on.

    Deliberately excludes music level, music on/off and ducking: those are applied
    live in the browser, so they can change without the mix being stale. Picture,
    narration and the bites are what the mix is built from.
    """
    import hashlib, json
    voice=(project.get('voiceover') or {})
    payload={
        'narration':{'path':voice.get('path'),'duration':voice.get('duration')},
        'bites':[{k:b.get(k) for k in ('asset_id','asset_revision','pause_s','source_in_s','source_out_s','narration_size','narration_mtime_ns')} for b in (plan.get('bites') or [])],
        'revision':plan.get('revision'),
    }
    return hashlib.sha256(json.dumps(payload,sort_keys=True,default=str).encode()).hexdigest()

def prune_renders(root, keep):
    """Delete superseded documentary audio previews for this project.

    Each rebuild used to leave its own 150MB file behind, so repeated rebuilds
    quietly filled the disk and made the project slow to work with.
    """
    import re
    renders=Path(root)/'renders'
    try:
        for path in renders.glob('documentary_audio_*'):
            if path.name!=keep and path.is_file() and re.fullmatch(r'documentary_audio_[a-f0-9]{32}\.(wav|m4a)',path.name):
                path.unlink(missing_ok=True)
    except OSError:
        pass


def start(root, project, plan, is_current, music=False):
    root=Path(root);pid=project['id']
    with GUARD:
        old=JOBS.get(pid)
        if old and old['status'] in ('queued','running'):raise ValueError('Documentary audio is already being prepared.')
        job={'status':'queued','completed':0,'total':0,'detail':'Preparing documentary audio preview.','project_version':project.get('updated'),'plan_revision':plan['revision']};JOBS[pid]=job
    def work():
        output=root/'renders'/('documentary_audio_'+uuid.uuid4().hex+'.m4a')
        try:
            with GUARD:job['status']='running'
            def progress(n,total):
                with GUARD:job.update(completed=n,total=total,detail=f'Preparing audio segment {min(n+1,total)} of {total}.')
            result=build_audio(root,project,plan,output,progress,music=music,compact_preview=True)
            if not is_current(project,plan):
                output.unlink(missing_ok=True)
                raise ValueError('The project or sound-bite plan changed during preparation. Prepare audio again.')
            prune_renders(root,output.name)
            signature_value=signature(project,plan)
            with GUARD:
                job.update(status='done',audio_signature=signature_value,detail='Documentary audio preview ready. Video and adjusted SRT are not yet built.',output=output.name,**result)
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
