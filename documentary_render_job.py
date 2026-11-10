"""Asynchronous, version-guarded Beat-documentary export job."""
import copy
import hashlib
import threading
import time
import uuid
from pathlib import Path


class DocumentaryRenderJob:
    def __init__(self, root, project, sound_plan, width, height, options, subtitle_ass,
                 adjusted_srt, validate_current, publish, on_change):
        self.root = Path(root); self.project = copy.deepcopy(project); self.sound_plan = copy.deepcopy(sound_plan)
        self.width = width; self.height = height; self.options = copy.deepcopy(options)
        self.subtitle_ass = subtitle_ass; self.adjusted_srt = adjusted_srt
        self.validate_current = validate_current; self.publish = publish; self.on_change = on_change
        self.lock = threading.RLock(); self.cancelled = False
        self.token = uuid.uuid4().hex
        self.state = {'status':'queued','progress':0,'stage':'Queued','detail':'Preparing documentary export.',
                      'output':None,'error':None,'started':time.time(),'finished':None}
        self.worker_thread = None

    def snapshot(self):
        with self.lock: return dict(self.state)

    def update(self, **values):
        with self.lock: self.state.update(values); state = dict(self.state)
        if self.on_change: self.on_change(state)

    def cancel(self):
        with self.lock: self.cancelled = True
        self.update(stage='Cancelling', detail='Stopping after the current encoding step.')

    def check_cancelled(self):
        with self.lock:
            if self.cancelled: raise ValueError('Render cancelled by user.')

    def signatures(self):
        used = {s.get('asset_id') for s in self.project.get('scenes', [])} | {b.get('asset_id') for b in self.sound_plan.get('bites', [])}
        paths = [self.root / a['path'] for a in self.project['assets'] if a['id'] in used]
        paths += [self.root / self.project['voiceover']['path'], self.root / 'inputs' / 'captions.srt']
        decisions = self.root / 'reviews' / 'documentary_frame_decisions.json'
        if decisions.exists(): paths.append(decisions)
        result = []
        for path in paths:
            path = path.resolve()
            if not path.is_relative_to(self.root.resolve()) or not path.is_file(): raise ValueError('A required input is missing.')
            stat = path.stat()
            with path.open('rb') as f: digest = hashlib.file_digest(f, 'sha256').hexdigest()
            after = path.stat()
            if (stat.st_size,stat.st_mtime_ns)!=(after.st_size,after.st_mtime_ns): raise ValueError('An input changed while it was being checked.')
            result.append((str(path.relative_to(self.root.resolve())),digest,stat.st_size,stat.st_mtime_ns))
        # Detect a decision file being introduced during assembly too.
        result.append(('frame_decisions_present', decisions.exists()))
        return result

    def start(self):
        self.worker_thread = threading.Thread(target=self.run, daemon=True, name='documentary-final-'+self.project['id'])
        self.worker_thread.start()

    def run(self):
        from documentary_final_renderer import render
        candidate = self.root/'renders'/('documentary_candidate_'+self.token+'.mp4')
        final = self.root/'renders'/('documentary_final_'+self.token+'.mp4')
        published = False
        try:
            self.update(status='running',stage='Checking inputs',detail='Verifying project and source files.')
            self.check_cancelled(); before = self.signatures(); self.validate_current(self.project, self.sound_plan)
            def progress(index, total):
                self.check_cancelled()
                self.update(stage='Assembling documentary',progress=round(5+85*index/max(1,total),1),detail=f'Picture segment {min(index+1,total)} of {total}.')
            result = render(self.root,self.project,self.sound_plan,candidate,self.width,self.height,progress,
                            enhance_picture=self.options['enhance_picture'],subtitle_ass=self.subtitle_ass,
                            cancel_check=self.check_cancelled,music=self.options.get('music') is True)
            self.update(stage='Final checks',progress=95,detail='Checking the saved project and inputs.')
            self.check_cancelled()
            if before != self.signatures(): raise ValueError('An input changed during rendering; no MP4 was published.')
            self.validate_current(self.project, self.sound_plan)
            self.check_cancelled()
            record={'project': self.project, 'project_version':self.project['updated'], 'plan_revision':self.sound_plan['revision'],
                    'output':final.name, 'result':result, 'export_type':'final', 'script_captions_requested':True,
                    'captions_burned_in':True, 'srt_subtitles_burned_in':self.options['srt_subtitles'],
                    'picture_enhancement':self.options['enhance_picture'], 'background_music':self.options.get('music') is True, 'fade':'omitted with user confirmation',
                    'reuse_note':'User attested; source identity, context, provenance and rights not independently verified.',
                    'created':time.time(), 'input_signatures':before}
            self.publish(self.project, self.sound_plan, candidate, final, record, self.adjusted_srt)
            published = True
            self.update(status='done',stage='Documentary MP4 ready',progress=100,detail='Export ready. Editorial, identity and rights attestations are not independent verification.',output=final.name,finished=time.time())
        except Exception as exc:
            if not published: candidate.unlink(missing_ok=True)
            with self.lock: cancelled = self.cancelled
            self.update(status='cancelled' if cancelled else 'error',stage='Cancelled' if cancelled else 'Export failed',error=str(exc),detail=str(exc),finished=time.time())
