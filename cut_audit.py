"""Compare saved cuts with document timecodes, without claiming visual correspondence."""
from document_plan import rows_for
from production import matching_assets

def audit(p):
 rows,mode=rows_for(p);by_id={r['beat_id']:r for r in rows};assets={a['id']:a for a in p['assets']};out=[]
 for i,s in enumerate(p['scenes'],1):
  r=by_id.get(s.get('edl_beat_id'));a=assets.get(s.get('asset_id'));derived=(a or {}).get('derived_from') or {};duration=(s['frame_out']-s['frame_in'])/30*1000
  inside=derived.get('requested_time_ms') if derived else s.get('in_s',0)*1000
  outside=inside if derived else inside+duration
  record={'scene':i,'srt_blocks':'/'.join(map(str,s['srt_blocks'])),'plan_row':s.get('edl_beat_id',''),'parent_beat':r.get('parent_beat_id','') if r else '', 'expected_file':r.get('source_file','') if r else '', 'selected_file':derived.get('source_file') or (a or {}).get('original_name',''), 'expected_in_ms':r.get('source_in_ms') if r else None,'saved_in_ms':inside,'in_delta_ms':round(inside-r['source_in_ms'],6) if r and r.get('source_in_ms') is not None else None,'expected_out_ms':r.get('source_out_ms') if r else None,'saved_picture_out_ms':outside,'render_source_out_ms':outside+(s.get('transition_handle_frames',4)/30*1000 if a and a['kind']=='video' and s['transition']=='dissolve' else 0),'expected_timeline_in_ms':r.get('timeline_in_ms') if r else None,'expected_timeline_out_ms':r.get('timeline_out_ms') if r else None,'saved_timeline_in_ms':s['frame_in']/30*1000,'saved_timeline_out_ms':s['frame_out']/30*1000,'transition_extra_frames':s.get('transition_handle_frames',0) if s['transition']=='dissolve' else 0,'visual_match':'NOT_CHECKED','status':'MATCHES_TIMECODES'}
  if not r:record['status']='NO_CONFORMED_PLAN_LINK'
  elif not a:record['status']='NO_ASSIGNED_MEDIA'
  elif (derived.get('asset_id') or a['id']) not in [a['id'] for a in matching_assets(p,r['source_file'])]:record['status']='DIFFERENT_SOURCE'
  elif str(r.get('visual_status','')).upper()=='STILL' and a['kind']!='image':record['status']='STILL_NOT_EXTRACTED'
  elif abs(record['in_delta_ms'])>1.001:record['status']='IN_POINT_MISMATCH'
  elif s['frame_in']!=round(r['timeline_in_ms']*.03) or s['frame_out']!=round(r['timeline_out_ms']*.03):record['status']='TIMELINE_MISMATCH'
  elif not derived and a['kind']=='video' and abs(outside-r['source_out_ms'])>1000/30+1:record['status']='OUT_POINT_MISMATCH'
  out.append(record)
 return {'project_id':p['id'],'project_title':p['title'],'mode':mode,'planned_rows':len(rows),'saved_scenes':len(out),'timing_matches':sum(r['status']=='MATCHES_TIMECODES' for r in out),'visual_verification':False,'note':'A timecode match proves saved interval conformity, not that the picture depicts the intended person/event. CSV source OUT excludes separately listed outgoing dissolve handles. Still OUT is not a moving interval.','rows':out}
