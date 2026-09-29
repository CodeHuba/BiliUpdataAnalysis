(() => {
  const $ = (id) => document.getElementById(id);
  const state = {data: null, selected: null, scope: 'all', kind: 'phrases', token: 0, poll: null};
  let initialPositioned = false;
  const currentWindow = () => document.querySelector('.window-tab.active')?.dataset.window || '24h';
  const minutes = (value) => `${String(Math.floor(value / 60)).padStart(2,'0')}:${String(value % 60).padStart(2,'0')}`;
  const timeAt = (ms) => ms == null ? '时间未知' : minutes(Math.floor(ms / 1000));
  const timeOf = (seconds) => new Date(seconds * 1000).toLocaleString('zh-CN', {timeZone:'Asia/Shanghai',month:'numeric',day:'numeric',hour:'2-digit',minute:'2-digit',hour12:false});
  const message = (text) => `<div class="danmaku-empty">${escapeHtml(text)}</div>`;

  function renderSummary() {
    const data = state.data;
    const rows = data?.videos || [];
    const collected = rows.filter((video) => video.latest_collected_at);
    const stored = rows.reduce((sum, video) => sum + video.stored_count, 0);
    const inWindow = rows.reduce((sum, video) => sum + (video.window_count || 0), 0);
    const good = rows.filter((video) => video.expected_segments > 0 && video.expected_segments === video.successful_segments);
    const currentLabel = currentWindow() === 'latest' ? '最近一次区间' : '最近 24 小时';
    const baselineReady = currentWindow() !== 'latest' || rows.some((row)=>row.has_baseline);
    $('danmaku-kpis').innerHTML = [
      ['已存可读取弹幕', number(stored), '按弹幕 ID 去重后的本机样本'],
      [currentLabel + '发送', collected.length && baselineReady ? number(inWindow) : '—', baselineReady ? '按实际发送时间统计' : '需两轮完整采集建立对比'],
      ['覆盖视频', `${collected.length} / ${rows.length}`, '当前最近 10 条视频'],
      ['完整分段视频', `${good.length} / ${rows.length}`, '失败分段会标明缺口']
    ].map(([label,value,note])=>`<div class="danmaku-kpi"><span>${escapeHtml(label)}</span><strong>${escapeHtml(value)}</strong><small>${escapeHtml(note)}</small></div>`).join('');
    $('danmaku-status').textContent = collected.length
      ? `最近弹幕采集 ${when(collected.map((v)=>v.latest_collected_at).sort().at(-1))} · ${good.length === collected.length ? '已采集视频的分段均完整' : '部分分段不完整，见各视频状态'}`
      : '尚无弹幕内容记录。点击“采集弹幕”建立本机样本；首次采集是历史基线。';
  }

  function heatCells(video) {
    const values = state.scope === 'all' ? video.heatmap_all : video.heatmap_window;
    const max = Math.max(1, ...values);
    return values.map((count,index)=>{
      const strength = count ? .17 + .83 * count / max : .045;
      const start = Math.floor(index / values.length * video.duration_seconds);
      const end = Math.floor((index + 1) / values.length * video.duration_seconds);
      return `<span class="danmaku-heat-cell" style="--heat:${strength.toFixed(3)}" title="${minutes(start)}–${minutes(end)} · ${number(count)} 条"></span>`;
    }).join('');
  }

  function renderHeatmap() {
    const rows = state.data?.videos || [];
    $('danmaku-heatmap').innerHTML = rows.length ? rows.map((video)=>{
      const quality = !video.latest_collected_at ? '未采集' : video.expected_segments !== video.successful_segments ? `分段 ${video.successful_segments}/${video.expected_segments}` : `${number(video.stored_count)} 条`;
      const windowCount = video.window_count == null ? '暂无对比' : `${number(video.window_count)} 条`;
      return `<button type="button" class="danmaku-heat-row ${state.selected === video.bvid ? 'active' : ''}" data-bvid="${escapeHtml(video.bvid)}" aria-pressed="${state.selected === video.bvid}">
        ${cover(video,'danmaku-row-cover')}<span class="danmaku-row-title" title="${escapeHtml(video.title)}">${escapeHtml(video.title)}</span>
        <span class="danmaku-heat-track">${heatCells(video)}</span><span class="danmaku-row-count"><strong>${escapeHtml(quality)}</strong><small>窗口 ${windowCount}</small></span>
      </button>`;
    }).join('') : message('尚无视频快照，先运行一次“立即采集”。');
  }

  function renderBars(targetId, values, recent, duration) {
    const target = $(targetId);
    if (!values?.some(Boolean)) { target.innerHTML = message('当前没有可定位的弹幕'); return; }
    const max = Math.max(1, ...values);
    target.innerHTML = `<div class="danmaku-bar-track">${values.map((value,index)=>{
      const recentValue = recent?.[index] || 0;
      const start = Math.floor(index / values.length * duration);
      const end = Math.floor((index + 1) / values.length * duration);
      return `<span class="danmaku-bar-column" title="${minutes(start)}–${minutes(end)} · 全部 ${number(value)} 条 · 窗口 ${number(recentValue)} 条"><i class="danmaku-bar-base" style="height:${Math.max(2,value/max*100)}%"></i><i class="danmaku-bar-current" style="height:${recentValue/max*100}%"></i></span>`;
    }).join('')}</div><div class="danmaku-axis"><span>00:00</span><span>${minutes(duration)}</span></div>`;
  }

  function renderTimeline(details) {
    const target = $('danmaku-timeline');
    const start = details?.window_start;
    if (start == null) { target.innerHTML = message('最近一次区间需要两轮完整采集'); return; }
    const end = Math.floor(new Date(state.data.generated_at).getTime() / 1000);
    const firstHour = Math.floor(start / 3600) * 3600;
    const lastHour = Math.floor(end / 3600) * 3600;
    const hours = [];
    for (let hour = firstHour; hour <= lastHour && hours.length < 49; hour += 3600) hours.push(hour);
    const counts = new Map((details.timeline || []).map((item)=>[item.hour,item.count]));
    const max = Math.max(1, ...hours.map((hour)=>counts.get(hour) || 0));
    target.innerHTML = `<div class="danmaku-bar-track">${hours.map((hour)=>{
      const count=counts.get(hour)||0;
      return `<span class="danmaku-bar-column" title="${timeOf(hour)} · ${number(count)} 条"><i class="danmaku-bar-base" style="height:${count ? Math.max(2,count/max*100) : 1}%"></i></span>`;
    }).join('')}</div><div class="danmaku-axis"><span>${timeOf(firstHour)}</span><span>${timeOf(lastHour)}</span></div>`;
  }

  function renderHot(details) {
    const key = state.scope === 'all' ? 'hot_all' : 'hot_window';
    const items = details?.[key]?.[state.kind] || [];
    const max = Math.max(1,...items.map((item)=>item.count));
    $('danmaku-hot').innerHTML = items.length ? items.map((item,index)=>`<div class="danmaku-hot-row">
      <span class="danmaku-rank">${String(index+1).padStart(2,'0')}</span>
      <div class="danmaku-hot-main"><strong title="${escapeHtml(item.content)}">${escapeHtml(item.content)}</strong><div class="danmaku-hot-track"><i style="width:${Math.max(2,item.count/max*100)}%"></i></div><small>原句 ${number(item.exact_count)} 条 · ${item.variants} 种写法${item.peak_minute == null ? '' : ` · 高峰 ${minutes(item.peak_minute*60)}`}</small></div>
      <span class="danmaku-hot-count">${number(item.count)} 条</span>
    </div>`).join('') : message(state.scope === 'window' && currentWindow() === 'latest' ? '等待第二轮完整采集建立对比' : '这个范围暂无重复弹幕');
  }

  function renderHotspots(details, card) {
    $('danmaku-hotspots').innerHTML = details?.hotspots?.length ? details.hotspots.map((item,index)=>{
      const second=item.minute*60;
      const phrase=item.phrases?.[0]?.content;
      return `<a class="danmaku-hotspot" href="https://www.bilibili.com/video/${encodeURIComponent(card.bvid)}?t=${second}" target="_blank" rel="noopener noreferrer"><span class="danmaku-hotspot-index">0${index+1}</span><span><strong>${minutes(second)}–${minutes(Math.min(second+60,card.duration_seconds))}</strong><small>${phrase ? `常见弹幕：${escapeHtml(phrase)}` : '打开视频查看片段'}</small></span><b>${number(item.count)} 条</b></a>`;
    }).join('') : message('当前视频尚无可定位的弹幕');
    $('danmaku-shared').innerHTML = state.data.shared_phrases?.length ? state.data.shared_phrases.slice(0,6).map((item)=>`<span class="danmaku-shared-chip" title="${escapeHtml(item.content)} · ${item.video_count} 条视频">${escapeHtml(item.content)} <small>${item.video_count} 视频</small><b>${number(item.count)} 条</b></span>`).join('') : '<span class="danmaku-muted">至少采集两条视频后显示</span>';
  }

  function renderDetail() {
    const card=state.data?.videos?.find((video)=>video.bvid===state.selected);
    const details=state.data?.selected;
    if (!card || !details) { $('danmaku-detail-head').innerHTML=message('选择一条视频查看详情'); return; }
    const coverage=card.page_count ? Math.round((card.readable_count||0)/card.page_count*100) : null;
    const quality=card.latest_error ? `采集失败：${card.latest_error}` : card.expected_segments && card.successful_segments !== card.expected_segments ? `分段不完整 ${card.successful_segments}/${card.expected_segments}` : card.latest_collected_at ? `分段 ${card.successful_segments}/${card.expected_segments} · 最近 ${when(card.latest_collected_at)}` : '尚未采集';
    $('danmaku-detail-head').innerHTML=`${cover(card,'danmaku-detail-cover')}<div class="danmaku-detail-text"><h3>${escapeHtml(card.title)}</h3><div class="danmaku-detail-stats"><span>已存可读取 <strong>${number(card.stored_count)}</strong></span><span>页面累计 <strong>${number(card.page_count)}</strong></span><span>本轮可读/页面 <strong>${coverage == null ? '—' : `${coverage}%`}</strong></span></div><p>${escapeHtml(quality)} · 页面累计与可读样本口径不同</p></div>`;
    renderBars('danmaku-density',details.density_all,details.density_window,card.duration_seconds);
    renderTimeline(details);
    renderHot(details);
    renderHotspots(details,card);
  }

  function positionInitialAnchor() {
    if (initialPositioned || location.hash !== '#danmaku' || !state.data || !$('kpi-grid').querySelector('.kpi-card')) return;
    initialPositioned = true;
    requestAnimationFrame(()=>$('danmaku').scrollIntoView({block:'start'}));
  }
  function render() { renderSummary(); renderHeatmap(); renderDetail(); positionInitialAnchor(); }

  async function load(bvid=state.selected) {
    const token=++state.token;
    try {
      const params=new URLSearchParams({window:currentWindow()});
      if (bvid) params.set('bvid',bvid);
      const response=await fetch(`/api/danmaku?${params}`,{cache:'no-store'});
      if (!response.ok) throw new Error('弹幕数据读取失败');
      const data=await response.json();
      if (token!==state.token) return;
      state.data=data;
      state.selected=data.selected?.bvid || data.videos?.[0]?.bvid || null;
      render();
    } catch(error) { $('danmaku-status').textContent=error.message; }
  }

  async function collect() {
    const button=$('danmaku-collect-button');
    button.disabled=true; button.textContent='采集中…';
    try {
      const response=await fetch('/api/danmaku/collect',{method:'POST'});
      if (!response.ok && response.status!==409) throw new Error('无法启动弹幕采集');
      $('danmaku-status').textContent='正在采集最近 10 条视频的弹幕；完成前已有数据仍可浏览。';
      if (state.poll) clearInterval(state.poll);
      state.poll=setInterval(async()=>{
        try {
          const status=await (await fetch('/api/status',{cache:'no-store'})).json();
          if (!status.running) {
            clearInterval(state.poll); state.poll=null;
            button.disabled=false; button.textContent='采集弹幕';
            await load();
            if (status.last_error || status.danmaku_error) $('danmaku-status').textContent=status.danmaku_error || status.last_error;
          }
        } catch(error) { $('danmaku-status').textContent=error.message; }
      },2000);
    } catch(error) { button.disabled=false; button.textContent='采集弹幕'; $('danmaku-status').textContent=error.message; }
  }

  async function search() {
    const query=$('danmaku-query').value.trim();
    const target=$('danmaku-search-results');
    if (!query || !state.selected) { target.innerHTML=''; return; }
    target.innerHTML=message('正在检索…');
    try {
      const params=new URLSearchParams({bvid:state.selected,q:query});
      const response=await fetch(`/api/danmaku/search?${params}`,{cache:'no-store'});
      if (!response.ok) throw new Error('检索失败');
      const rows=(await response.json()).results;
      target.innerHTML=rows.length ? rows.map((row)=>`<div class="danmaku-result"><span>${timeAt(row.progress_ms)}</span><strong>${escapeHtml(row.content)}</strong><small>${row.sent_at ? timeOf(row.sent_at) : '发送时间未知'}</small></div>`).join('') : message('当前视频没有匹配的可读取弹幕');
    } catch(error) { target.innerHTML=message(error.message); }
  }

  $('danmaku-heatmap').addEventListener('click',(event)=>{
    const row=event.target.closest('.danmaku-heat-row');
    if (row?.dataset.bvid && row.dataset.bvid!==state.selected) {
      state.selected=row.dataset.bvid;
      $('danmaku-search-results').innerHTML='';
      load(state.selected);
    }
  });
  document.querySelectorAll('.danmaku-scope').forEach((button)=>button.addEventListener('click',()=>{
    state.scope=button.dataset.scope;
    document.querySelectorAll('.danmaku-scope').forEach((item)=>{item.classList.toggle('active',item===button);item.setAttribute('aria-pressed',String(item===button));});
    if (state.data) { renderHeatmap(); renderHot(state.data.selected); }
  }));
  document.querySelectorAll('.danmaku-kind').forEach((button)=>button.addEventListener('click',()=>{
    state.kind=button.dataset.kind;
    document.querySelectorAll('.danmaku-kind').forEach((item)=>{item.classList.toggle('active',item===button);item.setAttribute('aria-pressed',String(item===button));});
    if (state.data) renderHot(state.data.selected);
  }));
  document.querySelectorAll('.window-tab').forEach((button)=>button.addEventListener('click',()=>load()));
  $('danmaku-collect-button').addEventListener('click',collect);
  $('danmaku-search-button').addEventListener('click',search);
  $('danmaku-query').addEventListener('keydown',(event)=>{if(event.key==='Enter') search();});
  window.addEventListener('collection:finished',()=>load());
  window.addEventListener('dashboard:rendered',positionInitialAnchor);
  load();
  setInterval(load,60000);
})();
