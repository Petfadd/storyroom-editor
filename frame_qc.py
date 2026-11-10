"""Frame-QC contact sheets: see the actual pictures before paying for a render.

One sheet per group of beats, each cell stamped with the source file and the exact
in-point, plus the scene number and its SRT blocks. Beside each cell the sheet notes
whether the beat's in-point moved from what the plan declared, so a wrong casting
decision is visible in seconds rather than after a three-minute render.

This labels nothing. It does not identify faces, does not decide whether the picture is
appropriate, and does not approve anything. A stamp records where the frame came from,
which is the one claim that can be made from a decode.
"""
import hashlib
import json
from pathlib import Path

FPS = 30
SHEET_VERSION = 3
CELL_W, CELL_H = 320, 180
PAD, CAPTION_H = 10, 34


def _fmt(seconds):
    ms = int(round(float(seconds) * 1000))
    h, rem = divmod(ms, 3600000)
    m, rem = divmod(rem, 60000)
    s, f = divmod(rem, 1000)
    return '%02d:%02d:%02d.%03d' % (h, m, s, f)


def _source_clock(scene, asset):
    """The moment in the SOURCE file a beat starts at, as the plan and the scene agree."""
    derived = (asset or {}).get('derived_from') or {}
    if derived:
        moment = derived.get('requested_time_ms')
        if moment is not None:
            return str(derived.get('source_file') or asset.get('original_name', '')), float(moment) / 1000
    return str((asset or {}).get('original_name') or (asset or {}).get('name') or ''), float(scene.get('in_s') or 0)


def drift(scene):
    """How far this scene's in-point moved from what the plan declared, in seconds."""
    row = scene.get('plan_row') or {}
    declared = row.get('source_in_ms')
    if declared is None:
        return None
    return round(float(scene.get('in_s') or 0) - declared / 1000, 3)


def _grab(root, asset, at_s):
    """One decoded frame, or None when the file or moment cannot be read."""
    import cv2
    from PIL import Image
    path = Path(root) / (asset or {}).get('path', '')
    if not path.is_file():
        return None
    if (asset or {}).get('kind') == 'image':
        try:
            with Image.open(path) as im:
                return im.convert('RGB').resize((CELL_W, CELL_H))
        except OSError:
            return None
    cap = cv2.VideoCapture(str(path))
    try:
        if not cap.isOpened():
            return None
        cap.set(cv2.CAP_PROP_POS_MSEC, max(0.0, at_s) * 1000)
        ok, frame = cap.read()
        if not ok:
            return None
        frame = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
        return Image.fromarray(frame).resize((CELL_W, CELL_H))
    finally:
        cap.release()


def sheet_key(p, group, scenes):
    # The sheet layout is part of the identity: a changed stamp must not sit behind an
    # old cached image that was drawn by the previous version of this renderer.
    payload = json.dumps([SHEET_VERSION, p.get('updated'), group,
                          [(s['id'], round(float(s.get('in_s') or 0), 3), s.get('asset_id')) for s in scenes]])
    return hashlib.sha256(payload.encode()).hexdigest()[:24]


def build(root, p, per_sheet=20, group=None):
    """Contact sheet(s). Returns a manifest with the flagged beats, never a verdict.

    `group` is a 1-based sheet number; without it the first sheet is built.
    """
    from PIL import Image, ImageDraw
    root = Path(root)
    scenes = p.get('scenes') or []
    if not scenes:
        raise ValueError('Build the storyboard first; there are no picture beats to inspect.')
    assets = {a['id']: a for a in p.get('assets') or []}
    per_sheet = max(1, min(int(per_sheet or 20), 40))
    groups = [scenes[i:i + per_sheet] for i in range(0, len(scenes), per_sheet)]
    number = int(group or 1)
    if not 1 <= number <= len(groups):
        raise ValueError('Sheet ' + str(number) + ' does not exist; this production has ' + str(len(groups)) + '.')
    chosen = groups[number - 1]
    first_index = (number - 1) * per_sheet

    cols = 5
    rows = len(chosen)
    width = cols * (CELL_W + PAD) + PAD
    header_h = 34
    height = header_h + rows * (CELL_H + CAPTION_H + PAD) + PAD
    canvas = Image.new('RGB', (width, height), '#0e1a22')
    draw = ImageDraw.Draw(canvas)
    draw.text((PAD, 10), 'Frame QC · sheet ' + str(number) + ' of ' + str(len(groups)) + ' · beats ' + str(first_index + 1) + '–' + str(first_index + len(chosen)), fill='white')

    manifest = {'sheet': number, 'sheets': len(groups), 'per_sheet': per_sheet,
                'beats': [], 'missing': [], 'moved': [],
                'scope': 'QC inspection frames only, not picture assets. No face recognition, content or identity verdict, or approval.'}

    for offset, scene in enumerate(chosen):
        index = first_index + offset + 1
        asset = assets.get(scene.get('asset_id'))
        file_name, at_s = _source_clock(scene, asset)
        moved = drift(scene)
        duration = (scene.get('frame_out', 0) - scene.get('frame_in', 0)) / FPS
        plan = scene.get('plan_row') or {}
        out_s = float(plan.get('source_out_ms', (at_s + duration) * 1000)) / 1000 if plan.get('strict_production') else at_s + duration
        source_fps = 0
        if asset and asset.get('kind') == 'video':
            import cv2
            cap = cv2.VideoCapture(str(root / asset.get('path', '')))
            source_fps = cap.get(cv2.CAP_PROP_FPS) or 0
            cap.release()
        last_s = max(at_s, out_s - (1 / source_fps if source_fps > 0 else 1 / FPS))
        moments = [('Before IN', at_s - 2), ('IN', at_s), ('Midpoint', (at_s + out_s) / 2), ('Last before OUT', last_s), ('After OUT', out_s + 2)]
        samples = []
        for col, (label, moment) in enumerate(moments):
            is_photo = (asset or {}).get('kind') == 'image'
            runtime = (asset or {}).get('duration')
            available = is_photo or (moment >= 0 and (not runtime or moment < runtime))
            frame = _grab(root, asset, moment) if asset and available else None
            x = PAD + col * (CELL_W + PAD)
            y = header_h + offset * (CELL_H + CAPTION_H + PAD)
            canvas.paste(frame, (x, y)) if frame else draw.rectangle([x, y, x + CELL_W, y + CELL_H], outline='#5b2c2c', fill='#1b1114')
            if not frame: draw.text((x + 8, y + 8), 'outside source or frame unavailable', fill='#e0a0a0')
            draw.text((x, y + CELL_H + 4), f'#{index} {label} ' + ('photo' if is_photo else _fmt(max(0, moment))), fill='white')
            draw.text((x, y + CELL_H + 18), (file_name[:44] or 'no media assigned'), fill='#9fb3c0')
            samples.append({'position': label, 'source_s': None if is_photo else round(moment, 3), 'decoded': bool(frame)})
        beat = {'scene': index, 'srt_blocks': scene.get('srt_blocks'), 'source_file': file_name,
                'in_clock': _fmt(at_s), 'in_s': round(at_s, 3), 'asset': (asset or {}).get('original_name'),
                'kind': (asset or {}).get('kind'), 'declared_drift_s': moved, 'decoded': samples[1]['decoded'], 'samples': samples}
        if moved is not None and abs(moved) >= 0.001: manifest['moved'].append(beat)
        if not samples[1]['decoded']: manifest['missing'].append(beat)
        manifest['beats'].append(beat)

    cache = root / '.cache' / 'frame_qc'
    cache.mkdir(parents=True, exist_ok=True)
    name = 'frame_qc_sheet_%02d_%s.jpg' % (number, sheet_key(p, number, chosen))
    destination = cache / name
    if not destination.exists():
        temporary = destination.with_name(destination.stem + '.tmp.jpg')
        canvas.save(temporary, quality=88)
        temporary.replace(destination)
    manifest.update(file=name, url_key=name, beats_on_sheet=len(chosen))
    if manifest['moved']:
        manifest['note'] = str(len(manifest['moved'])) + ' beat(s) moved from the in-point the plan declared.'
    else:
        manifest['note'] = 'Every beat starts at the in-point the plan declared.'
    return manifest
