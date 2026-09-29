const $ = (id) => document.getElementById(id);
const nf = new Intl.NumberFormat('zh-CN');
let polling = null;
let selectedWindow = '24h';
let loadToken = 0;
let lastRenderKey = null;
let selectedVideo = null;
let selectedMetric = 'view';
let selectedMode = 'rate';
let cachedData = null;

function escapeHtml(value) {
  return String(value ?? '').replace(/[&<>"']/g, (char) => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[char]));
}
function cover(video, extraClass='') {
  const source=video.cover ? `<img class="cover-image" src="${escapeHtml(video.cover)}" loading="eager" referrerpolicy="no-referrer" alt="">` : '';
  return `<span class="video-cover ${extraClass} ${video.cover?'':'cover-unavailable'}"><span class="cover-fallback" aria-hidden="true">▶</span>${source}</span>`;
}
function number(value) { return value == null ? '—' : nf.format(value); }
function signed(value) { return value == null ? '暂无对比' : `${value > 0 ? '+' : ''}${nf.format(value)}`; }
function when(iso) { return iso ? new Date(iso).toLocaleString('zh-CN', {timeZone:'Asia/Shanghai',month:'numeric',day:'numeric',hour:'2-digit',minute:'2-digit',hour12:false}) : '—'; }
function elapsed(hours) {
  if (hours == null) return '暂无对比';
  if (hours < 1) return `${Math.max(1, Math.round(hours * 60))} 分钟`;
  return `${hours.toFixed(1)} 小时`;
}
function icon(name) {
  const paths = {
    fans:'<circle cx="9" cy="8" r="3"/><path d="M3 19v-2a6 6 0 0 1 12 0v2M17 5a3 3 0 0 1 0 6M18 14a5 5 0 0 1 3 5"/>',
    rise:'<path d="M3 17l6-6 4 4 8-8M16 7h5v5"/>',
    play:'<rect x="3" y="4" width="18" height="16" rx="2"/><path d="m10 8 6 4-6 4z"/>',
    heart:'<path d="M20.8 4.6a5.5 5.5 0 0 0-7.8 0L12 5.7l-1.1-1.1a5.5 5.5 0 0 0-7.8 7.8L12 21l8.8-8.6a5.5 5.5 0 0 0 0-7.8z"/>',
    check:'<circle cx="12" cy="12" r="9"/><path d="m8 12 3 3 5-6"/>'
  };
  return `<svg viewBox="0 0 24 24" aria-hidden="true">${paths[name]}</svg>`;
}
function kpi(label,value,note,iconName,primary=false,tone='') {
  return `<article class="kpi-card"><div class="kpi-top"><span class="kpi-icon ${primary?'primary':''}">${icon(iconName)}</span><span class="kpi-label">${label}</span></div><div class="kpi-value">${value}</div><div class="kpi-note ${tone}">${note}</div></article>`;
}
function drawFanChart(history) {
  const target = $('fan-chart');
  const points = history.filter((item) => item.followers != null);
  if (points.length < 2) { target.innerHTML = '<div class="chart-empty">当前窗口暂无两次有效采样</div>'; return; }
  const width=690,height=210,left=42,right=18,top=13,bottom=29;
  const baseline=points[0].followers;
  const values=points.map((p)=>p.followers-baseline);
  const min=Math.min(0,...values),max=Math.max(0,...values);
  const bound=Math.max(1,Math.abs(min),Math.abs(max));
  const magnitude=10**Math.floor(Math.log10(bound));
  const step=Math.ceil(bound/(2*magnitude))*magnitude;
  const lo=Math.floor(min/step)*step;
  const hi=max===0 && min<0 ? 0 : Math.max(step,Math.ceil(max/step)*step);
  const times=points.map((p)=>new Date(p.at).getTime());
  const first=times[0],timeSpan=Math.max(1,times.at(-1)-first);
  const x=(i)=>left+(times[i]-first)/timeSpan*(width-left-right);
  const y=(v)=>top+(hi-v)/(hi-lo)*(height-top-bottom);
  const coords=points.map((p,i)=>[x(i),y(values[i])]);
  const line=coords.map((p,i)=>`${i?'L':'M'}${p[0].toFixed(1)},${p[1].toFixed(1)}`).join(' ');
  const area=`${line} L${coords.at(-1)[0]},${height-bottom} L${coords[0][0]},${height-bottom} Z`;
  const grids=[0,.5,1].map((f)=>{const value=lo+(hi-lo)*(1-f);const yy=top+(height-top-bottom)*f;return `<line class="chart-grid-line" x1="${left}" x2="${width-right}" y1="${yy}" y2="${yy}"/><text class="chart-label" x="0" y="${yy+4}">${signed(Math.round(value))}</text>`}).join('');
  const labels=[0,points.length-1].map((i)=>`<text class="chart-label" x="${x(i)}" y="${height-3}" text-anchor="${i?'end':'start'}">${when(points[i].at)}</text>`).join('');
  const dots=coords.map((p,i)=>`<circle class="chart-dot" cx="${p[0]}" cy="${p[1]}" r="4"><title>${when(points[i].at)} · ${number(points[i].followers)} 粉丝 · ${signed(values[i])}</title></circle>`).join('');
  target.innerHTML=`<svg viewBox="0 0 ${width} ${height}" role="img" aria-label="粉丝从 ${number(baseline)} 变化到 ${number(points.at(-1).followers)}，净增 ${signed(values.at(-1))}"><defs><linearGradient id="areaGradient" x1="0" x2="0" y1="0" y2="1"><stop stop-color="#00a6df" stop-opacity=".17"/><stop offset="1" stop-color="#00a6df" stop-opacity="0"/></linearGradient></defs>${grids}<path class="chart-area" d="${area}"/><path class="chart-line" d="${line}"/>${dots}${labels}</svg>`;
}
function renderGrowth(videos) {
  const ranked=videos.filter((v)=>v.deltas.view!=null).slice().sort((a,b)=>b.deltas.view-a.deltas.view).slice(0,5);
  if (!ranked.length) { $('growth-list').innerHTML='<div class="empty">等待第二次有效采样</div>'; return; }
  const max=Math.max(1,...ranked.map((v)=>Math.max(0,v.deltas.view)));
  $('growth-list').innerHTML=ranked.map((v,i)=>`<div class="growth-row"><span class="growth-rank">${String(i+1).padStart(2,'0')}</span>${cover(v,'rank-cover')}<div class="growth-info"><div class="growth-name" title="${escapeHtml(v.title)}">${escapeHtml(v.title)}</div><div class="growth-track"><div class="growth-fill" style="width:${Math.max(2,Math.max(0,v.deltas.view)/max*100)}%"></div></div></div><span class="growth-number">${signed(v.deltas.view)}</span></div>`).join('');
}
function renderBreakdown(data) {
  const fields=[['like','点赞'],['coin','投币'],['favorite','收藏'],['reply','评论'],['danmaku','弹幕'],['share','分享']];
  const rows=fields.map(([key,label])=>({key,label,value:data.interaction_breakdown[key]}));
  const max=Math.max(1,...rows.map((row)=>Math.abs(row.value || 0)));
  $('interaction-breakdown').innerHTML=rows.map((row)=>`<div class="breakdown-row"><span>${row.label}</span><div class="breakdown-track"><div class="breakdown-fill ${row.value<0?'negative':''}" style="width:${row.value==null?0:Math.max(2,Math.abs(row.value)/max*100)}%"></div></div><strong class="${row.value<0?'negative':''}">${signed(row.value)}</strong></div>`).join('');
}
function renderIntervals(intervals) {
  $('interval-count').textContent=`${intervals.length} 个区间`;
  $('interval-list').innerHTML=intervals.length ? intervals.slice().reverse().map((item)=>{
    const hours=(new Date(item.to)-new Date(item.from))/3600000;
    const speed=item.views!=null && hours>0 ? `区间均速 ${number(Math.round(item.views/hours))}/小时` : '';
    return `<div class="interval-row"><div class="interval-time"><strong>${when(item.to)}</strong><span>较 ${when(item.from)} · ${elapsed(item.hours)}</span></div><div class="interval-metrics"><div><span>粉丝</span><strong>${signed(item.followers)}</strong></div><div><span>播放</span><strong>${signed(item.views)}</strong><small>${speed}</small></div><div><span>互动</span><strong>${signed(item.interactions)}</strong></div></div></div>`;
  }).join('') : '<div class="empty">当前窗口暂无完整采样区间</div>';
}
function videoPoints(video, metric) {
  return (video.history || []).filter((point)=>point.metrics[metric] != null).map((point)=>({at:point.at,value:point.metrics[metric]}));
}
function videoIntervals(points) {
  return points.slice(1).map((point,index)=>{
    const prior=points[index];
    const hours=(new Date(point.at)-new Date(prior.at))/3600000;
    return {from:prior.at,to:point.at,delta:point.value-prior.value,hours,rate:hours>0?(point.value-prior.value)/hours:null};
  }).filter((item)=>item.rate!=null);
}
function niceAxis(minimum,maximum) {
  const range=Math.max(1,maximum-minimum);
  const magnitude=10**Math.floor(Math.log10(range));
  const step=Math.ceil(range/(2*magnitude))*magnitude;
  const low=Math.floor(minimum/step)*step;
  const high=Math.max(low+step,Math.ceil(maximum/step)*step);
  return {low,high};
}
function miniTrend(video) {
  const intervals=videoIntervals(videoPoints(video,'view'));
  if (!intervals.length) return '<span class="mini-empty">—</span>';
  const width=84,height=34,slot=width/intervals.length,bar=Math.min(13,slot*.45);
  const maximum=Math.max(1,...intervals.map((item)=>Math.abs(item.rate)));
  const rects=intervals.map((item,index)=>{
    const barHeight=Math.max(2,Math.abs(item.rate)/maximum*27);
    const x=index*slot+(slot-bar)/2,y=height-barHeight;
    return `<rect x="${x.toFixed(1)}" y="${y.toFixed(1)}" width="${bar.toFixed(1)}" height="${barHeight.toFixed(1)}" rx="2" fill="${item.rate<0?'#df7d83':'#20b6e4'}"><title>${when(item.from)} → ${when(item.to)} · ${elapsed(item.hours)} · 播放 ${signed(item.delta)} · 均速 ${signed(Math.round(item.rate))}/小时</title></rect>`;
  }).join('');
  return `<svg viewBox="0 0 ${width} ${height}" role="img" aria-label="${escapeHtml(video.title)}的播放增速走势">${rects}</svg>`;
}
function renderVideoTrend(data) {
  const videos=data.videos || [];
  if (!videos.length) return;
  if (!videos.some((video)=>video.bvid===selectedVideo)) selectedVideo=videos[0].bvid;
  const select=$('trend-video-select');
  select.innerHTML=videos.map((video)=>`<option value="${escapeHtml(video.bvid)}">${escapeHtml(video.title)}</option>`).join('');
  select.value=selectedVideo;
  const picker=$('video-picker'),pickerScroll=picker.scrollLeft;
  picker.innerHTML=videos.map((item)=>`<button type="button" class="video-pick ${item.bvid===selectedVideo?'active':''}" data-bvid="${escapeHtml(item.bvid)}" aria-pressed="${item.bvid===selectedVideo}" title="${escapeHtml(item.title)}">${cover(item,'pick-cover')}<span class="pick-title">${escapeHtml(item.title)}</span><span class="pick-growth">播放 ${signed(item.deltas.view)}</span></button>`).join('');
  picker.scrollLeft=pickerScroll;
  $('trend-metric-select').value=selectedMetric;
  const video=videos.find((item)=>item.bvid===selectedVideo);
  const points=videoPoints(video,selectedMetric);
  const intervals=videoIntervals(points);
  const metricLabel=$('trend-metric-select').selectedOptions[0].textContent;
  const change=points.length>1 ? points.at(-1).value-points[0].value : null;
  const hours=points.length>1 ? (new Date(points.at(-1).at)-new Date(points[0].at))/3600000 : null;
  $('video-history-count').textContent=`${points.length} 次采样`;
  $('video-trend-summary').innerHTML=`<div><span>当前累计${metricLabel}</span><strong>${points.length?number(points.at(-1).value):'—'}</strong></div><div><span>区间${metricLabel}增长</span><strong class="accent">${signed(change)}</strong></div><div><span>实际覆盖</span><strong>${elapsed(hours)}</strong></div>`;
  const target=$('video-trend-chart');
  if (selectedMode==='rate') {
    $('trend-explanation').textContent='每根柱表示相邻两次有效采样之间的增量 ÷ 实际间隔小时；这是区间均速，不是实时速度。悬停可看原始增量和时长。';
    if (!intervals.length) { target.innerHTML='<div class="chart-empty">至少需要两次采样才能计算区间均速</div>'; return; }
    const width=900,height=258,left=66,right=22,top=25,bottom=51;
    const rates=intervals.map((item)=>item.rate);
    const axis=niceAxis(Math.min(0,...rates),Math.max(0,...rates));
    const y=(value)=>top+(axis.high-value)/(axis.high-axis.low)*(height-top-bottom);
    const zero=y(0),slot=(width-left-right)/intervals.length,barWidth=Math.min(78,slot*.35);
    const grids=[axis.high,(axis.high+axis.low)/2,axis.low].map((value)=>`<line class="detail-grid" x1="${left}" x2="${width-right}" y1="${y(value)}" y2="${y(value)}"/><text class="detail-axis" x="${left-9}" y="${y(value)+4}" text-anchor="end">${signed(Math.round(value))}</text>`).join('');
    const bars=intervals.map((item,index)=>{
      const center=left+slot*(index+.5),yValue=y(item.rate),barHeight=Math.max(2,Math.abs(yValue-zero));
      const topY=item.rate>=0?Math.min(yValue,zero):zero;
      return `<g><rect class="detail-bar ${item.rate<0?'negative':''}" x="${center-barWidth/2}" y="${topY}" width="${barWidth}" height="${barHeight}" rx="5"><title>${when(item.from)} → ${when(item.to)} · ${elapsed(item.hours)} · 增量 ${signed(item.delta)} · 均速 ${signed(Math.round(item.rate))}/小时</title></rect><text class="detail-value" x="${center}" y="${item.rate>=0?Math.max(16,topY-8):Math.min(height-bottom-3,topY+barHeight+14)}" text-anchor="middle">${signed(Math.round(item.rate))}</text><text class="detail-axis" x="${center}" y="${height-21}" text-anchor="middle">${when(item.to)}</text><text class="detail-axis" x="${center}" y="${height-5}" text-anchor="middle">${elapsed(item.hours)}</text></g>`;
    }).join('');
    target.innerHTML=`<svg viewBox="0 0 ${width} ${height}" role="img" aria-label="${metricLabel}区间均速图">${grids}<line class="zero-line" x1="${left}" x2="${width-right}" y1="${zero}" y2="${zero}"/>${bars}</svg>`;
  } else {
    $('trend-explanation').textContent='圆点是实际采样值，横轴按真实时间间隔绘制；虚线只辅助阅读，不代表两次采样之间有实测数据。';
    if (points.length<2) { target.innerHTML='<div class="chart-empty">至少需要两次采样才能显示累计趋势</div>'; return; }
    const width=900,height=258,left=66,right=22,top=20,bottom=42;
    const values=points.map((point)=>point.value),axis=niceAxis(Math.min(...values),Math.max(...values));
    const start=new Date(points[0].at).getTime(),span=Math.max(1,new Date(points.at(-1).at).getTime()-start);
    const x=(point)=>left+(new Date(point.at).getTime()-start)/span*(width-left-right);
    const y=(value)=>top+(axis.high-value)/(axis.high-axis.low)*(height-top-bottom);
    const grids=[axis.high,(axis.high+axis.low)/2,axis.low].map((value)=>`<line class="detail-grid" x1="${left}" x2="${width-right}" y1="${y(value)}" y2="${y(value)}"/><text class="detail-axis" x="${left-9}" y="${y(value)+4}" text-anchor="end">${number(Math.round(value))}</text>`).join('');
    const line=points.map((point,index)=>`${index?'L':'M'}${x(point).toFixed(1)},${y(point.value).toFixed(1)}`).join(' ');
    const dots=points.map((point)=>`<circle class="detail-dot" cx="${x(point)}" cy="${y(point.value)}" r="5"><title>${when(point.at)} · 累计${metricLabel} ${number(point.value)}</title></circle>`).join('');
    const labels=[points[0],points.at(-1)].map((point,index)=>`<text class="detail-axis" x="${x(point)}" y="${height-8}" text-anchor="${index?'end':'start'}">${when(point.at)}</text>`).join('');
    target.innerHTML=`<svg viewBox="0 0 ${width} ${height}" role="img" aria-label="${metricLabel}累计值趋势图">${grids}<path class="detail-line" d="${line}"/>${dots}${labels}</svg>`;
  }
}
function renderVideos(videos) {
  $('video-count').textContent=`${videos.length} 条视频`;
  $('video-rows').innerHTML=videos.map((v)=>`<tr><td><div class="table-video"><button class="table-cover-button" type="button" data-bvid="${escapeHtml(v.bvid)}" title="查看${escapeHtml(v.title)}的趋势">${cover(v,'table-cover')}</button><div class="table-video-text"><a class="video-title" href="${v.url}" target="_blank" rel="noopener noreferrer" title="${escapeHtml(v.title)}">${escapeHtml(v.title)}</a><div class="video-id">${escapeHtml(v.bvid)}</div></div></div></td><td>${number(v.metrics.view)}</td><td><span class="delta ${v.deltas.view==null?'muted':v.deltas.view<0?'negative':''}">${signed(v.deltas.view)}</span></td><td><button class="spark-button" type="button" data-bvid="${escapeHtml(v.bvid)}" title="查看${escapeHtml(v.title)}的完整趋势">${miniTrend(v)}</button></td><td><span class="delta ${v.deltas.like==null?'muted':''}">${signed(v.deltas.like)}</span></td><td><span class="delta ${v.deltas.reply==null?'muted':''}">${signed(v.deltas.reply)}</span></td><td><span class="delta ${v.deltas.favorite==null?'muted':''}">${signed(v.deltas.favorite)}</span></td><td>${escapeHtml(v.published?.slice(0,10) || '—')}</td></tr>`).join('');
}
function renderStatus(data) {
  const state=data.status || {};
  const collection=state.running ? state.kind==='danmaku'?'正在采集弹幕':'正在采集' : state.last_error ? '上次采集失败' : state.danmaku_error ? '弹幕部分失败' : '最近一次采样可用';
  const tone=state.last_error||state.danmaku_error?'bad':'good';
  const problem=state.last_error||state.danmaku_error;
  $('status-grid').innerHTML=`<div class="status-item"><span>当前监测覆盖</span><strong>${data.matched_videos} / ${data.videos.length} 条可计算增长</strong></div><div class="status-item"><span>历史采样</span><strong>${data.snapshot_count} 次 · 最近 ${when(data.sampled_at)}</strong></div><div class="status-item"><span>采集任务</span><strong class="${tone}">${collection}</strong>${problem?`<small>${escapeHtml(problem)}</small>`:''}</div><div class="status-item"><span>下次计划采集</span><strong>${when(state.next_scheduled)} · 北京时间</strong></div>`;
}
function render(data) {
  if (data.empty) { $('updated-at').textContent='暂无采样'; $('kpi-grid').innerHTML='<div class="empty">暂无数据，点击“立即采集”开始记录</div>'; return; }
  $('updated-at').textContent=`最近采样 ${when(data.sampled_at)}`;
  $('history-count').textContent=`${data.follower_history.length} 次采样`;
  const coverage=data.baseline_at ? `${when(data.baseline_at)} → ${when(data.sampled_at)} · 实际覆盖 ${elapsed(data.coverage_hours)}` : '当前窗口暂无较早的完整采样';
  const partial=data.window==='24h' && data.coverage_hours!=null && data.coverage_hours<23.5;
  $('coverage').innerHTML=`<span class="coverage-dot ${partial?'partial':''}"></span>${coverage}${partial?'<strong>未满 24 小时</strong>':''}`;
  const note=data.baseline_at ? `实际覆盖 ${elapsed(data.coverage_hours)}` : '等待下一次完整采样';
  $('kpi-grid').innerHTML=[
    kpi('粉丝总数',number(data.followers),`精确值 · ${when(data.sampled_at)}`,'fans',true),
    kpi('粉丝增长',signed(data.follower_delta),note,'rise',false,data.follower_delta>=0?'positive':'negative'),
    kpi('播放增长',signed(data.view_growth),`${data.matched_videos} 条可比视频 · ${note}`,'play'),
    kpi('互动动作增长',signed(data.interaction_growth),`点赞、投币、收藏、评论等 · ${note}`,'heart'),
    kpi('可比视频',`${data.matched_videos} / ${data.videos.length}`,`最近发布的 ${data.videos.length} 条视频`,'check')
  ].join('');
  cachedData=data;
  drawFanChart(data.follower_history); renderGrowth(data.videos); renderBreakdown(data); renderIntervals(data.intervals); renderVideoTrend(data); renderVideos(data.videos); renderStatus(data);
}
async function load() {
  const token=++loadToken;
  try {
    const response=await fetch(`/api/dashboard?window=${selectedWindow}`,{cache:'no-store'});
    if(!response.ok) throw new Error('数据读取失败');
    const data=await response.json();
    const key=JSON.stringify([data.sampled_at,data.window,data.status?.running,data.status?.kind,data.status?.last_error,data.status?.danmaku_error,data.status?.last_finished,data.status?.next_scheduled]);
    if(token===loadToken && key!==lastRenderKey) { lastRenderKey=key; render(data); window.dispatchEvent(new Event('dashboard:rendered')); }
  }
  catch(error) { $('updated-at').textContent=error.message; }
}
async function collect() {
  const button=$('collect-button'); button.disabled=true; button.lastChild.textContent='采集中…';
  try {
    const response=await fetch('/api/collect',{method:'POST'});
    if(!response.ok && response.status!==409) throw new Error('无法启动采集');
    if(polling) clearInterval(polling);
    polling=setInterval(async()=>{
      const status=await (await fetch('/api/status',{cache:'no-store'})).json();
      if (!status.running) { clearInterval(polling); polling=null; button.disabled=false; button.lastChild.textContent='立即采集'; await load(); window.dispatchEvent(new Event('collection:finished')); }
    },2000);
  } catch(error) { button.disabled=false; button.lastChild.textContent='立即采集'; $('updated-at').textContent=error.message; }
}
function updateSectionNav() {
  const current=location.hash || '#overview';
  const labels={'#overview':'总览','#videos':'视频表现','#danmaku':'弹幕观察','#collection':'采集状态'};
  document.querySelectorAll('.rail-link').forEach((link)=>link.classList.toggle('active',link.getAttribute('href')===current));
  document.querySelector('.topbar-title strong').textContent=labels[current] || '总览';
}
window.addEventListener('hashchange',updateSectionNav);
updateSectionNav();
document.querySelectorAll('.window-tab').forEach((button)=>button.addEventListener('click',()=>{
  selectedWindow=button.dataset.window;
  document.querySelectorAll('.window-tab').forEach((item)=>{item.classList.toggle('active',item===button);item.setAttribute('aria-pressed',String(item===button));});
  load();
}));
function revealSelectedCover() {
  const picker=$('video-picker'),card=picker.querySelector('.video-pick.active');
  if(card) picker.scrollLeft=card.offsetLeft-picker.offsetLeft-12;
}
$('trend-video-select').addEventListener('change',(event)=>{selectedVideo=event.target.value;if(cachedData) renderVideoTrend(cachedData);revealSelectedCover();});
$('video-picker').addEventListener('click',(event)=>{
  const button=event.target.closest('.video-pick');
  if(!button) return;
  selectedVideo=button.dataset.bvid;
  if(cachedData) renderVideoTrend(cachedData);
});
$('trend-metric-select').addEventListener('change',(event)=>{selectedMetric=event.target.value;if(cachedData) renderVideoTrend(cachedData);});
document.querySelectorAll('.mode-tab').forEach((button)=>button.addEventListener('click',()=>{
  selectedMode=button.dataset.mode;
  document.querySelectorAll('.mode-tab').forEach((item)=>{item.classList.toggle('active',item===button);item.setAttribute('aria-pressed',String(item===button));});
  if(cachedData) renderVideoTrend(cachedData);
}));
$('video-rows').addEventListener('click',(event)=>{
  const button=event.target.closest('.spark-button, .table-cover-button');
  if (!button) return;
  selectedVideo=button.dataset.bvid;
  if(cachedData) renderVideoTrend(cachedData);
  revealSelectedCover();
  $('video-trend').scrollIntoView({behavior:'smooth',block:'start'});
});
document.addEventListener('error',(event)=>{
  if(event.target.matches?.('.cover-image')) event.target.closest('.video-cover')?.classList.add('cover-unavailable');
},true);
$('collect-button').addEventListener('click',collect);
load();
setInterval(load, 60000);
