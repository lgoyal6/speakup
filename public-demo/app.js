
const $ = id => document.getElementById(id);
function node(tag,text,cls){const n=document.createElement(tag);n.textContent=String(text);if(cls)n.className=cls;return n;}
function rows(items){$('details').replaceChildren(...items.map(([title,body])=>{const r=node('div','','row');r.append(node('strong',title),node('small',body));return r;}));}
function error(e){$('result').textContent=e.message||String(e);$('result').className='status error';$('details').replaceChildren();}
function ready(text){$('result').textContent=text;$('result').className='status';}


let traces,index=0;
function render(){if(!traces)return;const scenario=traces.scenarios[$('scenario').value];const shown=scenario.steps.slice(0,index+1);ready((index+1)+' of '+scenario.steps.length+': '+shown.at(-1).event);rows(shown.map(s=>[s.event,JSON.stringify(s.result)]));$('next').disabled=index>=scenario.steps.length-1;}
$('next').onclick=()=>{index++;render();};$('reset').onclick=()=>{index=0;render();};$('scenario').onchange=()=>{index=0;render();};
fetch('./traces.json').then(r=>{if(!r.ok)throw new Error('Replay could not load. Reload to retry.');return r.json();}).then(value=>{traces=value;render();}).catch(error);
