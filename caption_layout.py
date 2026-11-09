"""Short one-line SRT phrase presentation for rendering; never edits the uploaded SRT."""
import re
from PIL import ImageFont


def _stamp(ms):
    # ASS/libass uses centiseconds (as it also does when reading SRT directly).
    cs = max(0, (int(ms) + 5) // 10)
    return f'{cs // 360000}:{cs // 6000 % 60:02d}:{cs // 100 % 60:02d}.{cs % 100:02d}'


def caption_phrases(blocks, max_chars=36, max_words=6):
    """Split each SRT cue into short readable phrases with estimated within-cue timing.

    SRT has no word timestamps: weighted character counts provide an approximation,
    not speech alignment. Preserve all words and punctuation, and never cross cues.
    """
    result=[]
    for index,cue in enumerate(blocks):
        words=str(cue.get('text','')).split()
        if not words: continue
        chunks=[];current=[]
        for word in words:
            if current and (len(current)>=max_words or len(' '.join(current+[word]))>max_chars):
                chunks.append(current);current=[]
            current.append(word)
            if len(' '.join(current))>=16 and re.search(r'[.!?;:,]["\'”’)]*$',word):
                chunks.append(current);current=[]
        if current:chunks.append(current)
        start=int(cue['start_ms']);end=int(cue['end_ms']);duration=end-start
        # Avoid creating unrepresentable centisecond events in a very short cue.
        if duration < len(chunks)*20:chunks=[words]
        weights=[sum(len(w) for w in c) for c in chunks];total=sum(weights);done=0
        group=[]
        for chunk,weight in zip(chunks,weights):
            a=start+round(duration*done/total);done+=weight;b=start+round(duration*done/total)
            group.append({'cue_index':index,'text':' '.join(chunk),'start_ms':a,'end_ms':b})
        if any(_stamp(x['start_ms'])==_stamp(x['end_ms']) for x in group):
            group=[{'cue_index':index,'text':' '.join(words),'start_ms':start,'end_ms':end}]
        result.extend(group)
    return result


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
    for cue in caption_phrases(blocks):
        text=re.sub(r'\s*\n\s*',' ',str(cue.get('text',''))).strip()
        if not text: continue
        # SRT words stay intact; only original line breaks become spaces on screen.
        length=max(1.0,font.getlength(text));size=max(1,min(56,int(56*max_width/(length+12))))
        escaped=text.replace('\\','\\\\').replace('{','\\{').replace('}','\\}')
        start,end=_stamp(cue['start_ms']),_stamp(cue['end_ms'])
        if start==end: raise ValueError('An SRT cue is too short for subtitle rendering.')
        lines.append(f'Dialogue: 0,{start},{end},Default,,0,0,0,,{{\\q2\\fs{size}}}{escaped}')
    return header+'\n'.join(lines)+'\n'
