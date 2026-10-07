(() => {
  const RAW = 'https://raw.githubusercontent.com/PkLavc/Autotrader/main/data/';
  const money = new Intl.NumberFormat('en-US',{style:'currency',currency:'USD',maximumFractionDigits:2});
  const num = new Intl.NumberFormat('en-US',{maximumFractionDigits:4});

  const $ = id => document.getElementById(id);
  const pct = (v, digits=2) => v == null || !Number.isFinite(Number(v)) ? '—' : (Number(v)*100).toFixed(digits)+'%';
  const cls = v => Number(v) > 0 ? 'positive' : Number(v) < 0 ? 'negative' : '';
  const safe = v => String(v ?? '').replace(/[&<>"']/g, m=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[m]));

  async function read(name){
    const candidates = ['./data/'+name, RAW+name];
    let last;
    for(const url of candidates){
      try{
        const r=await fetch(url+'?t='+Date.now(),{cache:'no-store'});
        if(r.ok) return await r.json();
        last=new Error(r.status+' '+r.statusText);
      }catch(e){last=e}
    }
    throw last || new Error('Unable to load '+name);
  }

  function setSigned(el, value, formatter=pct){
    el.textContent=formatter(value);
    el.classList.remove('positive','negative');
    const c=cls(value); if(c) el.classList.add(c);
  }

  function drawEquity(rows){
    const canvas=$('equityChart'), empty=$('equityEmpty');
    const points=(rows||[]).map(r=>({t:new Date(r.timestamp),v:Number(r.nav)})).filter(p=>Number.isFinite(p.v));
    if(!points.length){empty.hidden=false;return}
    empty.hidden=true;
    const box=canvas.getBoundingClientRect(), dpr=Math.max(1,Math.min(2,window.devicePixelRatio||1));
    canvas.width=Math.floor(box.width*dpr); canvas.height=Math.floor(box.height*dpr);
    const ctx=canvas.getContext('2d'); ctx.scale(dpr,dpr);
    const w=box.width,h=box.height,pad={l:54,r:14,t:12,b:28};
    const vals=points.map(p=>p.v), min0=Math.min(...vals), max0=Math.max(...vals);
    const spread=Math.max(1,max0-min0), min=min0-spread*.12,max=max0+spread*.12;
    const x=i=>points.length===1?(pad.l+(w-pad.l-pad.r)/2):pad.l+i*(w-pad.l-pad.r)/(points.length-1);
    const y=v=>pad.t+(max-v)*(h-pad.t-pad.b)/(max-min);
    ctx.clearRect(0,0,w,h);
    ctx.strokeStyle='#202733';ctx.lineWidth=1;ctx.fillStyle='#6f7a88';ctx.font='10px ui-monospace, monospace';
    for(let i=0;i<5;i++){const yy=pad.t+i*(h-pad.t-pad.b)/4;ctx.beginPath();ctx.moveTo(pad.l,yy);ctx.lineTo(w-pad.r,yy);ctx.stroke();const val=max-i*(max-min)/4;ctx.fillText('$'+Math.round(val).toLocaleString(),4,yy+3)}
    if(points.length===1){ctx.fillStyle='#2ee59d';ctx.beginPath();ctx.arc(x(0),y(points[0].v),4,0,Math.PI*2);ctx.fill();return}
    const grad=ctx.createLinearGradient(0,pad.t,0,h-pad.b);grad.addColorStop(0,'rgba(46,229,157,.28)');grad.addColorStop(1,'rgba(46,229,157,0)');
    ctx.beginPath();points.forEach((p,i)=>{const xx=x(i),yy=y(p.v);i?ctx.lineTo(xx,yy):ctx.moveTo(xx,yy)});ctx.lineTo(x(points.length-1),h-pad.b);ctx.lineTo(x(0),h-pad.b);ctx.closePath();ctx.fillStyle=grad;ctx.fill();
    ctx.beginPath();points.forEach((p,i)=>{const xx=x(i),yy=y(p.v);i?ctx.lineTo(xx,yy):ctx.moveTo(xx,yy)});ctx.strokeStyle='#2ee59d';ctx.lineWidth=2;ctx.stroke();
    ctx.fillStyle='#75808e';ctx.font='9px ui-monospace, monospace';const first=points[0].t,last=points.at(-1).t;ctx.fillText(first.toLocaleDateString(),pad.l,h-7);const label=last.toLocaleDateString();ctx.fillText(label,w-pad.r-ctx.measureText(label).width,h-7);
  }

  function renderForecasts(forecasts){
    const root=$('forecastBars'), list=(forecasts||[]).slice(0,20);
    const max=Math.max(.001,...list.map(f=>Math.abs(Number(f.expected_return)||0)));
    root.innerHTML=list.map(f=>{
      const v=Number(f.expected_return)||0,w=Math.max(.5,Math.abs(v)/max*50);
      return '<div class="forecast-row"><span class="forecast-symbol">'+safe(f.symbol)+'</span><div class="bar-track"><i class="bar-fill '+(v>=0?'up':'down')+'" style="width:'+w+'%"></i></div><span class="forecast-value '+cls(v)+'">'+(v>=0?'+':'')+(v*100).toFixed(2)+'%</span></div>';
    }).join('') || '<p class="subtle">No forecast data.</p>';
  }

  function renderPositions(latest){
    const root=$('positionsList'), positions=latest.positions||{}, prices=Object.fromEntries((latest.forecasts||[]).map(f=>[f.symbol,Number(f.current_close)]));
    const nav=Number(latest.nav)||1;
    const rows=Object.entries(positions).map(([symbol,qty])=>({symbol,qty:Number(qty),value:Number(qty)*(prices[symbol]||0)})).sort((a,b)=>b.value-a.value);
    root.innerHTML=rows.map(r=>{
      const weight=Math.max(0,Math.min(100,r.value/nav*100));
      return '<div class="position"><div class="position-top"><strong>'+safe(r.symbol)+'</strong><span>'+money.format(r.value)+' · '+weight.toFixed(1)+'%</span></div><div class="position-track"><i style="width:'+weight+'%"></i></div></div>';
    }).join('') || '<p class="subtle">Portfolio is currently in cash.</p>';
  }

  function renderTrades(trades){
    const body=$('tradesBody'), rows=(trades||[]).slice().reverse().slice(0,100);
    body.innerHTML=rows.map(t=>{
      const buy=String(t.side).toUpperCase()==='BUY', q=Number(t.qty)||0,p=Number(t.price)||0;
      return '<tr><td><span class="trade-action '+(buy?'positive':'negative')+'"><b class="trade-arrow">'+(buy?'↗':'↘')+'</b>'+safe(t.side)+'</span></td><td><strong>'+safe(t.symbol)+'</strong></td><td>'+num.format(q)+'</td><td>'+money.format(p)+'</td><td>'+money.format(q*p)+'</td><td>'+new Date(t.timestamp).toLocaleString()+'</td></tr>';
    }).join('') || '<tr><td colspan="6" class="loading-cell">No trades recorded yet.</td></tr>';
  }

  async function load(){
    try{
      const [state,latest]=await Promise.all([read('state.json'),read('latest.json')]);
      const metrics=latest.metrics||{};
      $('navValue').textContent=money.format(Number(latest.nav)||0);
      setSigned($('returnValue'),Number(metrics.total_return));
      const history=state.nav_history||[];
      const prior=history.length>1?Number(history[history.length-2].nav):Number(metrics.initial_cash||latest.nav);
      const delta=prior?Number(latest.nav)/prior-1:0;
      $('navDelta').textContent=(delta>=0?'▲ ':'▼ ')+Math.abs(delta*100).toFixed(2)+'% last run';$('navDelta').className=cls(delta);
      $('cashValue').textContent=money.format(Number(latest.cash)||0);
      $('cashShare').textContent=(latest.nav?Number(latest.cash)/Number(latest.nav)*100:0).toFixed(1)+'% of portfolio';
      $('positionsValue').textContent=Object.keys(latest.positions||{}).length;
      $('tradesValue').textContent=metrics.trade_count ?? (state.trades||[]).length;
      setSigned($('drawdownValue'),Number(metrics.max_drawdown));
      setSigned($('benchmarkValue'),metrics.benchmark_return==null?NaN:Number(metrics.benchmark_return));
      $('lastRun').textContent=new Date(latest.generated_at).toLocaleString();
      $('modelName').textContent=String(latest.model||'Kronos-base').replace('NeoQuasar/','');
      renderForecasts(latest.forecasts);
      renderPositions(latest);
      renderTrades(state.trades);
      drawEquity(history);
      window.addEventListener('resize',()=>drawEquity(history),{passive:true});
    }catch(err){
      console.error(err);
      $('tradesBody').innerHTML='<tr><td colspan="6" class="loading-cell">Dashboard data is temporarily unavailable.</td></tr>';
    }
  }
  load();
})();