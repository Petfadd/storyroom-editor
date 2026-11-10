import sys, os, subprocess
sys.path.insert(0, '/home/user/storyroom-editor')
import av
from fractions import Fraction
from imageio_ffmpeg import get_ffmpeg_exe

ff = get_ffmpeg_exe()
out = '/tmp/tsfix'
os.makedirs(out, exist_ok=True)
fps = Fraction(30000, 1001)
rate = f'{fps.numerator}/{fps.denominator}'

# Rebuild the two clip kinds exactly as the renderer does, but with the new flag.
def clip(src_rate, n, name):
    src = os.path.join(out, 'src_' + name)
    subprocess.run([ff, '-hide_banner', '-loglevel', 'error', '-y', '-f', 'lavfi',
                    '-i', f'testsrc=size=320x240:rate={src_rate}:duration=40', '-an',
                    '-vf', 'format=yuv420p', '-c:v', 'libx264', '-preset', 'ultrafast',
                    '-crf', '20', '-frames:v', str(n + 30), src], check=True)
    path = os.path.join(out, name)
    filters = (f"trim=start_frame=0:end_frame={n},setpts=N/({rate}*TB),"
               f"scale=320:240:force_original_aspect_ratio=decrease,"
               f"pad=320:240:(ow-iw)/2:(oh-ih)/2:black,setsar=1,format=yuv420p")
    subprocess.run([ff, '-hide_banner', '-loglevel', 'error', '-y', '-i', src, '-an',
                    '-vf', filters, '-fps_mode', 'passthrough', '-c:v', 'libx264',
                    '-preset', 'ultrafast', '-crf', '20', '-threads', '2',
                    '-frames:v', str(n), '-video_track_timescale', str(fps.numerator), path], check=True)
    with av.open(path) as c:
        s = c.streams.video[0]
        print(f'  {name}: tb={s.time_base} base={s.base_rate} frames={s.frames}')
    return path

print('building clips with -video_track_timescale ...')
names = [clip('30000/1001', 400, 'a0.mp4'),
         clip('60000/1001', 500, 'b1.mp4'),
         clip('30000/1001', 300, 'a2.mp4'),
         clip('60000/1001', 450, 'b3.mp4'),
         clip('30000/1001', 350, 'a4.mp4')]
total = 400 + 500 + 300 + 450 + 350

lst = os.path.join(out, 'clips.txt')
with open(lst, 'w') as fh:
    for n in names:
        fh.write("file '" + n.replace("'", "'\\''") + "'\n")
joined = os.path.join(out, 'joined.mp4')
subprocess.run([ff, '-hide_banner', '-loglevel', 'error', '-y', '-f', 'concat', '-safe', '0',
                '-i', lst, '-c', 'copy', joined], check=True)

with av.open(joined) as c:
    s = c.streams.video[0]
    print('joined tb =', s.time_base, 'base =', s.base_rate, 'avg =', s.average_rate)
    prev = None; i = 0; gaps = []
    for fr in c.decode(s):
        pts = Fraction(fr.pts) * fr.time_base
        if prev is not None and float(pts - prev) > 0.05:
            gaps.append((i, round(float(pts - prev), 3)))
        prev = pts
        i += 1
print(f'frames = {i} (expected {total})')
print('gaps  =', gaps if gaps else 'NONE')
