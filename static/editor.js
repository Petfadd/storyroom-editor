/* The project system lives inside the established media/monitor/inspector/timeline shell. */
let sourceReady=false,sourceSeekToken=0,masterSeekToken=0,editorBuffering=false;
let timelineZoom=12,editorPlaying=false,editorMuted=false,editorCC=false,editorMaster=false,masterUrl='',sourceScene='',clockBase=0,clockAt=0,editorContext='',activeSRT=-1;
const masterNode=$('#program-master'),imageNode=$('#program-image'),narrNode=$('#program-narration');
const standbyNode=document.createElement('video');standbyNode.id='program-source-next';standbyNode.muted=true;standbyNode.playsInline=true;standbyNode.preload='auto';standbyNode.disablePictureInPicture=true;standbyNode.setAttribute('disableremoteplayback','');imageNode.before(standbyNode);
let sourceNode=$('#program-source'),nextSourceNode=standbyNode,preparedScene='',preparedReady=false,prepareToken=0;
const scenes=()=>P?P.scenes:[],assets=()=>P?P.assets:[];
const totalTime=()=>scenes().at(-1)?.frame_out/30||0;
const fmt=t=>{t=Math.max(0,t||0);const f=Math.floor(t*30+.00001),pad=n=>String(Math.floor(n)).padStart(2,'0');return `${pad(f/108000)}:${pad(f/1800%60)}:${pad(f/30%60)}:${pad(f%30)}`};
const editorTime=()=>editorMaster?(masterNode.currentTime||0):(P?.voiceover&&narrNode.getAttribute('src')?narrNode.currentTime:clockBase+(editorPlaying&&!editorBuffering?(performance.now()-clockAt)/1000:0));
list=async function(){const d=await api('/api/projects');projects=d.projects;PROFILES=d.profiles||PROFILES;$('#project-list').innerHTML=projects.map(p=>`<button class="project-link ${P?.id===p.id?'active':''}" onclick="openProject('${p.id}')">${E(p.title)}<small>${p.scenes} scenes</small></button>`).join('');return d};
function openProjects(){if(!leave())return;pauseEditor();list().then(()=>$('#projects-dialog').showModal())}
showHome=openProjects;
function pauseManagementMedia(){document.querySelectorAll('#manage-dialog audio,#manage-dialog video').forEach(node=>node.pause())}
function closeTools(){pauseManagementMedia();for(const id of ['#projects-dialog','#manage-dialog'])if($(id).open)$(id).close()}
openProject=async function(id){if(!leave())return;pauseEditor();closeTools();P=await api('/api/projects/'+id);sceneIndex=0;tab='brief';history.replaceState(null,'','#/production/'+id);await list();resetEditor();renderProject()};
function resetEditor(){clearPreparedSource();editorBuffering=false;sourceSeekToken++;masterSeekToken++;sourceReady=false;sourceScene='';clockBase=0;clockAt=performance.now();masterUrl='';editorMaster=false;masterNode.pause();masterNode.removeAttribute('src');masterNode.load();sourceNode.pause();sourceNode.removeAttribute('src');sourceNode.load();narrNode.pause();narrNode.removeAttribute('src');narrNode.load();$('#timeline-scroll').scrollLeft=0}
$('#new-form').onsubmit=async e=>{e.preventDefault();const button=e.submitter||$('#new-form button[type=submit]');button.disabled=true;try{pauseEditor();P=await api('/api/projects','POST',Object.fromEntries(new FormData(e.target)));sceneIndex=0;$('#new-dialog').close();history.replaceState(null,'','#/production/'+P.id);resetEditor();await list();renderProject();manage('brief');toast('New production created in the same timeline editor.')}finally{button.disabled=false}};
function manage(which){if(!['brief','assets','delivery'].includes(which))which='brief';if(!P){toast('Create a production first.');return}if(!leave())return;pauseEditor();tab=which;renderManagement();if(!$('#manage-dialog').open)$('#manage-dialog').showModal()}
changeTab=manage;
function closeManagement(){if(!leave())return;pauseManagementMedia();$('#manage-dialog').close();renderProject()}
function renderManagement(){if(!P)return;$('#crumb').textContent=P.title;$('#manage-title').textContent={brief:'Production script, SRT & narration',assets:'Media uploads',delivery:'Review & export'}[tab]||'Production setup';$('#content').innerHTML=({brief:briefHTML,assets:assetsHTML,delivery:deliveryHTML}[tab]||briefHTML)();if(P.render?.url&&!P.render.current)$('#content').insertAdjacentHTML('afterbegin','<div class="notice warn">The saved MP4 is from an earlier edit. Render again to include current changes.</div>')}
renderProject=function(){renderEditor();if($('#manage-dialog').open)renderManagement()};
const oldNewProduction=newProduction;newProduction=function(){pauseEditor();oldNewProduction()};
const savedBuild=buildStoryboard;
buildStoryboard=async function(){await savedBuild();if(P?.scenes.length){tab='brief';pauseManagementMedia();if($('#manage-dialog').open)$('#manage-dialog').close();sceneIndex=0;resetEditor();renderProject();if(P.script_application?.issues.length){manage('brief');toast('Storyboard built with unresolved script instructions. See the script report; no missing intervals were guessed.')}else toast(P.picture_policy==='clips_only'?'Clips-only storyboard assembled automatically. Source and timing checks completed.':'Storyboard built from your SRT and available script instructions. Review the cut on V1.')}};
selectScene=i=>selectEditorScene(i);
const savedSplit=splitBeat,savedMerge=mergeBeat;
splitBeat=async function(){pauseEditor();await savedSplit();sourceScene='';renderProject()};mergeBeat=async function(){pauseEditor();await savedMerge();sourceScene='';renderProject()};
function renderEditor(){
 const ss=scenes(),current=ss[sceneIndex];$('#project-name').textContent=P?.title||'Storyboard editor';document.title=(P?.title||'Storyroom')+' · Timeline editor';$('#project-status').textContent=P?`${ss.length} scenes · ${totalTime().toFixed(3)}s · ${P.scenes.filter(s=>s.approved).length} approved${P.script_application?.issues.length?' · '+P.script_application.issues.length+' script gaps':''}`:'Same timeline workflow · create a production to begin';
 const visibleAssets=assets().filter(a=>!(P?.picture_policy==='clips_only'&&a.kind==='image'&&a.derived_from));$('#source-count').textContent=visibleAssets.length;document.querySelector('.media-section-label').textContent=P?.picture_policy==='clips_only'?'CASE FOOTAGE · MP4 CLIPS ONLY':'Case footage & photographs';$('#media-list').innerHTML=visibleAssets.map((a,i)=>`<div class="media-item ${current?.asset_id===a.id?'selected':''}" role="button" tabindex="0" onclick="inspectAsset('${a.id.replace(/'/g,'')}')" onkeydown="if(event.key==='Enter')inspectAsset('${a.id.replace(/'/g,'')}')"><span class="source-icon">${a.reserve?E(a.source_label):a.kind==='image'?'PHOTO':/^[SV]\d+/i.test(a.name)?E(a.name.replace(/\.[^.]+$/,'')):String(i+1).padStart(2,'0')}</span><div class="media-meta"><strong>${E(a.name)}</strong><span>${a.reserve?(assetIsUsed(a)?'Reserve source · explicitly placed':'Held in reserve')+' · ':''}${a.kind==='image'?'Photograph':a.duration?a.duration.toFixed(1)+'s video':'Case video'} · ${a.verified?(a.approval_record?.independent_verification===false?'approved · user instruction':'approved'):'not approved'}</span></div><span class="ready-dot ${a.verified?'':'missing'}"></span></div>`).join('')||'<div class="loading">No case media imported.<br>Use Import media above.</div>';
 $('#audio-status').className='audio-card '+(P?.voiceover?'ready':'missing');$('#audio-status').innerHTML=`<strong>Voiceover · ${E(P?.voiceover?.name||'not imported')}</strong><span>${P?.voiceover?P.voiceover.duration.toFixed(3)+'s · continuous original audio':'Import from Setup. No per-cut audio restarts.'}</span>`;
 $('#srt-status').className='audio-card '+(P?.srt?'ready':'missing');$('#srt-status').innerHTML=`<strong>${P?.srt?P.srt.blocks.length:0} original SRT blocks</strong><span>Timing reference · captions not baked in</span>`;
 renderProgram();renderTimeline();renderInspector();renderChecks();renderExport();updatePosition();
}
function renderProgram(){
 const target=P?.render?.status==='done'&&P.render.current?P.render.url:(P?.preview?.status==='done'?P.preview.url:'');
 if(editorPlaying&&masterUrl!==target)return;
 const t=editorTime();
 if(target){
  if(masterUrl!==target){narrNode.pause();sourceNode.pause();masterUrl=target;editorMaster=true;masterNode.src=target;masterNode.load();const token=++masterSeekToken;masterNode.addEventListener('loadedmetadata',()=>{if(token===masterSeekToken&&masterNode.getAttribute('src')===target)masterNode.currentTime=Math.min(t,masterNode.duration||t)},{once:true})}
  editorMaster=true;masterNode.classList.add('is-active');sourceNode.classList.remove('is-active');imageNode.hidden=true;
 }else{
  if(masterUrl){masterNode.pause();masterNode.removeAttribute('src');masterNode.load();masterUrl=''}editorMaster=false;masterNode.classList.remove('is-active');
  const url=P?.voiceover?.url||'';if(narrNode.getAttribute('src')!==url){narrNode.pause();if(url){narrNode.src=url;narrNode.load()}else{narrNode.removeAttribute('src');narrNode.load()}}
  showSource(sceneIndex,t);
 }
 const assigned=scenes().length&&assets().some(a=>a.id===scenes()[sceneIndex]?.asset_id);$('#monitor-empty').hidden=!!scenes().length&&(editorMaster||assigned);if(scenes().length&&!assigned&&!editorMaster)$('#monitor-empty').innerHTML='<strong>No visual assigned to this scene</strong><span>Choose uploaded media in the shot inspector, or import case footage.</span><button class="button secondary" onclick="manage(\'assets\')">Import media</button>';else if(!scenes().length){const missing=(P?.source_match_report||[]).filter(x=>!['matched','selected'].includes(x.status));$('#monitor-empty').innerHTML=missing.length?`<strong>Storyboard waiting for footage links</strong><span>Script filenames need matching uploads: ${missing.map(x=>E(x.source_file)).join(', ')}. Your imported files are still here.</span><button class="button secondary" onclick="manage('assets')">Resolve script filenames</button>`:P?.srt?'<strong>Storyboard not built yet</strong><span>Your SRT is imported. Build the storyboard in Setup to place picture beats on V1.</span><button class="button secondary" onclick="manage(\'brief\')">Open production setup</button>':'<strong>Keep the editor. Start a new story.</strong><span>Import narration, SRT and case media from Setup.</span><button class="button secondary" onclick="newProduction()">＋ New production</button>';}$('#big-play').hidden=!scenes().length||(!assigned&&!editorMaster)||editorPlaying;$('#preview-mode').textContent=editorMaster?(target===P?.preview?.url?'SMOOTH PREVIEW':'CONTINUOUS MASTER'):(['queued','running'].includes(P?.preview?.status)?`PREPARING SMOOTH PREVIEW ${Math.floor(P.preview.progress||0)}%`:'SOURCE PREVIEW');
}
function clearPreparedSource(){
 prepareToken++;preparedScene='';preparedReady=false;nextSourceNode.pause();
 nextSourceNode.onloadedmetadata=nextSourceNode.onseeked=nextSourceNode.onloadeddata=nextSourceNode.onerror=null;
 nextSourceNode.removeAttribute('src');nextSourceNode.load();nextSourceNode.classList.remove('is-active');
}
function prepareNextSource(i,t){
 if(editorMaster||!editorPlaying)return;
 const current=scenes()[i],next=scenes()[i+1],asset=assets().find(a=>a.id===next?.asset_id);
 if(!current||!next||!asset||asset.kind!=='video'||current.frame_out/30-t>8)return;
 if(preparedScene===next.id)return;
 clearPreparedSource();preparedScene=next.id;const token=prepareToken,node=nextSourceNode,target=Number(next.in_s);
 const valid=()=>token===prepareToken&&preparedScene===next.id&&node===nextSourceNode;
 const ready=()=>{if(valid()&&!node.seeking&&node.readyState>=2&&Math.abs(node.currentTime-target)<.15)preparedReady=true};
 const seek=()=>{if(!valid()||node.readyState<1)return;if(!Number.isFinite(target)||target<0||target>node.duration+.034)return;try{node.currentTime=target;ready()}catch{}};
 node.onloadedmetadata=seek;node.onseeked=ready;node.onloadeddata=ready;node.onerror=()=>{if(valid())preparedReady=false};
 node.src=asset.url;node.load();
}
function usePreparedSource(s,a,t){
 if(preparedScene!==s.id||!preparedReady||nextSourceNode.getAttribute('src')!==a.url)return false;
 const offset=Math.max(0,t-s.frame_in/30);if(offset>.18)return false;
 sourceSeekToken++;sourceNode.pause();sourceNode.classList.remove('is-active');
 sourceNode.onloadedmetadata=sourceNode.onseeked=sourceNode.onloadeddata=sourceNode.onerror=null;
 const wasBuffering=editorBuffering;const old=sourceNode;sourceNode=nextSourceNode;nextSourceNode=old;prepareToken++;preparedScene='';preparedReady=false;
 sourceNode.onloadedmetadata=sourceNode.onseeked=sourceNode.onloadeddata=sourceNode.onerror=null;
 sourceReady=true;editorBuffering=false;clockAt=performance.now();sourceNode.playbackRate=1;sourceNode.classList.add('is-active');imageNode.hidden=true;$('#monitor-empty').hidden=true;
 if(editorPlaying&&wasBuffering&&P?.voiceover){narrNode.muted=editorMuted;narrNode.play().catch(()=>pauseEditor())}
 if(editorPlaying)sourceNode.play().catch(()=>{if(sourceScene===s.id&&!editorMaster)seekSourceAtTimeline(s,a,editorTime())});
 return true;
}
function seekSourceAtTimeline(s,a,t){
 // Freeze the preview clock while decoding. Never hide the last picture while audio runs.
 const token=++sourceSeekToken;sourceReady=false;sourceNode.pause();sourceNode.playbackRate=1;
 if(sourceNode.readyState>=2&&sourceNode.classList.contains('is-active')){
  try{const c=document.createElement('canvas');c.width=sourceNode.videoWidth;c.height=sourceNode.videoHeight;c.getContext('2d').drawImage(sourceNode,0,0);imageNode.src=c.toDataURL('image/jpeg',.88);imageNode.style.transform='none';imageNode.hidden=false}catch{}
 }
 sourceNode.classList.remove('is-active');editorBuffering=true;clockBase=t;clockAt=performance.now();narrNode.pause();
 const valid=()=>token===sourceSeekToken && sourceScene===s.id && !editorMaster;
 const desired=()=>Number(s.in_s)+Math.max(0,t-s.frame_in/30);
 const failed=()=>{if(!valid())return;sourceReady=false;pauseEditor();$('#monitor-empty').hidden=false;$('#monitor-empty').innerHTML='<strong>Media preview unavailable</strong><span>Playback paused with the narration; no empty slot was played.</span>'};
 let readyDone=false;
 const seek=()=>{if(!valid()||sourceNode.readyState<1)return;const target=desired();if(!Number.isFinite(target)||target<0||target>sourceNode.duration+.034){failed();return}try{sourceNode.currentTime=target;if(!sourceNode.seeking&&sourceNode.readyState>=2)ready()}catch{failed()}};
 const ready=()=>{if(readyDone||!valid()||sourceNode.seeking||sourceNode.readyState<2)return;if(Math.abs(sourceNode.currentTime-desired())>.15){seek();return}readyDone=true;sourceReady=true;editorBuffering=false;clockAt=performance.now();sourceNode.classList.add('is-active');imageNode.hidden=true;$('#monitor-empty').hidden=true;if(editorPlaying){const jobs=[sourceNode.play()];if(P?.voiceover){narrNode.muted=editorMuted;jobs.push(narrNode.play())}Promise.all(jobs).catch(()=>{if(valid()&&editorPlaying)failed()})}};
 sourceNode.onloadedmetadata=seek;sourceNode.onseeked=ready;sourceNode.onloadeddata=ready;sourceNode.onerror=failed;
 if(sourceNode.getAttribute('src')!==a.url){sourceNode.src=a.url;sourceNode.load()}else if(sourceNode.readyState>=1)seek();
}

// If the active video actually starves, hold narration until decoding resumes.
for(const node of [sourceNode,nextSourceNode]){
 node.addEventListener('waiting',()=>{
  if(node!==sourceNode||editorMaster||!editorPlaying||!sourceReady||editorBuffering)return;
  const now=editorTime();editorBuffering=true;clockBase=now;clockAt=performance.now();narrNode.pause();
 });
 node.addEventListener('playing',()=>{
  if(node!==sourceNode||editorMaster||!editorPlaying||!sourceReady||!editorBuffering)return;
  editorBuffering=false;clockAt=performance.now();
  if(P?.voiceover){narrNode.muted=editorMuted;narrNode.play().catch(()=>pauseEditor())}
 });
}

function showSource(i,t){
 if(editorMaster)return;const s=scenes()[i],a=assets().find(a=>a.id===s?.asset_id);if(!s||!a){sourceSeekToken++;sourceReady=false;sourceNode.pause();sourceNode.classList.remove('is-active');if(sourceNode.getAttribute('src')){sourceNode.removeAttribute('src');sourceNode.load()}imageNode.hidden=true;sourceScene='';$('#blur-backdrop').getContext('2d').clearRect(0,0,960,540);if(s){$('#monitor-empty').hidden=false;$('#monitor-empty').innerHTML='<strong>No visual assigned to this scene</strong><span>Select uploaded media in the shot inspector.</span>'}return}
 if(sourceScene!==s.id){
  $('#monitor-empty').hidden=true;sourceScene=s.id;
  if(a.kind==='image'){sourceNode.pause();sourceSeekToken++;sourceReady=false;sourceNode.onloadedmetadata=sourceNode.onseeked=sourceNode.onloadeddata=sourceNode.onerror=null;imageNode.src=a.url;imageNode.hidden=false;sourceNode.classList.remove('is-active')}
  else if(editorPlaying&&sourceReady&&!sourceNode.seeking&&sourceNode.getAttribute('src')===a.url&&Math.abs(sourceNode.currentTime-(Number(s.in_s)+Math.max(0,t-s.frame_in/30)))<.12){ /* Continuous interval in the same source: keep decoding without a seek. */ }
  else if(!usePreparedSource(s,a,t)){seekSourceAtTimeline(s,a,t)}
 }
 if(a.kind==='image')applyImageMotion(s,Math.max(0,t-s.frame_in/30));
 else if(editorPlaying&&sourceReady&&!sourceNode.seeking){
  // Small browser clock differences are normal. Correct them gradually rather than
  // repeatedly pausing narration and seeking the picture on every animation frame.
  const drift=sourceNode.currentTime-(Number(s.in_s)+Math.max(0,t-s.frame_in/30));
  sourceNode.playbackRate=Math.abs(drift)<.06?1:Math.max(.85,Math.min(1.15,1-drift*.12));
 }
}

function applyImageMotion(s,elapsed){const m=s.motion_plan;if(!m)return;const p=Math.max(0,Math.min(1,elapsed/m.duration_s)),q=m.easing==='linear'?p:m.easing==='quick_settle'?(p<.08?p*p/.1472:p>.92?1-(1-p)*(1-p)/.1472:(p-.04)/.92):m.easing==='sine'?.5-.5*Math.cos(Math.PI*p):m.easing==='smootherstep'?p*p*p*(p*(p*6-15)+10):p*p*(3-2*p);const z=m.zoom_start+(m.zoom_end-m.zoom_start)*q,x=m.x_start+(m.x_end-m.x_start)*q,y=m.y_start+(m.y_end-m.y_start)*q;imageNode.style.transform=`translate(${(.5-x)*(z-1)*100}%,${(.5-y)*(z-1)*100}%) scale(${z})`}
let lastBackdropDraw=0;
function drawBackdrop(){const now=performance.now();if(now-lastBackdropDraw<100)return;lastBackdropDraw=now;if(editorMaster||(imageNode.hidden&&!sourceReady))return;const node=imageNode.hidden?sourceNode:imageNode,w=node.videoWidth||node.naturalWidth,h=node.videoHeight||node.naturalHeight;if(!w||!h)return;const c=$('#blur-backdrop'),scale=Math.max(c.width/w,c.height/h);try{c.getContext('2d').drawImage(node,(c.width-w*scale)/2,(c.height-h*scale)/2,w*scale,h*scale)}catch{}}
function updatePosition(){const t=editorTime();const readout=$('#source-clock');if(readout){const current=scenes()[sceneIndex],asset=assets().find(a=>a.id===current?.asset_id);readout.textContent=asset?.kind==='video'?(sourceReady?'Actual player source: '+sourceNode.currentTime.toFixed(3)+'s':'Seeking to the saved source interval…'):'Still candidate; image identity not automatically verified.'}$('#timecode').textContent=fmt(t)+' / '+fmt(totalTime());$('#playhead').style.left=t*timelineZoom+'px';const caption=P?.srt?.blocks.find(b=>t>=b.start_ms/1000&&t<b.end_ms/1000)?.text||'';$('#editor-captions').textContent=caption;$('#editor-captions').classList.toggle('captions-off',!editorCC);const blocks=P?.srt?.blocks||[],index=blocks.findIndex(b=>t>=b.start_ms/1000&&t<b.end_ms/1000);if(index!==activeSRT){document.querySelector('.caption-block.active')?.classList.remove('active');document.querySelector(`.caption-block[data-caption-index="${index}"]`)?.classList.add('active');activeSRT=index}}
function renderTimeline(){
 activeSRT=-1;const ss=scenes(),width=Math.max(totalTime()*timelineZoom,$('#timeline-scroll').clientWidth-2,600);$('#timeline-content').style.width=width+'px';$('#track-legend').textContent=`${ss.length} full-frame scenes · ${P?.srt?.blocks.length||0} SRT blocks`;
 const step=timelineZoom<5?60:30;let ticks='';for(let t=0;t<=totalTime()+step;t+=step)ticks+=`<span class="ruler-tick" style="left:${t*timelineZoom}px">${Math.floor(t/60).toString().padStart(2,'0')}:${String(t%60).padStart(2,'0')}</span>`;$('#ruler-track').innerHTML=ticks;
 $('#video-track').innerHTML=ss.map((s,i)=>{const a=assets().find(a=>a.id===s.asset_id),thumb=s.thumbnail||(a&&P?`/api/projects/${P.id}/scenes/${s.id}/thumbnail`:'');return `<div class="shot-clip ${i===sceneIndex?'selected':''} ${a?.kind==='image'?'image-clip':''}" data-scene="${i}" role="button" tabindex="0" title="${E(s.narration)}" style="left:${s.frame_in/30*timelineZoom}px;width:${Math.max(3,(s.frame_out-s.frame_in)/30*timelineZoom-1)}px" onclick="selectEditorScene(${i})" onkeydown="if(event.key==='Enter')selectEditorScene(${i})">${thumb?`<img class="clip-thumb" src="${thumb}" loading="lazy" alt="" onerror="this.hidden=true">`:''}<div class="clip-text"><strong>C${String(i+1).padStart(2,'0')} · ${E(a?.name||'MEDIA NEEDED')}</strong><span>${((s.frame_out-s.frame_in)/30).toFixed(1)}s · ${E(s.purpose||s.focus)}</span></div>${s.transition==='dissolve'?'<i class="transition-mark"></i>':''}</div>`}).join('');
 const blocks=P?.srt?.blocks||[];
 $('#caption-track').innerHTML=blocks.map((b,i)=>`<div class="caption-block" data-caption-index="${i}" title="${E(b.text)}" style="left:${b.start_ms/1000*timelineZoom}px;width:${Math.max(2,(b.end_ms-b.start_ms)/1000*timelineZoom-1)}px" onclick="seekEditor(${b.start_ms/1000},true)">${E(b.text)}</div>`).join('')+'<span class="caption-note" style="left:8px">SRT TIMING REFERENCE · ORIGINAL NARRATION ORDER PRESERVED · NOT BURNED IN</span>';
}
function renderInspector(){const s=scenes()[sceneIndex];$('#shot-number').textContent=s?'C'+String(sceneIndex+1).padStart(2,'0'):'—';$('#monitor-shot-label').textContent=s?`CLIP C${String(sceneIndex+1).padStart(2,'0')} / ${scenes().length}`:'NO SCENES';
 if(!s){const missing=(P?.source_match_report||[]).filter(x=>!['matched','selected'].includes(x.status));$('#inspector-content').innerHTML=missing.length?`<div class="inspector-group"><label>Waiting for footage links</label><p>The script refers to ${missing.map(x=>E(x.source_file)).join(', ')}. Select the correct existing videos or upload the missing files.</p><button type="button" onclick="manage('assets')">Resolve filenames</button></div>`:P?.srt?'<div class="inspector-group"><label>No picture beats yet</label><p>The SRT is imported. Open Setup and build the storyboard.</p><button type="button" onclick="manage(\'brief\')">Open setup</button></div>':'<div class="inspector-group"><label>Ready for a new production</label><p>Upload your production script, SRT and narration under Setup.</p></div>';return}
 const a=assets().find(a=>a.id===s.asset_id),m=s.motion_plan||{};
 const flow=s.picture_flow,cut=s.cut_sync;
 const flowLabel={already_inside_one_shot:'inside one shot',continuous_with_previous:'runs on from previous beat',continued_running_take:'continued take',slid_inside_a_shot:'conformed inside a shot',slid_far_inside_a_shot:'moved far — check it',repeats_recent_footage:'repeats recent footage',jump_cut_within_shot:'deliberate jump cut',no_single_shot_fits:'no shot fits this beat',not_decoded:'source not decoded'};
 const cutLabel={in_a_breath:'cut in a breath',on_a_block_boundary:'cut on a line break',over_speech:'cut over a spoken word',between_blocks:'cut between blocks'};
 const meta=`<div class="mini-meta"><span>SRT ${s.srt_blocks.join('–')}</span><span>${((s.frame_out-s.frame_in)/30).toFixed(3)}s</span></div><div class="mini-meta"><span>Timeline</span><span>${(s.frame_in/30).toFixed(3)} → ${(s.frame_out/30).toFixed(3)}s</span></div>${flow?`<div class="mini-meta"><span>Picture flow</span><span title="${E(flow.note||'')}">${E(flowLabel[flow.status]||flow.status)}${flow.shot?` · shot ${flow.shot[0]}–${flow.shot[1]}s`:''}</span></div>`:''}${cut?`<div class="mini-meta"><span>Cut to next</span><span>${E(cutLabel[cut.quality]||cut.quality)}${cut.moved_from_s!=null?` · moved from ${cut.moved_from_s}s`:''}</span></div>`:''}`;
 $('#inspector-content').innerHTML=`<form id="scene-form" onsubmit="saveEditorScene(event)"><div class="inspector-group"><label>Narration / picture beat</label><p class="narration-excerpt">${E(s.narration)}</p></div>${meta}${sourceTimingHTML(s,a)}<label>Emotion<input name="emotion" value="${E(s.emotion)}"></label><label>Primary focus<input name="focus" value="${E(s.focus)}"></label><label>Named person<input name="person" value="${E(s.person)}"></label><label>Selected visual<select name="asset_id">${options([['','Choose uploaded media'],...P.assets.map(a=>[a.id,(a.reserve?'[reserve '+a.source_label+'] ':'')+(a.verified?'✓ ':'? ')+a.name])],s.asset_id||'')}</select></label><label>Video in-point (seconds)<input name="in_s" type="number" step=".001" min="0" value="${s.in_s}">${flow&&flow.declared_in_s!=null&&Math.abs(flow.declared_in_s-flow.conformed_in_s)>=.001?`<span class="small">Declared ${flow.declared_in_s}s · conformed to ${flow.conformed_in_s}s so the beat stays inside one continuous shot. ${E(flow.note||'')}</span>`:''}</label><label>Exit transition<select name="transition">${options(P.transition_policy==='clean_cuts_only'?[['cut','Clean cut · transitions disabled']]:[['cut','Clean action / phrase cut'],['dissolve','Short dissolve · handles required']],s.transition)}</select></label><label class="check-label"><input type="checkbox" name="context_only" ${s.context_only?'checked':''}>Disclosed wide context, not the exact person/exhibit</label><label>Editorial / exception notes<textarea name="notes" rows="3">${E(s.notes)}</textarea></label><div class="edit-actions"><button type="button" onclick="rebuildHere()">Rebuild this block</button><button type="button" onclick="splitBeat()">Split beat</button><button type="button" ${sceneIndex===P.scenes.length-1?'disabled':''} onclick="mergeBeat()">Merge next</button></div><label class="check-label"><input type="checkbox" name="approved" ${s.approved?'checked':''}>Editorially approved</label><button class="button primary" type="submit">Save scene</button></form><div id="scene-understanding"></div><div id="plan-tools"></div>`;
 if(a?.kind==='video')loadSceneUnderstanding(s.id);
 $('#plan-tools').innerHTML=planToolsHTML();candidateHTMLForScene(s).then(h=>{const n=$('#plan-tools');if(n)n.insertAdjacentHTML('beforeend',h)});
 if(s.automatic_match&&!P.manual_scene_edits){const form=$('#scene-form');for(const name of ['person','context_only','approved']){const field=form.querySelector('[name="'+name+'"]');if(field)field.closest('label').hidden=true}form.insertAdjacentHTML('afterbegin','<div class="inspector-group"><label>Automatic clips-only assembly</label><p>Source interval checked and linked to the script/SRT. No manual portrait selection or scene approval is required for this prepared timeline.</p></div>')}
}
function sourceTimingHTML(s,a){if(!a)return '';if(s.automatic_match)return `<div class="inspector-group"><label>Analyzed MP4 / SRT linkage</label><p>${E(a.original_name||a.name)} · SRT ${E(s.automatic_match.srt_block)}</p><p>Saved IN: ${Number(s.in_s).toFixed(3)}s → ${(Number(s.in_s)+(s.frame_out-s.frame_in)/30).toFixed(3)}s</p><p>Source-bank match: ${E(s.automatic_match.bank_id)}. Provisional CSV times were checked against actual footage.</p><p id="source-clock" class="small"></p></div>`;const derived=a.derived_from,planned=s.plan_row;const actual=derived?derived.requested_time_ms/1000:Number(s.in_s);return `<div class="inspector-group"><label>Source cut / CSV timing</label><p>${E(derived?.source_file||a.original_name||a.name)}</p><p>${derived?'Extracted frame':'Saved IN'}: ${actual.toFixed(3)}s${a.kind==='video'?' → '+(actual+(s.frame_out-s.frame_in)/30).toFixed(3)+'s':''}</p><p>${planned?'CSV IN: '+((planned.extracted_frame_ms??planned.source_in_ms)/1000).toFixed(3)+'s':'No conformed CSV row linked to this scene.'}</p><p id="source-clock" class="small"></p></div>`}


async function loadSceneUnderstanding(sceneId){
 const id=P.id;
 try{
  const r=await api(`/api/projects/${id}/scenes/${sceneId}/understanding`);
  const node=$('#scene-understanding');if(!node||P?.id!==id||scenes()[sceneIndex]?.id!==sceneId)return;
  if(!['partial','complete'].includes(r.status)){
   node.innerHTML=`<div class="inspector-group"><label>Source understanding</label><p class="small">${E(r.status==='not_started'?'No local analysis yet. Run it from Production setup.':r.status)}</p></div>`;return;
  }
  const entry=(time,text)=>({time:Number(time),html:`<p class="small">${Number(time).toFixed(2)}s · ${text}</p>`});
  const rows=[...r.transcript.map(x=>entry(x.timeline_start_s,`Dialogue: ${E(x.text)}`)),
   ...r.visual.map(x=>entry(x.timeline_time_s,`Frame: ${E(x.description)}`)),
   ...r.sound_events.filter(x=>x.predictions.length).map(x=>entry(x.timeline_start_s,`Sounds: ${x.predictions.map(p=>E(p.label)).join(', ')}`)),
   ...(r.speakers||[]).map(x=>entry(x.timeline_start_s,`Speaker: ${E(x.speaker)}`)),
   ...(r.low_energy_intervals||[]).map(x=>entry(x.timeline_start_s,'Low-energy audio (estimate)')),
   ...(r.scene_changes||[]).filter(x=>x.start_s>=r.source_in_s).map(x=>entry(x.timeline_start_s,'Detected source scene start'))].sort((a,b)=>a.time-b.time);
  node.innerHTML=`<div class="inspector-group"><label>Source understanding · timeline times</label><p class="small">Estimated observations, not verified. Only sampled frames and overlapping source intervals are shown; dialogue text may extend outside this beat.</p>${r.stage_status?.visual==='unavailable'?'<p class="small">Visual analysis unavailable for this source.</p>':''}${r.timing_warning?`<p class="small">${E(r.timing_warning)}</p>`:''}${rows.slice(0,12).map(x=>x.html).join('')||'<p class="small">No observations overlap this beat.</p>'}${rows.length>12?'<p class="small">More observations are available in the full report.</p>':''}</div>`;
 }catch(e){/* A missing or changed video leaves the existing inspector usable. */}
}

// ---- Planning aids: shot index, feasibility, block-map template -----------------
// Everything here reads decoded facts. Nothing places a picture by itself.
let planCache = {id: null, index: null, feasibility: null};

async function loadPlanning(id){
  if(planCache.id === id) return planCache;
  planCache = {id, index: null, feasibility: null};
  try{ planCache.index = await api(`/api/projects/${id}/shot-index`); }catch(e){ planCache.index = {error: e.message}; }
  try{ planCache.feasibility = await api(`/api/projects/${id}/feasibility`); }catch(e){ planCache.feasibility = {error: e.message}; }
  return planCache;
}

function planningSummaryHTML(){
  const f = planCache.feasibility || {};
  if(f.error) return `<div class="notice">Feasibility could not be read: ${E(f.error)}</div>`;
  const unfilled = (f.unfilled_beats || []).length;
  const blocked = (f.sources || []).filter(s => s.problems.length);
  const verdict = f.verdict === 'feasible' ? 'The plan fits the footage as written.'
    : f.verdict === 'no_plan' ? 'No block map yet, so there is nothing to check.'
    : `${unfilled} beat${unfilled===1?'':'s'} still unfilled and ${blocked.length} source${blocked.length===1?'':'s'} with a problem.`;
  const rows = (f.sources || []).map(s => `<tr><td>${E(s.file)}</td><td>${s.beats}</td><td>${s.required_s}s</td><td>${s.available_s}s</td><td>${s.longest_beat_s}s</td><td>${s.longest_shot_s}s</td></tr>`).join('');
  return `<p>${E(verdict)}</p>${rows?`<table class="plan-table"><thead><tr><th>Source</th><th>Beats</th><th>Needed</th><th>File length</th><th>Longest beat</th><th>Longest shot</th></tr></thead><tbody>${rows}</tbody></table>`:''}
  ${blocked.map(s => `<div class="notice warn"><strong>${E(s.file)}</strong>${s.problems.map(x => `<p class="small">${E(x)}</p>`).join('')}</div>`).join('')}
  ${unfilled ? `<p class="small">${unfilled} name${unfilled===1?'s':'s'} no source yet. Those rows are gaps by design; fill them in the block map rather than letting a clip be chosen by guesswork.</p>` : ''}`;
}

function planToolsHTML(){
  return `<div class="inspector-group"><label>Planning</label>
  <div class="inline"><button type="button" onclick="openPlanDialog('index')">Shot index</button><button type="button" onclick="openPlanDialog('feasibility')">Feasibility</button><button type="button" onclick="downloadMapTemplate()">Download map template</button><button type="button" onclick="openFrameQC(1)">Frame QC sheet</button><button type="button" onclick="approveReadyScenes()">Approve all ready beats</button></div>
  <p class="small">The template fills every clock and SRT block reference from your SRT and leaves the picture columns blank on purpose. A blank row is a named gap, not a choice.</p></div>`;
}

async function openPlanDialog(view){
  if(!P) return;
  $('#plan-title').textContent = view === 'index' ? 'Shot index' : 'Feasibility';
  $('#plan-body').innerHTML = '<p class="small">Reading decoded sources…</p>';
  $('#plan-dialog').showModal();
  await loadPlanning(P.id);
  if(view === 'index'){
    const sources = (planCache.index?.sources || []);
    if(!sources.length){ $('#plan-body').innerHTML = '<p>Upload at least one video, then run the build so the sources are decoded.</p>'; return; }
    $('#plan-body').innerHTML = sources.map(s => `<div class="card"><h3>${E(s.file)} · ${s.duration_s.toFixed(3)}s · ${s.shots.length} shots${s.not_decoded?' · not decoded yet':''}</h3>
      ${s.not_decoded ? '<p class="small">Run the build once so this file is decoded.</p>' : `<table class="plan-table"><thead><tr><th>#</th><th>IN</th><th>OUT</th><th>Length</th><th>Map columns</th><th></th></tr></thead><tbody>${s.shots.map((x,i)=>`<tr><td>${i+1}</td><td>${x.in_s}s</td><td>${x.out_s}s</td><td>${x.duration_s}s</td><td><code>${x.in_clock} → ${x.out_clock}</code></td><td>${x.black?'black':''}</td></tr>`).join('')}</tbody></table>`}</div>`).join('')
      + `<p class="small">A shot is a decoded span between detected changes. This list says nothing about who or what appears in it.</p>`;
  } else {
    $('#plan-body').innerHTML = `<div class="card"><h3>Does this plan fit the footage?</h3>${planningSummaryHTML()}</div>`;
  }
}

async function downloadMapTemplate(){
  if(!P) return;
  try{
    const r = await fetch(`/api/projects/${P.id}/block-map-template`);
    if(!r.ok){ const d = await r.json().catch(()=>({})); throw Error(d.error || 'The template could not be built.'); }
    const blob = await r.blob(); const url = URL.createObjectURL(blob);
    const a = document.createElement('a'); a.href = url; a.download = 'block-map-template.csv'; a.click();
    URL.revokeObjectURL(url);
    toast('Template downloaded. Clocks are filled; picture columns are blank on purpose.');
  }catch(e){ toast(e.message); }
}

async function candidateHTMLForScene(s){
  if(!P) return '';
  const length = (s.frame_out - s.frame_in) / 30;
  const handle = s.transition === 'dissolve' ? (s.transition_handle_frames || 0) / 30 : 0;
  try{
    const d = await api(`/api/projects/${P.id}/candidates?length_s=${length.toFixed(6)}&handle_s=${handle.toFixed(6)}&limit=6`);
    if(!d.candidates.length){
      const anyDecoded = (planCache.index?.sources || []).some(x => !x.not_decoded && x.shots.length);
      if(!anyDecoded && !planCache.index) await loadPlanning(P.id);
      const decoded = (planCache.index?.sources || []).some(x => !x.not_decoded && x.shots.length);
      return `<div class="inspector-group"><label>Shots that fit this beat</label><p class="small">${decoded
        ? `No decoded shot is exactly ${(length+handle).toFixed(3)}s long. Nothing is offered rather than something approximate; the build reports this as a gap.`
        : 'The sources have not been decoded yet. Run the build once, then this list is exact.'}</p></div>`;
    }
    return `<div class="inspector-group"><label>Shots that fit this beat · ${(length+handle).toFixed(3)}s</label>${d.candidates.map(c=>`<p class="small"><strong>${E(c.file)}</strong> · <code>${c.in_clock} → ${c.out_clock}</code> · ${c.duration_s}s · ${c.slack_s}s spare</p>`).join('')}<p class="small">Exact-length shots only. Choosing one is still your editorial decision.</p></div>`;
  }catch(e){ return `<div class="inspector-group"><label>Shots that fit this beat</label><p class="small">${E(e.message)}</p></div>`; }
}


// ---- Bulk approval and frame QC ----------------------------------------------
async function approveReadyScenes(){
  if(!P || !leave()) return;
  const unapproved = P.scenes.filter(s => !s.approved).length;
  if(!unapproved){ toast('Every picture beat is already approved.'); return; }
  if(!confirm(`Approve every picture beat that passes the same checks export readiness uses? ${unapproved} beat(s) are not approved. A beat with any blocking item is left unapproved and listed with its reason. This is your attestation that you reviewed the pictures, not an identity or rights clearance.`)) return;
  try{
    const d = await api(`/api/projects/${P.id}/scenes/approve`, 'POST', {attested: true, scope: 'ready'});
    P = d; renderProject(); await list();
    const done = d.bulk_approval?.approved?.length || 0, skipped = d.bulk_approval?.skipped || [];
    if(skipped.length){
      const first = skipped[0];
      toast(`${done} beat(s) approved. ${skipped.length} left unapproved — Scene ${first.scene}: ${first.reasons[0]}`);
    } else toast(`${done} picture beat(s) approved. All others were already approved.`);
  }catch(e){ toast(e.message); }
}

let qcCache = {id: null, data: null};
async function openFrameQC(sheet){
  if(!P) return;
  $('#plan-title').textContent = 'Frame QC';
  $('#plan-body').innerHTML = '<p class="small">Decoding one frame per beat and stamping its source timecode…</p>';
  if(!$('#plan-dialog').open) $('#plan-dialog').showModal();
  try{
    const d = await api(`/api/projects/${P.id}/frame-qc?format=json&sheet=${sheet || 1}`);
    qcCache = {id: P.id, data: d};
    const sheets = Math.max(1, d.sheets);
    $('#plan-body').innerHTML = `<div class="card"><h3>Sheet ${d.sheet} of ${sheets} · beats ${(d.sheet-1)*d.per_sheet+1}–${(d.sheet-1)*d.per_sheet+d.beats_on_sheet}</h3>
      <p>${E(d.note)}</p>
      <img style="width:100%;border-radius:6px" src="${d.url}" alt="Contact sheet of decoded frames stamped with source and in-point">
      <div class="inline">${Array.from({length: sheets}, (_, i) => `<button type="button" onclick="openFrameQC(${i+1})" ${i+1===d.sheet?'disabled':''}>Sheet ${i+1}</button>`).join('')}</div>
      ${d.missing.length ? `<div class="notice warn"><strong>${d.missing.length} beat(s) could not be decoded</strong>${d.missing.slice(0,6).map(b => `<p class="small">Scene ${b.scene} · SRT ${(b.srt_blocks||[]).join('-')} · ${E(b.source_file || 'no media assigned')} at ${b.in_clock}</p>`).join('')}</div>` : ''}
      ${d.moved.length ? `<details><summary>${d.moved.length} beat(s) start somewhere other than the plan declared</summary>${d.moved.map(b => `<p class="small">Scene ${b.scene} · SRT ${(b.srt_blocks||[]).join('-')} · ${E(b.source_file)} · planned in-point moved by ${b.declared_drift_s}s → now ${b.in_clock}</p>`).join('')}</details>` : ''}
      <p class="small">${E(d.scope)}</p></div>`;
  }catch(e){
    $('#plan-body').innerHTML = `<div class="notice">${E(e.message)}</div>`;
  }
}

async function saveEditorScene(e){pauseEditor();await saveScene(e);sourceScene='';renderProject()}

// Rebuild only the block range around the current beat, keeping approvals elsewhere.
async function rebuildHere(){
  if(!P||!leave())return;
  const s=P.scenes[sceneIndex];if(!s)return;
  const blocks=(s.srt_blocks||[])[0];
  if(blocks==null){toast('This beat has no SRT block to rebuild by.');return}
  if(!confirm(`Rebuild the plan for SRT block ${blocks} only? Picture beats outside this block keep their media and their approvals. Use the full rebuild for anything that changes timing.`))return;
  try{
    const d=await api(`/api/projects/${P.id}/storyboard`,'POST',{replace:true,blocks:[blocks]});
    P=d;renderProject();await list();
    const r=P.rebuild_report||{};
    toast(`Rebuilt block ${blocks}. ${r.approvals_carried||0} approval(s) carried over; ${r.needs_review||0} beat(s) need review.`);
  }catch(e){toast(e.message)}
}
function renderChecks(){const ss=scenes(),errors=P?.validation?.errors||[];$('#validation-summary').textContent=!P?'No production selected':errors.length?`${errors.length} item${errors.length===1?'':'s'} to review`:(P?.picture_policy==='clips_only'?'Ready · automatic checks passed':'Ready for review export');$('#validation-summary').style.color=errors.length?'var(--amber)':'var(--green)';$('#validation-list').innerHTML=P?`<div class="validation-item ok">✓ ${ss.length} picture beats · original narration / SRT timing preserved.</div>${errors.slice(0,3).map(x=>`<div class="validation-item warning">△ ${E(x)}</div>`).join('')}${errors.length>3?`<div class="validation-item">${errors.length-3} more items under Review / export checks.</div>`:''}${!editorMaster&&ss.length?'<div class="validation-item">Automatic continuous-master render is enabled. Play the rendered master for smooth cuts.</div>':''}`:'<div class="validation-item">Create a new production, then import the voiceover, SRT and source MP4s.</div>'}
function renderExport(){const r=P?.render||{},busy=['running','queued'].includes(r.status);$('#render-stage').textContent=r.output&&!r.current&&!busy?'New edits — render again':r.stage||'Not rendered';$('#render-progress').style.width=(r.progress||0)+'%';$('#render-detail').textContent=r.error?'Export needs attention; open Review / export checks.':r.detail||'Clean MP4 · original narration · separate SRT';$('#render-button').disabled=!P||busy;$('#render-button').title='Review checks and render a clean MP4';const link=$('#download-link');link.hidden=!r.url;link.href=r.url?r.url+'?download=1':'#';link.textContent=r.output&&!r.current?'Previous MP4':'Download MP4'}
async function renderFromEditor(){if(!P)return;if(P.validation?.errors?.length){manage('delivery');return}await startExport(false)}
function inspectAsset(id){if(!leave())return;const found=scenes().findIndex(s=>s.asset_id===id);if(found>=0){selectEditorScene(found);toast('Selected the first timeline scene using this asset.')}else manage('assets')}
function selectEditorScene(i){if(!scenes().length)return;if(!leave())return;pauseEditor();sceneIndex=Math.max(0,Math.min(scenes().length-1,i));sourceScene='';seekEditor(scenes()[sceneIndex].frame_in/30,false);renderInspector();markSelected()}
function markSelected(){document.querySelectorAll('.shot-clip').forEach(n=>n.classList.toggle('selected',Number(n.dataset.scene)===sceneIndex));$('#monitor-shot-label').textContent=scenes().length?`CLIP C${String(sceneIndex+1).padStart(2,'0')} / ${scenes().length}`:'NO SCENES'}
function seekEditor(t,select=true){if(!scenes().length)return;t=Math.max(0,Math.min(totalTime()-.001,t));if(select&&!leave())return;const was=editorPlaying;pauseEditor();clockBase=t;clockAt=performance.now();if(editorMaster){masterSeekToken++;try{masterNode.currentTime=t}catch{}}else if(P?.voiceover){try{narrNode.currentTime=t}catch{}}if(select){const i=scenes().findIndex(s=>t>=s.frame_in/30&&t<s.frame_out/30);if(i>=0)sceneIndex=i}sourceScene='';if(!editorMaster)showSource(sceneIndex,t);renderInspector();markSelected();updatePosition();if(was)playEditor()}
async function playEditor(){if(!scenes().length){toast('Import SRT and build the storyboard first.');return}if(dirty){toast('Save or discard scene edits before playing.');return}if(editorTime()>=totalTime()-.01)seekEditor(0,true);editorPlaying=true;clockAt=performance.now();$('#big-play').hidden=true;try{if(editorMaster){masterNode.muted=editorMuted;await masterNode.play()}else{const s=scenes()[sceneIndex],a=assets().find(a=>a.id===s?.asset_id);if(a?.kind==='video'&&!sourceReady){seekSourceAtTimeline(s,a,editorTime());return}showSource(sceneIndex,editorTime());if(P?.voiceover){narrNode.muted=editorMuted;await narrNode.play()}if(a?.kind==='video'&&sourceReady&&sourceNode.readyState>=2)await sourceNode.play()}}catch(e){pauseEditor();toast('Playback could not start. Check the selected media or try again.')}}
function pauseEditor(){if(editorPlaying&&!editorMaster&&!P?.voiceover)clockBase=editorTime();editorPlaying=false;masterNode.pause();sourceNode.pause();narrNode.pause();$('#big-play').hidden=!scenes().length;clockAt=performance.now()}
function stopEditor(){pauseEditor();seekEditor(0,true)}
function toggleEditorAudio(){editorMuted=!editorMuted;masterNode.muted=editorMuted;narrNode.muted=editorMuted;$('#audio-toggle').textContent=editorMuted?'Muted':'Narration'}
function toggleEditorCaptions(){editorCC=!editorCC;$('#caption-toggle').textContent=editorCC?'CC On':'CC Off';updatePosition()}
function setEditorZoom(v){timelineZoom=Number(v);renderTimeline();updatePosition()}
masterNode.addEventListener('ended',pauseEditor);narrNode.addEventListener('ended',pauseEditor);
$('#manage-dialog').addEventListener('cancel',e=>{e.preventDefault();closeManagement()});
$('#ruler-track').onclick=e=>{const r=$('#ruler-track').getBoundingClientRect();seekEditor((e.clientX-r.left)/timelineZoom,true)};
document.addEventListener('input',e=>{if(e.target.closest('#scene-form')&&editorPlaying)pauseEditor()});
document.addEventListener('keydown',e=>{if(e.code==='Space'&&!document.querySelector('dialog[open]')&&!['INPUT','TEXTAREA','SELECT','BUTTON'].includes(document.activeElement.tagName)){e.preventDefault();editorPlaying?pauseEditor():playEditor()}});
function tickEditor(){if(editorPlaying){const t=editorTime();if(t>=totalTime()){pauseEditor()}else{const i=scenes().findIndex(s=>t>=s.frame_in/30&&t<s.frame_out/30);if(i>=0&&i!==sceneIndex){sceneIndex=i;if(!editorMaster)showSource(i,t);renderInspector();markSelected()}if(!editorMaster){showSource(sceneIndex,t);prepareNextSource(sceneIndex,t);drawBackdrop()}}}else if(!editorMaster)drawBackdrop();updatePosition();requestAnimationFrame(tickEditor)}
setInterval(async()=>{if(!P||dirty||uploading||editorPlaying)return;try{const next=await api('/api/projects/'+P.id);if(next.updated!==P.updated||next.render?.status!==P.render?.status||next.render?.progress!==P.render?.progress||next.render?.output!==P.render?.output||next.preview?.status!==P.preview?.status||next.preview?.progress!==P.preview?.progress||next.scene_exports?.status!==P.scene_exports?.status||next.scene_exports?.progress!==P.scene_exports?.progress){P=next;sceneIndex=Math.min(sceneIndex,Math.max(0,P.scenes.length-1));renderProject()}}catch(e){toast(e.message)}},3000);
(async()=>{try{await list();const id=location.hash.match(/^#\/production\/([a-f0-9]{16})$/);if(id)await openProject(id[1]);else if(projects[0])await openProject(projects[0].id);else renderEditor();requestAnimationFrame(tickEditor)}catch(e){toast(e.message);renderEditor();requestAnimationFrame(tickEditor)}})();

// Manual block-map authoring. Only the visible fields are changed; other imported
// production instructions stay on the row. A draft remains in memory until Save.
let mapDraft=null;
async function openBlockMapEditor(){
 if(!P||!leave())return;
 const id=P.id;
 try{
  const data=await api(`/api/projects/${id}/block-map-editor`);
  if(!P||P.id!==id)return;
  mapDraft={id,signature:data.srt_signature,rows:data.rows,index:0,changed:false};
  renderMapEditor();$('#map-editor-dialog').showModal();
 }catch(e){toast(e.message)}
}
function mapValue(row,key){
 if(Object.hasOwn(row,'_edit_'+key))return row['_edit_'+key];
 if(key==='beat')return row.parent_beat_id??row.beat??'1';
 if(key==='media_file')return row.source_file??row.media_file??'';
 if(key==='source_in')return row.source_in??(row.source_in_ms==null?'':mapClock(row.source_in_ms));
 if(key==='source_out')return row.source_out??(row.source_out_ms==null?'':mapClock(row.source_out_ms));
 if(key==='status')return row.status??row.visual_status??'';
 return row[key]??'';
}
function mapClock(ms){const n=Math.round(Number(ms)),h=Math.floor(n/3600000),m=Math.floor(n/60000)%60,s=Math.floor(n/1000)%60;return `${String(h).padStart(2,'0')}:${String(m).padStart(2,'0')}:${String(s).padStart(2,'0')}.${String(n%1000).padStart(3,'0')}`}
function renderMapEditor(){
 const d=mapDraft,r=d.rows[d.index],block=P.srt?.blocks.find(b=>Number(b.number)===Number(r.block??r.srt_blocks?.[0]));
 const field=(label,key,placeholder='')=>`<label>${label}<input data-map-field="${key}" value="${E(mapValue(r,key))}" placeholder="${E(placeholder)}"></label>`;
 $('#map-editor-body').innerHTML=`<p class="small">Choose footage and exact source times for each SRT block. Nothing is selected automatically. Leave a source blank and explain the gap if footage is not ready. Save the map, then rebuild the storyboard; saving resets existing approvals.</p>
 <div class="inline"><button type="button" onclick="mapStep(-1)" ${d.index===0?'disabled':''}>← Previous</button><label>Block <select onchange="mapGo(this.value)">${d.rows.map((row,i)=>`<option value="${i}" ${i===d.index?'selected':''}>${E(row.block??row.srt_blocks?.[0]??i+1)}${mapValue(row,'media_file')?'':' · gap'}</option>`).join('')}</select></label><button type="button" onclick="mapStep(1)" ${d.index===d.rows.length-1?'disabled':''}>Next →</button></div>
 <div class="notice"><strong>${E(block?.start_ms!=null?mapClock(block.start_ms):r.start)} → ${E(block?.end_ms!=null?mapClock(block.end_ms):r.end)}</strong><p>${E(block?.text||'Narration not found for this block; review the map against the SRT.')}</p></div>
 <form id="map-row-form" onsubmit="event.preventDefault();saveBlockMap()"><div class="two">${field('Parent beat number','beat','1')}<label>Source filename<input data-map-field="media_file" list="map-source-files" value="${E(mapValue(r,'media_file'))}" placeholder="Select or type original filename"></label>${field('Source IN','source_in','00:00:00.000')}${field('Source OUT (exclusive)','source_out','00:00:04.000')}${field('Status (optional)','status','VIDEO, STILL or CTX')}${field('Footage group (optional)','footage_group','Same label for the same shot')}</div>${field('Picture description (optional)','visible_content','What the picture should show')}<label>Gap note (required if no source)<textarea data-map-field="gap_note" rows="2">${E(mapValue(r,'gap_note'))}</textarea></label><datalist id="map-source-files">${P.assets.map(a=>`<option value="${E(a.original_name||a.name)}"></option>`).join('')}</datalist><p class="small">Use HH:MM:SS.mmm for source times. OUT must follow IN; a moving interval must fit the block duration. Existing extra map instructions are preserved.</p><div id="map-save-error" class="notice warn" role="alert" hidden></div><div class="dialog-actions"><button type="button" onclick="closeBlockMapEditor()">Cancel</button><button class="new" type="submit">Save block map</button></div></form>`;
 $('#map-row-form').addEventListener('input',e=>{
  const key=e.target.dataset.mapField;if(!key)return;
  r['_edit_'+key]=e.target.value;d.changed=true;dirty=true;
 });
 // Show in-memory changes when returning to a row.
 $('#map-row-form').querySelectorAll('[data-map-field]').forEach(el=>{const k=el.dataset.mapField;if(Object.hasOwn(r,'_edit_'+k))el.value=r['_edit_'+k]});
}
function mapGo(i){mapDraft.index=Number(i);renderMapEditor()}
function mapStep(delta){mapGo(mapDraft.index+delta)}
function closeBlockMapEditor(){if(mapDraft?.changed&&!confirm('Discard unsaved block-map edits?'))return;mapDraft=null;dirty=false;$('#map-editor-dialog').close()}
$('#map-editor-dialog').addEventListener('cancel',e=>{e.preventDefault();closeBlockMapEditor()});
function mapSaveRows(rows){
 return rows.map(original=>{
  const r={...original},value=k=>r['_edit_'+k]??mapValue(r,k);
  const file=value('media_file').trim(),inside=value('source_in').trim(),out=value('source_out').trim(),beat=value('beat').trim(),status=value('status').trim();
  const description=value('visible_content'),group=value('footage_group'),gap=value('gap_note');
  // Normalized imports contain aliases and derived millisecond values. Remove them
  // before re-parsing edited clocks, otherwise the old values conflict with the edits.
  for(const key of Object.keys(r))if(key.startsWith('_edit_'))delete r[key];
  for(const key of ['beat_id','scene','local_file','media_filename','media','file','source','source_file_name','media_file_name','in','out','source_in_time','source_out_time','source_in_ms','source_out_ms','source_out_excl','source_in_ms_planned','source_out_ms_excl_planned'])delete r[key];
  r.beat=beat;r.media_file=file;r.source_file=file;r.source_in=inside;r.source_out=out;
  r.status=status;r.visual_status=status;r.visible_content=description;if('required_visual' in r)r.required_visual=description;r.footage_group=group;r.gap_note=gap;
  return r;
 });
}
async function saveBlockMap(){
 const d=mapDraft;if(!d||!P||P.id!==d.id)return;
 if(P.scenes.length&&!confirm('Saving the map resets storyboard approvals. Rebuild to apply the changes. Continue?'))return;
 const form=$('#map-row-form'),button=form.querySelector('button[type=submit]'),error=$('#map-save-error');button.disabled=true;error.hidden=true;
 try{
  const data=new FormData();data.append('kind','srt_block_map');data.append('expected_srt_signature',d.signature);
  data.append('file',new Blob([JSON.stringify(mapSaveRows(d.rows))],{type:'application/json'}),'authored-block-map.json');
  const result=await api(`/api/projects/${d.id}/upload`,'POST',data);
  if(P?.id===d.id)P=result;
  mapDraft=null;dirty=false;$('#map-editor-dialog').close();renderProject();toast('Block map saved. Rebuild the storyboard to apply it.');
 }catch(e){error.textContent=e.message;error.hidden=false;button.disabled=false}
}

let understandingTarget='',understandingTimer=null;
function understandingSources(){return [...(P?.voiceover?[['narration','Narration · '+P.voiceover.name]]:[]),...(P?.assets||[]).filter(a=>a.kind==='video').map(a=>[a.id,'Video · '+a.name])];}
async function openUnderstanding(){
 if(!P||!leave())return;
 understandingTarget=understandingSources()[0]?.[0]||'';
 $('#understanding-dialog').showModal();await refreshUnderstanding();
}
function closeUnderstanding(){clearTimeout(understandingTimer);$('#understanding-dialog').close()}
$('#understanding-dialog').addEventListener('close',()=>clearTimeout(understandingTimer));
async function refreshUnderstanding(){
 clearTimeout(understandingTimer);if(!P||!understandingTarget)return;
 const id=P.id,target=understandingTarget,body=$('#understanding-body');
 try{
  const [cap,result]=await Promise.all([api('/api/local-understanding/capabilities'),api(`/api/projects/${id}/understanding/${target}`)]);
  if(!$('#understanding-dialog').open||!P||id!==P.id||target!==understandingTarget)return;
  const stages=result.stages||{};
  const stage=(key,label,render)=>{const x=stages[key];if(!x)return '';return `<section class="card"><h3>${E(label)} · ${E(x.status)}</h3>${x.detail?`<p class="small">${E(x.detail)}</p>`:''}${x.result&&['complete','partial','running'].includes(x.status)?render(x.result):''}</section>`};
  body.innerHTML=`<p class="small">Local processing only. AI descriptions and timestamps are estimates, not verification. Analysis does not edit your captions or storyboard unless you explicitly apply the narration transcript.</p>
   <label>Media <select id="understanding-source" onchange="understandingTarget=this.value;refreshUnderstanding()">${understandingSources().map(([k,v])=>`<option value="${E(k)}" ${k===target?'selected':''}>${E(v)}</option>`).join('')}</select></label>
   <p class="small">Whisper: ${cap.whisper_model_ready&&cap.faster_whisper?'local model available':'local model or package missing'} · PySceneDetect: ${cap.scenedetect?'installed':'missing'} · Librosa: ${cap.librosa?'installed':'missing'} · vision: ${E(cap.vision_model)} on local Ollama (availability checked when run) · sound events: ${cap.sound_classifier?'local model configured':'optional model not configured'} · diarization: ${cap.speaker_diarization?'local pipeline configured':'optional pipeline not configured'}.</p>
   <p>Status: <strong>${E(result.status)}</strong> ${result.detail?E(result.detail):''}</p>${result.timing_warning?`<p class="small">${E(result.timing_warning)}</p>`:''}<button type="button" onclick="runUnderstanding()" ${['queued','running'].includes(result.status)?'disabled':''}>${result.status==='not_started'?'Analyse locally':'Run analysis again'}</button>
   ${result.status!=='not_started'?`<a class="button" href="/api/projects/${E(id)}/understanding/${E(target)}" target="_blank" rel="noopener">Open JSON report</a>`:''}
   ${cap.sound_classifier&&['complete','partial'].includes(result.status)?'<button type="button" onclick="runSoundEvents()">Analyse sound events only</button>':''}
   ${target!=='narration'&&['complete','partial'].includes(result.status)&&stages.scene_changes?.status==='complete'?'<button type="button" onclick="runVisualOnly()">Analyse video frames only</button>':''}
   ${stage('transcript','Transcript',r=>`<p class="small">Language: ${E(r.language)} · Review transcription before use; downloading does not change the project SRT.</p>${r.segments?.length?`<a class="button" href="/api/projects/${E(id)}/understanding/${E(target)}/transcript.srt" download>Download ${stages.transcript.status==='complete'?'':'partial '}SRT</a>`:''}${target==='narration'&&stages.transcript.status==='complete'&&r.segments?.length?'<button type="button" onclick="applyNarrationTranscript()">Use as project SRT</button>':''}${(r.segments||[]).map(x=>`<p><strong>${Number(x.start_s).toFixed(2)}–${Number(x.end_s).toFixed(2)}s</strong> ${x.speaker?`<span>${E(x.speaker)} · </span>`:''}${E(x.text)}</p>`).join('')||'<p>No speech detected.</p>'}`)}
   ${stage('scene_changes','Scene changes',r=>`<p>${r.length} detected spans</p><p class="small">${r.map(x=>`${Number(x.start_s).toFixed(2)}–${Number(x.end_s).toFixed(2)}s`).join(' · ')}</p>`)}
   ${stage('visual','Visual observations',r=>`<p class="small">${E(r.note)} ${r.sampled_frames} frames across ${r.total_scenes} detected scenes.</p>${r.observations.map(x=>`<p><strong>Frame ${Number(x.frame_time_s).toFixed(2)}s</strong> ${E(x.description)}</p>`).join('')}`)}
   ${stage('audio_features','Audio signal',r=>`<p class="small">${E(r.note)}</p>${(r.low_energy_intervals||[]).length?`<p>Low-energy intervals: ${r.low_energy_intervals.map(x=>`${Number(x.start_s).toFixed(1)}–${Number(x.end_s).toFixed(1)}s`).join(' · ')}</p>`:''}${r.windows.map(x=>`<p>${Number(x.start_s).toFixed(1)}–${Number(x.end_s).toFixed(1)}s · RMS ${x.rms} · centroid ${x.spectral_centroid_hz} Hz${x.low_energy?' · low energy':''}</p>`).join('')}`)}
   ${stage('sound_events','Sound events & music',r=>`<p class="small">${E(r.note)}</p>${r.windows.map(x=>`<p><strong>${Number(x.start_s).toFixed(2)}–${Number(x.end_s).toFixed(2)}s</strong> ${x.predictions.map(p=>`${E(p.label)} (${Number(p.score).toFixed(2)})`).join(' · ')||(x.too_short?'Too short to classify':'No strong prediction')}</p>`).join('')}`)}
   ${stage('speakers','Speakers',r=>`<p class="small">${E(r.note)}</p>${r.turns.map(x=>`<p>${Number(x.start_s).toFixed(2)}–${Number(x.end_s).toFixed(2)}s · ${E(x.speaker)}</p>`).join('')}`)}`;
  if(['queued','running'].includes(result.status))understandingTimer=setTimeout(refreshUnderstanding,3000);
 }catch(e){body.textContent='Could not load local analysis: '+e.message}
}
async function runUnderstanding(){
 const id=P.id,target=understandingTarget;
 try{await api(`/api/projects/${id}/understanding/${target}`,'POST');await refreshUnderstanding()}catch(e){toast(e.message)}
}

async function applyNarrationTranscript(){
 if(!P||understandingTarget!=='narration'||uploading)return;
 const id=P.id, replacing=!!(P.srt||P.scenes.length);
 const warning=replacing
  ? 'This machine transcript is unverified. Applying it replaces the existing SRT and clears the storyboard and its approvals. A ready automatic build may start. Continue?'
  : 'This machine transcript is unverified. Use it as the project SRT? A ready automatic build may start. Review the text and timing before export.';
 if(!confirm(warning))return;
 uploading=true;
 try{
  const updated=await api(`/api/projects/${id}/understanding/narration/apply-srt`,'POST',{confirm_generated:true,confirm_replace:replacing});
  if(P?.id!==id)return;
  P=updated;clearTimeout(understandingTimer);$('#understanding-dialog').close();renderProject();await list();
  toast('Machine transcript applied as SRT. Review text and timing before export.');
 }catch(e){toast(e.message)}finally{uploading=false}
}

async function runSoundEvents(){
 if(!P||!understandingTarget)return;
 const id=P.id,target=understandingTarget;
 try{await api(`/api/projects/${id}/understanding/${target}/sound-events`,'POST');await refreshUnderstanding()}catch(e){toast(e.message)}
}

async function runVisualOnly(){
 if(!P||!understandingTarget||understandingTarget==='narration')return;
 const id=P.id,target=understandingTarget;
 try{await api(`/api/projects/${id}/understanding/${target}/visual`,'POST');await refreshUnderstanding()}catch(e){toast(e.message)}
}
