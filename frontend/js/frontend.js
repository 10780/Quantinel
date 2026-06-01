const $=id=>document.getElementById(id);
const fmt=(x,d=2)=>(x===null||x===undefined||isNaN(x))?"—":Number(x).toFixed(d);
const pct=x=>(x*100).toFixed(1)+"%";
const esc=s=>String(s).replace(/[&<>]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;'}[c]));

// ---- slider readouts ----
const bind=(id,out,f=x=>x)=>{const e=$(id);const u=()=>$(out).textContent=f(e.value);e.addEventListener('input',u);u();};
bind('K','Kv');
bind('risk_aversion','lamv',v=>Number(v).toFixed(1));
bind('rebalance_every','rebv');
bind('lookback','lbv');
bind('holding','hpv');
bind('poll_timeout','ptv');
bind('queue_round','qrv');
bind('shots','shv');
bind('chaos_lookback','chlbv');
bind('chaos_threshold','chthv',v=>Number(v).toFixed(1));
bind('chaos_horizon','chhzv');
bind('cb_vs','vsv',v=>Number(v).toFixed(1));
bind('cb_dd','ddv');
// show/hide Crystal Ball and ChaosEngine flow nodes
function syncCbFlow(){
  const on=$('use_cb').checked;
  $('f_cb').style.display=on?'':'none';
  $('f_cb_arrow').style.display=on?'':'none';
}
function syncChaosFlow(){
  const on=$('use_chaos').checked;
  $('f_chaos').style.display=on?'':'none';
  $('f_chaos_arrow').style.display=on?'':'none';
}
$('use_cb').addEventListener('change',syncCbFlow);
syncCbFlow();
$('use_chaos').addEventListener('change',syncChaosFlow);
syncChaosFlow();
$('queue_round').addEventListener('input',()=>$('qr_lbl').textContent=$('queue_round').value);
$('qr_lbl').textContent=$('queue_round').value;

// ---- ticker buttons ----
const EQUITIES=["NVDA","GOOG","AAPL","MSFT","AMZN","META","TSLA","AMD","NFLX","INTC","AVGO","ADBE","CRM","ORCL","QCOM","CSCO","TSM","HDRN","JOBY"];
const COMMODITIES=["GOLD","SILVER","PLATINUM","PALLADIUM","OIL","URANIUM","LITHIUM","NEODYMIUM","CORN","WHEAT","RICE","SOYBEANS","SUGAR"];
const HOUSING=["HOUSING","HOMEBUILDERS","MORTGAGES","COMMERCIAL_RE","RESIDENTIAL"];
const UNIVERSE=[...EQUITIES,...COMMODITIES,...HOUSING];
const DEFAULT_ON=new Set(EQUITIES.slice(0,10));
function addTickerBtn(t,on,group){
  const wrap=$(group||'tk_btns');
  const existing=[...wrap.children].find(b=>b.dataset.t===t);
  if(existing){if(on) existing.classList.add('on'); return;}
  const cls=group==='cm_btns'?'cmd':group==='re_btns'?'re':'eq';
  const b=document.createElement('button');
  b.className='tkbtn '+cls+(on?' on':''); b.dataset.t=t; b.textContent=t;
  b.onclick=()=>b.classList.toggle('on');
  wrap.appendChild(b);
}
EQUITIES.forEach(t=>addTickerBtn(t,DEFAULT_ON.has(t),'tk_btns'));
COMMODITIES.forEach(t=>addTickerBtn(t,false,'cm_btns'));
HOUSING.forEach(t=>addTickerBtn(t,false,'re_btns'));
$('tk_addbtn').onclick=()=>{
  const v=$('tk_add').value.trim().toUpperCase();
  if(v) v.split(/[ ,]+/).filter(Boolean).forEach(t=>{
    const isCom=COMMODITIES.includes(t)||['GC=F','SI=F','PL=F','PA=F','CL=F','URA','LIT','MP','ZC=F','ZW=F','ZR=F','ZS=F','SB=F'].includes(t);
    const isRe=HOUSING.includes(t)||['VNQ','ITB','REM','IYR','REZ'].includes(t);
    addTickerBtn(t,true,isCom?'cm_btns':isRe?'re_btns':'tk_btns');
  });
  $('tk_add').value='';
};
function selectedTickers(){
  const eq=[...document.querySelectorAll('#tk_btns .tkbtn.on')].map(b=>b.dataset.t);
  const cm=[...document.querySelectorAll('#cm_btns .tkbtn.on')].map(b=>b.dataset.t);
  const re=[...document.querySelectorAll('#re_btns .tkbtn.on')].map(b=>b.dataset.t);
  const all=[...eq,...cm,...re];
  return all.length?all.join(','):EQUITIES.slice(0,10).join(',');
}

// ---- collect params ----
function collectParams(){
  return {
    data_source:$('data_source').value, tickers:selectedTickers(),
    start:$('start').value, end:$('end').value,
    days:+$('days').value, seed:+$('seed').value, K:+$('K').value,
    risk_aversion:+$('risk_aversion').value, penalty:$('penalty').value,
    rebalance_every:+$('rebalance_every').value, lookback:+$('lookback').value,
    holding:+$('holding').value, use_xpyq:$('use_xpyq').checked,
    poll_timeout:+$('poll_timeout').value, use_claude:$('use_claude').checked,
    meta_mode:$('meta_det').checked?'deterministic':'auto',
    shots:+$('shots').value, ibm_backend:$('ibm_device').value||null,
    use_chaos:$('use_chaos').checked,
    chaos_lookback:+$('chaos_lookback').value,
    chaos_threshold:+$('chaos_threshold').value/100,
    chaos_horizon:+$('chaos_horizon').value,
    use_crystal_ball:$('use_cb').checked,
    cb_horizon:+$('cb_horizon').value,
    cb_vol_surge_equity:+$('cb_vs').value,
    cb_drawdown_floor_equity:+$('cb_dd').value/100,
  };
}

// ---- health ----
let HEALTH={};
async function health(){
  try{
    const h=await (await fetch('/api/health')).json();
    HEALTH=h;
    const p=[];
    p.push(`<span class="pill ${h.xpyq_credits!=null?'on':(h.xpyq_key_present?'':'off')}">xpyq ${h.xpyq_credits!=null?h.xpyq_credits+' cr':(h.xpyq_key_present?'key set':'no key')}</span>`);
    p.push(`<span class="pill ${h.ibm_token_present?'on':'off'}">IBM ${h.ibm_token_present?'BYOK':'off'}</span>`);
    p.push(`<span class="pill ${h.anthropic_key_present?'on':'off'}">Claude ${h.anthropic_key_present?'ready':'off'}</span>`);
    p.push(`<span class="pill ${h.openrouter_key_present?'on':'off'}">OpenRouter ${h.openrouter_key_present?'ready':'off'}</span>`);
    $('health').innerHTML=p.join('');
    $('lab_ibm').disabled=!h.ibm_token_present;
  }catch(e){$('health').innerHTML='<span class="pill off">health unavailable</span>';}
}
health();

// ===================== dashboard render (/api/run) ===========================
function gauge(v,color){
  const deg=Math.max(0,Math.min(1,v))*360;
  return `<div class="ring" style="background:conic-gradient(${color} ${deg}deg,#1d2536 0)">
    <div style="width:40px;height:40px;border-radius:50%;background:var(--panel);display:grid;place-items:center">${(v*100).toFixed(0)}</div></div>`;
}

function agentCard(cls,name,p,extra){
  const chips=p.selected.map(t=>`<span class="chip">${esc(t)}</span>`).join('');
  const per=Object.entries(p.rationale.per_ticker).map(([t,r])=>`<div class="reason"><b>${esc(t)}</b> — ${esc(r)}</div>`).join('')||'<div class="cav">—</div>';
  const rej=p.rationale.rejected.map(r=>`<div class="rej"><b>${esc(r[0])}</b> · ${esc(r[1])}</div>`).join('')||'<div class="cav">—</div>';
  const sigs=Object.entries(p.rationale.key_signals).map(([k,v])=>`<div class="sig"><div class="k">${esc(k)}</div><div class="v">${fmt(v,4)}</div></div>`).join('');
  const cav=p.rationale.caveats.map(c=>`<div class="cav">⚠ ${esc(c)}</div>`).join('');
  const col=getComputedStyle(document.body).getPropertyValue(cls==='q'?'--quantum':'--ml');
  const bdgCls=cls==='q'?'q-live':'m-live';
  return `<div class="card ${cls}">
    <div class="chead"><div class="ctitle"><span class="dot"></span>${name}</div>
      <span class="badge live ${bdgCls}">${esc(p.backend||'—')}</span></div>
    <div class="gauge">${gauge(p.confidence,col)}<div><div class="lbl" style="margin:0">confidence</div>
      <div style="font-size:13px;color:var(--mut)">${esc(p.rationale.summary)}</div></div></div>
    <div class="lbl">selected basket</div><div class="chips">${chips}</div>
    <div class="lbl">per-ticker reasoning</div>${per}
    <div class="lbl">rejected runners-up</div>${rej}
    <div class="lbl">key signals</div><div class="sigs">${sigs}</div>
    <div class="lbl">caveats</div>${cav}
    ${extra||''}</div>`;
}

function heat(Q){
  if(!Q) return '';
  const flat=Q.flat(); const mx=Math.max(...flat.map(Math.abs))||1; const n=Q.length;
  const cells=Q.map(row=>row.map(v=>{
    const t=v/mx; const c=t>=0?`rgba(167,139,250,${Math.abs(t)})`:`rgba(248,113,113,${Math.abs(t)})`;
    return `<div class="cell" style="background:${c}" title="${fmt(v,3)}"></div>`;
  }).join('')).join('');
  return `<details open><summary>QUBO matrix Q (${n}×${n}) — violet +, red −</summary>
    <div class="heat" style="grid-template-columns:repeat(${n},1fr);max-width:${n*26}px">${cells}</div></details>`;
}

function mlFeatures(features,selected){
  if(!features||!Object.keys(features).length) return '';
  const rows=Object.entries(features).map(([t,f])=>{
    const sel=selected.includes(t);
    return `<tr style="${sel?'color:var(--ml);font-weight:700':''}"><td>${esc(t)}${sel?' ✓':''}</td>
      <td style="text-align:right">${pct(f.momentum_20)}</td><td style="text-align:right">${pct(f.vol_20)}</td>
      <td style="text-align:right">${fmt(f.beta_to_equal_weight,2)}</td><td style="text-align:right">${pct(f.max_drawdown_60)}</td>
      <td style="text-align:right">${fmt(f.pred_sharpe,2)}</td></tr>`;
  }).join('');
  return `<details><summary>ML feature table (per ticker)</summary>
    <table><thead><tr><th>ticker</th><th>mom20</th><th>vol20</th><th>beta</th><th>maxDD60</th><th>predSharpe</th></tr></thead>
    <tbody>${rows}</tbody></table></details>`;
}

function metaPanel(m){
  const seen=Object.entries(m.inputs_seen.proposals).map(([k,v])=>
    `<div class="reason"><b>${esc(k)}</b> → ${v.selected.map(esc).join(', ')} <span style="color:var(--mut)">(conf ${fmt(v.confidence)})</span></div>`).join('');
  const ctx=m.inputs_seen.context;
  const bw=m.blend_weights?`<div class="lbl">blend weights</div>`+Object.entries(m.blend_weights).map(([k,v])=>`<div class="reason"><b>${esc(k)}</b> ${pct(v)}</div>`).join(''):'';
  return `<div class="card full x">
    <div class="chead"><div class="ctitle"><span class="dot"></span>MetaAgent</div>
      <span class="badge ${m.fell_back?'':'live'}">${esc(m.backend)}${m.fell_back?' · fell back':''}</span></div>
    <div class="meta-grid">
      <div class="seen"><div class="lbl" style="margin-top:0">what it sees</div>${seen}
        <div class="reason" style="margin-top:8px"><b>regime</b> ${esc(ctx.vol_regime)} · <b>20d univ. return</b> ${pct(ctx.recent_universe_return)}</div>
        <div class="lbl">decision</div>
        <div class="reason">picks <b style="color:var(--meta)">${esc(m.method)}</b> → ${m.selected.map(esc).join(', ')}</div>${bw}
      </div>
      <div class="think"><div class="lbl" style="margin-top:0;color:var(--meta)">what it thinks</div>
        <div class="r">${esc(m.reasoning||'(no reasoning returned)')}</div>
        <details><summary>raw audit trail (inputs_seen)</summary><pre>${esc(JSON.stringify(m.inputs_seen,null,2))}</pre></details>
      </div>
    </div></div>`;
}

function perfPanel(r,hist,sum){
  const meta=r.meta;
  const boxes=`<div class="stat">
    <div class="box"><div class="v" style="color:var(--meta)">${pct(meta.return)}</div><div class="k">realized return</div></div>
    <div class="box"><div class="v">${pct(meta.vol)}</div><div class="k">volatility (ann.)</div></div>
    <div class="box"><div class="v" style="color:${meta.sharpe>=0?'var(--good)':'var(--bad)'}">${fmt(meta.sharpe)}</div><div class="k">sharpe (ann.)</div></div>
  </div>`;
  const mx=Math.max(1,...hist.map(h=>Math.abs(h.sharpe_meta)));
  const rows=hist.map(h=>{
    const w=Math.abs(h.sharpe_meta)/mx*100; const col=h.sharpe_meta>=0?'var(--good)':'var(--bad)';
    return `<tr><td>${h.date}</td><td class="q">${h.quantum.join(',')}</td>
      <td class="m">${h.ml.join(',')}</td><td class="x">${h.meta_method}</td>
      <td>${h.fell_back?'yes':'no'}</td>
      <td style="min-width:120px"><div class="bar"><span style="width:${w}%;background:${col}"></span></div></td>
      <td style="text-align:right;color:${col}">${fmt(h.sharpe_meta)}</td></tr>`;
  }).join('');
  const shares=Object.entries(sum.shares).map(([k,v])=>`<b>${esc(k)}</b> ${v.toFixed(0)}%`).join(' · ');
  return `<div class="card full">
    <div class="ctitle" style="margin-bottom:8px">Realized performance — MetaAgent choice</div>${boxes}
    <table><thead><tr><th>rebalance</th><th>quantum</th><th>ml</th><th>meta</th><th>fell back</th><th>realized sharpe</th><th></th></tr></thead>
      <tbody>${rows}</tbody></table>
    <div class="reason" style="margin-top:10px">Driven by → ${shares} &nbsp;·&nbsp; cumulative mean Sharpe <b style="color:var(--meta)">${fmt(sum.cumulative_sharpe)}</b></div>
  </div>`;
}

function universePanel(u){
  const mx=Math.max(...u.map(a=>Math.abs(a.exp_return)))||1;
  const rows=u.map(a=>{
    const w=Math.abs(a.exp_return)/mx*100; const col=a.exp_return>=0?'var(--ml)':'var(--bad)';
    const tags=[
      a.in_quantum?'<span class="badge" style="border-color:var(--quantum);color:var(--quantum)">Q</span>':'',
      a.in_ml?'<span class="badge" style="border-color:var(--ml);color:var(--ml)">ML</span>':'',
      a.in_meta?'<span class="badge" style="border-color:var(--meta);color:var(--meta)">META</span>':'',
    ].join(' ');
    const cls=a.asset_class||'equity';
    const clsBadge=cls==='commodity'
      ?'<span class="badge" style="border-color:#fbbf24;color:#fbbf24">CMD</span>'
      :cls==='real_estate'
      ?'<span class="badge" style="border-color:#34d399;color:#34d399">RE</span>'
      :'<span class="badge" style="border-color:var(--mut);color:var(--mut)">EQ</span>';
    return `<tr><td><b>${esc(a.ticker)}</b> ${clsBadge}</td>
      <td style="min-width:140px"><div class="bar"><span style="width:${w}%;background:${col}"></span></div></td>
      <td style="text-align:right">${pct(a.exp_return)}</td>
      <td style="text-align:right;color:var(--mut)">${pct(a.vol)}</td><td>${tags}</td></tr>`;
  }).join('');
  return `<div class="card full">
    <div class="ctitle" style="margin-bottom:6px">Universe snapshot (final rebalance)</div>
    <table><thead><tr><th>ticker</th><th>expected return (ann.)</th><th></th><th style="text-align:right">vol</th><th>selected by</th></tr></thead>
      <tbody>${rows}</tbody></table></div>`;
}

// ===================== crystal ball panel ====================================
function crystalBallPanel(cb,universe){
  if(!cb) return '';
  const p=cb.crash_probability||0;
  const pct100=(p*100).toFixed(1);
  const col=p>=0.65?'var(--bad)':p>=0.40?'#f59e0b':'var(--good)';
  const regime=p>=0.65?'HIGH — CRASH ALERT':p>=0.40?'CAUTION':'NORMAL';
  const statBoxes=`<div class="stat" style="margin:12px 0">
    <div class="box"><div class="v" style="color:${col}">${pct100}&nbsp;%</div><div class="k">crash probability</div></div>
    <div class="box"><div class="v" style="color:${col}">${esc(regime)}</div><div class="k">risk regime</div></div>
    <div class="box"><div class="v" style="color:var(--mut)">${fmt(cb.dominant_factor_var,4)}</div><div class="k">dominant factor var</div></div>
  </div>`;
  const bar=`<div class="crash-prob-bar"><span style="width:${pct100}%;background:linear-gradient(90deg,var(--good),var(--meta),var(--bad))"></span></div>`;
  const clsMap={};
  if(universe) universe.forEach(a=>{clsMap[a.ticker]=a.asset_class||'equity';});
  const groups={equity:[],commodity:[],real_estate:[]};
  Object.keys(cb.base_returns||{}).forEach(t=>{const c=clsMap[t]||'equity';if(!groups[c])groups[c]=[];groups[c].push(t);});
  function scenRow(t){
    const base=cb.base_returns[t]||0,bull=cb.bull_returns[t]||0;
    const bear=cb.bear_returns[t]||0,crash=(cb.crash_adjusted_returns||{})[t]||0;
    const vol=(cb.annual_volatility||{})[t];
    return `<div class="cb-ticker-row" style="display:block;padding:8px 0">
      <div style="display:flex;align-items:center;gap:8px;margin-bottom:6px">
        <span style="font-weight:700;min-width:110px">${esc(t)}</span>
        ${vol!=null?`<span style="color:var(--mut);font-size:11px">ann. vol ${(vol*100).toFixed(1)}&nbsp;%</span>`:''}
      </div>
      <div class="scenario-grid">
        <div class="sc-box base"><div class="sv">${base>=0?'+':''}${(base*100).toFixed(1)}&nbsp;%</div><div class="sk">base</div></div>
        <div class="sc-box bull"><div class="sv">${bull>=0?'+':''}${(bull*100).toFixed(1)}&nbsp;%</div><div class="sk">bull</div></div>
        <div class="sc-box bear"><div class="sv">${bear>=0?'+':''}${(bear*100).toFixed(1)}&nbsp;%</div><div class="sk">bear</div></div>
        <div class="sc-box crash"><div class="sv">${crash>=0?'+':''}${(crash*100).toFixed(1)}&nbsp;%</div><div class="sk">crash-adj</div></div>
      </div>
    </div>`;
  }
  const GRP=[['equity','Equities','cls-eq','EQ'],['commodity','Commodities','cls-cmd','CMD'],['real_estate','Real Estate','cls-re','RE']];
  const scenSect=GRP.filter(([g])=>groups[g]&&groups[g].length).map(([g,label,bc,bt])=>
    `<div class="cb-section"><h5>${esc(label)} <span class="${bc}">${bt}</span></h5>${groups[g].map(scenRow).join('')}</div>`
  ).join('');
  const allSect=scenSect||`<div class="cb-section"><h5 style="color:var(--accent)">All tickers</h5>${Object.keys(cb.base_returns||{}).map(scenRow).join('')}</div>`;
  const reasoning=cb.reasoning?`<details style="margin-top:12px"><summary>Full reasoning — IFTF Principles 2 (Signals) · 3 (Backcasting) · 4 (Two Curves)</summary><pre>${esc(cb.reasoning)}</pre></details>`:'';
  return `<div class="card full cb-card">
    <div class="chead">
      <div class="ctitle" style="color:var(--accent)">
        <span class="dot" style="background:var(--accent)"></span>Crystal Ball — Scenario Forecast
      </div>
      <span class="badge live a-live">xpyq eigendecomposition</span>
    </div>
    <div class="reason" style="color:var(--mut);margin-bottom:4px">
      IFTF futures methodology · Principles 2 · 3 · 4 · Dominant factor variance ${fmt(cb.dominant_factor_var,4)}${cb.horizon_days?' · horizon '+cb.horizon_days+' d':''}
    </div>
    ${statBoxes}
    ${bar}
    <div class="lbl" style="margin-top:14px">scenario projections per ticker</div>
    <div style="font-size:12px;color:var(--mut);margin-bottom:10px">Base: expected return compounded. Bull/Bear: base ± 1.5× annual vol (xpyq eigendecomposition). Crash-adj: base × ChaosEngine multiplier.</div>
    ${allSect}
    ${reasoning}
  </div>`;
}

// ===================== chaos engine panel ===================================
function chaosPanel(c){
  if(!c) return '';
  const p=c.crash_probability;
  const pct100=(p*100).toFixed(1);
  const col=p>=0.65?'var(--bad)':p>=0.40?'#f59e0b':'var(--good)';
  const label=p>=0.65?'HIGH — CRASH ALERT':p>=0.40?'MODERATE — CAUTION':'LOW — NORMAL';
  // probability bar with threshold markers
  const bar=`<div class="chaos-bar-wrap">
    <div class="chaos-bar-fill" style="width:${pct100}%;background:linear-gradient(90deg,var(--good),#f59e0b,var(--bad))"></div>
    <div class="chaos-marker" style="left:40%" title="Moderate threshold (40%)"></div>
    <div class="chaos-marker" style="left:65%" title="High threshold (65%)"></div>
  </div>
  <div style="display:flex;justify-content:space-between;font-size:11px;color:var(--mut);margin-bottom:8px">
    <span>0 %</span><span style="color:#f59e0b">▲ 40 %</span><span style="color:var(--bad)">▲ 65 %</span><span>100 %</span>
  </div>`;
  // per-ticker weight multipliers
  const adjBoxes=Object.entries(c.ticker_adjustments||{}).map(([t,v])=>{
    const isSh=v<0; const isRed=v>=0&&v<1;
    const cls=isSh?'adj-short':isRed?'adj-reduce':'adj-hold';
    const valColor=isSh?'var(--bad)':isRed?'#f59e0b':'var(--good)';
    const action=isSh?'SHORT':isRed?'REDUCE':'HOLD';
    return `<div class="adj-box ${cls}">
      <div class="ak">${esc(t)}</div>
      <div class="av" style="color:${valColor}">${v>=0?'+':''}${fmt(v,2)}×</div>
      <div class="ak">${action}</div>
    </div>`;
  }).join('');
  // trader recommendations — group tickers by action tier
  const REC_TIERS=[
    {key:'SELL & SHORT', col:'var(--bad)',  icon:'⬇⬇', hint:'Open / increase short position. High crash probability warrants directional short exposure.', test:v=>v<-0.5},
    {key:'SHORT',        col:'var(--bad)',  icon:'⬇',  hint:'Initiate short via put options or inverse ETF to hedge downside risk.',                      test:v=>v<0},
    {key:'SELL — REDUCE',col:'#f87171',    icon:'↘',  hint:'Exit most of the position. Preserve capital ahead of an expected drawdown.',                  test:v=>v<0.40},
    {key:'TRIM',         col:'#f59e0b',    icon:'↙',  hint:'Reduce position size. Limit downside while maintaining partial market exposure.',              test:v=>v<0.70},
    {key:'HOLD — WATCH', col:'#f59e0b',    icon:'⚠',  hint:'Hold but tighten stop-losses. Re-evaluate on the next rebalance window.',                     test:v=>v<1},
    {key:'HOLD',         col:'var(--good)', icon:'✓', hint:'No action required. Maintain current position at full weight.',                                test:v=>true},
  ];
  const recGroups={};
  Object.entries(c.ticker_adjustments||{}).forEach(([t,v])=>{
    const tier=REC_TIERS.find(r=>r.test(v));
    if(tier){ if(!recGroups[tier.key]) recGroups[tier.key]=tier; recGroups[tier.key].tickers=recGroups[tier.key].tickers||[]; recGroups[tier.key].tickers.push(t); }
  });
  const recRows=Object.values(recGroups).filter(g=>g.tickers&&g.tickers.length).map(g=>`
    <tr>
      <td style="white-space:nowrap"><span style="color:${g.col};font-weight:800;font-size:13px">${g.icon} ${esc(g.key)}</span></td>
      <td style="font-weight:700">${g.tickers.map(esc).join(', ')}</td>
      <td style="color:var(--mut);font-size:12px">${g.hint}</td>
    </tr>`).join('');
  const recTable=recRows?`<div class="lbl" style="margin-top:14px">trader recommendations</div>
    <table><thead><tr><th>action</th><th>assets</th><th>guidance</th></tr></thead>
    <tbody>${recRows}</tbody></table>`:'';
  const reasoning=c.reasoning?`<details style="margin-top:10px"><summary>Full reasoning &amp; feature signals</summary><pre>${esc(c.reasoning)}</pre></details>`:'';
  return `<div class="card full chaos">
    <div class="chead">
      <div class="ctitle"><span class="dot" style="background:var(--bad)"></span>Chaos Engine — Event Probability Assessment</div>
      <span class="badge" style="background:${col};border-color:${col};color:#06121f;font-weight:700">${esc(label)}</span>
    </div>
    <div style="display:flex;align-items:baseline;gap:12px;margin-bottom:4px">
      <span style="font-size:36px;font-weight:800;color:${col}">${pct100} %</span>
      <span style="color:var(--mut);font-size:13px">crash probability · as of ${esc(c.as_of||'—')}</span>
    </div>
    ${bar}
    <div class="lbl">per-ticker position multipliers</div>
    <div style="font-size:12px;color:var(--mut);margin-bottom:6px">Applied to portfolio weights. Values below 1 reduce exposure; negative values signal short.</div>
    <div class="adj-grid">${adjBoxes}</div>
    ${recTable}
    ${reasoning}
  </div>`;
}

function render(d){
  const out=$('out');
  const qExtra=`<div class="lbl">QUBO penalty P</div><div class="reason">${fmt(d.proposals.quantum.penalty,4)}</div>`+heat(d.qubo);
  const mlExtra=mlFeatures(d.proposals.ml.features,d.proposals.ml.selected);
  const cbSect=d.crystal_ball?`<div style="height:16px"></div>${crystalBallPanel(d.crystal_ball,d.universe)}`:'';
  const chaosSect=d.chaos?`<div style="height:16px"></div>${chaosPanel(d.chaos)}`:'';
  const nEq=d.data.n_equities,nCmd=d.data.n_commodities,nRe=d.data.n_real_estate;
  const assetBreak=nEq!=null?` (${nEq} eq${nCmd?' · '+nCmd+' cmd':''}${nRe?' · '+nRe+' re':''})`:'';
  out.innerHTML=
    `<div style="color:var(--mut);font-size:13px;margin-bottom:16px;background:var(--panel);border:1px solid var(--line);border-radius:10px;padding:10px 14px">
       ${esc(d.data.source)} data · <b style="color:var(--ink)">${d.data.n_assets} assets</b>${assetBreak}·
       ${d.data.rows} rows · ${d.data.rebalances} rebalances · as of <b style="color:var(--ink)">${d.data.as_of}</b>
     </div>
     <div class="cards">
       ${agentCard('q','QuantumAgent',d.proposals.quantum,qExtra)}
       ${agentCard('ml','MLAgent',d.proposals.ml,mlExtra)}
     </div>
     <div style="height:16px"></div>${metaPanel(d.meta)}
     ${cbSect}
     ${chaosSect}
     <div style="height:16px"></div>${perfPanel(d.realized,d.history,d.summary)}
     <div style="height:16px"></div>${universePanel(d.universe)}`;
}

$('run').addEventListener('click',async()=>{
  const btn=$('run'); btn.disabled=true; const old=btn.innerHTML;
  btn.innerHTML='<span class="spin"></span> running…';
  const t0=performance.now();
  try{
    const res=await fetch('/api/run',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(collectParams())});
    const d=await res.json();
    if(!res.ok||d.error){throw new Error(d.error||('HTTP '+res.status));}
    render(d);
    $('out').insertAdjacentHTML('afterbegin',`<div class="reason" style="color:var(--mut)">completed in ${(performance.now()-t0).toFixed(0)} ms</div>`);
  }catch(e){
    $('out').innerHTML=`<div class="err">Run failed: ${esc(e.message)}</div>`+$('out').innerHTML;
  }finally{ btn.disabled=false; btn.innerHTML=old; }
});

// ===================== shared quantum result renderer =========================
function countsDist(top){
  if(!top||!top.length) return '';
  const mx=Math.max(...top.map(c=>c.n))||1;
  const rows=top.map(c=>`<div class="crow"><span class="mono">${esc(c.bits)}</span>
    <span class="cbar"><span class="bar"><span style="width:${(c.n/mx*100).toFixed(0)}%;background:var(--quantum)"></span></span></span>
    <span>${c.n}</span></div>`).join('');
  return `<div class="lbl">measurement distribution (top bitstrings)</div><div class="cdist">${rows}</div>`;
}

function quantumResult(source,res,color){
  const chips=res.selected.map(t=>`<span class="chip">${esc(t)}</span>`).join('');
  const ru=res.runner_up?`<div class="rej">runner-up: ${res.runner_up.map(esc).join(', ')}${res.energy_gap!=null?` · ΔE=${fmt(res.energy_gap,4)}`:''}</div>`:'';
  const meta=[
    ['backend',res.backend],['shots',res.shots],['outcomes',res.n_outcomes],
    ['penalty',res.penalty!=null?fmt(res.penalty,3):null],['credits',res.credits],
    ['boards',res.boards],['duration',res.duration!=null?res.duration+'ms':null],
  ];
  const sigs=meta.filter(x=>x[1]!=null&&x[1]!=='').map(x=>`<div class="sig"><div class="k">${esc(x[0])}</div><div class="v" style="font-size:13px">${esc(x[1])}</div></div>`).join('');
  $('lab_result').innerHTML=`
    <div class="lbl" style="margin-top:0">solved on <b style="color:${color}">${esc(source)}</b></div>
    <div class="chips">${chips}</div>
    <div class="reason">ground-state energy x'Qx = <b>${fmt(res.energy,4)}</b></div>${ru}
    <div class="sigs" style="margin-top:8px">${sigs}</div>${countsDist(res.top_counts)}`;
  $('lab_result').style.display='';
}

function labErr(msg){ $('lab_result').innerHTML=`<div class="err">${esc(msg)}</div>`; $('lab_result').style.display=''; }
function setAlt(disabled){ ['lab_submit','lab_sim','lab_local'].forEach(id=>$(id).disabled=disabled); $('lab_ibm').disabled=disabled||!HEALTH.ibm_token_present; }

// ===================== xpyq queue lifecycle ==================================
const LAB={runId:null,tickers:[],startTs:0,polls:0,startPos:null,pollTimer:null,roundTimer:null,elapsedTimer:null,done:false};
function labSet(id,v){$(id).textContent=v;}
function labBadge(t,live){const b=$('lab_badge');b.textContent=t;b.className='badge'+(live?' live':'');}
function labShow(id,on){$(id).style.display=on?'':'none';}
function labStopTimers(){clearInterval(LAB.pollTimer);clearTimeout(LAB.roundTimer);LAB.pollTimer=LAB.roundTimer=null;}
function labStopAll(){labStopTimers();clearInterval(LAB.elapsedTimer);LAB.elapsedTimer=null;}
function labElapsed(){return Math.round((performance.now()-LAB.startTs)/1000);}
function labTickElapsed(){labSet('q_elapsed',labElapsed()+'s');}
function labProgress(pos,status){
  if(status==='completed'){$('q_bar').style.width='100%';return;}
  if(status==='running'||pos===0){$('q_bar').style.width='92%';return;}
  if(pos==null||LAB.startPos==null||LAB.startPos<=0){$('q_bar').style.width='8%';return;}
  const adv=Math.max(0,LAB.startPos-pos);
  $('q_bar').style.width=Math.min(95,8+adv/LAB.startPos*87).toFixed(0)+'%';
}

async function labSubmit(){
  labStopAll();
  Object.assign(LAB,{runId:null,polls:0,startPos:null,done:false,startTs:performance.now()});
  setAlt(true); labShow('lab_result',false); labShow('lab_decide',false); labBadge('submitting…');
  try{
    const r=await fetch('/api/xpyq/submit',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(collectParams())});
    const d=await r.json();
    if(!r.ok||d.error||!d.run_id) throw new Error(d.error||'submit failed (no run_id)');
    LAB.runId=d.run_id; LAB.tickers=d.tickers; LAB.startPos=d.queue_position;
    labShow('lab_live',true);
    labSet('q_status',d.status||'queued');
    labSet('q_pos',d.queue_position??'—'); labSet('q_polls','0'); labSet('q_elapsed','0s');
    labBadge('on xpyq',true);
    $('lab_note').textContent='Submitted to xpyq · run '+d.run_id.slice(0,8)+'… · N='+d.N+', K='+d.K;
    LAB.elapsedTimer=setInterval(labTickElapsed,1000); labStartRound();
  }catch(e){ labBadge('error'); setAlt(false); $('lab_note').innerHTML='<span style="color:var(--bad)">'+esc(e.message)+'</span>'; labShow('lab_live',true); }
}

function labStartRound(){
  labShow('lab_decide',false);
  LAB.pollTimer=setInterval(labPollOnce,2000); labPollOnce();
  LAB.roundTimer=setTimeout(labEndRound,(+$('queue_round').value)*1000);
}

function labEndRound(){
  clearInterval(LAB.pollTimer); LAB.pollTimer=null; if(LAB.done) return;
  $('lab_decide_msg').innerHTML='Still on xpyq — status <b>'+esc($('q_status').textContent)+'</b>, queue position <b>'+esc($('q_pos').textContent)+'</b> after '+labElapsed()+'s. Keep waiting, or use the instant local solver?';
  $('qr_lbl').textContent=$('queue_round').value; labShow('lab_decide',true);
}

async function labPollOnce(){
  if(!LAB.runId||LAB.done) return;
  LAB.polls++; labSet('q_polls',LAB.polls);
  try{
    const r=await fetch('/api/xpyq/status',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({run_id:LAB.runId,tickers:LAB.tickers})});
    const d=await r.json(); if(d.error) throw new Error(d.error);
    labSet('q_status',d.status||'—');
    labSet('q_pos',d.queue_position??(d.status==='running'?'running':'—'));
    labProgress(d.queue_position,d.status);
    if(['completed','failed','timed_out','cancelled'].includes(d.status)) labFinish(d);
  }catch(e){ $('lab_note').innerHTML='<span style="color:var(--bad)">poll error: '+esc(e.message)+'</span>'; }
}

function labFinish(d){
  LAB.done=true; labStopAll(); labShow('lab_decide',false); setAlt(false); labTickElapsed();
  if(d.status==='completed'&&d.result){
    labProgress(null,'completed'); labBadge('xpyq completed ✓',true);
    quantumResult('xpyq compute',{...d.result,backend:'xpyq hardware',credits:d.credits_charged,
      boards:d.boards_used?Object.keys(d.boards_used).join(','):null,duration:d.duration_ms},'var(--quantum)');
  }else{ labBadge(d.status); labErr('xpyq run '+d.status+(d.stdout?': '+d.stdout:' — try another backend.')); }
}

async function labKeepWaiting(){ if(!LAB.done) labStartRound(); }
async function labFallback(){
  labStopAll(); LAB.done=true; labShow('lab_decide',false); labBadge('local fallback');
  if(LAB.runId) fetch('/api/xpyq/cancel',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({run_id:LAB.runId})});
  $('lab_note').textContent='Cancelled the xpyq run; solving locally…'; await solveLocal(); setAlt(false);
}

// ===================== standalone backends: simulator / local / IBM ==========
async function solveLocal(){
  try{
    const r=await fetch('/api/qubo/local',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(collectParams())});
    const d=await r.json(); if(!r.ok||d.error) throw new Error(d.error||'local solve failed');
    quantumResult('local brute force',d,'var(--ml)');
  }catch(e){ labErr(e.message); }
}

async function labLocal(){ setAlt(true); labBadge('local solve'); await solveLocal(); setAlt(false); }

async function labSim(){
  setAlt(true); labBadge('simulator…'); labShow('lab_result',true);
  $('lab_result').innerHTML='<div class="reason"><span class="spin"></span> running QAOA circuit on the Aer simulator…</div>';
  try{
    const r=await fetch('/api/quantum/simulate',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(collectParams())});
    const d=await r.json(); if(!r.ok||d.error) throw new Error(d.error||'simulate failed');
    labBadge('simulator ✓',true); quantumResult('Aer simulator (QAOA p=1)',d,'var(--accent)');
  }catch(e){ labBadge('error'); labErr(e.message); } finally{ setAlt(false); }
}

const IBMJOB={jobId:null,backend:null,params:null,timer:null,startTs:0,polls:0,done:false};
async function loadIbmDevices(){
  try{
    const r=await fetch('/api/quantum/ibm/devices',{method:'POST',headers:{'Content-Type':'application/json'},body:'{}'});
    const d=await r.json(); if(d.error||!d.devices) return;
    const sel=$('ibm_device'); const cur=sel.value;
    sel.innerHTML='<option value="">IBM QPU: least-busy device</option>'+
      d.devices.map(x=>`<option value="${esc(x.name)}">${esc(x.name)} · ${x.num_qubits}q · ${x.pending_jobs} queued</option>`).join('');
    sel.value=cur;
  }catch(e){}
}

function ibmRender(status,extra){
  const el=$('lab_ibmjob'); el.style.display='';
  const spin=IBMJOB.done?'':'<span class="spin"></span> ';
  el.innerHTML=`<div class="lbl" style="margin-top:0">IBM QPU job</div>
    <div class="stat" style="grid-template-columns:repeat(3,1fr)">
      <div class="box"><div class="v" style="font-size:15px">${spin}${esc(status)}</div><div class="k">status</div></div>
      <div class="box"><div class="v" style="font-size:14px">${esc(IBMJOB.backend||'—')}</div><div class="k">backend</div></div>
      <div class="box"><div class="v">${Math.round((performance.now()-IBMJOB.startTs)/1000)}s</div><div class="k">elapsed · ${IBMJOB.polls} polls</div></div>
    </div>
    <div class="reason" style="color:var(--mut)">job ${esc((IBMJOB.jobId||'').slice(0,12))}… ${extra||''}</div>
    ${IBMJOB.done?'':'<button class="run alt" id="ibm_stop" style="margin-top:8px">■ Stop polling (use local instead)</button>'}`;
  if(!IBMJOB.done) $('ibm_stop').onclick=ibmStop;
}

function ibmStopTimers(){ clearInterval(IBMJOB.timer); IBMJOB.timer=null; }

async function labIbm(){
  if(!HEALTH.ibm_token_present){ labErr('No IBM token configured on the server.'); return; }
  setAlt(true); labBadge('IBM: submitting…',true); await loadIbmDevices();
  Object.assign(IBMJOB,{jobId:null,params:collectParams(),polls:0,done:false,startTs:performance.now()});
  $('lab_ibmjob').style.display=''; $('lab_ibmjob').innerHTML='<div class="reason"><span class="spin"></span> submitting QAOA circuit to a real IBM QPU…</div>';
  try{
    const r=await fetch('/api/quantum/ibm/submit',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(IBMJOB.params)});
    const d=await r.json(); if(!r.ok||d.error||!d.job_id) throw new Error(d.error||'IBM submit failed');
    IBMJOB.jobId=d.job_id; IBMJOB.backend=d.backend; labBadge('on IBM QPU',true);
    ibmRender('QUEUED','(real hardware — this can take minutes)');
    IBMJOB.timer=setInterval(ibmPoll,5000); ibmPoll();
  }catch(e){ labBadge('error'); setAlt(false); $('lab_ibmjob').innerHTML='<div class="err">'+esc(e.message)+'</div>'; }
}

async function ibmPoll(){
  if(!IBMJOB.jobId||IBMJOB.done) return;
  IBMJOB.polls++;
  try{
    const r=await fetch('/api/quantum/ibm/status',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({...IBMJOB.params,job_id:IBMJOB.jobId})});
    const d=await r.json(); if(d.error) throw new Error(d.error);
    if(['DONE','ERROR','CANCELLED','FAILED'].includes(d.status)){
      IBMJOB.done=true; ibmStopTimers(); setAlt(false); ibmRender(d.status);
      if(d.status==='DONE'&&d.result){ labBadge('IBM done ✓',true);
        quantumResult('IBM QPU · '+(d.backend||''),{...d.result,backend:d.backend,n_outcomes:d.n_outcomes,top_counts:d.top_counts},'var(--ibm)'); }
      else labErr('IBM job '+d.status+' (no counts returned).');
    } else { ibmRender(d.status||'QUEUED'); }
  }catch(e){ ibmRender('poll error','<span style="color:var(--bad)">'+esc(e.message)+'</span>'); }
}

function ibmStop(){ IBMJOB.done=true; ibmStopTimers(); setAlt(false); labBadge('IBM polling stopped'); ibmRender('stopped'); labLocal(); }

$('lab_submit').addEventListener('click',labSubmit);
$('lab_keep').addEventListener('click',labKeepWaiting);
$('lab_fallback').addEventListener('click',labFallback);
$('lab_sim').addEventListener('click',labSim);
$('lab_local').addEventListener('click',labLocal);
$('lab_ibm').addEventListener('click',labIbm);
loadIbmDevices();
