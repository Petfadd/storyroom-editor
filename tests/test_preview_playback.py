import subprocess
import unittest
from pathlib import Path

class PreviewPlaybackTest(unittest.TestCase):
    def test_prepared_cut_switches_without_pausing_narration(self):
        script=Path('static/editor.js').read_text()
        functions=script[script.index('function clearPreparedSource()'):script.index('function seekSourceAtTimeline(')]
        harness='''
const assert=require('assert');
function player(){return {currentTime:0,duration:100,readyState:2,seeking:false,src:'',classList:{add(){},remove(){}},pause(){this.pauses=(this.pauses||0)+1},play(){this.plays=(this.plays||0)+1;return Promise.resolve()},load(){},removeAttribute(){this.src=''},getAttribute(){return this.src}}}
let sourceNode=player(),nextSourceNode=player(),preparedScene='',preparedReady=false,prepareToken=0,sourceSeekToken=0,sourceReady=true,editorBuffering=false,editorMaster=false,editorPlaying=true,editorVideoMuted=false,sourceScene='s2';
const imageNode={hidden:false},monitor={hidden:false};const $=()=>monitor;
const footage=[{id:'a',kind:'video',url:'/a'}];const shots=[{id:'s1',frame_out:300},{id:'s2',frame_in:300,in_s:12,asset_id:'a'}];
const scenes=()=>shots,assets=()=>footage,editorTime=()=>10;function seekSourceAtTimeline(){throw Error('unexpected fallback')}
''' + functions + '''
editorBuffering=true;prepareNextSource(0,5);assert.equal(preparedScene,'');editorBuffering=false;prepareNextSource(0,5);assert.equal(preparedScene,'s2');nextSourceNode.onloadedmetadata();
assert(preparedReady);const queued=nextSourceNode,old=sourceNode;
assert(usePreparedSource(shots[1],footage[0],10.05));assert.strictEqual(sourceNode,queued);assert.strictEqual(nextSourceNode,old);assert(sourceReady);assert.equal(sourceNode.plays,1);assert.equal(sourceNode.muted,false);assert.equal(nextSourceNode.muted,true);
assert(!usePreparedSource(shots[1],footage[0],10.05));
// A prepared cut may arrive while an earlier seek is buffering: narration must resume.
let P={voiceover:{}},editorMuted=false;let narrNode=player();function pauseEditor(){throw Error('unexpected pause')}
prepareNextSource(0,5);nextSourceNode.onloadedmetadata();editorBuffering=true;
assert(usePreparedSource(shots[1],footage[0],10.05));assert.equal(narrNode.plays,1);

'''
        subprocess.run(['node','-e',harness],check=True)

    def test_contiguous_same_video_is_not_restarted(self):
        script=Path('static/editor.js').read_text()
        function=script[script.index('function showSource('):script.index('function applyImageMotion(')]
        harness="""
const assert=require('assert');let sourceScene='previous',sourceReady=true,sourceSeekToken=0,editorPlaying=true,editorMaster=false;
let paused=0,seeks=0;const sourceNode={currentTime:13.42,seeking:false,readyState:2,pause(){paused++},getAttribute(){return '/clip'},classList:{remove(){} }};
const s={id:'next',asset_id:'a',frame_in:300,in_s:13.4},a={id:'a',kind:'video',url:'/clip'};
const scenes=()=>[s],assets=()=>[a],imageNode={hidden:false},$=()=>({hidden:false});
function usePreparedSource(){return false}function seekSourceAtTimeline(){seeks++}
"""+function+"""
showSource(0,10.02);assert.equal(paused,0);assert.equal(seeks,0);assert.equal(sourceScene,'next');sourceNode.currentTime=14;showSource(0,10.02);assert.equal(seeks,0);assert(sourceNode.playbackRate<1);
"""
        subprocess.run(['node','-e',harness],check=True)

if __name__=='__main__':unittest.main()
