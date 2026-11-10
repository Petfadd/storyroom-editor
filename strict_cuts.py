"""Conservative, frame-indexed verification for script-directed exports.

CFR sources are verified by decoded frame timestamps; unreliable timestamps fail closed.
Source frames are selected by index; normal output-rate conversion keeps 1x playback.
"""
from pathlib import Path
from threading import Lock
from fractions import Fraction

_cache = {}
_lock = Lock()
FPS = 30

def _nearest_frame(ms, fps=Fraction(FPS,1)):
    # Exactly half a frame is ambiguous and is reported, not silently rounded.
    f=Fraction(int(ms),1000)*fps
    n=f.numerator//f.denominator
    if f-n==Fraction(1,2):
        raise ValueError('time is exactly halfway between frame boundaries; choose a boundary')
    return n + int(f-n>Fraction(1,2))

def source_rate(path):
    import av
    try:
        with av.open(str(path)) as container:
            rate=container.streams.video[0].average_rate
            if not rate or rate<=0 or rate>120:raise ValueError('source frame rate is unavailable or unsupported')
            return Fraction(rate)
    except ValueError:raise
    except Exception as e:raise ValueError('source frame rate could not be verified: '+str(e)[:200]) from e

def inspect_video(path, required_frames):
    """Decode from the first frame, verifying each PTS through the exclusive OUT."""
    import av
    path=Path(path);stat=path.stat();key=(str(path.resolve()),stat.st_size,stat.st_mtime_ns)
    with _lock:
        cached=_cache.get(key)
        if cached and cached['frames']>=required_frames:return cached
        try:
            with av.open(str(path)) as container:
                stream=container.streams.video[0]
                rate=stream.average_rate
                if not rate or rate<=0 or rate>120:raise ValueError('source frame rate is unavailable or unsupported')
                rate=Fraction(rate)
                first=None;count=0
                for frame in container.decode(stream):
                    if frame.pts is None or frame.time_base is None:raise ValueError('source frame timestamps are unavailable')
                    pts=Fraction(frame.pts)*frame.time_base
                    if first is None:first=pts
                    # Permit only timestamp quantization (one source time-base tick).
                    expected=Fraction(count,1)/rate
                    if abs((pts-first)-expected)>max(Fraction(stream.time_base),Fraction(1,1000000)):
                        raise ValueError(f'variable or discontinuous frame timestamps near source frame {count}')
                    count+=1
                    if count>=required_frames:break
                if count<required_frames:raise ValueError(f'source ended after {count} frames, before the exclusive OUT frame {required_frames}')
                result={'frames':count,'fps':rate};_cache[key]=result;return result
        except ValueError:raise
        except Exception as e:raise ValueError('source frames could not be verified: '+str(e)[:200]) from e

def report(project, root, progress=None):
    if project.get('edit_mode') == 'documentary' and project.get('documentary_beat_review'):
        from documentary_cue_groups import report as documentary_report
        return documentary_report(project, root, progress)
    root=Path(root);scenes=project.get('scenes') or [];assets={a['id']:a for a in project.get('assets',[])}
    required=bool(project.get('production_script')) or any((s.get('plan_row') or {}).get('strict_production') for s in scenes)
    result={'required':required,'checked_version':project.get('updated'),'mode':'strict frame-indexed 30 fps' if required else 'not applicable','shots':[],'errors':[],'adjustments':[]}
    if not required:return result
    if not scenes:result['errors'].append('No storyboard shots are placed.');return result
    last=0;previous_script_out=0
    expected=[b.get('number') for b in (project.get('srt') or {}).get('blocks',[])]
    seen=[]
    for i,s in enumerate(scenes):
        if progress:progress(i,len(scenes))
        row=s.get('plan_row') or {};label=row.get('shot_id') or f'Scene {i+1}';a=assets.get(s.get('asset_id'))
        item={'scene':i+1,'id':s.get('id'),'shot':label,'file':row.get('source_file'),'assigned_file':(a or {}).get('original_name',(a or {}).get('name')),'script_in_ms':row.get('source_in_ms'),'script_out_ms':row.get('source_out_ms'),'placed_in_ms':None,'placed_out_ms':None,'in_deviation_ms':None,'out_deviation_ms':None,'saved_in_ms':float(s.get('in_s',0))*1000,'timeline_script_in_ms':row.get('timeline_in_ms'),'timeline_script_out_ms':row.get('timeline_out_ms'),'timeline_placed_in_ms':s.get('frame_in',0)*1000/FPS,'timeline_placed_out_ms':s.get('frame_out',0)*1000/FPS,'errors':[],'adjustments':[],'photo':bool(row.get('uploaded_photo'))}
        err=item['errors']
        def fail(message):err.append(message)
        if not row.get('strict_production'):fail('No exact script shot and source timing is attached to this slot.')
        cue=list(dict.fromkeys(row.get('srt_blocks') or []))
        if len(cue)!=1:fail('Exactly one SRT cue must be assigned to this shot.')
        else:
            seen.append(cue[0])
            if i>=len(expected) or cue[0]!=expected[i]:fail('Shot does not match its SRT cue in the original order.')
        tin,tout=row.get('timeline_in_ms'),row.get('timeline_out_ms')
        if s.get('frame_in')!=last or s.get('frame_out',0)<=s.get('frame_in',0):fail('Placed timeline has a gap, overlap, or empty shot.')
        last=s.get('frame_out',last)
        if tin is None or tout is None:fail('Script narration IN/OUT is missing.')
        else:
            if tin!=previous_script_out:fail('Script narration shots have a gap or overlap.')
            previous_script_out=tout
            try:
                if s.get('frame_in')!=_nearest_frame(tin) or s.get('frame_out')!=_nearest_frame(tout):fail('Placed timeline differs from the nearest output-frame boundaries.')
            except ValueError as e:fail(str(e)+' (narration timeline).')
        if s.get('transition')!='cut':fail('Only a clean cut is allowed in strict mode.')
        if not a:fail('Shot is unassigned or the named source is missing.')
        elif not (root/a.get('path','')).is_file():fail('Assigned source file is missing from disk.')
        if a and item['assigned_file']!=row.get('source_file'):fail('Assigned filename does not exactly match the script.')
        if row.get('uploaded_photo'):
            if a and a.get('kind')!='image':fail('The named photograph is not an uploaded image.')
        else:
            si,so=row.get('source_in_ms'),row.get('source_out_ms')
            if si is None or so is None:fail('Script source IN or exclusive OUT is missing.')
            else:
                if so<=si:fail('Source OUT must be after IN.')
                if tin is not None and tout is not None and so-si!=tout-tin:fail('Source OUT minus IN does not equal the script narration span.')
                try:
                    if not a or a.get('kind')!='video':
                        if a:fail('The named video is not assigned as a video.')
                    else:
                        path=root/a.get('path','')
                        if path.resolve()!=(root/'sources'/path.name).resolve():fail('Assigned video path cannot be matched to the renderer source file.')
                        if so>float(a.get('duration') or 0)*1000+0.000001:fail('Script OUT exceeds the source runtime.')
                        if path.is_file():
                            rate=source_rate(path);fi,fo=_nearest_frame(si,rate),_nearest_frame(so,rate)
                            pin=float(Fraction(fi,1)/rate*1000);pout=float(Fraction(fo,1)/rate*1000)
                            item.update(source_start_frame=fi,source_end_frame=fo,source_fps=str(rate),placed_in_ms=pin,placed_out_ms=pout,in_deviation_ms=pin-si,out_deviation_ms=pout-so)
                            actual_duration=Fraction(fo-fi,1)/rate;output_duration=Fraction(s.get('frame_out',0)-s.get('frame_in',0),FPS)
                            difference=output_duration-actual_duration
                            # A user-authorized exception: for a shortfall of at most one
                            # output frame, use exactly the next source frame if that
                            # makes the durations compatible. Never slow, freeze or loop.
                            extend=(difference>=Fraction(1,2*FPS) and difference<=Fraction(1,FPS)
                                    and abs(actual_duration+Fraction(1,1)/rate-output_duration)<Fraction(1,2*FPS))
                            if fo<=fi:fail('Snapped source interval contains no frames.')
                            if fo>fi>=0:
                                try:
                                    inspect_video(path,fo+1 if extend else fo)
                                    if extend:
                                        fo+=1;pout=float(Fraction(fo,1)/rate*1000)
                                        item.update(source_end_frame=fo,placed_out_ms=pout,out_deviation_ms=pout-so,added_source_frames=1)
                                        item['adjustments'].append('One source frame added after the snapped script OUT; it may also appear at the next cut.')
                                    elif abs(difference)>=Fraction(1,2*FPS):fail('Snapped source and output durations differ by at least half an output frame; exact placement is not possible.')
                                except (ValueError,OSError,ImportError) as e:
                                    fail(('The extra source frame could not be verified: ' if extend else '')+str(e))
                    if abs(float(s.get('in_s',0))*1000-si)>1e-6:fail('Saved source IN differs from the script.')
                except (ValueError,OSError,ImportError) as e:fail(str(e)+' (source).')
        result['shots'].append(item)
        result['errors'].extend(f'{label}: {e}' for e in err)
        result['adjustments'].extend(f'{label}: {e}' for e in item['adjustments'])
    blocks=(project.get('srt') or {}).get('blocks',[])
    if blocks and blocks[-1].get('end_ms') is not None and previous_script_out!=blocks[-1]['end_ms']:result['errors'].append('The script timeline does not end at the original final SRT OUT.')
    if len(scenes)!=len(expected):result['errors'].append(f'Expected {len(expected)} shots for {len(expected)} SRT cues, found {len(scenes)}.')
    return result
