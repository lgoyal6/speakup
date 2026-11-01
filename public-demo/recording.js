const $=id=>document.getElementById(id);
let revision=0;
async function run(step){
 const current=++revision;for(const id of ['upload','save','export'])$(id).disabled=true;$('download').hidden=true;$('status').textContent='Running the original upload and store workflow...';
 try{
  const response=await fetch('/api/workflow',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({step,text:$('review').value})});
  if(!response.headers.get('content-type')?.includes('application/json'))throw new Error('The workflow endpoint is unavailable.');
  const r=await response.json();if(!response.ok)throw new Error(r.error||'Workflow failed');if(current!==revision)return;
  $('review').disabled=false;$('review').value=r.text;$('status').textContent=`${r.state}. Revision ${r.revision}. Audio checksum verified.`;
  $('receipt').textContent=`${r.bytes} real audio bytes. Recovered offset: ${r.recovered_offset}. Retry did not append duplicate bytes.\n${r.transcription_mode}\n${r.audio_sha256}`;
  if(step==='export'){const prior=$('download').href;if(prior.startsWith('blob:'))URL.revokeObjectURL(prior);$('download').href=URL.createObjectURL(new Blob([r.export_content],{type:'text/plain'}));$('download').hidden=false;}
 }catch(e){$('status').textContent=e.message;}
 finally{for(const id of ['upload','save','export'])$(id).disabled=id!=='upload'&&$('review').disabled;}
}
$('upload').onclick=()=>run('upload');$('save').onclick=()=>run('review');$('export').onclick=()=>run('export');
$('review').oninput=()=>{$('download').hidden=true;$('status').textContent='Unsaved review. Save or export to create a new reviewed revision.';};
