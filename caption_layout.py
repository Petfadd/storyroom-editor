"""One-line SRT presentation for rendering; never edits the uploaded SRT."""
import re
from PIL import ImageFont


def _stamp(ms):
    # ASS/libass uses centiseconds (as it also does when reading SRT directly).
    cs = max(0, (int(ms) + 5) // 10)
    return f'{cs // 360000}:{cs // 6000 % 60:02d}:{cs // 100 % 60:02d}.{cs % 100:02d}'


def make_single_line_ass(blocks, width, height):
    """Fit each cue independently. No cue text or source file is changed."""
    xres = round(1080 * width / height)
    max_width = xres * .84
    try:
        font = ImageFont.truetype('DejaVuSans.ttf', 56)
    except OSError as exc:
        raise ValueError('DejaVu Sans is needed to fit captions on one line.') from exc
    header = f'''[Script Info]
ScriptType: v4.00+
PlayResX: {xres}
PlayResY: 1080
WrapStyle: 2
ScaledBorderAndShadow: yes
[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Default,DejaVu Sans,56,&H00FFFFFF,&H00FFFFFF,&H00101010,&H88000000,0,0,0,0,100,100,0,0,1,3,1,2,20,20,65,1
[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
'''
    lines=[]
    for cue in blocks:
        text=re.sub(r'\s*\n\s*',' ',str(cue.get('text',''))).strip()
        if not text: continue
        # SRT words stay intact; only original line breaks become spaces on screen.
        length=max(1.0,font.getlength(text));size=max(1,min(56,int(56*max_width/(length+12))))
        escaped=text.replace('\\','\\\\').replace('{','\\{').replace('}','\\}')
        start,end=_stamp(cue['start_ms']),_stamp(cue['end_ms'])
        if start==end: raise ValueError('An SRT cue is too short for subtitle rendering.')
        lines.append(f'Dialogue: 0,{start},{end},Default,,0,0,0,,{{\\q2\\fs{size}}}{escaped}')
    return header+'\n'.join(lines)+'\n'
