const STAGES = ["Novo", "Contato", "Conversa", "Fechamento", "Produção", "Entregue"];
const state = {
  leads: JSON.parse(localStorage.getItem("zap_agent_leads") || "[]"),
  selectedId: null,
};

const $ = (id) => document.getElementById(id);
const save = () => localStorage.setItem("zap_agent_leads", JSON.stringify(state.leads));
const escapeHtml = (s="") => String(s).replace(/[&<>'"]/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;","'":"&#39;",'"':"&quot;"}[c]));

async function api(path, payload) {
  const res = await fetch(path, {method:"POST", headers:{"Content-Type":"application/json"}, body:JSON.stringify(payload)});
  const data = await res.json().catch(() => ({}));
  if (!res.ok || !data.ok) throw new Error(data.error || `Erro ${res.status}`);
  return data;
}

async function loadStatus(){
  try{
    const res = await fetch("/api/status");
    const data = await res.json();
    $("status").textContent = data.gemini_configured ? `IA pronta · ${data.model}` : "Falta configurar GEMINI_API_KEY";
  }catch(e){$("status").textContent = "Sessão ou IA indisponível";}
}

function renderPipeline(){
  $("pipeline").innerHTML = STAGES.map(stage => {
    const cards = state.leads.filter(l => (l.stage || "Novo") === stage).map(l => `
      <div class="pipeline-card" data-id="${l.id}"><b>${escapeHtml(l.name)}</b><span>${escapeHtml(l.segment || "")}</span></div>`).join("");
    return `<div class="column"><h3>${stage}</h3>${cards || `<span class="hint">vazio</span>`}</div>`;
  }).join("");
  document.querySelectorAll(".pipeline-card").forEach(el => el.onclick = () => selectLead(el.dataset.id));
}

function selectLead(id){
  state.selectedId = id;
  const lead = state.leads.find(l => l.id === id);
  if(!lead) return;
  $("workspaceEmpty").hidden = true;
  $("workspace").hidden = false;
  $("selectedLabel").textContent = lead.name;
  $("selectedSummary").innerHTML = `<b>${escapeHtml(lead.name)}</b> · ${escapeHtml(lead.segment || "")}<br>${escapeHtml(lead.signal || lead.opportunity || "Sem observação")}
    <div style="margin-top:10px"><label>Etapa<select id="stageSelect">${STAGES.map(s=>`<option ${s===lead.stage?'selected':''}>${s}</option>`).join("")}</select></label></div>`;
  $("stageSelect").onchange = e => {lead.stage=e.target.value;save();renderPipeline();};
  $("conversation").value = lead.notes || "";
  $("output").textContent = lead.lastOutput || "O resultado aparecerá aqui.";
}

function addLead(lead){
  const normalized = {...lead, id: crypto.randomUUID(), stage:"Novo", notes:"", lastOutput:""};
  state.leads.unshift(normalized); save(); renderPipeline(); return normalized;
}

function renderProspects(leads, sources=[]){
  const sourceText = sources.length ? `<p class="hint">Fontes consultadas: ${sources.map(s=>`<a href="${escapeHtml(s.url)}" target="_blank" rel="noopener">${escapeHtml(s.title)}</a>`).join(" · ")}</p>` : "";
  $("prospectResults").innerHTML = sourceText + leads.map((lead,i)=>`
    <article class="lead-card"><h3>${escapeHtml(lead.name)}</h3><a href="${escapeHtml(lead.public_url || '#')}" target="_blank" rel="noopener">${escapeHtml(lead.public_url || 'sem URL retornada')}</a><p><b>Sinal:</b> ${escapeHtml(lead.signal || '')}</p><p><b>Oportunidade:</b> ${escapeHtml(lead.opportunity || '')}</p><p><b>Oferta:</b> ${escapeHtml(lead.offer || '')}</p><div class="actions"><button data-add="${i}">Adicionar ao pipeline</button></div></article>
  `).join("");
  document.querySelectorAll("[data-add]").forEach(btn => btn.onclick = () => {addLead(leads[Number(btn.dataset.add)]);btn.textContent="Adicionado";btn.disabled=true;});
}

$("prospectBtn").onclick = async () => {
  const btn = $("prospectBtn"); btn.disabled=true; btn.textContent="Pesquisando...";
  $("prospectResults").innerHTML = "<p class='hint'>A IA está verificando oportunidades públicas...</p>";
  try{
    const data = await api("/api/prospect", {niche:$("niche").value, city:$("city").value, count:Number($("count").value)});
    renderProspects(data.leads || [], data.sources || []);
  }catch(e){$("prospectResults").innerHTML=`<p class="error">${escapeHtml(e.message)}</p>`;}
  finally{btn.disabled=false;btn.textContent="Buscar oportunidades";}
};

function selectedContext(){
  const lead = state.leads.find(l => l.id === state.selectedId);
  if(!lead) throw new Error("Selecione um lead.");
  lead.notes = $("conversation").value; save();
  return {lead, notes:lead.notes};
}

async function runAction(action){
  const lead = state.leads.find(l=>l.id===state.selectedId); if(!lead) return;
  $("output").textContent="Gerando...";
  try{
    const data = await api("/api/ai", {action, context:selectedContext()});
    const text = typeof data.result === "string" ? data.result : JSON.stringify(data.result,null,2);
    lead.lastOutput=text; save(); $("output").textContent=text;
  }catch(e){$("output").textContent=`Erro: ${e.message}`;}
}

document.querySelectorAll("[data-action]").forEach(btn => btn.onclick=()=>runAction(btn.dataset.action));
$("replyBtn").onclick=()=>runAction("reply");
$("copyBtn").onclick=async()=>{await navigator.clipboard.writeText($("output").textContent);$("copyBtn").textContent="Copiado";setTimeout(()=>$("copyBtn").textContent="Copiar",1200)};
$("downloadBtn").onclick=()=>{const lead=state.leads.find(l=>l.id===state.selectedId);const blob=new Blob([$("output").textContent],{type:"text/plain;charset=utf-8"});const a=document.createElement("a");a.href=URL.createObjectURL(blob);a.download=`${(lead?.name||'produto').replace(/[^a-z0-9]+/gi,'-').toLowerCase()}-zapvenda.txt`;a.click();URL.revokeObjectURL(a.href)};
$("clearBtn").onclick=()=>{if(confirm("Apagar todos os leads salvos neste navegador?")){state.leads=[];state.selectedId=null;save();renderPipeline();$("workspace").hidden=true;$("workspaceEmpty").hidden=false;}};
$("conversation").addEventListener("input",()=>{const lead=state.leads.find(l=>l.id===state.selectedId);if(lead){lead.notes=$("conversation").value;save();}});

loadStatus(); renderPipeline();
