// ─── sidebar sliders ───────────────────────────────────────────────────────────
const bind=(id,out,f=x=>x)=>{
  const e=document.getElementById(id);
  const u=()=>document.getElementById(out).textContent=f(e.value);
  e.addEventListener('input',u); u();
};
bind('K','Kv');
bind('lam','lamv',v=>Number(v).toFixed(1));
bind('reb','rebv');
bind('lb','lbv');
bind('hp','hpv');
bind('vs','vsv',v=>Number(v).toFixed(1));
bind('dd','ddv');
bind('chaos_lookback_s','chlbv');
bind('chaos_thresh_s','chthv',v=>Number(v).toFixed(1));
bind('chaos_hz_s','chhzv');

// ─── chaos toggle ─────────────────────────────────────────────────────────────
const CHAOS_ADJ={
  NVDA:{v:0.40,cls:'adj-reduce',label:'REDUCE'},
  GOOG:{v:0.40,cls:'adj-reduce',label:'REDUCE'},
  GOLD:{v:0.40,cls:'adj-reduce',label:'REDUCE'},
  SILVER:{v:-0.10,cls:'adj-short',label:'SHORT'},
  PLATINUM:{v:0.40,cls:'adj-reduce',label:'REDUCE'},
  PALLADIUM:{v:0.34,cls:'adj-reduce',label:'REDUCE'},
  OIL:{v:-0.05,cls:'adj-short',label:'SHORT'},
  HOUSING:{v:0.40,cls:'adj-reduce',label:'REDUCE'},
  HOMEBUILDERS:{v:0.30,cls:'adj-reduce',label:'REDUCE'},
  MORTGAGES:{v:0.28,cls:'adj-reduce',label:'REDUCE'},
  COMMERCIAL_RE:{v:0.40,cls:'adj-reduce',label:'REDUCE'},
  RESIDENTIAL:{v:0.38,cls:'adj-reduce',label:'REDUCE'},
};
function renderAdjGrid(){
  document.getElementById('adj_grid').innerHTML=Object.entries(CHAOS_ADJ).map(([t,a])=>{
    const col=a.cls==='adj-short'?'var(--bad)':a.cls==='adj-reduce'?'#f59e0b':'var(--good)';
    const sign=a.v>=0?'+':'';
    return `<div class="adj-box ${a.cls}">
      <div class="ak">${t}</div>
      <div class="av" style="color:${col}">${sign}${a.v.toFixed(2)}×</div>
      <div class="ak">${a.label}</div>
    </div>`;
  }).join('');
  // build trader recommendations grouped by action tier
  const REC_TIERS=[
    {key:'SELL &amp; SHORT',col:'var(--bad)',  icon:'⬇⬇',hint:'Open / increase short position. High crash probability warrants directional short exposure.',test:v=>v<-0.5},
    {key:'SHORT',          col:'var(--bad)',  icon:'⬇', hint:'Initiate short via put options or inverse ETF to hedge downside risk.',                     test:v=>v<0},
    {key:'SELL — REDUCE', col:'#f87171',    icon:'↘', hint:'Exit most of the position. Preserve capital ahead of an expected drawdown.',                 test:v=>v<0.40},
    {key:'TRIM',           col:'#f59e0b',    icon:'↙', hint:'Reduce position size. Limit downside while maintaining partial market exposure.',             test:v=>v<0.70},
    {key:'HOLD — WATCH', col:'#f59e0b',    icon:'⚠', hint:'Hold but tighten stop-losses. Re-evaluate on the next rebalance window.',                    test:v=>v<1},
    {key:'HOLD',           col:'var(--good)',icon:'✓', hint:'No action required. Maintain current position at full weight.',                               test:v=>true},
  ];
  const groups={};
  Object.entries(CHAOS_ADJ).forEach(([t,a])=>{
    const tier=REC_TIERS.find(r=>r.test(a.v));
    if(!tier) return;
    if(!groups[tier.key]){groups[tier.key]={...tier,tickers:[]};}
    groups[tier.key].tickers.push(t);
  });
  const rows=Object.values(groups).filter(g=>g.tickers.length).map(g=>
    `<tr>
      <td style="white-space:nowrap"><span style="color:${g.col};font-weight:800;font-size:13px">${g.icon} ${g.key}</span></td>
      <td style="font-weight:700">${g.tickers.join(', ')}</td>
      <td style="color:var(--mut);font-size:12px">${g.hint}</td>
    </tr>`).join('');
  document.getElementById('chaos_rec').innerHTML=
    `<div class="lbl" style="margin-top:14px">trader recommendations</div>
    <table><thead><tr><th>action</th><th>assets</th><th>guidance</th></tr></thead>
    <tbody>${rows}</tbody></table>`;
}
renderAdjGrid();

function syncChaosPanel(){
  const on=document.getElementById('chaos_toggle').checked;
  const panel=document.getElementById('chaos_panel');
  const fn=document.getElementById('flow_chaos');
  const fa=document.getElementById('flow_chaos_arrow');
  panel.style.display=on?'':'none';
  if(fn){ fn.style.display=on?'':'none'; fa.style.display=on?'':'none'; }
}
document.getElementById('chaos_toggle').addEventListener('change',syncChaosPanel);
syncChaosPanel();

// ─── ticker buttons ──────────────────────────────────────────────────────────
const EQUITIES=["NVDA","GOOG","AAPL","MSFT","AMZN","META","TSLA","AMD","NFLX","INTC","TSM","HDRN","JOBY"];
const COMMODITIES=["GOLD","SILVER","PLATINUM","PALLADIUM","OIL","URANIUM","LITHIUM","NEODYMIUM","CORN","WHEAT","RICE","SOYBEANS","SUGAR"];
const HOUSING=["HOUSING","HOMEBUILDERS","MORTGAGES","COMMERCIAL_RE","RESIDENTIAL"];
const EQ_ON=new Set(["NVDA","GOOG"]);
const CMD_ON=new Set(["GOLD","OIL"]);
const RE_ON=new Set(["HOUSING","RESIDENTIAL"]);

function mkBtn(t, cls, on){
  const b=document.createElement('button');
  b.className='tkbtn '+cls+(on?' on':'');
  b.dataset.t=t; b.textContent=t;
  b.title={eq:'Equity',cmd:'Commodity (futures)',re:'Real Estate ETF'}[cls]||'';
  b.onclick=()=>b.classList.toggle('on');
  return b;
}
const tk=document.getElementById('tk_btns');
const cm=document.getElementById('cm_btns');
const re=document.getElementById('re_btns');
EQUITIES.forEach(t=>tk.appendChild(mkBtn(t,'eq',EQ_ON.has(t))));
COMMODITIES.forEach(t=>cm.appendChild(mkBtn(t,'cmd',CMD_ON.has(t))));
HOUSING.forEach(t=>re.appendChild(mkBtn(t,'re',RE_ON.has(t))));

// ─── Lab buttons ─────────────────────────────────────────────────────────────
const MOCK_RESULTS={
  xpyq:{
    tickers:['GOLD','OIL','NVDA','HOUSING'],
    energy:-2.8341, energy_gap:0.1027, runner_up:['SILVER','HOMEBUILDERS'],
    backend:'xpyq hardware', N:12, K:4, subsets:495, credits:3, duration:'1 420 ms', penalty:'2.417',
    badgeLabel:'xpyq completed ✓', badgeClass:'q-live',
  },
  sim:{
    tickers:['GOLD','OIL','NVDA','RESIDENTIAL'],
    energy:-2.7810, energy_gap:0.0882, runner_up:['HOUSING','GOOG'],
    backend:'Aer simulator (QAOA p=1)', N:12, K:4, subsets:'—', credits:'—', duration:'840 ms', penalty:'2.417',
    badgeLabel:'simulator ✓', badgeClass:'a-live',
    counts:[{bits:'101001000010',n:312},{bits:'101000010010',n:184},{bits:'100001000110',n:97},{bits:'001001000110',n:42}],
  },
  local:{
    tickers:['GOLD','OIL','NVDA','HOUSING'],
    energy:-2.8341, energy_gap:0.1027, runner_up:['SILVER','HOMEBUILDERS'],
    backend:'local brute force', N:12, K:4, subsets:495, credits:'—', duration:'112 ms', penalty:'2.417',
    badgeLabel:'local ✓', badgeClass:'m-live',
  },
  ibm:{
    tickers:['GOLD','OIL','GOOG','HOUSING'],
    energy:-2.6990, energy_gap:0.2410, runner_up:['NVDA','RESIDENTIAL'],
    backend:'IBM ibm_brisbane', N:12, K:4, subsets:'—', credits:'—', duration:'4 320 ms', penalty:'2.417',
    badgeLabel:'IBM done ✓', badgeClass:'ibm-on',
    counts:[{bits:'101001000010',n:289},{bits:'100001000110',n:201},{bits:'101000010010',n:158},{bits:'001001000110',n:88}],
  },
};

function renderLabResult(key){
  const r=MOCK_RESULTS[key];
  const badge=document.getElementById('lab_badge');
  badge.textContent=r.badgeLabel; badge.className='badge live '+r.badgeClass;
  const chips=r.tickers.map(t=>`<span class="chip" style="border-color:var(--quantum);color:var(--quantum)">${t}</span>`).join('');
  const counts=r.counts?`
    <div class="lbl">measurement distribution (top bitstrings)</div>
    <div style="display:flex;flex-direction:column;gap:3px;margin-top:4px">
      ${r.counts.map(c=>{
        const mx=Math.max(...r.counts.map(x=>x.n));
        return `<div style="display:flex;align-items:center;gap:8px;font-size:11px;color:var(--mut)">
          <span style="font-family:monospace;width:100px">${c.bits}</span>
          <span style="flex:1"><div class="bar"><span style="width:${(c.n/mx*100).toFixed(0)}%;background:var(--quantum)"></span></div></span>
          <span>${c.n}</span></div>`;
      }).join('')}
    </div>`:''
  ;
  document.getElementById('lab_result').innerHTML=`
    <div class="lbl" style="margin-top:0">solved on <b style="color:var(--quantum)">${r.backend}</b></div>
    <div class="chips">${chips}</div>
    <div class="reason">ground-state energy x'Qx = <b>${r.energy.toFixed(4)}</b></div>
    <div class="rej">runner-up: ${r.runner_up.join(', ')} · ΔE = ${r.energy_gap.toFixed(4)}</div>
    <div class="sigs" style="margin-top:10px">
      <div class="sig"><div class="k">backend</div><div class="v" style="font-size:13px">${r.backend}</div></div>
      <div class="sig"><div class="k">N / K</div><div class="v" style="font-size:13px">${r.N} / ${r.K}</div></div>
      <div class="sig"><div class="k">subsets evaluated</div><div class="v" style="font-size:13px">${r.subsets}</div></div>
      <div class="sig"><div class="k">credits used</div><div class="v" style="font-size:13px">${r.credits}</div></div>
      <div class="sig"><div class="k">duration</div><div class="v" style="font-size:13px">${r.duration}</div></div>
      <div class="sig"><div class="k">penalty P</div><div class="v" style="font-size:13px">${r.penalty}</div></div>
    </div>${counts}`;
}

document.getElementById('btn_xpyq').onclick=()=>renderLabResult('xpyq');
document.getElementById('btn_sim').onclick=()=>renderLabResult('sim');
document.getElementById('btn_ibm').onclick=()=>renderLabResult('ibm');
document.getElementById('btn_local').onclick=()=>renderLabResult('local');

// ─── Crystal Ball tabs ────────────────────────────────────────────────────────
const TABS=['signals','backcast','curves','scenarios','reasoning'];
TABS.forEach(name=>{
  const btn=document.getElementById('tab_'+name);
  const panel=document.getElementById('panel_'+name);
  btn.addEventListener('click',()=>{
    TABS.forEach(n=>{
      document.getElementById('panel_'+n).style.display='none';
      const b=document.getElementById('tab_'+n);
      b.style.borderColor=''; b.style.color='';
    });
    panel.style.display='';
    btn.style.borderColor='var(--accent)'; btn.style.color='var(--accent)';
  });
});

// ─── Run pipeline button ──────────────────────────────────────────────────────
document.getElementById('run_btn').addEventListener('click',function(){
  const btn=this; btn.disabled=true;
  const orig=btn.innerHTML;
  btn.innerHTML='<span style="display:inline-block;width:14px;height:14px;border:2px solid #ffffff33;border-top-color:var(--accent);border-radius:50%;animation:s .7s linear infinite;vertical-align:-2px"></span> running…';
  // Show loading state, then reveal results after 1.8 s
  document.getElementById('perf_rows').innerHTML='<tr><td colspan="7" style="color:var(--mut);text-align:center">computing…</td></tr>';
  setTimeout(()=>{
    btn.disabled=false; btn.innerHTML=orig;
    renderPerf(); renderUniverse();
    // scroll to results
    document.getElementById('perf_card').scrollIntoView({behavior:'smooth',block:'start'});
  }, 1800);
});

// ─── Performance table mock data ──────────────────────────────────────────────
const PERF=[
  {date:'2026-01-15',quantum:['GOLD','OIL','NVDA'],ml:['GOLD','OIL','GOOG'],meta:'quantum',fb:false,sharpe:2.31},
  {date:'2026-01-29',quantum:['GOLD','OIL','NVDA','HOUSING'],ml:['GOLD','OIL','NVDA','RESIDENTIAL'],meta:'quantum',fb:false,sharpe:1.97},
  {date:'2026-02-12',quantum:['GOLD','PALLADIUM','NVDA'],ml:['GOLD','OIL','NVDA'],meta:'blend',fb:false,sharpe:1.44},
  {date:'2026-02-26',quantum:['GOLD','OIL','NVDA','HOUSING'],ml:['GOLD','OIL','NVDA','RESIDENTIAL'],meta:'quantum',fb:false,sharpe:2.08},
  {date:'2026-03-12',quantum:['GOLD','OIL','GOOG'],ml:['GOLD','SILVER','GOOG'],meta:'ml',fb:false,sharpe:0.87},
  {date:'2026-03-26',quantum:['GOLD','OIL','NVDA','HOUSING'],ml:['GOLD','OIL','NVDA','HOUSING'],meta:'quantum',fb:false,sharpe:2.42},
  {date:'2026-04-09',quantum:['GOLD','OIL'],ml:['GOLD','GOOG'],meta:'local brute force',fb:true,sharpe:1.12},
  {date:'2026-04-23',quantum:['GOLD','OIL','NVDA','HOUSING'],ml:['GOLD','OIL','NVDA','RESIDENTIAL'],meta:'quantum',fb:false,sharpe:2.19},
  {date:'2026-05-07',quantum:['GOLD','OIL','NVDA'],ml:['GOLD','OIL','GOOG'],meta:'quantum',fb:false,sharpe:1.88},
  {date:'2026-05-21',quantum:['GOLD','OIL','NVDA','HOUSING'],ml:['GOLD','OIL','NVDA','RESIDENTIAL'],meta:'quantum',fb:false,sharpe:2.04},
];
const mx=Math.max(...PERF.map(p=>Math.abs(p.sharpe)));
function cls(t){return COMMODITIES.includes(t)?'<span class="cls-cmd" style="font-size:9px">CMD</span>':HOUSING.includes(t)?'<span class="cls-re" style="font-size:9px">RE</span>':''}
function fmtTickers(arr){return arr.map(t=>`${t}${cls(t)}`).join(', ')}

function renderPerf(){
  document.getElementById('perf_rows').innerHTML=PERF.map(p=>{
    const w=(Math.abs(p.sharpe)/mx*100).toFixed(0);
    const col=p.sharpe>=0?'var(--good)':'var(--bad)';
    return `<tr>
      <td>${p.date}</td>
      <td class="q" style="font-size:11px">${fmtTickers(p.quantum)}</td>
      <td class="m" style="font-size:11px">${fmtTickers(p.ml)}</td>
      <td class="x">${p.meta}</td>
      <td style="color:${p.fb?'var(--bad)':'var(--mut)'}">${p.fb?'yes':'—'}</td>
      <td style="min-width:100px"><div class="bar"><span style="width:${w}%;background:${col}"></span></div></td>
      <td style="text-align:right;color:${col};font-weight:700">${p.sharpe.toFixed(2)}</td>
    </tr>`;
  }).join('');
}

// ─── Universe table mock data ─────────────────────────────────────────────────
const UNIV=[
  {t:'NVDA',   cls:'equity',      exp:.181, vol:.393, q:true,  m:true,  meta:true},
  {t:'GOOG',   cls:'equity',      exp:.105, vol:.253, q:false, m:false, meta:false},
  {t:'GOLD',   cls:'commodity',   exp:.094, vol:.180, q:true,  m:true,  meta:true},
  {t:'SILVER', cls:'commodity',   exp:.076, vol:.295, q:false, m:false, meta:false},
  {t:'PLATINUM',cls:'commodity',  exp:.063, vol:.227, q:false, m:false, meta:false},
  {t:'PALLADIUM',cls:'commodity', exp:.127, vol:.397, q:false, m:false, meta:false},
  {t:'OIL',    cls:'commodity',   exp:.082, vol:.321, q:true,  m:true,  meta:true},
  {t:'HOUSING',cls:'real_estate', exp:.056, vol:.180, q:true,  m:false, meta:true},
  {t:'HOMEBUILDERS',cls:'real_estate',exp:.031,vol:.252,q:false,m:false,meta:false},
  {t:'MORTGAGES',cls:'real_estate',exp:.079,vol:.218,q:false,m:false,meta:false},
  {t:'COMMERCIAL_RE',cls:'real_estate',exp:.051,vol:.195,q:false,m:false,meta:false},
  {t:'RESIDENTIAL',cls:'real_estate',exp:.041,vol:.181,q:false,m:true,meta:false},
];
const uxMx=Math.max(...UNIV.map(u=>Math.abs(u.exp)));
function clsBadge(cls){
  if(cls==='commodity') return '<span class="cls-cmd">CMD</span>';
  if(cls==='real_estate') return '<span class="cls-re">RE</span>';
  return '<span class="cls-eq">EQ</span>';
}

function renderUniverse(){
  document.getElementById('univ_rows').innerHTML=UNIV.map(u=>{
    const w=(Math.abs(u.exp)/uxMx*100).toFixed(0);
    const col=u.exp>=0?'var(--ml)':'var(--bad)';
    const tags=[
      u.q?'<span class="badge" style="border-color:var(--quantum);color:var(--quantum);font-size:10px">Q</span>':'',
      u.m?'<span class="badge" style="border-color:var(--ml);color:var(--ml);font-size:10px">ML</span>':'',
      u.meta?'<span class="badge" style="border-color:var(--meta);color:var(--meta);font-size:10px">META</span>':'',
    ].join(' ');
    return `<tr>
      <td><b>${u.t}</b></td>
      <td>${clsBadge(u.cls)}</td>
      <td style="min-width:140px"><div class="bar"><span style="width:${w}%;background:${col}"></span></div></td>
      <td style="text-align:right">${(u.exp*100).toFixed(1)}%</td>
      <td style="text-align:right;color:var(--mut)">${(u.vol*100).toFixed(1)}%</td>
      <td>${tags}</td>
    </tr>`;
  }).join('');
}

// render on load
renderPerf();
renderUniverse();
