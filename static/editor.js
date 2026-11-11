/* The project system lives inside the established media/monitor/inspector/timeline shell. */
let sourceReady=false,sourceSeekToken=0,masterSeekToken=0,editorBuffering=false;
let pictureScaleSaving=false,exportScriptCaptions=false;
let timelineZoom=12,editorPlaying=false,editorMuted=false,editorVideoMuted=true,editorCC=false,editorMaster=false,masterUrl='',sourceScene='',clockBase=0,clockAt=0,editorContext='',activeSRT=-1;
const masterNode=$('#program-master'),imageNode=$('#program-image'),narrNode=$('#program-narration');
const standbyNode=document.createElement('video');standbyNode.id='program-source-next';standbyNode.muted=true;standbyNode.playsInline=true;standbyNode.preload='auto';standbyNode.disablePictureInPicture=true;standbyNode.setAttribute('disableremoteplayback','');imageNode.before(standbyNode);
let sourceNode=$('#program-source'),nextSourceNode=standbyNode,preparedScene='',preparedReady=false,prepareToken=0;
sourceNode.loop=false;nextSourceNode.loop=false;
const scenes=()=>P?P.scenes:[],assets=()=>P?P.assets:[];
const totalTime=()=>P?.edit_mode==='documentary'?(window.docTimelineData?.duration||0):(scenes().at(-1)?.frame_out/30||0);
const fmt=t=>{t=Math.max(0,t||0);const f=Math.floor(t*30+.00001),pad=n=>String(Math.floor(n)).padStart(2,'0');return `${pad(f/108000)}:${pad(f/1800%60)}:${pad(f/30%60)}:${pad(f%30)}`};
const frameAt=t=>Math.floor(t*30+.001);
let enhancePicture=true,docMusic={enabled:false,volume_percent:35,narration_level:'duck',file_present:false},docTrackVolume={audio:100,video:0,music:35,narration:100,clip:100},waveformKey='',waveformPeaks=null,waveformPending=null,slotScalePreview=null,selectedSlotIds=new Set(),selectionExplicitlyEmpty=false,marquee=null,suppressClipClick=false;
const editorTime=()=>editorMaster?(masterNode.currentTime||0):(P?.voiceover&&narrNode.getAttribute('src')?narrNode.currentTime:clockBase+(editorPlaying&&!editorBuffering?(performance.now()-clockAt)/1000:0));
const deletingProjects=new Set();
list=async function(){const d=await api('/api/projects');projects=d.projects;PROFILES=d.profiles||PROFILES;$('#project-list').innerHTML=projects.map(p=>`<div class="project-row ${P?.id===p.id?'active':''}"><button type="button" class="project-link" onclick="openProject('${p.id}')">${E(p.title)}<small>${p.scenes} scenes · ${p.edit_mode==='documentary'?'Documentary':'Script review'}</small></button><button type="button" class="project-delete" aria-label="Delete ${E(p.title)}" onclick="deleteProduction('${p.id}')" ${deletingProjects.has(p.id)?'disabled':''}>Delete</button></div>`).join('')||'<p class="muted">No productions yet.</p>';return d};
async function deleteProduction(id){
 const project=projects.find(p=>p.id===id);if(!project||deletingProjects.has(id))return;
 if(uploading){toast('Please wait for the import to finish before deleting a production.');return}
 if(!confirm(`Permanently delete “${project.title}”?\n\nThis deletes the production and all its uploaded files, scripts, previews, and exports stored in this project. This cannot be undone.`))return;
 const active=P?.id===id;
 if(active&&!leave())return;
 deletingProjects.add(id);const button=document.querySelector(`.project-delete[onclick="deleteProduction('${id}')"]`);if(button)button.disabled=true;
 if(active){pauseEditor();resetEditor()}
 try{
  await api('/api/projects/'+id,'DELETE',{confirm:true});
  if(P?.id===id){P=null;sceneIndex=0;dirty=false;history.replaceState(null,'',location.pathname+location.search);renderProject()}
  await list();toast('Production and its project files deleted.');
 }catch(e){if(active&&P?.id===id)renderProject();toast(e.message)}
 finally{deletingProjects.delete(id);if(button&&button.isConnected)button.disabled=false}
}
function openProjects(){if(!leave())return;pauseEditor();list().then(()=>$('#projects-dialog').showModal())}
showHome=openProjects;
function pauseManagementMedia(){document.querySelectorAll('#manage-dialog audio,#manage-dialog video').forEach(node=>node.pause())}
function closeTools(){pauseManagementMedia();for(const id of ['#projects-dialog','#manage-dialog'])if($(id).open)$(id).close()}
openProject=async function(id){if(!leave())return;pauseEditor();closeTools();P=await api('/api/projects/'+id);sceneIndex=P.script_moving_only?Math.max(0,P.scenes.findIndex(s=>P.assets.some(a=>a.id===s.asset_id&&a.kind==='video'))):0;tab='brief';history.replaceState(null,'','#/production/'+id);await list();resetEditor();renderProject();if(P.script_moving_only&&scenes()[sceneIndex]?.asset_id)seekEditor(scenes()[sceneIndex].frame_in/30,false)};
function resetEditor(){if(window.resetDocTimeline)window.resetDocTimeline();if(window.closeDocumentaryDraft)window.closeDocumentaryDraft();exportScriptCaptions=false;editorCC=false;$('#caption-toggle').textContent='CC Off';$('#caption-toggle').setAttribute('aria-pressed','false');selectedSlotIds=new Set();selectionExplicitlyEmpty=false;slotScalePreview=null;clearPreparedSource();editorBuffering=false;sourceSeekToken++;masterSeekToken++;sourceReady=false;sourceScene='';clockBase=0;clockAt=performance.now();masterUrl='';editorMaster=false;masterNode.pause();masterNode.removeAttribute('src');masterNode.load();sourceNode.pause();sourceNode.removeAttribute('src');sourceNode.load();narrNode.pause();narrNode.removeAttribute('src');narrNode.load();$('#timeline-scroll').scrollLeft=0}
$('#new-form').onsubmit=async e=>{e.preventDefault();const button=e.submitter||$('#new-form button[type=submit]');button.disabled=true;try{pauseEditor();P=await api('/api/projects','POST',Object.fromEntries(new FormData(e.target)));sceneIndex=0;$('#new-dialog').close();history.replaceState(null,'','#/production/'+P.id);resetEditor();await list();renderProject();manage('brief');toast('New production created in the same timeline editor.')}finally{button.disabled=false}};
function manage(which){if(!['brief','assets','delivery'].includes(which))which='brief';if(!P){toast('Create a production first.');return}if(!leave())return;pauseEditor();tab=which;renderManagement();if(!$('#manage-dialog').open)$('#manage-dialog').showModal()}
changeTab=manage;
function closeManagement(){if(!leave())return;pauseManagementMedia();$('#manage-dialog').close();renderProject()}
function renderManagement(){if(!P)return;ensureStrictStatus();if(P.edit_mode==='documentary'&&tab==='delivery'){documentaryDeliveryHTML();return}$('#crumb').textContent=P.title;$('#manage-title').textContent={brief:'Production script, SRT & narration',assets:'Media uploads',delivery:'Review & export'}[tab]||'Production setup';$('#content').innerHTML=({brief:briefHTML,assets:assetsHTML,delivery:deliveryHTML}[tab]||briefHTML)();if(P.edit_mode==='documentary'&&P.documentary_beat_review&&tab==='brief')loadDocumentaryReadiness(P.id,'doc-brief-readiness');
 // The music card reads its saved state after the panel is on screen, so opening
 // Setup never waits on an extra request.
 if(P.edit_mode==='documentary'&&tab==='brief'&&typeof loadDocMusicCard==='function')loadDocMusicCard().then(()=>{if(P?.edit_mode==='documentary'&&tab==='brief')$('#content').innerHTML=briefHTML()});if(P.render?.url&&!isCurrentRender(P.render))$('#content').insertAdjacentHTML('afterbegin','<div class="notice warn"><strong>Previous MP4, not current.</strong> It was made before the latest timeline or export changes. Resolve the checks below, then render again to include your changes.</div>')}
renderProject=function(){syncMonitorShape();syncAspectChip();renderEditor();if($('#manage-dialog').open)renderManagement();const briefBox=document.getElementById('doc-brief-readiness');if(briefBox&&P?.id&&P?.documentary_beat_review&&typeof loadDocumentaryReadiness==='function')loadDocumentaryReadiness(P.id,'doc-brief-readiness')};
const oldNewProduction=newProduction;newProduction=function(){pauseEditor();oldNewProduction()};
selectScene=i=>selectEditorScene(i);
const savedSplit=splitBeat,savedMerge=mergeBeat;
splitBeat=async function(){pauseEditor();await savedSplit();sourceScene='';renderProject()};mergeBeat=async function(){pauseEditor();await savedMerge();sourceScene='';renderProject()};
function tileLen(seconds){const t=Math.max(0,Math.round(seconds||0));return Math.floor(t/60)+':'+String(t%60).padStart(2,'0')}
let poolFilter='all';
function setPoolFilter(which){poolFilter=which;document.querySelectorAll('.pool-chip').forEach(b=>b.classList.toggle('is-on',b.dataset.poolFilter===which));renderEditor()}
/* One tile shape for every upload. Video and photographs show a poster frame;
   narration and captions get a plate in the same rhythm so the grid reads as a
   single list rather than two kinds of box. */
function mediaTileHTML(a){
  const poster=`/api/projects/${P?.id}/assets/${a.id}/poster`;
  const isPhoto=a.kind==='image';
  const badge=a.reserve?'RESERVE':(isPhoto?'PHOTO':'VIDEO');
  const len=!isPhoto&&a.duration?tileLen(a.duration):'';
  // `current` is scoped to renderEditor, so read the selected scene directly.
  const sel=(scenes()[sceneIndex]||{}).asset_id===a.id?'selected':'';
  return {kind:isPhoto?'photo':'video',html:`<button type="button" class="pool-tile ${sel} ${isPhoto?'is-photo':''}" onclick="inspectAsset('${a.id.replace(/'/g,'')}')" title="${E(a.name)}"><span class="tile-shot"><img src="${poster}" alt="" loading="lazy" onerror="this.closest('.tile-shot').classList.add('no-poster')"><em>${badge}</em>${len?`<b>${len}</b>`:''}<i class="ready-dot ${a.verified?'':'missing'}"></i></span><span class="tile-name">${E(a.name.replace(/\.[^.]+$/,''))}</span></button>`};
}
function poolAudioTile(vo){
  const bars=[38,62,84,48,72,96,58,80,44,68,92,54,76,60,88,46,70,94,56,82,50,74,66,90,52,78];
  return {kind:'audio',html:`<button type="button" class="pool-tile pool-audio" onclick="manage('brief')" title="${E(vo.name)}"><span class="tile-shot"><span class="wave" aria-hidden="true">${bars.map(h=>`<i style="height:${h}%"></i>`).join('')}</span><em>AUDIO</em><b>${tileLen(vo.duration)}</b></span><span class="tile-name">${E(vo.name.replace(/\.[^.]+$/,''))}</span></button>`};
}
function poolSrtTile(srt){
  return {kind:'srt',html:`<button type="button" class="pool-tile pool-srt" onclick="manage('brief')" title="${E(srt.name||'captions')}"><span class="tile-shot"><span class="srt-plate" aria-hidden="true">SRT</span><em>TIMING</em><b>${srt.blocks.length}</b></span><span class="tile-name">${E((srt.name||'captions').replace(/\.[^.]+$/,''))}</span></button>`};
}
function renderEditor(){
 const isDoc=P?.edit_mode==='documentary';if(window.ensureDocTimeline)window.ensureDocTimeline();if(window.refreshDocumentaryDraft)window.refreshDocumentaryDraft();document.body.classList.toggle('documentary-workspace',isDoc);$('#documentary-button').hidden=!isDoc;$('#documentary-timeline-label').hidden=!isDoc;$('#documentary-prepare-timeline').hidden=!isDoc;$('#doc-source-track').hidden=!isDoc;$('#doc-source-label').hidden=!isDoc;
 const ss=scenes(),current=ss[sceneIndex];selectedSlotIds=new Set([...selectedSlotIds].filter(id=>ss.some(s=>s.id===id)));$('#project-name').textContent=P?.title?(P.title+(isDoc?' · Documentary':'')):'Storyboard editor';document.title=(P?.title||'Storyroom')+' · Timeline editor';const cueGroups=isDoc&&P?.documentary_beat_review&&typeof docCueGroups==='function'?docCueGroups():null;$('#project-status').textContent=P?`${cueGroups?`${cueGroups.length} cue shots · ${ss.length} picture slots`:ss.length+' scenes'} · ${totalTime().toFixed(3)}s · ${P.scenes.filter(s=>s.approved).length} ${cueGroups?'slots approved':'approved'}${P.script_application?.issues.length?' · '+P.script_application.issues.length+' script gaps':''}`:'Same timeline workflow · create a production to begin';
 // Clicking a source opens it, so the pool carries that instruction on the list
 // itself. It used to be written through `.media-hint`, which matched the
 // footage-count line first and wiped that count on every render.
 const pool=$('#media-list');
 if(pool){
  const clickable=P?.script_moving_only||!scenes().length;
  pool.title=clickable?'Click a source to play the original file.':'';
 }
 const requirement=P?.footage_requirements;
 // "11 matched · 0 missing" is the normal state, so it earns no space. The line
 // only appears when it has something to act on: a missing file or a duplicate.
 const reqPool=$('#footage-requirements-pool');
 const reqClean=requirement?.status==='found'&&!requirement.missing&&!requirement.ambiguous;
 if(reqPool)reqPool.hidden=!!reqClean;
 reqPool.textContent=requirement?.status==='found'?`${requirement.total} script file${requirement.total===1?'':'s'} · ${requirement.matched} matched · ${requirement.missing} missing${requirement.ambiguous?' · '+requirement.ambiguous+' duplicate':''}`:P?.production_script?'No video filenames recognized in script':'Upload a production script to see the footage count.';
 const visibleAssets=assets().filter(a=>!(P?.picture_policy==='clips_only'&&a.kind==='image'&&a.derived_from));
 if($('#source-count'))$('#source-count').textContent=visibleAssets.length+(P?.voiceover?1:0)+(P?.srt?1:0);
 const want=(typeof poolFilter==='string'?poolFilter:'all');
 const tiles=[];
 visibleAssets.forEach((a,i)=>{tiles.push(mediaTileHTML(a,i))});
 if(P?.voiceover)tiles.push(poolAudioTile(P.voiceover));
 if(P?.srt)tiles.push(poolSrtTile(P.srt));
 // Filtering only hides tiles; the pool order never changes underneath.
 const shown=tiles.filter(t=>want==='all'||t.kind===want||(want==='audio'&&t.kind==='srt'));
 $('#media-list').innerHTML=shown.map(t=>t.html).join('');
 if($('#pool-empty'))$('#pool-empty').hidden=shown.length>0;
 if($('#footage-requirements-pool'))$('#footage-requirements-pool').hidden=shown.length===0;
 // A quiet footer keeps narration and SRT timing visible without spending a
 // whole card on each: the same facts, one line.
 if($('#pool-foot')){
  const bits=[];
  if(P?.voiceover)bits.push(`<strong>${tileLen(P.voiceover.duration)}</strong> narration`);
  if(P?.srt)bits.push(`<strong>${P.srt.blocks.length}</strong> SRT blocks`);
  if(visibleAssets.length)bits.push(`<strong>${visibleAssets.length}</strong> source${visibleAssets.length===1?'':'s'}`);
  $('#pool-foot').hidden=!bits.length;
  $('#pool-foot').innerHTML=bits.join('<span aria-hidden="true">·</span>');
 }
renderProgram();renderTimeline();renderInspector();renderChecks();renderExport();updateTrackMuteControls();updatePosition();
}
function missingVisualHTML(s){return s?.replacement_issue?`<strong>Replaced footage needs attention</strong><span>${E(s.replacement_issue)} No substitute was chosen.</span><button class="button secondary" onclick="goToNextVideo()">Go to next available video</button>`:s?.missing_video_out?`<strong>Still placement not shown in video review</strong><span>${E(s.plan_row?.shot_id||'This shot')} specifies a still or freeze. No screenshot, extracted frame or guessed video is being shown.</span><button class="button secondary" onclick="goToNextVideo()">Go to next available video</button>`:s?.plan_row?.strict_production?`<strong>Scripted video unavailable</strong><span>${E(s.plan_row.shot_id||'This shot')} requires ${E(s.plan_row.source_file||'a source file')} at ${E(mapClock(s.plan_row.source_in_ms))}–${E(mapClock(s.plan_row.source_out_ms))}. ${E((P?.script_application?.issues||[]).find(x=>x.startsWith('Beat '+s.plan_row.shot_id+':'))||'The file may be missing, too short, or overlap another shot.')} No substitute was chosen.</span><button class="button secondary" onclick="goToNextVideo()">Go to next available video</button>`:'<strong>No visual assigned to this scene</strong><span>Choose uploaded media in the shot inspector, or import case footage.</span><button class="button secondary" onclick="manage(\'assets\')">Import media</button>'}
function goToNextVideo(){const ss=scenes(),video=s=>assets().some(a=>a.id===s.asset_id&&a.kind==='video');let index=ss.findIndex((s,i)=>i>sceneIndex&&video(s));if(index<0)index=ss.findIndex(video);if(index>=0)selectEditorScene(index);else toast('No playable scripted video is available.') }
function isCurrentRender(r){const o=r?.options;if(!r?.current||!o)return false;if(P?.edit_mode==='documentary')return !!o.srt_subtitles===editorCC&&!!o.enhance_picture===enhancePicture;return !!o.script_captions===exportScriptCaptions&&!!o.srt_subtitles===editorCC&&!!o.enhance_picture===enhancePicture}
function renderProgram(){
 if(P?.edit_mode==='documentary'){docRenderProgram();return}
 const target=editorVideoMuted?(P?.render?.status==='done'&&isCurrentRender(P.render)?P.render.url:(P?.preview?.status==='done'?P.preview.url:'')):'';
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
 syncPictureScale();
 const assigned=scenes().length&&assets().some(a=>a.id===scenes()[sceneIndex]?.asset_id);$('#monitor-empty').hidden=!!scenes().length&&(editorMaster||assigned);if(scenes().length&&!assigned&&!editorMaster)$('#monitor-empty').innerHTML=missingVisualHTML(scenes()[sceneIndex]);else if(!scenes().length){if(P?.script_reading?.status==='read'){const r=P.script_reading;$('#monitor-empty').innerHTML=`<strong>Script read · storyboard not built</strong><span>${r.shots} shots found. Captions, stills and other script instructions need attention before an accurate edit can be assembled.</span><button class="button secondary" onclick="createScriptDraft()" ${buildDisabled()}>Build review timeline</button><button class="button secondary" onclick="manage('brief');showScriptReading()">Review the shot plan</button>`}else if(P&&assets().some(a=>a.kind==='video')){const count=assets().filter(a=>a.kind==='video').length;$('#monitor-empty').innerHTML=`<strong>${count} source video${count===1?'':'s'} uploaded</strong><span>${P.srt?'Your storyboard has not been built yet.':'Your SRT has not been imported into this project, so there is no timeline yet.'} You can play the uploaded videos now.</span><button class="button secondary" onclick="openFirstSourceFile()">Play an uploaded video</button><button class="button secondary" onclick="manage('brief')">Open setup</button>`}else {const missing=(P?.source_match_report||[]).filter(x=>!['matched','selected'].includes(x.status));$('#monitor-empty').innerHTML=missing.length?`<strong>Storyboard waiting for footage links</strong><span>Script filenames need matching uploads: ${missing.map(x=>E(x.source_file)).join(', ')}. Your imported files are still here.</span><button class="button secondary" onclick="manage('brief')">Resolve script filenames</button>`:P?.srt?'<strong>Storyboard not built yet</strong><span>Your SRT is imported. Build the storyboard in Setup to place picture beats on the MP4 track.</span><button class="button secondary" onclick="manage(\'brief\')">Open production setup</button>':'<strong>Keep the editor. Start a new story.</strong><span>Import narration, SRT and case media from Setup.</span><button class="button secondary" onclick="newProduction()">＋ New production</button>';}}$('#preview-mode').textContent=P?.script_moving_only?'REVIEW PLAYBACK · NOT FINAL':P?.review_only_draft?'REVIEW DRAFT · NOT FINAL':editorMaster?(target===P?.preview?.url?'SMOOTH PREVIEW':'CONTINUOUS MASTER'):(['queued','running'].includes(P?.preview?.status)?`PREPARING SMOOTH PREVIEW ${Math.floor(P.preview.progress||0)}%`:'SOURCE PREVIEW');
}
function effectivePictureScale(scene){return Number(scene?.picture_scale??P?.picture_scale??100)}
function selectedScenes(){return scenes().filter(s=>selectedSlotIds.has(s.id))}
function updatePictureScalePreview(){
 // In documentary mode the picture is a source <video> swapped per frame, so the
 // scale is shared with the timeline repaint (it would be wiped otherwise).
 if(P?.edit_mode==='documentary'&&typeof docApplyPictureScale==='function'){docApplyPictureScale();return}
 const scene=scenes()[sceneIndex],saved=effectivePictureScale(scene),desired=slotScalePreview?.ids.has(scene?.id)?slotScalePreview.value:saved;$('#picture-layer').style.transform=`scale(${editorMaster?desired/saved:desired/100})`}
function syncPictureScale(){updatePictureScalePreview()}
function previewSlotScale(value){const chosen=selectedScenes();if(!chosen.length)return;value=Math.max(100,Math.min(200,Math.round(Number(value)||100)));slotScalePreview={ids:new Set(chosen.map(s=>s.id)),value};for(const id of ['#slot-scale-range','#slot-scale-number']){const node=$(id);if(node)node.value=value}updatePictureScalePreview()}
async function saveSlotScale(value){const chosen=selectedScenes();if(!P||!chosen.length||pictureScaleSaving)return;if(!leave()){slotScalePreview=null;syncPictureScale();renderInspector();return}pauseEditor();const id=P.id,ids=chosen.map(s=>s.id);value=value===null?null:Math.max(100,Math.min(200,Math.round(Number(value)||100)));if(value!==null)previewSlotScale(value);pictureScaleSaving=true;for(const x of ['#slot-scale-range','#slot-scale-number'])if($(x))$(x).disabled=true;renderExport();try{const updated=await api(`/api/projects/${id}/scenes/picture-scale`,'PATCH',{scene_ids:ids,picture_scale:value});if(P?.id!==id)return;P=updated;slotScalePreview=null;toast(value===null?`${ids.length} selected slot(s) now use the project scale.`:`Scale saved for ${ids.length} selected slot(s). Render again to include it.`)}catch(e){slotScalePreview=null;toast(e.message)}finally{pictureScaleSaving=false;renderProject()}}
function clearPreparedSource(){
 prepareToken++;preparedScene='';preparedReady=false;nextSourceNode.pause();
 nextSourceNode.onloadedmetadata=nextSourceNode.onseeked=nextSourceNode.onloadeddata=nextSourceNode.onerror=null;
 nextSourceNode.removeAttribute('src');nextSourceNode.load();nextSourceNode.classList.remove('is-active');
}
function prepareNextSource(i,t){
 if(editorMaster||!editorPlaying||editorBuffering||!sourceReady)return;
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
 sourceReady=true;editorBuffering=false;clockAt=performance.now();sourceNode.playbackRate=1;sourceNode.muted=editorVideoMuted;nextSourceNode.muted=true;sourceNode.classList.add('is-active');imageNode.hidden=true;$('#monitor-empty').hidden=true;
 if(editorPlaying&&wasBuffering&&P?.voiceover){narrNode.muted=editorMuted;narrNode.play().catch(()=>pauseEditor())}
 if(editorPlaying)sourceNode.play().catch(()=>{if(sourceScene===s.id&&!editorMaster)seekSourceAtTimeline(s,a,editorTime())});
 return true;
}
function seekSourceAtTimeline(s,a,t){
 // Seek a second video element while the previous decoded picture stays visible.
 // Nothing is captured, saved, or added to the edit.
 const token=++sourceSeekToken;sourceReady=false;sourceNode.pause();sourceNode.muted=editorVideoMuted;sourceNode.playbackRate=1;
 prepareToken++;preparedScene='';preparedReady=false;const reuseActive=sourceNode.classList.contains('is-active')&&sourceNode.getAttribute('src')===a.url;const node=reuseActive?sourceNode:nextSourceNode;node.pause();node.muted=reuseActive?editorVideoMuted:true;node.playbackRate=1;
 editorBuffering=true;clockBase=t;clockAt=performance.now();narrNode.pause();
 const valid=()=>token===sourceSeekToken&&sourceScene===s.id&&!editorMaster&&node===(reuseActive?sourceNode:nextSourceNode);
 const desired=()=>Number(s.in_s)+Math.max(0,t-s.frame_in/30);
 const failed=()=>{if(token!==sourceSeekToken||sourceScene!==s.id||editorMaster)return;sourceReady=false;pauseEditor();$('#monitor-empty').hidden=false;$('#monitor-empty').innerHTML='<strong>Media preview unavailable</strong><span>Playback paused with the narration; no empty slot was played.</span>'};
 let readyDone=false;
 const seek=()=>{if(!valid()||node.readyState<1)return;const target=desired();if(!Number.isFinite(target)||target<0||target>node.duration+.034){failed();return}try{node.currentTime=target;if(!node.seeking&&node.readyState>=2)ready()}catch{failed()}};
 const ready=()=>{if(readyDone||!valid()||node.seeking||node.readyState<2)return;if(Math.abs(node.currentTime-desired())>.15){seek();return}readyDone=true;
  if(!reuseActive){const old=sourceNode;sourceNode=node;nextSourceNode=old;old.classList.remove('is-active');old.onloadedmetadata=old.onseeked=old.onloadeddata=old.onerror=null;}
  sourceNode.onloadedmetadata=sourceNode.onseeked=sourceNode.onloadeddata=sourceNode.onerror=null;sourceNode.muted=editorVideoMuted;sourceNode.classList.add('is-active');imageNode.hidden=true;
  sourceReady=true;editorBuffering=false;clockAt=performance.now();$('#monitor-empty').hidden=true;if(editorPlaying){const jobs=[sourceNode.play()];if(P?.voiceover){narrNode.muted=editorMuted;jobs.push(narrNode.play())}Promise.all(jobs).catch(()=>{if(token===sourceSeekToken&&editorPlaying&&!editorMaster)failed()})}};
 node.onloadedmetadata=seek;node.onseeked=ready;node.onloadeddata=ready;node.onerror=failed;
 if(node.getAttribute('src')!==a.url){node.src=a.url;node.load()}else if(node.readyState>=1)seek();
}

// If the active video actually starves, hold narration until decoding resumes.
for(const node of [sourceNode,nextSourceNode]){
 node.addEventListener('waiting',()=>{
  // Documentary review runs on an audio clock; a starving video must not pause it.
  if(P?.edit_mode==='documentary')return;
  if(node!==sourceNode||editorMaster||!editorPlaying||!sourceReady||editorBuffering)return;
  const now=editorTime();editorBuffering=true;clockBase=now;clockAt=performance.now();narrNode.pause();
 });
 node.addEventListener('playing',()=>{
  if(P?.edit_mode==='documentary')return;
  if(node!==sourceNode||editorMaster||!editorPlaying||!sourceReady||!editorBuffering)return;
  editorBuffering=false;clockAt=performance.now();
  if(P?.voiceover){narrNode.muted=editorMuted;narrNode.play().catch(()=>pauseEditor())}
 });
}

function showSource(i,t){
 if(editorMaster)return;const s=scenes()[i],a=assets().find(a=>a.id===s?.asset_id);if(!s||!a||s.missing_video_out){sourceSeekToken++;sourceReady=false;sourceNode.pause();sourceNode.classList.remove('is-active');if(sourceNode.getAttribute('src')){sourceNode.removeAttribute('src');sourceNode.load()}imageNode.hidden=true;sourceScene='';$('#blur-backdrop').getContext('2d').clearRect(0,0,960,540);if(s){if(editorPlaying)pauseEditor();$('#monitor-empty').hidden=false;$('#monitor-empty').innerHTML=missingVisualHTML(s)}return}
 if(sourceScene!==s.id){
  $('#monitor-empty').hidden=true;sourceScene=s.id;
  if(a.kind==='image'){sourceNode.pause();sourceSeekToken++;sourceReady=false;editorBuffering=false;clockAt=performance.now();imageNode.style.transform='none';sourceNode.onloadedmetadata=sourceNode.onseeked=sourceNode.onloadeddata=sourceNode.onerror=null;imageNode.src=a.url;imageNode.hidden=false;sourceNode.classList.remove('is-active')}
  else if(editorPlaying&&sourceReady&&!sourceNode.seeking&&sourceNode.getAttribute('src')===a.url&&Math.abs(sourceNode.currentTime-(Number(s.in_s)+Math.max(0,t-s.frame_in/30)))<.12){ /* Continuous interval in the same source: keep decoding without a seek. */ }
  else if(!usePreparedSource(s,a,t)){seekSourceAtTimeline(s,a,t)}
 }
 if(a.kind==='image'){if(s.plan_row?.uploaded_photo)imageNode.style.transform='none';else applyImageMotion(s,Math.max(0,t-s.frame_in/30));}
 else if(editorPlaying&&sourceReady&&!sourceNode.seeking){
  // Small browser clock differences are normal. Correct them gradually rather than
  // repeatedly pausing narration and seeking the picture on every animation frame.
  const drift=sourceNode.currentTime-(Number(s.in_s)+Math.max(0,t-s.frame_in/30));
  sourceNode.playbackRate=s.plan_row?.strict_production?1:(Math.abs(drift)<.06?1:Math.max(.85,Math.min(1.15,1-drift*.12)));
 }
}

function applyImageMotion(s,elapsed){const m=s.motion_plan;if(!m)return;const p=Math.max(0,Math.min(1,elapsed/m.duration_s)),q=m.easing==='linear'?p:m.easing==='quick_settle'?(p<.08?p*p/.1472:p>.92?1-(1-p)*(1-p)/.1472:(p-.04)/.92):m.easing==='sine'?.5-.5*Math.cos(Math.PI*p):m.easing==='smootherstep'?p*p*p*(p*(p*6-15)+10):p*p*(3-2*p);const z=m.zoom_start+(m.zoom_end-m.zoom_start)*q,x=m.x_start+(m.x_end-m.x_start)*q,y=m.y_start+(m.y_end-m.y_start)*q;imageNode.style.transform=`translate(${(.5-x)*(z-1)*100}%,${(.5-y)*(z-1)*100}%) scale(${z})`}
let lastBackdropDraw=0;
function drawBackdrop(){const now=performance.now();if(now-lastBackdropDraw<100)return;lastBackdropDraw=now;if(editorMaster||(imageNode.hidden&&!sourceReady))return;const node=imageNode.hidden?sourceNode:imageNode,w=node.videoWidth||node.naturalWidth,h=node.videoHeight||node.naturalHeight;if(!w||!h)return;const c=$('#blur-backdrop'),scale=Math.max(c.width/w,c.height/h);try{c.getContext('2d').drawImage(node,(c.width-w*scale)/2,(c.height-h*scale)/2,w*scale,h*scale)}catch{}}
// Correct known UTF-8 decoding artifacts for display only; never rewrite uploaded text.
function captionDisplayText(text){return String(text||'').replaceAll('â€¢','•').replaceAll('â€™','’')}
let captionMeasureKey='';const captionMeasureCanvas=document.createElement('canvas');
function fitSrtCaption(cue){
 const overlay=$('#editor-captions'),textNode=$('#editor-caption-text');
 const text=String(cue?.text||'').replace(/\s*\n\s*/g,' ').trim();
 const width=overlay.clientWidth,key=text+'|'+width;if(key===captionMeasureKey)return;
 captionMeasureKey=key;textNode.textContent=text;
 if(!text||!width)return;
 const ctx=captionMeasureCanvas.getContext('2d'),fontSize=Math.max(13,Math.min(22,width*.034));
 ctx.font=`650 ${fontSize}px Inter, ui-sans-serif, system-ui, sans-serif`;
 const measured=ctx.measureText(text).width+fontSize;const scale=Math.min(1,(width-4)/Math.max(1,measured));
 textNode.style.fontSize=(fontSize*scale).toFixed(2)+'px';
}
function updatePosition(){if(P?.edit_mode==='documentary'){docUpdatePosition();return}updatePictureScalePreview();const t=editorTime();const draftRow=P?.review_only_draft?scenes()[sceneIndex]?.plan_row:null;
 const currentScene=scenes()[sceneIndex],actualAsset=assets().find(a=>a.id===currentScene?.asset_id);
 const readout=$('#source-clock');if(readout){const current=scenes()[sceneIndex],asset=assets().find(a=>a.id===current?.asset_id);readout.textContent=asset?.kind==='video'?(sourceReady?'Actual player source: '+sourceNode.currentTime.toFixed(3)+'s':'Seeking to the saved source interval…'):'Still candidate; image identity not automatically verified.'}$('#timecode-current').textContent=fmt(t);$('#timecode-total').textContent=fmt(totalTime());$('#playhead').style.left=t*timelineZoom+'px';const handle=$('#playhead-handle');handle.setAttribute('aria-valuemax',totalTime().toFixed(3));handle.setAttribute('aria-valuenow',t.toFixed(3));handle.setAttribute('aria-valuetext',fmt(t));const blocks=P?.srt?.blocks||[],index=blocks.findIndex(b=>t>=b.start_ms/1000&&t<b.end_ms/1000),cue=(P?.caption_phrases||[]).find(b=>t>=b.start_ms/1000&&t<b.end_ms/1000),overlay=$('#editor-captions');overlay.classList.toggle('captions-off',!editorCC||!cue||(editorMaster&&P?.render?.url===masterUrl&&P?.render?.options?.srt_subtitles));if(index!==activeSRT){document.querySelector('.caption-block.active')?.classList.remove('active');document.querySelector(`.caption-block[data-caption-index="${index}"]`)?.classList.add('active');activeSRT=index}fitSrtCaption(cue);if(cue){const fade=Math.min(.12,(cue.end_ms-cue.start_ms)/4000);overlay.style.opacity=fade?String(Math.max(0,Math.min(1,(t-cue.start_ms/1000)/fade,(cue.end_ms/1000-t)/fade))):'1'}}
function renderTimeline(){
 if(P?.edit_mode==='documentary'){docRenderTimeline();return}
 activeSRT=-1;const ss=scenes(),duration=Math.max(totalTime(),Number(P?.voiceover?.duration)||0,(P?.srt?.blocks||[]).at(-1)?.end_ms/1000||0),width=Math.max(duration*timelineZoom,$('#timeline-scroll').clientWidth-2,600);$('#timeline-content').style.width=width+'px';$('#track-legend').textContent='';
 const step=timelineZoom<5?60:30;let ticks='';for(let t=0;t<=duration+step;t+=step)ticks+=`<span class="ruler-tick" style="left:${t*timelineZoom}px">${Math.floor(t/60).toString().padStart(2,'0')}:${String(t%60).padStart(2,'0')}</span>`;$('#ruler-track').innerHTML=ticks;
 $('#video-track').innerHTML=ss.map((s,i)=>{const a=assets().find(a=>a.id===s.asset_id),thumb=a&&P?`/api/projects/${P.id}/scenes/${s.id}/thumbnail?v=${encodeURIComponent(a.revision||a.id)}&in=${encodeURIComponent(s.in_s||0)}`:'';return `<div class="shot-clip ${selectedSlotIds.has(s.id)?'selected':''} ${a?.kind==='image'?'image-clip':''}" data-scene="${i}" role="button" tabindex="0" title="${E(s.narration)}" style="left:${s.frame_in/30*timelineZoom}px;width:${Math.max(3,(s.frame_out-s.frame_in)/30*timelineZoom-1)}px" onclick="selectEditorScene(${i},event)" onkeydown="if(event.key==='Enter'||event.key===' '){event.preventDefault();selectEditorScene(${i},event)}">${thumb?`<img class="clip-thumb" src="${thumb}" loading="lazy" alt="" onerror="this.hidden=true">`:''}<div class="clip-text"><strong>${E((s.plan_row?.shot_id||('SRT '+s.srt_blocks.join('–'))))} · ${E(a?.original_name||a?.name||s.plan_row?.source_file||'MEDIA NEEDED')}</strong><span>${((s.frame_out-s.frame_in)/30).toFixed(1)}s · ${E(s.purpose||s.focus)}</span></div>${s.transition==='dissolve'?'<i class="transition-mark"></i>':''}</div>`}).join('');
 const blocks=P?.srt?.blocks||[];
 $('#caption-track').innerHTML=blocks.map((b,i)=>`<div class="caption-block" data-caption-index="${i}" title="${E(b.text)}" style="left:${b.start_ms/1000*timelineZoom}px;width:${Math.max(2,(b.end_ms-b.start_ms)/1000*timelineZoom-1)}px" onclick="seekEditor(${b.start_ms/1000},true)">${E(b.text)}</div>`).join('')+'<span class="caption-note" style="left:8px">SRT REFERENCE · PHRASE TIMING ESTIMATED · ORIGINAL SRT UNCHANGED</span>';
 const voice=P?.voiceover, audioDuration=Number(voice?.duration)||0;
 $('#audio-track').innerHTML=voice&&audioDuration>0?`<div class="audio-clip" role="button" tabindex="0" title="${E(voice.name||'Narration')} · ${audioDuration.toFixed(3)} seconds. Click to seek." style="width:${Math.max(1,audioDuration*timelineZoom)}px" onclick="seekAudioTrack(event)" onkeydown="if(event.key==='Enter'||event.key===' '){event.preventDefault();seekEditor(0,true)}"><canvas class="audio-waveform" aria-hidden="true"></canvas><div class="audio-label"><strong>♫ ${E(voice.name||'Narration')}</strong><span>${audioDuration.toFixed(3)}s · original narration</span></div></div>`:'<span class="audio-empty">No narration audio uploaded</span>';
 if(voice&&audioDuration>0)loadNarrationWaveform(voice);
}
function drawNarrationWaveform(){
 if(P?.edit_mode==='documentary'){docDrawNarrationWaveforms();return}
 const canvas=$('#audio-track .audio-waveform');if(!canvas||!waveformPeaks)return;
 const width=Math.max(1,Math.min(8192,Math.ceil(canvas.clientWidth))),height=40;
 canvas.width=width;canvas.height=height;const ctx=canvas.getContext('2d');ctx.clearRect(0,0,width,height);ctx.fillStyle='rgba(180, 255, 227, .65)';
 for(let x=0;x<width;x++){const start=Math.floor(x*waveformPeaks.length/width),end=Math.max(start+1,Math.floor((x+1)*waveformPeaks.length/width));let peak=0;for(let i=start;i<end&&i<waveformPeaks.length;i++)peak=Math.max(peak,waveformPeaks[i]);if(peak>0){const h=Math.max(1,peak*(height-4));ctx.fillRect(x,(height-h)/2,1,h)}}
}
async function loadNarrationWaveform(voice){
 const key=voice.url;if(!key)return;if(waveformKey===key&&waveformPeaks){drawNarrationWaveform();return}if(waveformKey===key&&waveformPending)return;
 waveformKey=key;waveformPeaks=null;const task=(async()=>{let context;try{
  const response=await fetch(key,{cache:'no-store'});if(!response.ok)throw Error('Narration could not be loaded');const bytes=await response.arrayBuffer();
  context=new (window.AudioContext||window.webkitAudioContext)();const audio=await context.decodeAudioData(bytes);const channels=Array.from({length:audio.numberOfChannels},(_,i)=>audio.getChannelData(i));
  const bins=Math.min(50000,Math.max(1,Math.ceil(audio.duration*100))),peaks=new Float32Array(bins);
  for(let b=0;b<bins;b++){const start=Math.floor(b*audio.length/bins),end=Math.floor((b+1)*audio.length/bins);let peak=0;for(let i=start;i<end;i++)for(const channel of channels)peak=Math.max(peak,Math.abs(channel[i]));peaks[b]=peak}
  if(waveformKey===key){waveformPeaks=peaks;drawNarrationWaveform()}
 }catch(err){if(waveformKey===key){const canvas=$('#audio-track .audio-waveform');if(canvas)canvas.title='Waveform unavailable: '+err.message}}finally{if(context)context.close().catch(()=>{});if(waveformKey===key)waveformPending=null}})();waveformPending=task;
}
function seekAudioTrack(event){const rect=$('#audio-track').getBoundingClientRect();seekEditor((event.clientX-rect.left)/timelineZoom,true)}

function renderInspector(){const s=scenes()[sceneIndex];
 if(P?.edit_mode==='documentary'&&editorPlaying){if(s){const lbl=s.plan_row?.shot_id||'SRT '+s.srt_blocks.join('\u2013');$('#shot-number').textContent=lbl}return}$('#shot-number').textContent=s?((s.plan_row?.shot_id||'SRT '+s.srt_blocks.join('–'))):'—';
 if(!s){const missing=(P?.source_match_report||[]).filter(x=>!['matched','selected'].includes(x.status));$('#inspector-content').innerHTML=missing.length?`<div class="inspector-group"><label>Waiting for footage links</label><p>The script refers to ${missing.map(x=>E(x.source_file)).join(', ')}. Select the correct existing videos or upload the missing files.</p><button type="button" onclick="manage('brief')">Resolve filenames</button></div>`:P?.srt?(P?.edit_mode==='documentary'&&P?.script_reading?.status==='not_detailed'?'<div class="inspector-group"><label>No picture beats yet</label><p>The SRT is imported and the Beat script was read. Build the picture from the reviewed Beat ranges; Setup cannot build this script automatically.</p><button type="button" onclick="openBeatProposal()">Review the Beat picture plan</button></div>':'<div class="inspector-group"><label>No picture beats yet</label><button type="button" onclick="manage(\'brief\')">Open setup</button></div>'):'<div class="inspector-group"><label>Ready for a new production</label></div>';return}
 const a=assets().find(a=>a.id===s.asset_id),m=s.motion_plan||{};
 const chosen=selectedScenes(),scaleTarget=chosen.find(x=>x.id===s.id)||chosen[0],scaleValue=scaleTarget?effectivePictureScale(scaleTarget):100,scaleMixed=new Set(chosen.map(effectivePictureScale)).size>1;
 const flow=s.picture_flow,cut=s.cut_sync;
 const flowLabel={already_inside_one_shot:'inside one shot',continuous_with_previous:'runs on from previous beat',continued_running_take:'continued take',slid_inside_a_shot:'conformed inside a shot',slid_far_inside_a_shot:'moved far — check it',repeats_recent_footage:'repeats recent footage',jump_cut_within_shot:'deliberate jump cut',no_single_shot_fits:'no shot fits this beat',not_decoded:'source not decoded'};
 const cutLabel={in_a_breath:'cut in a breath',on_a_block_boundary:'cut on a line break',over_speech:'cut over a spoken word',between_blocks:'cut between blocks'};
 const meta=`<div class="mini-meta"><span>SRT ${s.srt_blocks.join('–')}</span><span>${((s.frame_out-s.frame_in)/30).toFixed(3)}s</span></div><div class="mini-meta"><span>Timeline</span><span>${(s.frame_in/30).toFixed(3)} → ${(s.frame_out/30).toFixed(3)}s</span></div>${flow?`<div class="mini-meta"><span>Picture flow</span><span title="${E(flow.note||'')}">${E(flowLabel[flow.status]||flow.status)}${flow.shot?` · shot ${flow.shot[0]}–${flow.shot[1]}s`:''}</span></div>`:''}${cut?`<div class="mini-meta"><span>Cut to next</span><span>${E(cutLabel[cut.quality]||cut.quality)}${cut.moved_from_s!=null?` · moved from ${cut.moved_from_s}s`:''}</span></div>`:''}`;
 $('#inspector-content').innerHTML=`<div class="slot-scale-control"><label for="slot-scale-range">Scale · ${chosen.length?chosen.length+' selected'+(scaleMixed?' · mixed values':''): 'no slots selected'}</label><div class="slot-scale-row"><input id="slot-scale-range" type="range" min="100" max="200" step="1" value="${scaleValue}" ${chosen.length?'':'disabled'} oninput="previewSlotScale(this.value)" onchange="saveSlotScale(this.value)"><input id="slot-scale-number" type="number" min="100" max="200" step="1" aria-label="Slot picture scale percent" value="${scaleValue}" ${chosen.length?'':'disabled'} oninput="previewSlotScale(this.value)" onchange="saveSlotScale(this.value)"><span>%</span></div><button type="button" onclick="saveSlotScale(null)" ${!chosen.some(x=>x.picture_scale!=null)?'disabled':''}>Use project scale (${Number(P.picture_scale)||100}%)</button></div>${docVolumeHTML()}<form id="scene-form" onsubmit="saveEditorScene(event)"><div class="inspector-group"><label>Narration / picture beat</label><p class="narration-excerpt">${E(s.narration)}</p></div>${s.plan_row?.native_static_interval?'<div class="notice warn"><strong>Native static interval · review only</strong><p>Playing the original video, without a freeze or extracted frame. Count its full duration toward the still allowance; eligibility, identity and rights must be checked.</p></div>':''}${meta}${sourceTimingHTML(s,a)}<label>Emotion<input name="emotion" value="${E(s.emotion)}"></label><label>Primary focus<input name="focus" value="${E(s.focus)}"></label><label>Named person<input name="person" value="${E(s.person)}"></label><label>Selected visual<select name="asset_id">${options([['','Choose uploaded media'],...P.assets.filter(a=>!P.script_moving_only||a.kind==='video'||(s.plan_row?.uploaded_photo&&a.kind==='image'&&!a.derived_from)).map(a=>[a.id,(a.reserve?'[reserve '+a.source_label+'] ':'')+(a.verified?'✓ ':'? ')+a.name])],s.asset_id||'')}</select></label><label>${s.plan_row?.uploaded_photo?'Photo source time (not applicable)':'Video in-point (seconds)'}<input name="in_s" type="number" step=".001" min="0" value="${s.in_s}">${flow&&flow.declared_in_s!=null&&Math.abs(flow.declared_in_s-flow.conformed_in_s)>=.001?`<span class="small">Declared ${flow.declared_in_s}s · conformed to ${flow.conformed_in_s}s so the beat stays inside one continuous shot. ${E(flow.note||'')}</span>`:''}</label><label>Exit transition<select name="transition">${options(P.transition_policy==='clean_cuts_only'?[['cut','Clean cut · transitions disabled']]:[['cut','Clean action / phrase cut'],['dissolve','Short dissolve · handles required']],s.transition)}</select></label><label class="check-label"><input type="checkbox" name="context_only" ${s.context_only?'checked':''}>Disclosed wide context, not the exact person/exhibit</label><label>Editorial / exception notes<textarea name="notes" rows="3">${E(s.notes)}</textarea></label><div class="edit-actions"><button type="button" onclick="rebuildHere()">Rebuild this block</button></div><label class="check-label"><input type="checkbox" name="approved" ${s.approved?'checked':''}>Editorially approved</label><button class="button primary" type="submit">Save scene</button></form><div id="scene-understanding"></div><div id="plan-tools"></div>`;
 if(a?.kind==='video')loadSceneUnderstanding(s.id);
 $('#plan-tools').innerHTML=planToolsHTML();candidateHTMLForScene(s).then(h=>{const n=$('#plan-tools');if(n)n.insertAdjacentHTML('beforeend',h)});
 if(s.automatic_match&&!P.manual_scene_edits){const form=$('#scene-form');for(const name of ['person','context_only','approved']){const field=form.querySelector('[name="'+name+'"]');if(field)field.closest('label').hidden=true}form.insertAdjacentHTML('afterbegin','<div class="inspector-group"><label>Automatic clips-only assembly</label><p>Source interval checked and linked to the script/SRT. No manual portrait selection or scene approval is required for this prepared timeline.</p></div>')}
}
function sourceTimingHTML(s,a){if(!a)return '';if(a.kind==='image'&&!a.derived_from)return `<div class="inspector-group"><label>Uploaded photograph · static hold</label><p>${E(a.original_name||a.name)}</p><p>Timeline duration: ${((s.frame_out-s.frame_in)/30).toFixed(3)}s. No video source IN/OUT. Counts toward still allowance; identity and rights require review.</p><p id="source-clock" class="small"></p></div>`;if(s.automatic_match)return `<div class="inspector-group"><label>Analyzed MP4 / SRT linkage</label><p>${E(a.original_name||a.name)} · SRT ${E(s.automatic_match.srt_block)}</p><p>Saved IN: ${Number(s.in_s).toFixed(3)}s → ${(Number(s.in_s)+(s.frame_out-s.frame_in)/30).toFixed(3)}s</p><p>Source-bank match: ${E(s.automatic_match.bank_id)}. Provisional CSV times were checked against actual footage.</p><p id="source-clock" class="small"></p></div>`;const derived=a.derived_from,planned=s.plan_row;const actual=derived?derived.requested_time_ms/1000:Number(s.in_s);
 // The script's IN/OUT is only worth a line when it disagrees with what is saved.
 // On a conformed beat the two are identical, so printing both just invited the
 // reader to hunt for a difference that is not there.
 const plannedIn=planned?(planned.extracted_frame_ms??planned.source_in_ms)/1000:null;
 const plannedOut=planned?.source_out_ms!=null?planned.source_out_ms/1000:null;
 const moved=plannedIn!=null&&(Math.abs(plannedIn-actual)>0.0005||(plannedOut!=null&&Math.abs(plannedOut-(actual+(s.frame_out-s.frame_in)/30))>0.0005));
 return `<div class="inspector-group"><label>Source cut</label><p>${E(derived?.source_file||a.original_name||a.name)}</p><p>${derived?'Extracted frame':'Saved IN'}: ${actual.toFixed(3)}s${a.kind==='video'?' → '+(actual+(s.frame_out-s.frame_in)/30).toFixed(3)+'s':''}</p>${moved?`<p>Script asked for: ${plannedIn.toFixed(3)}s${plannedOut!=null?' → '+plannedOut.toFixed(3)+'s (exclusive)':''}</p>`:''}${planned?'':'<p>Source timing selected in the editor.</p>'}<p id="source-clock" class="small"></p></div>`}


async function loadSceneUnderstanding(sceneId){
 const id=P.id;
 try{
  const r=await api(`/api/projects/${id}/scenes/${sceneId}/understanding`);
  const node=$('#scene-understanding');if(!node||P?.id!==id||scenes()[sceneIndex]?.id!==sceneId)return;
  if(!['partial','complete'].includes(r.status)){
   node.innerHTML=`<div class="inspector-group"><label>Source understanding</label><p class="small">${E(r.status==='not_started'?'No local analysis yet. Open Setup, then Case footage & photos, and choose Local analysis.':r.status)}</p></div>`;return;
  }
  const entry=(time,text)=>({time:Number(time),html:`<p class="small">${Number(time).toFixed(2)}s · ${text}</p>`});
  const rows=[...r.transcript.map(x=>entry(x.timeline_start_s,`Dialogue: ${E(x.text)}`)),
   ...r.visual.map(x=>entry(x.timeline_time_s,`Frame: ${E(x.description)}`)),
   ...r.sound_events.filter(x=>x.predictions.length).map(x=>entry(x.timeline_start_s,`Sounds: ${x.predictions.map(p=>E(p.label)).join(', ')}`)),
   ...(r.speakers||[]).map(x=>entry(x.timeline_start_s,`Speaker: ${E(x.speaker)}`)),
   ...(r.low_energy_intervals||[]).map(x=>entry(x.timeline_start_s,'Low-energy audio (estimate)')),
   ...(r.scene_changes||[]).filter(x=>x.start_s>=r.source_in_s).map(x=>entry(x.timeline_start_s,'Detected source scene start'))].sort((a,b)=>a.time-b.time);
  node.innerHTML=`<div class="inspector-group"><label>Source understanding · timeline times</label>${r.stage_status?.visual==='unavailable'?'<p class="small">Visual analysis unavailable for this source.</p>':''}${r.timing_warning?`<p class="small">${E(r.timing_warning)}</p>`:''}${rows.slice(0,12).map(x=>x.html).join('')||'<p class="small">No observations overlap this beat.</p>'}${rows.length>12?'<p class="small">More observations are available in the full report.</p>':''}</div>`;
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


// The shot inspector edits one shot, so it no longer carries a block of
// project-wide planning links. Those live in Setup now, next to the material
// they inspect. Kept as a function because the inspector still calls it.
// Three levels, in the order they sit in the mix: narration, then the audio
// carried by the footage, then the bed. They are saved with the project and
// applied on the next export, so a change never re-renders behind your back.
function docVolumeHTML(){
  if(!P || P.edit_mode!=='documentary' || !P.documentary_beat_review) return '';
  const v = docTrackVolume;
  // Labels only. Each fader says what it controls and nothing else.
  const mix = (label, key, value, max) => `<div class="doc-volume-row"><label for="doc-vol-${key}">${label}</label><input id="doc-vol-${key}" type="range" min="0" max="${max}" step="1" value="${Math.round(value)}" aria-label="${label} level percent" oninput="previewDocVolume('${key}',this.value)" onchange="saveDocVolume('${key}',this.value)"><input id="doc-vol-${key}-n" type="number" min="0" max="${max}" step="1" value="${Math.round(value)}" aria-label="${label} level percent" oninput="previewDocVolume('${key}',this.value)" onchange="saveDocVolume('${key}',this.value)"><span>%</span></div>`;
  const musicOff = !(docMusic && docMusic.file_present);
  return `<div class="doc-volume-control"><label>Audio</label>
  ${mix('Narration', 'narration', v.narration, 150)}
  ${mix('Clip audio', 'clip', v.clip, 150)}
  ${mix('Music', 'music', v.music, 100)}</div>`;
}

// The review preview is one already-mixed file: narration and the selected sound
// bites are level-matched and concatenated before the browser ever sees them, so
// there is no per-track node to turn down. Narration and clip levels therefore
// share that stream, and the quieter of the two is what can honestly be applied
// to it: raising one alone would lift the other with it. Music has its own layer
// and is unaffected. Nothing here touches the exported mix until the project is
// saved and rendered again.
function previewDocVolume(key, raw){
  const value = Math.max(0, Math.min(150, Number(raw) || 0));
  if(key==='music') docTrackVolume.music = value;
  else docTrackVolume[key] = value;
  const sync = document.querySelectorAll(`#doc-vol-${key}, #doc-vol-${key}-n`);
  sync.forEach(node => { if(Number(node.value)!==Math.round(value)) node.value = Math.round(value); });
  window.docApplyPreviewVolumes?.();
}

async function saveDocVolume(key, raw){
  if(!P) return;
  const value = Math.max(0, key==='music' ? Math.min(100, Number(raw) || 0) : Math.min(150, Number(raw) || 0));
  previewDocVolume(key, value);
  // Send only the fader that moved. Sending all three overwrote the music level
  // with the last value that happened to be in memory, which is how moving the
  // narration fader could quietly reset the bed.
  const keyName = {narration:'narration_percent', clip:'clip_percent', music:'volume_percent'}[key];
  const body = {[keyName]: key==='music' ? Math.max(1, Math.round(value)) : Math.round(value)};
  try{
    P = await api(`/api/projects/${P.id}/documentary-audio-levels`, 'PATCH', body);
    toast(`Saved. ${key==='music'?'Music':'Narration and clip'} level is applied on the next export.`);
  }catch(e){ toast(e.message); }
}

function planToolsHTML(){ return ''; }

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
  // A documentary cut is driven by the script's own IN/OUT against the source the
  // beat names, so offering "other shots of this length" could only invite replacing
  // scripted footage. Nothing here is actionable, so the panel stays clean.
  if(P.edit_mode==='documentary'&&P.documentary_beat_review) return '';
  const length = (s.frame_out - s.frame_in) / 30;
  const handle = s.transition === 'dissolve' ? (s.transition_handle_frames || 0) / 30 : 0;
  try{
    const d = await api(`/api/projects/${P.id}/candidates?length_s=${length.toFixed(6)}&handle_s=${handle.toFixed(6)}&limit=6`);
    if(!d.candidates.length){
      if(!planCache.index) await loadPlanning(P.id);
      const decoded = (planCache.index?.sources || []).some(x => !x.not_decoded && x.shots.length);
      return `<div class="inspector-group"><label>Shots that fit this beat</label><p class="small">${decoded
        ? `No decoded shot is exactly ${(length+handle).toFixed(3)}s long. Nothing is offered rather than something approximate; the build reports this as a gap.`
        : 'The footage has not been scanned for shots yet. Build the storyboard once; this list is exact after that.'}</p></div>`;
    }
    return `<div class="inspector-group"><label>Shots that fit this beat · ${(length+handle).toFixed(3)}s</label>${d.candidates.map(c=>`<p class="small"><strong>${E(c.file)}</strong> · <code>${c.in_clock} → ${c.out_clock}</code> · ${c.duration_s}s · ${c.slack_s}s spare</p>`).join('')}</div>`;
  }catch(e){ return `<div class="inspector-group"><label>Shots that fit this beat</label><p class="small">${E(e.message)}</p></div>`; }
}


// ---- Bulk approval and frame QC ----------------------------------------------
async function approveReadyScenes(){
  if(!P || !leave()) return;
  const unapproved = P.scenes.filter(s => !s.approved).length;
  if(!unapproved){ toast('Every picture beat is already approved.'); return; }
  if(!confirm(`Approve every picture beat that passes the same checks export readiness uses? ${unapproved} beat(s) are not approved. A beat with any blocking item is left unapproved and listed with its reason. This does not inspect or verify picture content or identity. You are attesting that you reviewed the pictures; this is not rights clearance.`)) return;
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
let qcRequest = 0;
function qcPlayRange(id,start,end){
  const media=document.getElementById(id);if(!media)return;
  const play=()=>{media.currentTime=start;media.ontimeupdate=()=>{if(media.currentTime>=end){media.pause();media.ontimeupdate=null}};media.play().catch(()=>toast('Press play to start the selected interval.'))};
  if(media.readyState>=1)play();else media.addEventListener('loadedmetadata',play,{once:true});
}
async function openFrameQC(shot){
  if(!P)return;
  const n=Math.max(1,Math.min(Number(shot)||1,scenes().length));if(!scenes().length){toast('Build the storyboard first.');return}
  const request=++qcRequest, projectId=P.id;
  $('#plan-title').textContent='Shot-by-shot frame QC';
  $('#plan-body').innerHTML='<p class="small">Loading shot '+n+' and decoding its source frames…</p>';
  if(!$('#plan-dialog').open)$('#plan-dialog').showModal();
  try{
    const d=await api(`/api/projects/${projectId}/frame-qc?format=json&per_sheet=1&sheet=${n}`);
    if(request!==qcRequest||P?.id!==projectId)return;
    qcCache={id:projectId,data:d};
    const scene=scenes()[n-1], row=scene.plan_row||{}, asset=assets().find(a=>a.id===scene.asset_id), beat=d.beats[0];
    const blocks=[...new Set(row.srt_blocks||scene.srt_blocks||[])], srt=(P.srt?.blocks||[]).filter(b=>blocks.includes(b.number)).map(b=>b.text).join('\n');
    const start=Number(scene.in_s)||0,end=row.strict_production&&Number.isFinite(Number(row.source_out_ms))?Number(row.source_out_ms)/1000:start+(scene.frame_out-scene.frame_in)/30;
    const timeIn=scene.frame_in/30,timeOut=scene.frame_out/30;
    const note=row.continuity??row.match_reason??'';
    const caption=row.plain_cue&&!Object.prototype.hasOwnProperty.call(row,'continuity')?'Not specified separately in this older script':(row.required_caption||'None');
    $('#plan-body').innerHTML=`<div class="qc-review">
      <div class="qc-navigation"><button type="button" onclick="openFrameQC(${n-1})" ${n<=1?'disabled':''}>← Previous shot</button><label>Shot <select aria-label="Choose shot" onchange="openFrameQC(this.value)">${scenes().map((s,i)=>`<option value="${i+1}" ${i+1===n?'selected':''}>${i+1} · ${E(s.plan_row?.shot_id||'Scene '+(i+1))}</option>`).join('')}</select> of ${scenes().length}</label><button type="button" onclick="openFrameQC(${n+1})" ${n>=scenes().length?'disabled':''}>Next shot →</button></div>
      <h3>Shot ${n} · ${E(row.shot_id||'Scene '+n)}</h3>
      <p><strong>Original SRT narration:</strong> ${E(srt||scene.narration||'Not available')}</p>
      <p><strong>Script narration:</strong> ${E(row.script_narration||'Not specified')}</p>
      <p><strong>Visual direction:</strong> ${E(row.visible_content||'Not specified')}</p>
      <p><strong>Continuity note:</strong> ${E(note||'None')}</p><p><strong>On-screen caption:</strong> ${E(caption)}</p>
      <p><strong>Assigned source:</strong> ${E(asset?.original_name||asset?.name||'Unassigned')} · <strong>Script source:</strong> ${E(row.source_file||'Not specified')}<br><strong>Source interval:</strong> ${E(mapClock(start*1000))} → ${E(mapClock(end*1000))} (OUT exclusive) · <strong>Timeline:</strong> ${E(mapClock(timeIn*1000))} → ${E(mapClock(timeOut*1000))}</p>
      <h4>Boundary frames</h4><p>From left: 2 seconds before IN, IN, midpoint, last frame before OUT, and 2 seconds after OUT. Unavailable frames are marked. Review only: these extracted frames are never placed in the edit or exported as picture assets.</p>
      <div class="qc-image-scroll"><img src="${d.url}" alt="Five source inspection frames for shot ${n}"></div><p><a href="${d.url}" target="_blank" rel="noopener">Open full-size QC image</a></p>
      ${beat.samples.some(x=>!x.decoded)?'<p class="notice warn">One or more inspection frames are unavailable; a boundary may be outside the source or the frame could not be decoded.</p>':''}
      <h4>Play the full shot</h4>${asset?.kind==='video'?`<video id="qc-video" controls muted preload="metadata" src="${E(asset.url)}"></video><p><button type="button" onclick="qcPlayRange('qc-video',${start},${end})">Play selected source interval</button> Source audio is muted; use the controls to inspect nearby context.</p>`:asset?.kind==='image'?`<img class="qc-photo" src="${E(asset.url)}" alt="Assigned original photograph"><p>Original uploaded photograph. Verify who or what it shows.</p>`:'<p>No source is assigned to this shot.</p>'}
      ${P.voiceover?.url?`<h4>Listen to narration for this shot</h4><audio id="qc-audio" controls preload="metadata" src="${E(P.voiceover.url)}"></audio><p><button type="button" onclick="qcPlayRange('qc-audio',${timeIn},${timeOut})">Play shot narration</button></p>`:''}
      <div class="notice"><strong>Review checklist</strong><p>1. Inspect the boundary frames for unwanted cuts. 2. Play the whole interval against the narration and visual direction. 3. Independently confirm identities, source and date. 4. Correct unsuitable sources or ranges in the shot inspector. 5. Record any reuse decision separately.</p></div>
      <div class="qc-navigation"><button type="button" onclick="openFrameQC(${n-1})" ${n<=1?'disabled':''}>← Previous shot</button><button type="button" onclick="openFrameQC(${n+1})" ${n>=scenes().length?'disabled':''}>Next shot →</button></div>
    </div>`;
    $('#plan-body').scrollTop=0;
  }catch(e){if(request===qcRequest)$('#plan-body').innerHTML=`<div class="notice">${E(e.message)}</div>`;}
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
// Documentary exports use a separate, guarded readiness check, not review-draft validation.
let documentaryCheck={key:null,state:'idle',result:null,error:null};
function ensureDocumentaryCheck(){
 const key=P?`${P.id}:${P.updated}`:null;if(!key||documentaryCheck.key===key)return documentaryCheck;
 const id=P.id,version=P.updated,state={key,state:'checking',result:null,error:null};documentaryCheck=state;
 async function poll(){try{const result=await api(`/api/projects/${id}/documentary-export-readiness`);if(documentaryCheck!==state||P?.id!==id||P?.updated!==version)return;state.result=result;state.state=result.status==='checking'?'checking':result.status==='ready'?'ready':'blocked';renderChecks();if(state.state==='checking')setTimeout(poll,1500);else setTimeout(()=>{if(documentaryCheck===state&&P?.id===id&&P?.updated===version){state.state='checking';state.result=null;renderChecks();poll()}},15000)}catch(e){if(documentaryCheck!==state||P?.id!==id||P?.updated!==version)return;state.state='error';state.error=e.message;renderChecks()}}
 poll();return state;
}
function retryDocumentaryCheck(){documentaryCheck.key=null;renderChecks()}
function renderDocumentaryChecks(){
 const state=ensureDocumentaryCheck(),errors=state.result?.errors||[],groups=typeof docCueGroups==='function'?docCueGroups():null;
 // Export-stage items are grouped so the count is meaningful: approvals and reuse
 // decisions are expected now, while timing or source problems are real blockers.
 const pending=errors.filter(x=>/editorial approval is pending|record explicit reuse-rights approval/i.test(x));
 const other=errors.filter(x=>!pending.includes(x));
 const summary=state.state==='checking'?'Checking documentary…':state.state==='error'?'Check unavailable':other.length?`${other.length} item${other.length===1?'':'s'} to review`:pending.length?`${pending.length} approval${pending.length===1?'':'s'} left`:state.state==='ready'?'Checks passed':'Export blocked';
 $('#validation-summary').textContent=summary;$('#validation-summary').style.color=state.state==='ready'?'var(--green)':'var(--amber)';
 $('#validation-list').innerHTML=`<div class="validation-item">${groups?`${groups.length} cue shots · `:''}${scenes().length} picture slots · original narration and SRT unchanged.</div>${other.slice(0,3).map(x=>`<div class="validation-item warning">△ ${E(x)}</div>`).join('')}${other.length>3?`<div class="validation-item">${other.length-3} more items under Review / export checks.</div>`:''}${pending.length?`<div class="validation-item">${pending.length} scene approval${pending.length===1?'':'s'} and reuse-rights decisions remain before export; the timeline is ready to review.</div>`:''}${state.state==='error'?`<div class="validation-item warning">△ ${E(state.error)} <button type="button" onclick="retryDocumentaryCheck()">Retry</button></div>`:''}${state.state==='ready'?'<div class="validation-item ok">✓ Source, timing and approval checks passed. Content, identity and rights still require your review.</div>':''}`;
}
function renderChecks(){if(P?.edit_mode==='documentary'&&P?.documentary_beat_review){renderDocumentaryChecks();return}const ss=scenes(),errors=P?.validation?.errors||[],unresolved=P?.script_application?.issues||[];$('#validation-summary').textContent=!P?'No production selected':errors.length?`${errors.length} item${errors.length===1?'':'s'} to review`:(P?.picture_policy==='clips_only'?'Ready · automatic checks passed':'Ready for review export');$('#validation-summary').style.color=errors.length?'var(--amber)':'var(--green)';$('#validation-list').innerHTML=P?`<div class="validation-item ok">✓ ${ss.length} picture beats · original narration / SRT timing preserved.</div>${unresolved.length?`<div class="validation-item warning">△ ${unresolved.length} unassigned script shots. Select a gap to see its required source and range.</div>`:''}${errors.slice(0,3).map(x=>`<div class="validation-item warning">△ ${E(x)}</div>`).join('')}${errors.length>3?`<div class="validation-item">${errors.length-3} more items under Review / export checks.</div>`:''}${!editorMaster&&ss.length?(P?.review_only_draft?'<div class="validation-item">Review-only timeline. Rendering remains blocked until outstanding checks are resolved.</div>':'<div class="validation-item">A continuous master may be rendered when export checks pass.</div>'):''}`:'<div class="validation-item">Create a new production, then import the voiceover, SRT and source MP4s.</div>'}
function renderExport(){
 const r=P?.render||{},doc=P?.edit_mode==='documentary',beat=doc&&!!P?.documentary_beat_review,busy=['running','queued'].includes(r.status);
 const dialog=$('#render-dialog');
 if(dialog?.open)renderRenderPage();
 const button=$('#render-button');
 if(button){button.disabled=!P||busy||pictureScaleSaving||(doc&&!beat)||!scenes().length;button.title=doc?(beat?'Open the export page and render MP4':'Documentary export requires a Beat storyboard'):'Open the export page and render a clean MP4'}
}
function openRenderPage(){
 if(!P)return;pauseEditor();
 const dialog=$('#render-dialog');renderRenderPage();if(!dialog.open)dialog.showModal();
}
function closeRenderPage(){const dialog=$('#render-dialog');if(dialog?.open)dialog.close()}
function renderRenderPage(){
 const box=$('#render-page-body');if(!box)return;
 const r=P?.render||{},doc=P?.edit_mode==='documentary',busy=['running','queued'].includes(r.status);
 const ready=!busy&&(doc?!!P?.documentary_beat_review:!!scenes().length)&&!pictureScaleSaving;
 const stage=r.output&&!isCurrentRender(r)&&!busy?'New edits — render again':r.stage||'Not rendered yet';
 const detail=r.error?'The last export needs attention. Details are below.':r.detail||(doc?'Narration and sound bites are mixed; the fade is omitted.':'Clean MP4 with the combined tracks.');
 const actions=doc
  ?`<button type="button" class="button primary" ${ready?'':'disabled'} onclick="renderFromEditor()">Render documentary MP4</button>`
  :`<button type="button" class="button primary" ${ready?'':'disabled'} onclick="renderFromEditor()">Render review MP4</button><button type="button" class="button" ${!P.validation?.can_export||busy?'disabled':''} onclick="startExport(true)">Render final MP4</button>`;
 const previous=r.url&&!isCurrentRender(r)?`<div class="notice warn"><strong>Previous MP4 — not current</strong><p>This file does not include your latest edits. Render again to include them.</p><video controls class="reference-video" src="${r.url}"></video><a class="download" href="${r.url}?download=1">↓ Download previous MP4</a></div>`:'';
 const current=r.url&&isCurrentRender(r)?`<div class="notice"><strong>Current MP4</strong><video controls class="reference-video" src="${r.url}"></video><a class="download" href="${r.url}?download=1">↓ Download current MP4</a> <a class="download" href="${r.url.replace(/\.mp4$/,'.srt')}?download=1">↓ Separate SRT</a></div>`:'';
 box.innerHTML=`
 <section class="card"><h3>Picture</h3><label class="check-label"><input id="enhance-picture" type="checkbox" ${enhancePicture?'checked':''} onchange="setEnhancePicture(this.checked)"> Enhance all videos &amp; photos on export</label></section>
 <section class="card"><h3>Captions &amp; subtitles</h3>${doc?`<label class="check-label"><input id="burn-on-screen-captions" type="checkbox" ${P?.burn_captions!==false?'checked':''} onchange="setBurnCaptions(this.checked)"> Burn on-screen script captions and spoken sound-bite words</label><label class="check-label"><input id="doc-fade-enabled" type="checkbox" ${(P?.documentary_fades?.enabled!==false)?'checked':''} onchange="setDocumentaryFades({enabled:this.checked})"> Include the script's Cut Action fade in the export</label><p class="small"> and in the export.</p><label class="check-label"><input id="burn-srt-subtitles" type="checkbox" ${editorCC?'checked':''} onchange="setEditorCaptions(this.checked)"> Burn adjusted SRT subtitles (matches CC preview)</label><p class="small">The original uploaded SRT is never changed.</p>`:`<label class="check-label"><input type="checkbox" id="burn-script-captions" ${exportScriptCaptions?'checked':''} onchange="setScriptCaptionExport(this.checked)"> Burn on-screen script captions</label><label class="check-label"><input type="checkbox" id="burn-srt-subtitles" ${editorCC?'checked':''} onchange="setEditorCaptions(this.checked)"> Burn SRT subtitles (matches CC preview)</label>`}</section>
 <section class="card"><h3>Audio</h3>${docMusic.file_present?`<p class="small"><button class="button secondary" type="button" onclick="docPickMusic()">Replace the music file</button> <button class="button secondary" type="button" onclick="docToggleMusic()">${docMusic.enabled?'Switch the music off':'Switch the music on'}</button>${docMusic.enabled?'':''}</p>`:'<p class="small"><button class="button secondary" type="button" onclick="docPickMusic()">Add background music or SFX</button></p>'}</section>
 <section class="card"><h3>Export status</h3><p><strong>${E(stage)}</strong></p><p class="small">${E(detail)}</p><progress max="100" value="${Number(r.progress)||0}"></progress><p class="small">${Math.round(Number(r.progress)||0)}%</p>${r.status==='error'?`<div class="notice error">Export could not finish. The production is intact.</div><details><summary>Technical details</summary><pre style="white-space:pre-wrap;word-break:break-word;font-size:10px">${E(r.error)}</pre></details>${doc?'':'<button type="button" onclick="documentaryRetry()">Retry</button>'}`:''}${busy?'<button type="button" onclick="cancelExport()">Cancel export</button>':''}${current}${previous}</section>
 <section class="card"><h3>Checks &amp; files</h3><p><button type="button" class="text-button" onclick="closeRenderPage();manage('delivery')">Open review / export checks</button></p>${doc?'<p><a class="download" href="/api/projects/'+P.id+'/coverage-summary">↓ Download coverage summary</a></p>':''}</section>
 <div class="actionbar">${actions}</div>`;
}
function documentaryRetry(){if(!P)return;toast('Open Review / export checks to retry the export.')}
function setEnhancePicture(checked){enhancePicture=!!checked;renderExport();renderProgram()}

async function renderFromEditor(){
 if(!P)return;
 if(P.edit_mode==='documentary'){
  if(!$('#render-dialog')?.open){openRenderPage();return}
  if(!P.documentary_beat_review){toast('Final export currently supports script-driven Beat storyboards only.');return}
  if(dirty){toast('Save or discard scene edits before rendering.');return}
  const id=P.id,version=P.updated;let status=ensureStrictStatus();toast('Checking documentary cue shots and source frames…');
  try{const checked=status.report||await status.promise;if(P?.id!==id||P?.updated!==version){toast('The project changed. Check the current timeline again.');return}if(!checked||checked.errors.length){await openStrictCutCheck(null);return}
   let splits=[];if(editorCC){const saved=window.documentaryExportSplits;const crossing=(window.docTimelineData?.bites||[]).some(b=>(P.srt?.blocks||[]).some(c=>c.start_ms<b.pause_s*1000&&b.pause_s*1000<c.end_ms));if(crossing&&(!saved||saved.id!==id||saved.revision!==window.docTimelineData?.revision)){await openDocumentary();toast('Choose and confirm the subtitle split in Documentary tools before rendering with SRT.');return}splits=saved?.id===id&&saved.revision===window.docTimelineData?.revision?saved.splits:[]}
   const fades=P?.documentary_fades||{},useFade=fades.enabled!==false&&((Number(fades.in_s)||0)>0||(Number(fades.out_s)||0)>0);
   if(!confirm(`Render the documentary MP4? ${useFade?`The scripted fade will be included (${Number(fades.in_s)||0}s in${(Number(fades.out_s)||0)>0?`, ${Number(fades.out_s)}s out`:''})`: 'No fade will be applied'}. Source identity and rights attestations still require your review.`))return;
   pauseEditor();const response=await api(`/api/projects/${id}/render`,'POST',{final:true,strict_checked_version:version,omit_fade:!useFade,fade:useFade,enhance_picture:enhancePicture,srt_subtitles:editorCC,subtitle_splits:splits});if(P?.id!==id)return;P.render=response.render;renderProject();toast('Documentary export started. Please wait for it to finish.');
  }catch(e){toast(e.message);manage('delivery')}return
 }
 if(P.validation?.errors?.length){manage('delivery');await openStrictCutCheck(null);return}await startExport(false)
}
function inspectAsset(id){if(!leave())return;if((P?.script_moving_only||!scenes().length)&&assets().find(a=>a.id===id)?.kind==='video'){openSourceFile(id);return}const found=scenes().findIndex(s=>s.asset_id===id);if(found>=0){selectEditorScene(found);toast('Selected the first timeline scene using this asset.')}else manage('brief')}
function openFirstSourceFile(){const asset=assets().find(a=>a.kind==='video');if(asset)openSourceFile(asset.id);else toast('No source video is available.') }
function openSourceFile(id){
 const asset=assets().find(a=>a.id===id);if(!asset||asset.kind!=='video'){toast('This source video is unavailable.');return}
 pauseEditor();const dialog=$('#source-file-dialog'),video=$('#source-file-video');$('#source-file-title').textContent=asset.original_name||asset.name;video.pause();video.loop=false;video.muted=true;video.playbackRate=1;video.src=asset.url;video.load();if(!dialog.open)dialog.showModal();
}
function closeSourceFile(){const dialog=$('#source-file-dialog'),video=$('#source-file-video');video.pause();video.removeAttribute('src');video.load();if(dialog.open)dialog.close()}
$('#source-file-dialog').addEventListener('cancel',event=>{event.preventDefault();closeSourceFile()});
function selectEditorScene(i,event){if(P?.edit_mode==='documentary'){docSelectScene(i,event);return}if(!scenes().length)return;if(suppressClipClick){suppressClipClick=false;return}if(!leave())return;pauseEditor();i=Math.max(0,Math.min(scenes().length-1,i));const id=scenes()[i].id;if(event&&(event.ctrlKey||event.metaKey)){if(selectedSlotIds.has(id))selectedSlotIds.delete(id);else selectedSlotIds.add(id)}else selectedSlotIds=new Set([id]);selectionExplicitlyEmpty=!selectedSlotIds.size;slotScalePreview=null;sceneIndex=i;sourceScene='';seekEditor(scenes()[sceneIndex].frame_in/30,false);renderInspector();markSelected();syncPictureScale()}
function markSelected(){if(typeof updateSelectAllButton==='function')updateSelectAllButton();document.querySelectorAll('.shot-clip').forEach(n=>{const active=selectedSlotIds.has(scenes()[Number(n.dataset.scene)]?.id);n.classList.toggle('selected',active);n.setAttribute('aria-pressed',String(active))});}
function videoSlotIds(){const video=new Set(assets().filter(a=>a.kind==='video').map(a=>a.id));return new Set(scenes().filter(s=>video.has(s.asset_id)).map(s=>s.id))}
function selectAllVideoSlots(){if(!P||!leave())return;pauseEditor();const all=videoSlotIds(),already=all.size>0&&[...all].every(id=>selectedSlotIds.has(id));selectedSlotIds=already?new Set():all;selectionExplicitlyEmpty=!selectedSlotIds.size;slotScalePreview=null;markSelected();renderInspector();syncPictureScale();updateSelectAllButton();toast(already?'Selection cleared.':`${selectedSlotIds.size} MP4 slot(s) selected.`)}
function updateSelectAllButton(){const b=$('#select-all-video');if(!b)return;const all=videoSlotIds(),all_on=all.size>0&&[...all].every(id=>selectedSlotIds.has(id));b.textContent=all_on?'Deselect all MP4':'Select all MP4';b.setAttribute('aria-pressed',String(all_on));b.title=all_on?'Clear the current selection':'Select every slot that uses MP4 footage'}
function seekEditor(t,select=true){if(P?.edit_mode==='documentary'){docSeekTimeline(t,select);return}if(!scenes().length)return;t=Math.max(0,Math.min(totalTime()-.001,t));if(select&&!leave())return;const previousScene=sceneIndex,was=editorPlaying;pauseEditor();clockBase=t;clockAt=performance.now();if(editorMaster){masterSeekToken++;try{masterNode.currentTime=t}catch{}}else if(P?.voiceover){try{narrNode.currentTime=t}catch{}}if(select){const i=scenes().findIndex(s=>frameAt(t)>=s.frame_in&&frameAt(t)<s.frame_out);if(i>=0)sceneIndex=i}sourceScene='';if(!editorMaster)showSource(sceneIndex,t);if(sceneIndex!==previousScene){renderInspector();markSelected()}updatePosition();if(was)playEditor()}
// The transport shows the playhead in one colour and the programme length in
// another, like a deck: current / total. Both come from the same clock.
function setTimecode(current,total){const a=$('#timecode-current'),b=$('#timecode-total');if(a)a.textContent=current;if(b)b.textContent=total}
// Monitor chrome. Full fit / aspect / fullscreen are view preferences only: they
// never touch the project, a render, or the stored picture scale.
let monitorFit='contain',monitorAspect='16:9';
function toggleMonitorFullscreen(){const box=$('#monitor');if(!box)return;try{if(document.fullscreenElement)document.exitFullscreen();else box.requestFullscreen()}catch(e){toast('This browser did not allow fullscreen for the monitor.')}}
document.addEventListener('fullscreenchange',()=>{const m=$('#monitor');if(m)m.classList.toggle('is-fullscreen',!!document.fullscreenElement)});
// The monitor is the project's frame, not a fixed 16:9 box. Portrait and 4:5 films
// get a tall frame and crop-to-fill, the same framing the renderer uses, so what is
// reviewed is what is exported. View preference only: never touches a stored edit.
const MONITOR_SHAPES={landscape:'16:9',portrait:'9:16',square:'1:1',vertical_4_5:'4:5',cinema:'239:100'};
// The shape switch lives beside the monitor, where you can see what it does. It
// writes the same project setting as Setup's Aspect ratio control, so the two can
// never disagree. Only an explicit click changes a production.
// Only the two shapes this workflow actually delivers: a 16:9 film for YouTube and
// a 9:16 reel for TikTok/Reels. Square and 4:5 were never used and only made the
// choice longer. The engine still knows those shapes for existing projects; they are
// simply no longer offered here.
const ASPECT_ORDER=['landscape','portrait'];
const ASPECT_LABEL={landscape:'16:9',portrait:'9:16',square:'1:1',vertical_4_5:'4:5',cinema:'2.39:1'};
const ASPECT_NAME={landscape:'Landscape',portrait:'Portrait'};
function syncAspectChip(){const chip=$('#aspect-toggle');if(!chip)return;const shape=MONITOR_SHAPES[P?.format]||'16:9';chip.textContent=ASPECT_LABEL[P?.format]||shape;chip.hidden=!P;chip.title='Frame shape · '+shape+'. Click to choose.'}
// A menu, not a cycle: clicking used to jump to the next shape, so reading the
// current one and choosing one were the same action and an accidental click moved
// the production. The menu lists every shape and marks the current one.
function aspectMenuOpen(){const m=$('#aspect-menu');return !!m&&!m.hidden}
function closeAspectMenu(){const m=$('#aspect-menu'),b=$('#aspect-toggle');if(m)m.hidden=true;if(b)b.setAttribute('aria-expanded','false')}
function buildAspectMenu(){const m=$('#aspect-menu');if(!m)return;
 m.innerHTML=ASPECT_ORDER.map(k=>`<button type="button" role="option" aria-selected="${P?.format===k}" data-format="${k}" class="${P?.format===k?'current':''}" onclick="chooseAspect('${k}')"><span class="aspect-size">${ASPECT_LABEL[k]}</span><span class="aspect-name">${ASPECT_NAME[k]}</span><span class="aspect-tick">${P?.format===k?'✓':''}</span></button>`).join('')}
// Positioned against the button, then flipped and clamped so it always lands on
// screen even though the transport itself clips its overflow.
function placeAspectMenu(){const m=$('#aspect-menu'),b=$('#aspect-toggle');if(!m||!b)return;
 const r=b.getBoundingClientRect();m.hidden=false;
 const w=m.offsetWidth||180,h=m.offsetHeight||150;
 let left=r.right-w;if(left<8)left=8;
 if(left+w>window.innerWidth-8)left=window.innerWidth-w-8;
 let top=r.top-h-8;if(top<8)top=r.bottom+8;
 m.style.left=Math.round(left)+'px';m.style.top=Math.round(top)+'px'}
function toggleAspectMenu(event){if(event&&event.stopPropagation)event.stopPropagation();if(!P)return;
 if(aspectMenuOpen()){closeAspectMenu();return}buildAspectMenu();placeAspectMenu();$('#aspect-toggle').setAttribute('aria-expanded','true')}
async function chooseAspect(format){closeAspectMenu();if(!P||P.format===format)return;if(typeof setFormat==='function')await setFormat(format);syncAspectChip()}
document.addEventListener('click',e=>{if(!aspectMenuOpen())return;if(e.target.closest&&e.target.closest('#aspect-menu,#aspect-toggle'))return;closeAspectMenu()});
document.addEventListener('keydown',e=>{if(e.key==='Escape'&&aspectMenuOpen()){closeAspectMenu();$('#aspect-toggle')?.focus()}});
window.addEventListener('resize',()=>{if(aspectMenuOpen())placeAspectMenu()});
window.addEventListener('scroll',()=>{if(aspectMenuOpen())placeAspectMenu()},true);
function syncMonitorShape(){const box=$('#monitor');if(!box)return;const shape=MONITOR_SHAPES[P?.format]||'16:9';box.dataset.aspect=shape;box.dataset.fit=shape==='16:9'?'contain':'cover';
 // A vertical film gets a taller panel so the frame can be a useful size; leaving
 // portrait returns the grid to its default proportions untouched.
 const grid=box.closest('.editor-grid');if(grid)grid.classList.toggle('is-vertical',(P?.format||'landscape')!=='landscape')}
function updatePlaybackButton(){const button=$('#play-button');button.textContent=editorPlaying?'❚❚':'▶';button.title=editorPlaying?'Pause':'Play';button.setAttribute('aria-label',button.title)}
function toggleEditorPlayback(){if(editorPlaying)pauseEditor();else playEditor()}
async function playEditor(){if(P?.edit_mode==='documentary'){return docPlayTimeline()}if(!scenes().length){toast('Import SRT and build the storyboard first.');return}if(scenes()[sceneIndex]?.missing_video_out){toast('This still or freeze is not shown in the moving-video review.');return}if(dirty){toast('Save or discard scene edits before playing.');return}if(editorTime()>=totalTime()-.01)seekEditor(0,true);editorPlaying=true;updatePlaybackButton();clockAt=performance.now();try{if(editorMaster){masterNode.muted=editorMuted;await masterNode.play()}else{const s=scenes()[sceneIndex],a=assets().find(a=>a.id===s?.asset_id);if(a?.kind==='video'&&!sourceReady){seekSourceAtTimeline(s,a,editorTime());return}showSource(sceneIndex,editorTime());if(P?.voiceover){narrNode.muted=editorMuted;await narrNode.play()}if(a?.kind==='video'&&sourceReady&&sourceNode.readyState>=2)await sourceNode.play()}}catch(e){pauseEditor();toast('Playback could not start. Check the selected media or try again.')}}
function pauseEditor(){if(P?.edit_mode==='documentary')window.cancelDocPlay?.();const wasPlaying=editorPlaying;if(editorPlaying&&!editorMaster&&!P?.voiceover)clockBase=editorTime();editorPlaying=false;updatePlaybackButton();masterNode.pause();sourceNode.pause();narrNode.pause();clockAt=performance.now();if(wasPlaying&&P?.edit_mode==='documentary')setTimeout(()=>{if(!editorPlaying)renderInspector()},0)}
function updateTrackMuteControls(){
 if(P?.edit_mode==='documentary'){docUpdateMuteControls();return}
 const audio=$('#mute-audio'), video=$('#mute-mp4');
 if(audio){audio.textContent=editorMuted?'Muted':'On';audio.setAttribute('aria-pressed',String(editorMuted));audio.setAttribute('aria-label',(editorMuted?'Unmute':'Mute')+' narration track in preview');audio.title=editorMuted?'Narration muted in preview':'Narration on in preview'}
 if(video){video.textContent=editorVideoMuted?'Muted':'On';video.setAttribute('aria-pressed',String(editorVideoMuted));video.setAttribute('aria-label',(editorVideoMuted?'Unmute':'Mute')+' MP4 source audio for preview');video.title=editorVideoMuted?'MP4 source audio muted in preview':'MP4 source audio on in preview'}
}
function toggleEditorAudio(){editorMuted=!editorMuted;masterNode.muted=P?.edit_mode==='documentary'&&window.docTimelineData?.direct?true:editorMuted;narrNode.muted=editorMuted;updateTrackMuteControls()}
function toggleVideoTrackMute(){
 if(P?.edit_mode==='documentary'){toast('Regular footage stays muted. The selected sound-bite audio is in the audio track.');return}
 const t=editorTime(),was=editorPlaying;pauseEditor();editorVideoMuted=!editorVideoMuted;
 sourceNode.muted=editorVideoMuted;nextSourceNode.muted=true;
 // A rendered master has a mixed audio track, so source audio can only be auditioned
 // independently from the original source in timeline preview, never from the master.
 renderProgram();seekEditor(t,false);updateTrackMuteControls();
 if(was)playEditor();
 toast(editorVideoMuted?'MP4 source audio muted in preview.':'MP4 source audio on for source preview only; exports remain muted.');
}
// Cut Action fades are a project setting read by both the preview and the export.
async function setDocumentaryFades(patch){
 if(!P)return;
 const previous=JSON.parse(JSON.stringify(P.documentary_fades||{}));
 P.documentary_fades=Object.assign({},previous,patch);
 try{
  const updated=await api(`/api/projects/${P.id}`,'PATCH',{documentary_fades:patch});
  if(P?.id!==updated.id)return;
  P.documentary_fades=updated.documentary_fades||{};
  renderExport();toast(P.documentary_fades.enabled===false?'The export will not fade.':'The export will include the scripted fade.');
 }catch(e){P.documentary_fades=previous;renderExport();toast(e.message)}
}
// On-screen script captions are a project setting: the preview and the export
// read the same flag, so switching it never needs a rebuild of the timeline.
async function setBurnCaptions(enabled){
 if(!P)return;
 const previous=P.burn_captions!==false;
 P.burn_captions=!!enabled;
 try{
  const updated=await api(`/api/projects/${P.id}`,'PATCH',{burn_captions:!!enabled});
  if(P?.id!==updated.id)return;
  P.burn_captions=updated.burn_captions!==false;
  $('#editor-captions').classList.add('captions-off');
  renderProgram();updatePosition();toast(P.burn_captions?'On-screen captions will be burned in.':'On-screen text is off: clean picture, nothing burned in.');
 }catch(e){
  P.burn_captions=previous;const burnBox=$('#burn-on-screen-captions');if(burnBox)burnBox.checked=previous;toast(e.message);
 }
}
function setEditorCaptions(enabled){editorCC=!!enabled;$('#caption-toggle').textContent=editorCC?'CC On':'CC Off';$('#caption-toggle').setAttribute('aria-pressed',String(editorCC));const c=$('#burn-srt-subtitles');if(c)c.checked=editorCC;renderProgram();updatePosition();renderExport();if($('#manage-dialog').open)renderManagement()}
function toggleEditorCaptions(){setEditorCaptions(!editorCC)}
function setEditorZoom(v){timelineZoom=Number(v);renderTimeline();updatePosition()}
masterNode.addEventListener('ended',pauseEditor);narrNode.addEventListener('ended',pauseEditor);
$('#manage-dialog').addEventListener('cancel',e=>{e.preventDefault();closeManagement()});
$('#ruler-track').onclick=e=>{const r=$('#ruler-track').getBoundingClientRect();seekEditor((e.clientX-r.left)/timelineZoom,true)};
// Scrub the timeline without editing SRT, source ranges or narration.
let playheadDrag=null;
function scrubPlayhead(clientX,previous=null){
 const rect=$('#timeline-content').getBoundingClientRect(),target=Math.max(0,Math.min(totalTime()-.001,(clientX-rect.left)/timelineZoom));
 const drag=playheadDrag||previous;if(drag&&drag.lastTarget!=null&&Math.abs(drag.lastTarget-target)<.001)return;
 if(drag)drag.lastTarget=target;seekEditor(target,true);
}
// Drag across the MP4 track to draw a selection. Ctrl/Command adds to it.
const videoTrack=$('#video-track');
videoTrack.addEventListener('pointerdown',e=>{if(P?.edit_mode==='documentary')return;if(e.button!==0||!P||!scenes().length)return;const rect=videoTrack.getBoundingClientRect();marquee={id:e.pointerId,startX:e.clientX-rect.left,x:e.clientX,y:e.clientY,initial:new Set(selectedSlotIds),add:e.ctrlKey||e.metaKey,active:false,raf:0}});
function updateMarquee(){if(!marquee?.active)return;const scroll=$('#timeline-scroll'),viewport=scroll.getBoundingClientRect();if(marquee.x>viewport.right-30)scroll.scrollLeft+=Math.min(18,Math.max(0,(marquee.x-viewport.right+30)/2));else if(marquee.x<viewport.left+30)scroll.scrollLeft-=Math.min(18,Math.max(0,(viewport.left+30-marquee.x)/2));const rect=videoTrack.getBoundingClientRect(),end=marquee.x-rect.left,left=Math.min(marquee.startX,end),right=Math.max(marquee.startX,end);let box=videoTrack.querySelector('.selection-marquee');if(!box){box=document.createElement('div');box.className='selection-marquee';videoTrack.append(box)}box.style.left=left+'px';box.style.width=(right-left)+'px';selectedSlotIds=marquee.add?new Set(marquee.initial):new Set();for(const scene of scenes())if(scene.frame_in/30*timelineZoom<right&&scene.frame_out/30*timelineZoom>left)selectedSlotIds.add(scene.id);selectionExplicitlyEmpty=!selectedSlotIds.size;markSelected();marquee.raf=requestAnimationFrame(updateMarquee)}
videoTrack.addEventListener('pointermove',e=>{if(!marquee||marquee.id!==e.pointerId)return;marquee.x=e.clientX;marquee.y=e.clientY;const rect=videoTrack.getBoundingClientRect();if(!marquee.active&&Math.abs(e.clientX-rect.left-marquee.startX)>5){if(!leave()){marquee=null;return}pauseEditor();slotScalePreview=null;marquee.active=true;videoTrack.setPointerCapture(e.pointerId);updateMarquee()}if(marquee?.active)e.preventDefault()});
function finishMarquee(e){if(!marquee||marquee.id!==e.pointerId)return;const was=marquee.active;if(was){cancelAnimationFrame(marquee.raf);videoTrack.querySelector('.selection-marquee')?.remove();if(e.type==='pointercancel'){selectedSlotIds=marquee.initial;selectionExplicitlyEmpty=!selectedSlotIds.size}else{const first=scenes().findIndex(s=>selectedSlotIds.has(s.id));if(first>=0){sceneIndex=first;sourceScene='';seekEditor(scenes()[first].frame_in/30,false)}}markSelected();renderInspector();syncPictureScale();suppressClipClick=true;setTimeout(()=>{suppressClipClick=false},0)}marquee=null}
videoTrack.addEventListener('pointerup',finishMarquee);videoTrack.addEventListener('pointercancel',finishMarquee);
$('#timeline-scroll').addEventListener('keydown',e=>{if((e.ctrlKey||e.metaKey)&&e.key.toLowerCase()==='a'){e.preventDefault();selectAllVideoSlots()}});
$('#playhead-handle').addEventListener('pointerdown',e=>{
 if(e.button!==0||!scenes().length||!leave())return;
 e.preventDefault();const wasPlaying=editorPlaying;pauseEditor();
 playheadDrag={pointerId:e.pointerId,x:e.clientX,wasPlaying,last:0,raf:0};
 e.currentTarget.setPointerCapture(e.pointerId);e.currentTarget.classList.add('dragging');
 const step=now=>{
  const d=playheadDrag;if(!d)return;
  const scroll=$('#timeline-scroll'),rect=scroll.getBoundingClientRect(),edge=32;
  if(d.x<rect.left+edge)scroll.scrollLeft-=Math.min(24,(rect.left+edge-d.x)*.6);
  else if(d.x>rect.right-edge)scroll.scrollLeft+=Math.min(24,(d.x-(rect.right-edge))*.6);
  if(now-d.last>=45){scrubPlayhead(d.x);d.last=now}
  d.raf=requestAnimationFrame(step);
 };playheadDrag.raf=requestAnimationFrame(step);
});
$('#playhead-handle').addEventListener('pointermove',e=>{if(playheadDrag?.pointerId===e.pointerId)playheadDrag.x=e.clientX});
function endPlayheadDrag(e){
 const d=playheadDrag;if(!d||d.pointerId!==e.pointerId)return;
 cancelAnimationFrame(d.raf);playheadDrag=null;e.currentTarget.classList.remove('dragging');
 // The drag itself only moved the picture. Drop the dedupe marker so this final
 // seek runs in full and settles the scene selection and the inspector.
 d.lastTarget=null;scrubPlayhead(e.clientX,d);if(d.wasPlaying)playEditor();
}
$('#playhead-handle').addEventListener('pointerup',endPlayheadDrag);
$('#playhead-handle').addEventListener('pointercancel',endPlayheadDrag);
$('#playhead-handle').addEventListener('keydown',e=>{let t=editorTime(),step=e.shiftKey?5:1;if(e.key==='ArrowLeft')t-=step;else if(e.key==='ArrowRight')t+=step;else if(e.key==='Home')t=0;else if(e.key==='End')t=totalTime();else return;e.preventDefault();seekEditor(t,true)});

document.addEventListener('input',e=>{if(e.target.closest('#scene-form')&&editorPlaying)pauseEditor()});
document.addEventListener('keydown',e=>{if(e.code==='Space'&&!document.querySelector('dialog[open]')&&!['INPUT','TEXTAREA','SELECT','BUTTON'].includes(document.activeElement.tagName)){e.preventDefault();editorPlaying?pauseEditor():playEditor()}});
function tickEditor(){if(P?.edit_mode==='documentary'){docTick();updatePosition();requestAnimationFrame(tickEditor);return}if(editorPlaying){const t=editorTime();if(t>=totalTime()){pauseEditor()}else{const i=scenes().findIndex(s=>frameAt(t)>=s.frame_in&&frameAt(t)<s.frame_out);if(i>=0&&i!==sceneIndex){sceneIndex=i;if(!editorMaster)showSource(i,t);renderInspector();markSelected()}if(!editorMaster){showSource(sceneIndex,t);prepareNextSource(sceneIndex,t);drawBackdrop()}}}else if(!editorMaster)drawBackdrop();updatePosition();requestAnimationFrame(tickEditor)}
setInterval(async()=>{if(!P||dirty||uploading||editorPlaying||deletingProjects.has(P.id))return;const id=P.id;try{const next=await api('/api/projects/'+id);if(P?.id!==id||deletingProjects.has(id))return;if(next.updated!==P.updated||next.render?.status!==P.render?.status||next.render?.progress!==P.render?.progress||next.render?.output!==P.render?.output||next.preview?.status!==P.preview?.status||next.preview?.progress!==P.preview?.progress||next.scene_exports?.status!==P.scene_exports?.status||next.scene_exports?.progress!==P.scene_exports?.progress){P=next;sceneIndex=Math.min(sceneIndex,Math.max(0,P.scenes.length-1));renderProject()}}catch(e){if(P?.id===id&&!deletingProjects.has(id))toast(e.message)}},3000);
(async()=>{try{await list();const id=location.hash.match(/^#\/production\/([a-f0-9]{16})$/);if(id)await openProject(id[1]);else if(projects[0])await openProject(projects[0].id);else renderEditor();requestAnimationFrame(tickEditor)}catch(e){toast(e.message);renderEditor();requestAnimationFrame(tickEditor)}})();

// Manual block-map authoring. Only the visible fields are changed; other imported
// production instructions stay on the row. A draft remains in memory until Save.
let mapDraft=null;
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
 $('#map-editor-body').innerHTML=`<p class="small">Save the map, then rebuild the storyboard; saving resets existing approvals.</p>
 <div class="inline"><button type="button" onclick="mapStep(-1)" ${d.index===0?'disabled':''}>← Previous</button><label>Block <select onchange="mapGo(this.value)">${d.rows.map((row,i)=>`<option value="${i}" ${i===d.index?'selected':''}>${E(row.block??row.srt_blocks?.[0]??i+1)}${mapValue(row,'media_file')?'':' · gap'}</option>`).join('')}</select></label><button type="button" onclick="mapStep(1)" ${d.index===d.rows.length-1?'disabled':''}>Next →</button></div>
 <div class="notice"><strong>${E(block?.start_ms!=null?mapClock(block.start_ms):r.start)} → ${E(block?.end_ms!=null?mapClock(block.end_ms):r.end)}</strong><p>${E(block?.text||'Narration not found for this block; review the map against the SRT.')}</p></div>
 <form id="map-row-form" onsubmit="event.preventDefault();saveBlockMap()"><div class="two">${field('Parent beat number','beat','1')}<label>Source filename<input data-map-field="media_file" list="map-source-files" value="${E(mapValue(r,'media_file'))}" placeholder="Select or type original filename"></label>${field('Source IN','source_in','00:00:00.000')}${field('Source OUT (exclusive)','source_out','00:00:04.000')}${field('Status (optional)','status','VIDEO, STILL or CTX')}${field('Footage group (optional)','footage_group','Same label for the same shot')}</div>${field('Picture description (optional)','visible_content','What the picture should show')}<label>Gap note (required if no source)<textarea data-map-field="gap_note" rows="2">${E(mapValue(r,'gap_note'))}</textarea></label><datalist id="map-source-files">${P.assets.map(a=>`<option value="${E(a.original_name||a.name)}"></option>`).join('')}</datalist><div id="map-save-error" class="notice warn" role="alert" hidden></div><div class="dialog-actions"><button type="button" onclick="closeBlockMapEditor()">Cancel</button><button class="new" type="submit">Save block map</button></div></form>`;
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
  body.innerHTML=`<p class="small">apply the narration transcript.</p>
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

// Only the documentary music bed has volume steppers. Narration and MP4 audio
// keep their preview on/off buttons.
window.stepTrackVolume=function(track,delta){
 if(P?.edit_mode==='documentary'&&window.docStepVolume)return window.docStepVolume(track,delta);
 if(track==='music'&&window.docStepVolume)return window.docStepVolume('music',delta);
};
