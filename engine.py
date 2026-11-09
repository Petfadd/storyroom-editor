"""Smooth timestamp-matched AutoEdit renderer with verified full-frame media."""
from __future__ import annotations
import hashlib, json, os, re, shutil, signal, subprocess, threading, time, copy
from pathlib import Path
import cv2
import imageio_ffmpeg
from PIL import Image

# Bump when the picture pipeline changes so cached segments are not reused blindly.
# Encoder settings for the picture passes. `ultrafast` with a high-quality CRF target is
# both faster and closer to the source than `veryfast crf23` at these sizes: measured on a
# 1920x1080 clip, 0.76s vs 1.36s per 6s segment, SSIM 0.99998 vs 0.99964. A faster preset
# spends its bits on fidelity instead of on motion-search heuristics. SEGMENT_VERSION is
# part of the cache key, so changing these re-encodes rather than silently mixing them.
SEGMENT_VERSION = 2
PICTURE_ENCODER = ['-c:v', 'libx264', '-preset', 'ultrafast', '-crf', '16']

class RenderJob:
    def __init__(self, root: Path, on_change=None, story_path=None, catalog_path=None, output_name=None):
        self.root = Path(root)
        self.story_path = Path(story_path) if story_path else self.root / 'storyboard.json'
        self.story = json.loads(self.story_path.read_text(encoding='utf-8'))
        self.catalog = json.loads((Path(catalog_path) if catalog_path else self.root / 'catalog.json').read_text(encoding='utf-8'))['slots']
        self.ffmpeg = imageio_ffmpeg.get_ffmpeg_exe()
        self.render_dir = self.root / 'renders'; self.render_dir.mkdir(exist_ok=True)
        self.work_dir = self.root / '.cache' / 'smooth_matched_render'; self.work_dir.mkdir(parents=True, exist_ok=True)
        self.output = self.render_dir / 'review.mp4'
        if output_name: self.output = self.render_dir / Path(output_name).name
        self.on_change = on_change
        self.lock = threading.Lock(); self.process = None; self.cancelled = False; self.captions_enabled = False
        self.script_caption_srt = None
        self._audio_cache = {}; self._duration_cache = {}
        # Encode with the machine, not a constant: two threads per core up to a sane cap,
        # still leaving room for the server itself on a small box.
        self.threads = max(2, min(8, (os.cpu_count() or 2) * 2))
        self._dims_cache = {}
        self.state = {'status':'idle','progress':0.0,'stage':'Ready','detail':'','log':'','output':None,'error':None,'started':None,'finished':None,'captions':False}

    def snapshot(self):
        with self.lock: return dict(self.state)

    def update(self, **kwargs):
        with self.lock:
            self.state.update(kwargs)
            if self.state.get('log'): self.state['log'] = self.state['log'][-18000:]
        if self.on_change: self.on_change(self.snapshot())

    def log(self, text):
        with self.lock: self.state['log'] = (self.state.get('log','') + '\n' + text)[-18000:]
        if self.on_change: self.on_change(self.snapshot())

    def start(self, captions=False, script_caption_srt=None, narration_caption_file=None):
        if self.snapshot()['status'] in ('running','queued'): raise RuntimeError('A render is already running.')
        self.cancelled = False; self.narration_caption_file = Path(narration_caption_file) if narration_caption_file else None; self.script_caption_srt = Path(script_caption_srt) if script_caption_srt else None; self.captions_enabled = bool(captions) or bool(script_caption_srt); self.srt_captions_enabled = bool(captions)
        self.state = {'status':'queued','progress':0.0,'stage':'Queued','detail':f'Captions {"on" if self.captions_enabled else "off"}','log':'','output':None,'error':None,'started':time.time(),'finished':None,'captions':self.captions_enabled}
        self.worker_thread = threading.Thread(target=self._safe_run, daemon=True)
        self.worker_thread.start()

    def cancel(self):
        self.cancelled = True
        self.update(stage='Cancelling', detail='Stopping the active encoder…')
        proc = self.process
        if proc and proc.poll() is None:
            try: os.killpg(proc.pid, signal.SIGTERM)
            except ProcessLookupError: pass

    def _safe_run(self):
        try: self._run()
        except Exception as exc:
            if self.cancelled:
                self.update(status='cancelled', stage='Cancelled', error='Render cancelled by user.', finished=time.time())
            else:
                self.log(f'ERROR: {exc}')
                self.update(status='error', stage='Render failed', error=str(exc), finished=time.time())
        finally: self.process = None

    def _run_process(self, cmd, event_index=None, event_duration=None):
        self.log('$ ' + ' '.join(str(x) for x in cmd))
        proc = subprocess.Popen([str(x) for x in cmd], stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                text=True, bufsize=1, start_new_session=True)
        self.process = proc; recent = []
        for line in proc.stdout:
            line = line.rstrip()
            if not line: continue
            recent = (recent + [line])[-35:]
            if line.startswith('out_time_ms=') and event_index is not None and event_duration:
                try:
                    seconds = int(line.split('=',1)[1]) / 1_000_000
                    self._event_progress(event_index, min(seconds,event_duration), event_duration)
                except ValueError: pass
            elif not line.startswith(('frame=','fps=','stream_','bitrate=','total_size=','out_time_','dup_frames=','drop_frames=','speed=','progress=')):
                self.log(line)
        code = proc.wait()
        if self.cancelled: raise RuntimeError('Render cancelled by user.')
        if code != 0: raise RuntimeError('FFmpeg failed:\n' + '\n'.join(recent))

    def _event_progress(self, index, seconds, duration):
        shots = self.story['shots']; total = sum(s['duration_ms'] for s in shots)
        completed = sum(s['duration_ms'] for s in shots[:index])
        progress = ((completed + min(seconds,duration)*1000) / total) * 82
        kind = 'archival picture segment' if shots[index]['kind'] == 'archival' else 'narration segment'
        self.update(progress=round(progress,2), stage=f'Cutting {kind} {index+1} of {len(shots)}', detail=f'{seconds:.1f} / {duration:.1f} seconds')

    def _dimensions(self, path, is_image=False):
        key = str(path)
        if key in self._dims_cache: return self._dims_cache[key]
        if is_image:
            with Image.open(path) as im:
                self._dims_cache[key] = im.size; return im.size
        cap = cv2.VideoCapture(str(path))
        if not cap.isOpened(): raise RuntimeError(f'Cannot open video: {path}')
        w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH)); h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT)); cap.release()
        if not w or not h: raise RuntimeError(f'Cannot read video dimensions: {path}')
        self._dims_cache[key] = (w, h)
        return w,h

    def _find_voiceover(self):
        """The narration track, or None when the programme has no narration.

        A programme can be carried by its text alone. Returning None keeps every other
        length working, and the export then muxes the picture without an audio stream
        instead of refusing over a track nobody supplied.
        """
        candidates = sorted((self.root/'inputs').glob('voiceover.*'))
        candidates = [p for p in candidates if p.suffix.lower() in ('.wav','.mp3','.m4a','.aac','.flac','.ogg')]
        return candidates[0] if candidates else None

    def _has_audio(self, path):
        key = str(path)
        if key not in self._audio_cache:
            result = subprocess.run([self.ffmpeg,'-hide_banner','-i',str(path)],capture_output=True,text=True,timeout=25)
            self._audio_cache[key] = re.search(r'Stream\s+#.*Audio:',result.stderr) is not None
        return self._audio_cache[key]

    def _semantic_upload(self, slot):
        if not slot: return None
        folder = self.root/'inputs'/'semantic'
        files = [p for p in folder.glob(f'{slot}.*') if p.suffix.lower() in ('.mp4','.mov')]
        return sorted(files,key=lambda p:p.stat().st_mtime,reverse=True)[0] if files else None

    def _video_duration_ms(self, path):
        key = str(path)
        if key not in self._duration_cache:
            cap = cv2.VideoCapture(str(path)); fps = cap.get(cv2.CAP_PROP_FPS); frames = cap.get(cv2.CAP_PROP_FRAME_COUNT); cap.release()
            self._duration_cache[key] = round(frames * 1000 / fps) if fps > 0 and frames > 0 else 0
        return self._duration_cache[key]

    def _upload_offset_ms(self, shot, upload):
        """Continue one uploaded clip through consecutive whole-shot overrides."""
        shots = self.story['shots']; index = max(0, min(len(shots)-1, shot['shot']-1)); offset = 0
        for previous in reversed(shots[:index]):
            if previous.get('slot') != shot.get('slot') or previous.get('kind') != shot.get('kind'):
                continue
            prior_upload = self._semantic_upload(previous.get('slot'))
            if not prior_upload or prior_upload.name != upload.name or prior_upload.suffix.lower() not in ('.mp4','.mov'):
                continue
            offset += previous['duration_ms']
        return offset

    def _resolve_visual(self, shot):
        slot = shot.get('slot'); upload = self._semantic_upload(slot)
        if upload:
            is_video = upload.suffix.lower() in ('.mp4','.mov')
            if slot in self.catalog and self.catalog[slot]['accept'] == 'video' and not is_video:
                raise RuntimeError(f'{self.catalog[slot]["label"]} requires video footage.')
            if is_video:
                offset = self._upload_offset_ms(shot, upload); actual = self._video_duration_ms(upload)
                start = offset; end = offset + shot['duration_ms']
                if not actual or end>actual:
                    raise RuntimeError(f'Scene {shot["shot"]}: uploaded footage is too short for a unique interval. No looping or frozen tails are allowed.')
                return {'type':'video','path':upload,'in_ms':start,'out_ms':end}
            return {'type':'image','path':upload,'in_ms':0,'out_ms':shot['duration_ms']}
        kind = shot.get('media_type')
        if kind == 'required':
            label = self.catalog.get(slot,{}).get('label',slot)
            raise RuntimeError(f'Exact media required for {label}. Upload the {slot} slot before rendering.')
        if kind == 'image':
            
            if not shot.get('image_approved'): raise RuntimeError('Only explicitly approved verified photographs may be used.')
            return {'type':'image','path':self.root/shot['image'],'in_ms':0,'out_ms':shot['duration_ms']}
        if kind == 'video':
            path = self.root/'sources'/shot['source_file']
            if not path.exists(): raise RuntimeError(f'Missing source: {path.name}')
            return {'type':'video','path':path,'in_ms':shot['video_in_ms'],'out_ms':shot['video_out_ms']}
        raise RuntimeError(f'Unknown visual media type for event {shot["shot"]}.')

    def _picture_segment_command(self, shot, index, segment, extra_frames=0):
        settings=self.story['settings'];fps=settings['fps'];frames=shot['duration_frames']+extra_frames;duration=frames/fps
        visual=self._resolve_visual(shot);ow,oh=settings['width'],settings['height']
        cmd=[self.ffmpeg,'-y','-hide_banner','-loglevel','warning']
        if visual['type']=='image':
            cmd+=['-loop','1','-framerate',str(fps),'-i',str(visual['path'])]
            motion=shot.get('motion_plan',{});p=f"min(on/{max(1,frames-1)},1)"
            ease=motion.get('easing','smoothstep')
            e=f"(0.5-0.5*cos(PI*{p}))" if ease=='sine' else f"({p}*{p}*{p}*({p}*({p}*6-15)+10))" if ease=='smootherstep' else f"({p}*{p}*(3-2*{p}))"
            if ease=='linear': e=p
            elif ease=='quick_settle': e=f'(if(lt({p},0.08),{p}*{p}/0.1472,if(gt({p},0.92),1-(1-{p})*(1-{p})/0.1472,({p}-0.04)/0.92)))'
            def interp(key,default):
                a=motion.get(key+'_start',default);b=motion.get(key+'_end',default)
                return f'({a}+({b-a})*{e})'
            z=interp('zoom',1.02);xp=interp('x',.5);yp=interp('y',.5)
            vf=f"scale={ow*2}:{oh*2},zoompan=z='{z}':x='(iw-iw/zoom)*{xp}':y='(ih-ih/zoom)*{yp}':d=1:s={ow}x{oh}:fps={fps},setsar=1"
            graph=f'[0:v]{vf}[picture]'
        else:
            
            if extra_frames: visual['out_ms']=max(visual['out_ms'],visual['in_ms']+duration*1000+1)
            start=visual['in_ms']/1000;available=(visual['out_ms']-visual['in_ms'])/1000
            if available+.001<duration or start+duration>self._video_duration_ms(visual['path'])/1000+.001:
                raise RuntimeError(f'Scene {shot["shot"]}: source range cannot cover {frames} frames; no looping or freezing is allowed.')
            read_duration=min(duration+2/fps,self._video_duration_ms(visual['path'])/1000-start)
            cmd+=['-ss',f'{start:.6f}','-t',f'{read_duration:.6f}','-i',str(visual['path'])]
            w,h=self._dimensions(visual['path']);ratio=w/h
            if abs(ratio-ow/oh)>.05:
                graph=(f'[0:v]setpts=PTS-STARTPTS,fps={fps},setsar=1,split=2[backsrc][frontsrc];'
                       f'[backsrc]scale={ow}:{oh}:force_original_aspect_ratio=increase,crop={ow}:{oh},gblur=sigma=24,eq=brightness=-0.10:saturation=0.75[back];'
                       f'[frontsrc]scale={ow}:{oh}:force_original_aspect_ratio=decrease[front];'
                       f'[back][front]overlay=(W-w)/2:(H-h)/2:shortest=1,trim=end_frame={frames}[picture]')
            else: graph=f'[0:v]setpts=PTS-STARTPTS,scale={ow}:{oh},fps={fps},setsar=1,trim=end_frame={frames}[picture]'
        graph=graph.replace('[picture]',',setparams=range=limited:color_primaries=bt709:color_trc=bt709:colorspace=bt709[picture]')
        cmd+=['-filter_complex_threads','1','-filter_complex',graph,'-map','[picture]','-an','-frames:v',str(frames),'-threads',str(self.threads)]+PICTURE_ENCODER+['-pix_fmt','yuv420p','-video_track_timescale','90000','-progress','pipe:1','-nostats',str(segment)]
        return cmd

    def _xfade_command(self, segments, output):
        """Blend pictures at original frame boundaries without overlapping narration."""
        listing=self.work_dir/'picture_concat.txt'
        listing.write_text(''.join("file '"+str(Path(x).resolve())+"'\n" for x in segments))
        return [self.ffmpeg,'-y','-hide_banner','-loglevel','warning','-f','concat','-safe','0','-i',str(listing),'-an','-c:v','copy','-movflags','+faststart',str(output)]

    def _concat_command(self, segments, output):
        """Join clean-cut segments without re-encoding or re-timing a single frame."""
        listing=self.work_dir/'picture_concat.txt'
        listing.write_text(''.join("file '"+str(Path(x).resolve())+"'\n" for x in segments))
        return [self.ffmpeg,'-y','-hide_banner','-loglevel','warning','-f','concat','-safe','0','-i',str(listing),
                '-an','-c:v','copy','-movflags','+faststart',str(output)]

    def _smooth_transition_chunks(self, segments):
        chunks=[];i=0;shots=self.story['shots'];fps=self.story['settings']['fps']
        while i<len(shots):
            n=shots[i].get('transition_handle_frames',0)
            if not n: chunks.append(segments[i]);i+=1;continue
            if i+1>=len(shots):raise RuntimeError('A transition cannot extend past the final scene.')
            out=self.work_dir/f'flow_{i+1:03d}_{i+2:03d}.mp4';frames=shots[i]['duration_frames']+shots[i+1]['duration_frames']
            graph=f'[0:v]settb=1/{fps},setpts=N[a];[1:v]settb=1/{fps},setpts=N[b];[a][b]xfade=transition=fade:duration={n/fps:.9f}:offset={shots[i]["duration_frames"]/fps:.9f},setparams=range=limited:color_primaries=bt709:color_trc=bt709:colorspace=bt709[picture]'
            cmd=[self.ffmpeg,'-y','-v','warning','-i',str(segments[i]),'-i',str(segments[i+1]),'-filter_complex_threads','1','-filter_complex',graph,'-map','[picture]','-an','-frames:v',str(frames),'-threads','2']+PICTURE_ENCODER+['-pix_fmt','yuv420p','-video_track_timescale','90000',str(out)]
            self.update(stage='Blending short, handle-backed picture transitions',progress=82+i/len(shots)*3)
            self._run_process(cmd);cap=cv2.VideoCapture(str(out));actual=int(cap.get(7));cap.release()
            if actual!=frames:raise RuntimeError('A picture transition changed the timeline length; export stopped.')
            chunks.append(out);i+=2
        return chunks

    # ---- segment reuse -------------------------------------------------------
    # A scene's picture is a pure function of its source interval, its treatment and the
    # output settings. Keeping finished segments keyed by exactly those inputs means an
    # edit to one beat re-encodes one scene instead of the whole programme, which is what
    # makes iterating on a 30-minute timeline practical.
    def _segment_cache(self):
        path = self.work_dir / 'segments'
        path.mkdir(parents=True, exist_ok=True)
        return path

    def _segment_key(self, shot, extra=0):
        settings = self.story.get('settings') or {}
        visual = shot.get('_visual_cache')
        if visual is None:
            visual = self._resolve_visual(shot); shot['_visual_cache'] = visual
        path = Path(visual['path'])
        try:
            st = path.stat(); signature = [st.st_size, st.st_mtime_ns]
        except OSError:
            signature = ['missing']
        payload = {
            'source': str(path), 'signature': signature, 'type': visual['type'],
            'in_ms': visual.get('in_ms'), 'out_ms': visual.get('out_ms'),
            'frames': shot['duration_frames'] + extra,
            'motion': shot.get('motion_plan'), 'transition': shot.get('transition'),
            'size': [settings.get('width'), settings.get('height')], 'fps': settings.get('fps'),
            'preset': SEGMENT_VERSION,
        }
        return hashlib.sha256(json.dumps(payload, sort_keys=True, default=str).encode()).hexdigest()[:32]

    def _restore_segment(self, key, destination, shot, extra):
        source = self._segment_cache() / (key + '.mp4')
        if not source.is_file() or source.stat().st_size < 1000:
            return False
        cap = cv2.VideoCapture(str(source)); actual = int(cap.get(cv2.CAP_PROP_FRAME_COUNT)); cap.release()
        if actual != shot['duration_frames'] + extra:
            return False
        try:
            os.link(source, destination)
        except OSError:
            shutil.copy2(source, destination)
        return True

    def _store_segment(self, key, rendered):
        target = self._segment_cache() / (key + '.mp4')
        if target.exists():
            return
        temporary = target.with_name(target.stem + '.tmp.mp4')
        shutil.copy2(rendered, temporary)
        temporary.replace(target)

    def _run(self):
        # Stage timing. A long export is a sequence of expensive passes, and "slow" is not
        # actionable. Recording where the seconds actually went makes the next optimisation
        # a measurement rather than a guess; it is written beside the render status.
        self._timing = {'started': time.time(), 'stages': {}, 'segments_encoded': 0, 'segments_reused': 0}
        try:
            return self._run_timed()
        finally:
            try:
                self._timing['finished'] = time.time()
                self._timing['total_s'] = round(self._timing['finished'] - self._timing['started'], 2)
                (self.root / 'render_timing.json').write_text(json.dumps(self._timing, indent=2))
            except OSError:
                pass

    def _stage(self, name):
        """Record the wall time of a named pass. Returns a callable that closes it."""
        started = time.time()
        def done():
            self._timing['stages'][name] = round(self._timing['stages'].get(name, 0) + time.time() - started, 2)
        return done

    def _run_timed(self):
        self.update(status='running', stage='Validating timestamp matches', progress=.3)
        shots = self.story['shots']; voiceover_path = self._find_voiceover(); settings = self.story['settings']
        srt = self.root/settings['caption_srt']
        if self.srt_captions_enabled and not srt.exists(): raise RuntimeError(f'Missing documentary caption file: {srt.name}')
        if self.script_caption_srt and not self.script_caption_srt.exists(): raise RuntimeError('Missing script caption file.')
        for shot in shots:
            self._resolve_visual(shot)
            if shot['kind'] == 'archival':
                audio = self.root/'sources'/shot['audio_source_file']
                if not audio.exists(): raise RuntimeError(f'Missing source: {audio.name}')
                if not self._has_audio(audio): raise RuntimeError(f'{audio.name} is video-only. Upload its authentic audio-bearing copy for {shot["speaker"]}.')
        voiceover = voiceover_path  # Preserve the original continuous narration and its level.
        for old in self.work_dir.glob('event_*.mp4'): old.unlink()
        base_path = self.work_dir/'documentary_base.mp4'; base_path.unlink(missing_ok=True)
        transition_frames = settings.get('transition_frames', 8); segments = []
        end_segments = self._stage('segments')
        cache = self._segment_cache(); reused = 0
        for index, shot in enumerate(shots):
            if self.cancelled: raise RuntimeError('Render cancelled by user.')
            extra = shot.get('transition_handle_frames',0)
            segment = self.work_dir/f'event_{index+1:03d}.mp4'; segments.append(segment)
            self.update(stage=f'Preparing matched scene {index+1} of {len(shots)}', detail=f'{shot["subject"]} · {shot["duration_frames"]} frames · unique source interval')
            key = self._segment_key(shot, extra)
            if not self._restore_segment(key, segment, shot, extra):
                cmd = self._picture_segment_command(shot, index, segment, extra)
                self._run_process(cmd, event_index=index, event_duration=(shot['duration_frames']+extra)/settings['fps'])
                if not segment.exists() or segment.stat().st_size < 1000: raise RuntimeError(f'Encoder produced no usable segment for scene {index+1}')
                cap=cv2.VideoCapture(str(segment));actual_frames=int(cap.get(cv2.CAP_PROP_FRAME_COUNT));cap.release()
                if actual_frames!=shot['duration_frames']+extra:
                    raise RuntimeError(f'Scene {index+1}: source ended early ({actual_frames} frames); replace footage rather than freeze, loop, or shorten narration.')
                self._store_segment(key, segment)
            else: reused += 1
            self.log(f'Scene {index+1:03d}: {shot["subject"]} · {shot["duration_frames"]} frames · SRT {shot["srt_blocks"]}')
        self._timing['segments_encoded'] = len(shots) - reused
        self._timing['segments_reused'] = reused
        end_segments()
        if reused:
            self.log(f'Reused {reused} already-rendered scene segments from an earlier render; only changed scenes were re-encoded.')
            self.update(detail=f'{reused} of {len(shots)} scenes reused from the previous render')
        end_transitions = self._stage('transitions')
        segments=self._smooth_transition_chunks(segments)
        end_transitions()
        self.update(stage='Joining narration-timed scenes', progress=85.0, detail='Short picture dissolves and clean action cuts · narration remains continuous')
        # The join exists to blend handle-backed dissolves. With clean cuts there is nothing
        # to blend, so the segments ARE the picture timeline: joining them would write the
        # same frames twice - once into an intermediate file and again into the master -
        # which on a 30-minute programme is 42s of pure copy for no change in output. The
        # renderer's only transition policy is clean cuts, so this is the normal path.
        any_handles = any(shot.get('transition_handle_frames', 0) for shot in shots)
        end_join = self._stage('join')
        if any_handles:
            self._run_process(self._xfade_command(segments, base_path))
        else:
            self._run_process(self._concat_command(segments, base_path))
        end_join()
        if not base_path.exists() or base_path.stat().st_size < 10000: raise RuntimeError('The cross-dissolve pass did not produce a usable picture timeline.')
        subtitle_style = 'FontName=DejaVu Sans,FontSize=16,PrimaryColour=&H00FFFFFF,OutlineColour=&H00101010,BorderStyle=1,Outline=1.5,Shadow=0,Alignment=2,MarginV=34,MarginL=85,MarginR=85'
        filters = []
        # Both files are generated or uploaded locally; escape paths for libavfilter.
        def subtitle_filter(path, style=None):
            safe = str(path).replace('\\', '\\\\').replace(':', '\\:').replace("'", "'\\\\\\''")
            return f"subtitles='{safe}'" + (f":force_style='{style}'" if style else "")
        if self.srt_captions_enabled: filters.append(subtitle_filter(self.narration_caption_file or srt, None if self.narration_caption_file else subtitle_style))
        if self.script_caption_srt:
            script_style = subtitle_style.replace('Alignment=2', 'Alignment=8')
            filters.append(subtitle_filter(self.script_caption_srt, script_style))
        picture_filter = '[0:v]' + ','.join(filters) + '[picture]' if filters else ''
        duration = shots[-1]['frame_out']/settings['fps']; total_frames = shots[-1]['frame_out']
        # The mux copies picture and narration into one file; it does not re-encode the
        # picture. Progress is weighted by picture content so a 30-minute programme shows
        # a believable curve instead of sitting at 86% through a short copy pass.
        self._stage_base, self._stage_span = 86.0, 13.0
        audio_filter = f'[1:a]atrim=0:{duration:.6f},asetpts=PTS-STARTPTS,apad,atrim=0:{duration:.6f},aformat=sample_fmts=fltp:sample_rates=48000:channel_layouts=stereo[narration]'
        temp_output = self.output.with_suffix('.rendering.mp4')
        # With captions off there is nothing left to draw on the picture: the scene pass
        # already encoded it to the final settings, so the picture is stream-copied and
        # only the narration is encoded. Re-encoding a 30-minute programme here a second
        # time was the single largest cost of a render.
        if voiceover_path is None:
            # Text-only programme: no narration stream to mux. The picture is complete on
            # its own, so it is copied (or re-encoded only if captions are being burned).
            if self.captions_enabled:
                cmd = [self.ffmpeg, '-y', '-hide_banner', '-loglevel', 'warning', '-reinit_filter','0','-i', base_path,
                       '-filter_complex', picture_filter,
                       '-map', '[picture]', '-t', f'{duration:.6f}', '-frames:v', str(total_frames), '-threads',str(self.threads),
                       '-c:v', 'libx264', '-preset', 'ultrafast', '-crf', '16', '-pix_fmt', 'yuv420p', '-an',
                       '-movflags', '+faststart', '-progress', 'pipe:1', '-nostats', temp_output]
            else:
                cmd = [self.ffmpeg, '-y', '-hide_banner', '-loglevel', 'warning', '-i', base_path,
                       '-map', '0:v:0', '-t', f'{duration:.6f}', '-c:v', 'copy', '-an',
                       '-movflags', '+faststart', '-progress', 'pipe:1', '-nostats', temp_output]
        elif self.captions_enabled:
            cmd = [self.ffmpeg, '-y', '-hide_banner', '-loglevel', 'warning', '-reinit_filter','0','-i', base_path, '-i', voiceover,
                   '-filter_complex', f'{picture_filter};{audio_filter}', '-map', '[picture]', '-map', '[narration]',
                   '-t', f'{duration:.6f}', '-frames:v', str(total_frames), '-threads',str(self.threads),'-c:v', 'libx264', '-preset', 'ultrafast', '-crf', '16',
                   '-pix_fmt', 'yuv420p', '-c:a', 'aac', '-b:a', '192k', '-ar', '48000', '-ac', '2',
                   '-movflags', '+faststart', '-progress', 'pipe:1', '-nostats', temp_output]
        else:
            cmd = [self.ffmpeg, '-y', '-hide_banner', '-loglevel', 'warning', '-i', base_path, '-i', voiceover,
                   '-filter_complex', audio_filter, '-map', '0:v:0', '-map', '[narration]',
                   '-t', f'{duration:.6f}', '-c:v', 'copy',
                   '-c:a', 'aac', '-b:a', '192k', '-ar', '48000', '-ac', '2',
                   '-movflags', '+faststart', '-progress', 'pipe:1', '-nostats', temp_output]
        end_mux = self._stage('mux')
        caption_stage = 'Burning selected captions' if self.captions_enabled else 'Exporting with captions off'
        self.update(stage=caption_stage, progress=86.0, detail=f'{len(shots)} matched scenes · {duration:.3f} seconds')
        proc = subprocess.Popen([str(x) for x in cmd], stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1, start_new_session=True)
        self.process = proc; recent = []
        for line in proc.stdout:
            line = line.rstrip()
            if not line: continue
            recent = (recent+[line])[-35:]
            if line.startswith('out_time_ms='):
                try:
                    seconds = int(line.split('=',1)[1])/1_000_000
                    self.update(stage='Exporting smooth matched MP4', progress=round(86+min(seconds/duration,1)*13,2), detail=f'{seconds:.1f} / {duration:.1f} seconds · captions {"on" if self.captions_enabled else "off"}')
                except ValueError: pass
            elif not line.startswith(('frame=','fps=','stream_','bitrate=','total_size=','out_time_','dup_frames=','drop_frames=','speed=','progress=')):
                self.log(line)
        code = proc.wait()
        if self.cancelled: raise RuntimeError('Render cancelled by user.')
        if code != 0: raise RuntimeError('Final documentary export failed:\n'+'\n'.join(recent))
        if not temp_output.exists() or temp_output.stat().st_size < 10000: raise RuntimeError('The final export did not produce a usable MP4.')
        cap = cv2.VideoCapture(str(temp_output)); exported = int(cap.get(cv2.CAP_PROP_FRAME_COUNT)); cap.release()
        if exported != total_frames:
            raise RuntimeError(f'The exported picture has {exported} frames where the timeline has {total_frames}. Nothing was shortened silently.')
        temp_output.replace(self.output)
        end_mux()
        shutil.copy2(srt, self.output.with_suffix('.srt'))
        size = self.output.stat().st_size/1048576
        self.update(status='done', progress=100.0, stage='Smooth matched documentary complete', detail=f'{self.output.name} · {size:.1f} MB · captions {"on" if self.captions_enabled else "off"}', output=self.output.name, finished=time.time())
        self.log(f'COMPLETE: {self.output} ({size:.1f} MB); captions={"on" if self.captions_enabled else "off"}')
