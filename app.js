const tg = window.Telegram?.WebApp;
if (tg) tg.expand();

const state = {
  me: null,
  balance: 0,
  deals: [],
  topups: [],
  currentDeal: null,
};

function initData(){
  return tg?.initData || "";
}

async function api(path, method="GET", body=null){
  const res = await fetch(path, {
    method,
    headers: {
      "Content-Type":"application/json",
      "X-Telegram-InitData": initData(),
    },
    body: body ? JSON.stringify(body) : null,
  });
  const data = await res.json().catch(()=> ({}));
  if(!res.ok){
    throw new Error(data?.detail || "Ошибка API");
  }
  return data;
}

function rub(n){ return `${Math.round(n)} ₽`; }

function setTab(name){
  document.querySelectorAll(".tab").forEach(b=>{
    b.classList.toggle("active", b.dataset.tab===name);
  });
  document.querySelectorAll(".tabPanel").forEach(p=>{
    p.classList.toggle("hidden", p.dataset.panel!==name);
  });
}

function toast(text){
  if(tg?.showPopup){
    tg.showPopup({title:"GUARANT", message:text, buttons:[{type:"ok"}]});
  }else{
    alert(text);
  }
}

function renderMe(){
  const pill = document.getElementById("mePill");
  const meId = document.getElementById("meId");
  const meUser = document.getElementById("meUser");
  if(!state.me){ pill.textContent="…"; return; }
  pill.textContent = `ID ${state.me.id}`;
  meId.textContent = state.me.id;
  meUser.textContent = state.me.username ? "@"+state.me.username : "—";
}

function renderBalance(){
  document.getElementById("balanceVal").textContent = rub(state.balance);
}

function tag(status){
  if(status==="CREATED") return `<span class="tag wait">создано</span>`;
  if(status==="FUNDED") return `<span class="tag wait">оплачено</span>`;
  if(status==="DELIVERED") return `<span class="tag wait">выполнено</span>`;
  if(status==="RELEASED") return `<span class="tag ok">завершено</span>`;
  if(status==="REFUNDED") return `<span class="tag bad">возврат</span>`;
  if(status==="DISPUTE") return `<span class="tag warn">спор</span>`;
  return `<span class="tag">—</span>`;
}

function renderDeals(){
  const el = document.getElementById("dealList");
  if(!state.deals.length){
    el.innerHTML = `<div class="item"><b>Сделок пока нет.</b><div class="muted">Создай новую — кнопка сверху.</div></div>`;
    return;
  }
  el.innerHTML = state.deals.map(d=>`
    <div class="item" data-deal="${d.id}">
      <div class="itemTop">
        <div>
          <b>#${d.id}</b> • ${rub(d.amount)} • <span class="muted">${d.role==="seller"?"продавец":"покупатель"}</span>
        </div>
        ${tag(d.status)}
      </div>
      <div class="muted" style="margin-top:8px">${d.description}</div>
      <div class="actions">
        <button class="btn primary" onclick="openDeal(${d.id})">Открыть</button>
        <button class="btn" onclick="copyLink(${d.id})">Ссылка</button>
      </div>
    </div>
  `).join("");
}

function renderTopups(){
  const el = document.getElementById("topupList");
  if(!state.topups.length){
    el.innerHTML = `<div class="item"><b>Заявок пока нет.</b><div class="muted">Создай заявку — админ подтвердит.</div></div>`;
    return;
  }
  el.innerHTML = state.topups.map(t=>`
    <div class="item">
      <div class="itemTop">
        <div><b>#${t.id}</b> • ${rub(t.amount)}</div>
        <span class="tag ${t.status==="APPROVED"?"ok":(t.status==="REJECTED"?"bad":"wait")}">${t.status.toLowerCase()}</span>
      </div>
      <div class="muted" style="margin-top:8px">${t.note||"—"}</div>
    </div>
  `).join("");
}

async function loadAll(){
  state.me = await api("/api/me");
  renderMe();
  const b = await api("/api/balance");
  state.balance = b.balance;
  renderBalance();
  state.deals = (await api("/api/deals")).items;
  renderDeals();
  state.topups = (await api("/api/topups")).items;
  renderTopups();
}

window.copyLink = async (dealId)=>{
  const d = await api(`/api/deals/${dealId}`);
  const url = `${location.origin}/?deal=${d.public_code}`;
  await navigator.clipboard.writeText(url);
  toast("Ссылка скопирована");
};

window.openDeal = async (dealId)=>{
  const d = await api(`/api/deals/${dealId}`);
  showDeal(d);
};

function showDeal(d){
  state.currentDeal = d;
  document.getElementById("dealEditor").classList.add("hidden");
  document.getElementById("dealView").classList.remove("hidden");

  document.getElementById("dealViewTitle").textContent = `Сделка #${d.id}`;
  document.getElementById("dealViewMeta").textContent = d.description;
  document.getElementById("dealStatus").textContent = d.status;
  document.getElementById("dealViewAmount").textContent = rub(d.amount);
  document.getElementById("dealViewFee").textContent = rub(d.fee);

  const act = document.getElementById("dealActions");
  act.innerHTML = "";

  const hint = document.getElementById("dealHint");
  hint.textContent = "";

  // Buttons based on status/role
  const btn = (text, cls, fn)=> {
    const b = document.createElement("button");
    b.className = "btn "+(cls||"");
    b.textContent = text;
    b.onclick = fn;
    act.appendChild(b);
  };

  btn("Ссылка", "", ()=>copyLink(d.id));

  if(d.status==="CREATED"){
    hint.textContent = "Нужна вторая сторона. Открой ссылку сделки у второго участника.";
    if(d.can_join){
      btn("Присоединиться", "primary", async ()=>{
        await api(`/api/deals/${d.public_code}/join`, "POST");
        await refreshDeal();
      });
    }
  }

  if(d.status==="FUNDED"){
    hint.textContent = "Деньги в резерве. Ждём 'Выполнено' от продавца.";
  }

  if(d.status==="DELIVERED"){
    hint.textContent = "Продавец отметил 'Выполнено'. Проверь и подтверди.";
    if(d.can_confirm){
      btn("✅ Подтвердить", "primary", async ()=>{
        await api(`/api/deals/${d.id}/confirm`, "POST");
        await loadAll(); await refreshDeal();
      });
    }
    if(d.can_dispute){
      btn("⚠️ Открыть спор", "warn", async ()=>{
        await api(`/api/deals/${d.id}/dispute`, "POST");
        await refreshDeal();
      });
    }
  }

  if(d.status==="CREATED" || d.status==="FUNDED" || d.status==="DELIVERED"){
    if(d.can_pay){
      btn("💳 Оплатить с баланса", "primary", async ()=>{
        await api(`/api/deals/${d.id}/pay`, "POST");
        await loadAll(); await refreshDeal();
      });
    }
    if(d.can_deliver){
      btn("📦 Выполнено", "", async ()=>{
        await api(`/api/deals/${d.id}/deliver`, "POST");
        await refreshDeal();
      });
    }
  }

  if(d.status==="DISPUTE"){
    hint.textContent = "Спор открыт. Решение принимает админ.";
  }
}

async function refreshDeal(){
  if(!state.currentDeal) return;
  const d = await api(`/api/deals/${state.currentDeal.id}`);
  showDeal(d);
}

function parseDealFromUrl(){
  const u = new URL(location.href);
  const code = u.searchParams.get("deal");
  return code;
}

async function openDealByCode(code){
  setTab("deals");
  const d = await api(`/api/deals/by/${code}`);
  document.getElementById("dealEditor").classList.add("hidden");
  document.getElementById("dealView").classList.remove("hidden");
  showDeal(d);
}

document.querySelectorAll(".tab").forEach(b=>{
  b.addEventListener("click", ()=> setTab(b.dataset.tab));
});

document.getElementById("btnRefresh").onclick = loadAll;

document.getElementById("goCreateDeal").onclick = ()=>{ setTab("deals"); showEditor(); };
document.getElementById("btnNewDeal").onclick = ()=> showEditor();
document.getElementById("goTopup").onclick = ()=> setTab("topup");
document.getElementById("goMyDeals").onclick = ()=> setTab("deals");
document.getElementById("goSupport").onclick = ()=> toast("Поддержка: напиши админу в Telegram.");
document.getElementById("btnCopyMyId").onclick = async ()=>{
  await navigator.clipboard.writeText(String(state.me?.id||""));
  toast("ID скопирован");
};
document.getElementById("btnOpenBot").onclick = ()=>{
  if(tg?.openTelegramLink) tg.openTelegramLink("https://t.me/"+(tg?.initDataUnsafe?.user?.username||""));
  else toast("Открой бота вручную");
};

function showEditor(){
  document.getElementById("dealView").classList.add("hidden");
  document.getElementById("dealEditor").classList.remove("hidden");
  document.getElementById("dealCreateHint").textContent = "";
}

document.getElementById("btnCreateDeal").onclick = async ()=>{
  const description = document.getElementById("dealDesc").value.trim();
  const amount = Number(document.getElementById("dealAmount").value);
  const role = document.getElementById("dealRole").value;
  if(!description || !amount){
    document.getElementById("dealCreateHint").textContent = "Заполни описание и сумму.";
    return;
  }
  const d = await api("/api/deals", "POST", {description, amount, role});
  document.getElementById("dealCreateHint").textContent = "Готово. Скопируй ссылку и отправь второй стороне.";
  await loadAll();
  showDeal(d);
};

document.getElementById("btnTopup").onclick = async ()=>{
  const amount = Number(document.getElementById("topupAmount").value);
  const note = document.getElementById("topupNote").value.trim();
  if(!amount){
    document.getElementById("topupHint").textContent = "Введи сумму.";
    return;
  }
  await api("/api/topups", "POST", {amount, note});
  document.getElementById("topupHint").textContent = "Заявка отправлена. Ждём подтверждения.";
  document.getElementById("topupAmount").value = "";
  document.getElementById("topupNote").value = "";
  await loadAll();
};

(async ()=>{
  try{
    await loadAll();
    const code = parseDealFromUrl();
    if(code) await openDealByCode(code);
  }catch(e){
    toast(e.message || "Ошибка");
  }
})();
