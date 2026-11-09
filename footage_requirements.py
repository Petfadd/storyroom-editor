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

# This scan works even before an SRT is uploaded. It reports names mentioned in
# the document, not proof that a name is a scheduled or eligible photograph.
PHOTO_FILENAMES = re.compile(r'''(?P<quote>["'`])(?P<quoted>[^\n\r"'`/\\]{1,180}\.(?:jpg|jpeg|png|webp))(?P=quote)|(?<![\w./\\-])(?P<plain>[A-Za-z][A-Za-z0-9_-]*\.(?:jpg|jpeg|png|webp))(?![\w.-])''', re.I)
PHOTO_FIELD = re.compile(r'^\s*(?:Photograph|Full source)\s*:\s*(?:PROPOSED:\s*)?[`"\']?([^\n\r/\\`"\']+?\.(?:jpg|jpeg|png|webp))[`"\']?\s*$',re.M|re.I)

def photo_requirements(project):
    from production import asset_matches
    script=project.get('production_script')
    if not script:return {'status':'no_script','files':[],'total':0,'matched':0,'missing':0,'ambiguous':0}
    text=script.get('text') or '';names={}
    def add(name):
        name=name.strip();names.setdefault(name,name)
    # Remove recognized fields from the loose scan so a spaced filename isn't
    # also misread as its last word, e.g. "Victim Photo.jpg" -> "Photo.jpg".
    for match in PHOTO_FIELD.finditer(text):add(match.group(1))
    rest=PHOTO_FIELD.sub('',text)
    for match in PHOTO_FILENAMES.finditer(rest):add(match.group('quoted') or match.group('plain'))
    photos=[a for a in project.get('assets',[]) if a.get('kind')=='image' and not a.get('derived_from')]
    files=[]
    for name in names.values():
        hits=[a for a in photos if asset_matches(a,name)]
        files.append({'name':name,'status':'missing' if not hits else 'matched' if len(hits)==1 else 'ambiguous','matches':len(hits),'matched_names':[a.get('original_name') or a.get('name','') for a in hits]})
    return {'status':'found' if files else 'not_found','files':files,'total':len(files),'matched':sum(f['status']=='matched' for f in files),'missing':sum(f['status']=='missing' for f in files),'ambiguous':sum(f['status']=='ambiguous' for f in files)}
