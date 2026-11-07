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
let sourceNode=player(),nextSourceNode=player(),preparedScene='',preparedReady=false,prepareToken=0,sourceSeekToken=0,sourceReady=false,editorBuffering=false,editorMaster=false,editorPlaying=true,sourceScene='s2';
const imageNode={hidden:false},monitor={hidden:false};const $=()=>monitor;
const footage=[{id:'a',kind:'video',url:'/a'}];const shots=[{id:'s1',frame_out:300},{id:'s2',frame_in:300,in_s:12,asset_id:'a'}];
const scenes=()=>shots,assets=()=>footage,editorTime=()=>10;function seekSourceAtTimeline(){throw Error('unexpected fallback')}
''' + functions + '''
prepareNextSource(0,5);assert.equal(preparedScene,'s2');nextSourceNode.onloadedmetadata();
assert(preparedReady);const queued=nextSourceNode,old=sourceNode;
assert(usePreparedSource(shots[1],footage[0],10.05));assert.strictEqual(sourceNode,queued);assert.strictEqual(nextSourceNode,old);assert(sourceReady);assert.equal(sourceNode.plays,1);
assert(!usePreparedSource(shots[1],footage[0],10.05));
'''
        subprocess.run(['node','-e',harness],check=True)

if __name__=='__main__':unittest.main()
