"""Project-local uploader attestations; not independent identity or legal clearance."""
import time
from production import asset_matches
BASIS='Uploader declares that this file has been reviewed for its intended use and may be reused. This is a user attestation, not independent copyright clearance or face recognition.'
AUTO_POLICY_VERSION=1
AUTO_BASIS='Automatic media-use approval requested by the user. This is uploader-authorized reuse approval, not independent copyright clearance, face recognition, or editorial scene approval.'
def approve_asset(p,a,method='bulk_user_attestation'):
 if not str(a.get('source','')).strip():
  from document_plan import source_register
  links=list(dict.fromkeys(str(r.get('youtube_url') or '').strip() for r in source_register(p) if asset_matches(a,str(r.get('source_file') or '')) and str(r.get('youtube_url') or '').strip()))
  a['source']=links[0] if len(links)==1 else 'User-provided file: '+str(a.get('original_name') or a['name'])+'; original publisher not recorded.'
 if a.get('rights','unreviewed')=='unreviewed':a['rights']='reviewed_other'
 a['verified']=True
 basis=AUTO_BASIS if method.startswith(('automatic_','user_requested_','derived_')) else BASIS
 if basis not in a.get('notes',''):a['notes']=(a.get('notes','')+'\n'+basis).strip()
 a['approval_record']={'basis':basis,'method':method,'at':time.time(),'active':True,'independent_verification':False}
 # Existing person identifications are preserved; no names are invented from captions or filenames.
 return a

def auto_approve_upload(p,a):
 if (p.get('media_approval_policy') or {}).get('auto_upload',True):
  approve_asset(p,a,'automatic_local_upload_user_instruction')
  a['approval_record']['basis']=AUTO_BASIS
 return a


def ensure_automatic_policy(p):
 """One-time migration for existing projects; do not repeatedly undo explicit later revocations."""
 if p.get('automatic_media_policy_version')==AUTO_POLICY_VERSION:return False
 for a in p['assets']:
  approve_asset(p,a,'user_requested_automatic_existing_uploads')
  a['approval_record']['basis']=AUTO_BASIS
 p['automatic_media_policy_version']=AUTO_POLICY_VERSION
 p['media_approval_policy']={'auto_upload':True,'basis':AUTO_BASIS,'at':time.time(),'scope':'local_uploads_and_frames_from_approved_local_uploads'}
 p.setdefault('media_approval_history',[]).append({'action':'automatic_approval_requested','count':len(p['assets']),'at':time.time(),'basis':AUTO_BASIS})
 return True
