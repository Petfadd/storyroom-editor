"""Quick, non-authoritative count of video filenames mentioned in a script."""
import re

# Quoted filenames can contain spaces; unquoted names must be a single token.
# C identifiers without a video extension are clip references, not source files.
FILENAMES = re.compile(r'''(?P<quote>["'`])(?P<quoted>[^\n\r"'`/\\]{1,180}\.(?:mp4|mov))(?P=quote)|(?<![\w./\\-])(?P<plain>[A-Za-z][A-Za-z0-9_-]*\.(?:mp4|mov))(?![\w.-])''', re.I)

def footage_requirements(project):
    script=project.get('production_script')
    if not script:return {'status':'no_script','files':[],'total':0,'matched':0,'missing':0,'ambiguous':0}
    text=script.get('text') or ''
    names={}
    for match in FILENAMES.finditer(text):
        name=(match.group('quoted') or match.group('plain')).strip()
        names.setdefault(name.casefold(),name)
    # Stable natural ordering does not change source filenames or imply clip order.
    def natural(name):
        return [(0,int(s)) if s.isdigit() else (1,s.casefold()) for s in re.split(r'(\d+)',name)]
    videos=[a for a in project.get('assets',[]) if a.get('kind')=='video' and not a.get('derived_from')]
    files=[]
    for name in sorted(names.values(),key=natural):
        hits=[a for a in videos if str(a.get('original_name') or a.get('name') or '').casefold()==name.casefold()]
        status='missing' if not hits else 'matched' if len(hits)==1 else 'ambiguous'
        files.append({'name':name,'status':status,'matches':len(hits)})
    return {'status':'found' if files else 'not_found','files':files,'total':len(files),
            'matched':sum(f['status']=='matched' for f in files),
            'missing':sum(f['status']=='missing' for f in files),
            'ambiguous':sum(f['status']=='ambiguous' for f in files)}
