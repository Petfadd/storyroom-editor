/* Separate, non-destructive documentary handoff planning. This is not an export. */
let docPlan=null,docPid=null,docRun=0,docTimer=null,docSpeechBusy='';
const docTime=n=>Number(n).toFixed(3)+' s';
function stopDocumentaryPreview(){docRun++;clearInterval(docTimer);docTimer=null;for(const id of ['#doc-narration','#doc-source'])$(id)?.pause();const s=$('#doc-preview-status');if(s)s.textContent='Preview stopped.'}
function closeDocumentary(){stopDocumentaryPreview();clearTimeout(docAudioPoll);$('#documentary-dialog').close()}
$('#documentary-dialog').addEventListener('cancel',()=>stopDocumentaryPreview());
async function openDocumentary(){
 if(!P)return;pauseEditor();pauseManagementMedia();docPid=P.id;docPlan=null;$('#documentary-body').innerHTML='<p>Loading the separate sound-bite plan…</p>';if(!$('#documentary-dialog').open)$('#documentary-dialog').showModal();
 try{const data=await api(`/api/projects/${docPid}/documentary-bites`);if(P?.id!==docPid||!$('#documentary-dialog').open)return;docPlan=data;renderDocumentary();pollDocumentaryAudio()}catch(e){$('#documentary-body').innerHTML=`<p class="notice warn">${E(e.message)}</p>`}
}
function documentaryTimelineNotice(){
 const bites=docPlan?.bites||[],stale=bites.some(b=>b.stale_reasons?.length),timeline=window.docTimelineData;
 const current=timeline?.id===P?.id&&timeline?.version===P?.updated&&timeline?.revision===docPlan?.revision;
 const count=bites.length?`${bites.length} saved sound bite${bites.length===1?'':'s'}.`:'No sound bites are saved.';
 let preview=!current||timeline?.loading?'Timeline preview status is loading.':stale?'A sound bite needs review because its source or narration changed.':timeline?.status==='ready'&&timeline.url?(timeline.direct?'Browser timeline preview is ready; cuts are approximate.':'Timeline preview is ready.'):['queued','running'].includes(timeline?.status)?'Timeline preview is being prepared.':timeline?.status==='error'?`Timeline preview could not be prepared: ${timeline.detail||'unknown error'}.`:'Prepare the timeline preview to play it.';
 const exportText=P?.documentary_beat_review?'Use Render MP4 after the source, timing and approval checks.':'Final MP4 export is not available for this storyboard type.';
 return `<div id="doc-timeline-notice" class="notice ${stale||timeline?.status==='error'?'warn':''}" role="status"><strong>Documentary timeline.</strong> ${E(count)} ${E(preview)} ${E(exportText)}</div>`;
}
window.updateDocumentaryTimelineNotice=function(){const el=$('#doc-timeline-notice');if(el&&P?.id===docPid&&docPlan)el.outerHTML=documentaryTimelineNotice()};
function renderDocumentary(){
 if(!P||!docPlan)return;
 $('#documentary-body').innerHTML=`${documentaryTimelineNotice()}
 <section class="card"><h3>Documentary audio preview</h3><p>Prepare a separate WAV with narration and the saved sound bites. The original files remain unchanged.</p><button type="button" onclick="prepareDocumentaryAudio()">Prepare documentary audio preview</button><div id="doc-audio-status"><p>Checking audio status…</p></div></section><section class="card"><h3>Adjusted subtitles</h3><p>Download a separate SRT shifted around the saved bites. If a pause crosses a cue, listen and choose the last word spoken before that pause. The original SRT is untouched.</p>${docSubtitleSplitHTML()}<button type="button" onclick="saveDocumentaryExportSplits()">Use these reviewed splits for MP4 export</button> <button type="button" onclick="downloadDocumentarySrt()">Download adjusted SRT</button></section><section class="card"><h3>Saved sound bites (${docPlan.bites.length})</h3><p>Each bite can show captions of what is actually said in that footage. Speech is transcribed locally from the saved source IN/OUT; nothing is uploaded, and the original SRT and narration are untouched.</p><p class="small">Machine transcription, not identity verification. Listen to each bite before publishing. The scripted caption stays available as a label.</p><div id="bite-speech-status"><p class="small">Checking the local speech model…</p></div>${docPlan.bites.length?docPlan.bites.map(b=>docBiteSpeechRow(b)).join(''):'<p>No sound bites saved.</p>'}</section>`;
 checkBiteSpeech();
}
function docBiteSpeechRow(b){
 const speech=b.speech||[],busy=docSpeechBusy===b.id;
 return `<div class="notice ${b.stale_reasons?.length?'warn':''}" data-bite-speech="${E(b.id)}"><strong>Pause at ${docTime(b.pause_s)}</strong> · ${E(b.asset_name)} ${docTime(b.source_in_s)}–${docTime(b.source_out_s)}${b.stale_reasons?.length?`<p>${b.stale_reasons.map(E).join(' ')}</p>`:''}
 <p class="small">Scripted caption: ${E(b.script_caption&&!['none','n/a','na','—','-'].includes(String(b.script_caption).trim().toLowerCase())?b.script_caption:'none')}</p>
 ${speech.length?`<details open><summary>Spoken words (${speech.length} line${speech.length===1?'':'s'})</summary>${speech.map(x=>`<p>“${E(x.text)}”</p>`).join('')}</details><button type="button" ${busy?'disabled':''} onclick="transcribeBiteSpeech('${E(b.id)}')">${busy?'Transcribing…':'Transcribe again'}</button> <button type="button" ${busy?'disabled':''} onclick="removeBiteSpeech('${E(b.id)}')">Remove spoken caption</button>`:`<button type="button" ${busy?'disabled':''} title="Transcribe this bite's own audio on this computer" onclick="transcribeBiteSpeech('${E(b.id)}')">${busy?'Transcribing…':'Add spoken-word caption'}</button>`}</div>`}
function checkBiteSpeech(){fetch(`/api/projects/${docPid}/documentary-bite-speech`).then(r=>r.json()).then(d=>{const box=$('#bite-speech-status');if(!box)return;box.innerHTML=d.available?`<p class="small">Local speech model: <strong>${E(d.model)}</strong> · CPU only, no upload.</p>`:`<p class="notice warn">${E(d.detail)}</p>`}).catch(e=>{const box=$('#bite-speech-status');if(box)box.innerHTML=`<p class="notice warn">${E(e.message)}</p>`})}
async function transcribeBiteSpeech(id){
 if(!docPlan||docSpeechBusy)return;const bite=docPlan.bites.find(b=>b.id===id);if(!bite)return;
 if(bite.stale_reasons?.length){toast('Review this stale sound bite before transcribing it.');return}
 docSpeechBusy=id;renderDocumentary();
 try{const data=await api(`/api/projects/${docPid}/documentary-bites/${id}/speech`,'POST',{revision:docPlan.revision});if(P?.id!==docPid)return;
  docPlan=await api(`/api/projects/${docPid}/documentary-bites`);
  toast(`${data.segments.length} spoken caption line(s) saved. Listen to the bite before publishing.`);reloadDocTimeline();
 }catch(e){toast(e.message)}finally{docSpeechBusy='';if(P?.id===docPid&&docPlan)renderDocumentary()}
}
async function removeBiteSpeech(id){
 if(!docPlan)return;
 try{await api(`/api/projects/${docPid}/documentary-bites/${id}/speech`,'DELETE');
  docPlan=await api(`/api/projects/${docPid}/documentary-bites`);toast('Spoken caption removed; the scripted caption is used again.');reloadDocTimeline()
 }catch(e){toast(e.message)}finally{if(P?.id===docPid&&docPlan)renderDocumentary()}
}
function documentaryDeliveryHTML(){
 const beat=!!P.documentary_beat_review,r=P.render||{},running=['queued','running'].includes(r.status);
 $('#crumb').textContent=P.title;$('#manage-title').textContent='Documentary delivery';$('#content').innerHTML=`<div class="notice ${beat?'':'warn'}"><strong>${beat?'Documentary MP4 export':'Final documentary MP4 is not available for this storyboard'}</strong><p>${beat?'Render MP4 checks the cue shots, source frames and approvals. Script captions are included; the fade is omitted after confirmation.':'This export path supports script-driven Beat storyboards.'}</p><div id="doc-delivery-readiness">Checking saved project…</div>${running?`<progress max="100" value="${r.progress||0}"></progress><p>${E(r.stage)} · ${E(r.detail||'')}</p><button type="button" onclick="cancelExport()">Cancel export</button>`:''}${r.error?`<p class="notice warn">${E(r.error)}</p>`:''}${r.url?`<p><a class="download" href="${E(r.url)}?download=1">↓ Download ${isCurrentRender(r)?'current':'previous'} MP4</a>${r.adjusted_srt_url?` · <a class="download" href="${E(r.adjusted_srt_url)}?download=1">↓ Adjusted SRT</a>`:''}</p>`:''}<button type="button" onclick="approveDocumentaryScenes()" ${P.scenes.some(s=>!s.approved)?'':'disabled'}>Approve all ready scenes</button><p class="small">Approve only after reviewing picture, identity, context and source. Your attestation is not independent verification or reuse-rights approval.</p><div id="doc-approval-result" role="status"></div><button type="button" onclick="openDocumentary()">Documentary audio and subtitles</button></div>`;loadDocumentaryReadiness(P.id);
}
async function approveDocumentaryScenes(){
 if(!P||P.edit_mode!=='documentary'||!leave())return;
 const id=P.id,count=P.scenes.filter(s=>!s.approved).length;
 if(!count){toast('Every scene is already approved.');return}
 if(!confirm(`Approve all technically ready scenes? ${count} scene(s) are currently unapproved. Confirm only if you personally reviewed each picture, identity, context and source. This does not verify rights; final export requires separate checks. Scenes with technical blockers will be skipped; existing approvals with blockers may be reset.`))return;
 const button=document.querySelector('#content button[onclick="approveDocumentaryScenes()"]');if(button)button.disabled=true;
 try{const result=await api(`/api/projects/${id}/scenes/approve`,'POST',{attested:true,scope:'ready'});if(P?.id!==id)return;P=result;renderProject();await list();const box=$('#doc-approval-result'),approved=result.bulk_approval?.approved||[],skipped=result.bulk_approval?.skipped||[];if(box)box.innerHTML=`<p>${approved.length} scene(s) approved. ${skipped.length} scene(s) skipped.</p>${skipped.length?`<ul>${skipped.map(x=>`<li>Scene ${E(x.scene)}: ${x.reasons.map(E).join(' ')}</li>`).join('')}</ul>`:''}`;toast(`${approved.length} scene(s) approved; ${skipped.length} skipped.`);loadDocumentaryReadiness(id);loadDocumentaryReadiness(id,'doc-brief-readiness')}catch(e){toast(e.message);if(button?.isConnected)button.disabled=false}
}
async function loadDocumentaryReadiness(id,boxId='doc-delivery-readiness'){
 try{const state=await api(`/api/projects/${id}/documentary-export-readiness`);const box=document.getElementById(boxId);if(!box||P?.id!==id)return;
 const approved=P?.scenes?P.scenes.filter(x=>x.approved).length:null,total=P?.scenes?P.scenes.length:null; const countText=total?` ${approved}/${total} picture slots approved.`:''; const heading=state.status==='checking'?'Checking source frames and timing…':state.errors.length?`${state.errors.length} export blocker(s) need attention`:'Documentary export checks passed';
 box.innerHTML=`<p><strong>${E(heading)}</strong>${E(countText)}</p>${state.errors.length?`<ul class="issues">${state.errors.map(x=>`<li>${E(x)}</li>`).join('')}</ul>`:''}${state.warnings.length?`<details><summary>Review notes (${state.warnings.length})</summary><ul class="issues">${state.warnings.map(x=>`<li>${E(x)}</li>`).join('')}</ul></details>`:''}<p class="small">These checks do not independently verify content, identity or rights.</p>`;
 if(state.status==='checking')setTimeout(()=>{if(P?.id===id&&document.getElementById(boxId))loadDocumentaryReadiness(id,boxId)},1500)
 }catch(e){const box=document.getElementById(boxId);if(box&&P?.id===id)box.textContent='Export readiness unavailable: '+e.message}}



let docAudioPoll=null;
async function prepareDocumentaryAudio(){try{const id=P.id;const result=await api(`/api/projects/${id}/documentary-audio`,'POST');if(P?.id!==id)return;showDocumentaryAudio(result);pollDocumentaryAudio()}catch(e){toast(e.message)}}
function showDocumentaryAudio(state){const box=$('#doc-audio-status');if(!box)return;const text=state.status==='done'?'Documentary audio preview is ready.':state.detail||'Not prepared.';box.innerHTML=`<p>${E(text)}</p>${state.status==='done'?`<p class="small">Audio only · ${docTime(state.duration_s)}. This is not the documentary MP4 or a retimed SRT.</p><audio controls preload="metadata" src="${E(state.url)}"></audio><p><a class="download" href="${E(state.url)}?download=1">Download documentary audio preview (WAV)</a></p>`:''}`}
async function pollDocumentaryAudio(){clearTimeout(docAudioPoll);if(!P||P.edit_mode!=='documentary'||!$('#documentary-dialog').open)return;const id=P.id;try{const state=await api(`/api/projects/${id}/documentary-audio`);if(P?.id!==id||!$('#documentary-dialog').open)return;showDocumentaryAudio(state);if(['queued','running'].includes(state.status))docAudioPoll=setTimeout(pollDocumentaryAudio,1500)}catch(e){const box=$('#doc-audio-status');if(box)box.textContent=e.message}}

function docSubtitleSplitHTML(){
 const blocks=P?.srt?.blocks||[],seen=new Set(),items=[];
 for(const bite of docPlan.bites){const ms=Math.round(bite.pause_s*1000),block=blocks.find(b=>b.start_ms<ms&&ms<b.end_ms),key=block&&`${block.number}:${ms}`;if(!block||seen.has(key))continue;seen.add(key);const words=block.text.split(/\s+/);items.push(`<label>Original cue ${E(block.number)} · pause ${docTime(bite.pause_s)}<span class="small">${E(block.text)}</span><select class="doc-subtitle-split" data-cue="${block.number}" data-pause="${ms}"><option value="">Choose the last word spoken before the pause</option>${words.slice(0,-1).map((w,i)=>`<option value="${i+1}">After: ${E(words.slice(0,i+1).join(' '))}</option>`).join('')}</select></label>`)}
 return items.join('');
}
window.documentaryExportSplits=null;
async function saveDocumentaryExportSplits(){
 try{const splits=[...document.querySelectorAll('.doc-subtitle-split')].map(el=>({cue:Number(el.dataset.cue),pause_ms:Number(el.dataset.pause),words_before:Number(el.value)}));if(splits.some(x=>!x.words_before))throw Error('Choose where every crossing cue splits first. Listen to the narration to verify it.');if(splits.length&&!confirm('Have you listened and checked which words are spoken before each pause? The split uses your choices, not automatic word alignment.'))return;const response=await fetch(`/api/projects/${docPid}/documentary-subtitles`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({revision:docPlan.revision,splits})});if(!response.ok){const e=await response.json();throw Error(e.error||'Could not validate the subtitle split.')}window.documentaryExportSplits={id:docPid,revision:docPlan.revision,splits};toast('Reviewed subtitle split saved for this export session.');}catch(e){toast(e.message)}
}
async function downloadDocumentarySrt(){
 try{const splits=[...document.querySelectorAll('.doc-subtitle-split')].map(el=>({cue:Number(el.dataset.cue),pause_ms:Number(el.dataset.pause),words_before:Number(el.value)}));if(splits.some(x=>!x.words_before))throw Error('Choose where every crossing cue splits first. Listen to the narration to verify it.');if(splits.length&&!confirm('Have you listened and checked which words are spoken before each pause? The split uses your choices; it is not automatic word alignment.'))return;const r=await fetch(`/api/projects/${docPid}/documentary-subtitles`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({revision:docPlan.revision,splits})});if(!r.ok){const e=await r.json();throw Error(e.error||'Could not adjust subtitles.')}const blob=await r.blob(),url=URL.createObjectURL(blob),a=document.createElement('a');a.href=url;a.download='documentary-adjusted.srt';a.click();setTimeout(()=>URL.revokeObjectURL(url),10000)}catch(e){toast(e.message)}
}

let documentaryDraft=null,documentaryDraftRequest=0;
async function refreshDocumentaryDraft(){
 const button=$('#documentary-draft-button'),id=P?.id,token=++documentaryDraftRequest;if(button)button.hidden=true;documentaryDraft=null;
 if(!id||P.edit_mode!=='documentary'){closeDocumentaryDraft();return}
 try{const state=await api(`/api/projects/${id}/documentary-review-preview`);if(token!==documentaryDraftRequest||P?.id!==id)return;if(state.status==='ready'){documentaryDraft={id,url:state.url};button.hidden=false}else closeDocumentaryDraft()}catch{if(token===documentaryDraftRequest)closeDocumentaryDraft()}
}
async function openDocumentaryDraft(){const id=P?.id;await refreshDocumentaryDraft();if(!documentaryDraft||documentaryDraft.id!==id||P?.id!==id){toast('The review draft is no longer current.');return}pauseEditor();pauseManagementMedia();const v=$('#documentary-draft-video');v.src=documentaryDraft.url;v.load();$('#documentary-draft-download').href=documentaryDraft.url+'?download=1';$('#documentary-draft-dialog').showModal()}
function closeDocumentaryDraft(){const dialog=$('#documentary-draft-dialog'),v=$('#documentary-draft-video');if(v){v.pause();v.removeAttribute('src');v.load()}if(dialog?.open)dialog.close()}
$('#documentary-draft-dialog').addEventListener('cancel',()=>{const v=$('#documentary-draft-video');v.pause();v.removeAttribute('src');v.load()});


/* ---- Beat picture plan: proposed before anything is written ---- */
let beatProposal=null,beatProposalFocus=null;
// The plan used to hard-code Beat 007 whenever anything needed a decision, so a
// problem in Beat 010 pointed the replacement picker at the wrong Beat.
function beatBlocker(){const first=(beatProposal?.issues||[])[0]||'';return (first.match(/^Beat (\d+)/)||[])[1]||''}
function beatProposalHTML(){
 const d=beatProposal;if(!d)return '<p>Loading the Beat picture plan…</p>';
 return `<p class="small">Every scripted Beat keeps its own narration clock. Where the scripted picture range cannot cover that clock, the change is listed below and saved with the storyboard. Nothing is re-used outside the file the script names unless you choose it.</p>
 <p>Planned picture: <strong>${(d.planned_ms/60000).toFixed(3)} min</strong> · original narration ${(d.narration_ms/60000).toFixed(3)} min</p>
 ${d.issues.length?`<div class="notice warn"><strong>${d.issues.length} Beat(s) need your decision</strong><ul class="issues">${d.issues.map(x=>`<li>${E(x)}</li>`).join('')}</ul>
 <label>Use a different uploaded video for Beat ${E(beatBlocker())}<select onchange="beatPickSource(this.value)"><option value="">Choose a video…</option>${d.assets.map(a=>`<option value="${E(a.name)}" ${beatProposalFocus===a.name?'selected':''}>${E(a.name)} · ${a.duration.toFixed(1)}s</option>`).join('')}</select></label></div>`:
 `<div class="notice"><strong>Every Beat fits inside an uploaded file.</strong><p class="small">Review the adjustments below, then build.</p></div>`}
 ${(d.narration_gaps||[]).length?`<details open><summary>Narration with no Beat (${d.narration_gaps.length})</summary><p class="small">This narration time belongs to no Beat in the script, so no footage was assigned to it. These are proposed ranges from files the script already names, using seconds no other Beat is using. Building applies them.</p><ul class="issues">${d.narration_gaps.map(g=>`<li>${(g.narration_in_ms/1000).toFixed(3)}–${(g.narration_out_ms/1000).toFixed(3)}s · ${E(g.source_file)} ${(g.source_in_ms/1000).toFixed(3)}–${(g.source_out_ms/1000).toFixed(3)}s</li>`).join('')}</ul></details>`:''}
 <details ${d.adjustments.length?'open':''}><summary>Picture range changes (${d.adjustments.length})</summary><ul class="issues">${d.adjustments.map(x=>`<li>${E(x)}</li>`).join('')||'<li>None.</li>'}</ul></details>
 <details><summary>Repeated footage to approve (${d.repeated_ranges.length} pair${d.repeated_ranges.length===1?'':'s'})</summary><p class="small">One video appears in more than one Beat. Approving lets the same footage appear again; it does not verify the picture.</p><ul class="issues">${d.repeated_ranges.map(p=>`<li>Beat ${p[0]} and Beat ${p[1]}</li>`).join('')||'<li>None.</li>'}</ul></details>
 ${d.consecutive_bites.length?`<details open><summary>Sound bites with no narration between them</summary><p class="small">Beat ${d.consecutive_bites.map(p=>p.join(' and ')).join(', ')} sit at the same pause. They will play back to back.</p></details>`:''}
 <label class="check-label"><input type="checkbox" id="beat-confirm" ${d.issues.length?'disabled':''}> I have read these picture-range changes and the repeated-footage list.</label>
 <div id="beat-build-state" aria-live="polite"></div>
 <p><button type="button" onclick="buildBeatStoryboard()" ${d.issues.length?'disabled':''}>Build the Beat timeline</button> <button type="button" onclick="openDocumentary()">Open the documentary tools</button></p>
 <p class="small">This builds a review timeline only. Content, identity, speaker and reuse rights still need your review, and nothing is exported yet.</p>`;
}
async function openBeatProposal(){
 if(!P||P.edit_mode!=='documentary')return;
 $('#plan-title').textContent='Beat picture plan';$('#plan-body').innerHTML='<p>Reading the scripted Beats…</p>';
 if(!$('#plan-dialog').open)$('#plan-dialog').showModal();
 try{
  const focusBeat=beatBlocker();
  const query=(beatProposalFocus&&focusBeat)?`?scripted_review_beat=${encodeURIComponent(focusBeat)}&scripted_review_source=${encodeURIComponent(beatProposalFocus)}`:'';
  beatProposal=await api(`/api/projects/${P.id}/documentary-beat-proposal${query}`);
  $('#plan-body').innerHTML=beatProposalHTML();
 }catch(e){$('#plan-body').innerHTML=`<p class="notice warn">${E(e.message)}</p>`}
}
function beatPickSource(name){
 if(!beatProposal)return;beatProposalFocus=name||null;openBeatProposal();
}
// The build decodes every source up to its last used frame, which takes minutes on a
// real production. It runs as a job so this dialog can show what it is doing instead
// of leaving a disabled button as the only evidence that anything was asked for.
let beatBuildTimer=null;
function beatBuildHTML(job){
 const pct=Math.max(0,Math.min(100,Math.round(job.percent||0)));
 return `<div class="build-progress">
  <div class="progress-track"><i class="progress-fill" style="width:${pct}%"></i></div>
  <p class="small"><strong>${pct}%</strong> · ${E(job.detail||'Building…')}</p>
 </div>`;
}
function stopBeatBuildPolling(){clearTimeout(beatBuildTimer);beatBuildTimer=null}
async function pollBeatBuild(){
 beatBuildTimer=null;
 if(!P?.id)return;
 let job;
 try{job=await api(`/api/projects/${P.id}/documentary-beat-storyboard`)}
 catch(e){$('#beat-build-state').innerHTML=`<p class="notice warn">${E(e.message)}</p>`;return}
 if(!$('#beat-build-state'))return;   // the dialog was closed while we were asking
 if(job.status==='done'){
  const built=job.project||{};
  P=built;beatProposal=null;beatProposalFocus=null;
  stopBeatBuildPolling();
  $('#plan-dialog').close();
  toast(`${(built.scenes||[]).length} picture slots built. Review the timeline, then check the pictures.`);
  renderProject();list().catch(()=>{});
  return;
 }
 if(job.status==='error'){
  $('#beat-build-state').innerHTML=`<p class="notice warn">${E(job.error||'The build could not finish.')}</p>
   <p><button type="button" onclick="buildBeatStoryboard()">Try again</button></p>`;
  return;
 }
 if(job.status==='cancelled'){
  $('#beat-build-state').innerHTML='<p class="small">Build cancelled. Nothing was changed.</p>';
  return;
 }
 $('#beat-build-state').innerHTML=beatBuildHTML(job)+
  '<p><button type="button" class="text-button danger" onclick="cancelBeatBuild()">Stop building</button></p>';
 beatBuildTimer=setTimeout(pollBeatBuild,700);
}
async function cancelBeatBuild(){
 stopBeatBuildPolling();
 try{await api(`/api/projects/${P.id}/documentary-beat-storyboard/cancel`,'POST',{})}catch{}
 $('#beat-build-state').innerHTML='<p class="small">Stopping after the current step…</p>';
 beatBuildTimer=setTimeout(pollBeatBuild,700);
}
async function buildBeatStoryboard(){
 if(!beatProposal)return;
 if(!$('#beat-confirm')?.checked){toast('Read and confirm the picture-range changes first.');return}
 const button=document.querySelector('#plan-body button[onclick="buildBeatStoryboard()"]');if(button)button.disabled=true;
 stopBeatBuildPolling();
 const holder=$('#beat-build-state');
 if(holder)holder.innerHTML=beatBuildHTML({percent:0,detail:'Reading the scripted Beats'});
 try{
  const job=await api(`/api/projects/${P.id}/documentary-beat-storyboard`,'POST',{
   confirmed:true,project_version:beatProposal.project_version,plan_revision:0,script_sha256:beatProposal.script_sha256,
   approved_gaps:beatProposal.narration_gaps||[],approved_overlaps:beatProposal.repeated_ranges,allow_silent_tail:false,
   approve_consecutive_bites:beatProposal.consecutive_bites.length>0,
   scripted_review_beat:(beatProposal.issues.length?beatBlocker():null)||null,
   scripted_review_source:beatProposal.issues.length?beatProposalFocus:null});
  if(holder)holder.innerHTML=beatBuildHTML(job);
  beatBuildTimer=setTimeout(pollBeatBuild,400);
 }catch(e){
  if(holder)holder.innerHTML=`<p class="notice warn">${E(e.message)}</p>`;
  else toast(e.message);
  if(button?.isConnected)button.disabled=false;
 }
}
