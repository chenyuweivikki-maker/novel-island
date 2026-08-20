// ===== 我的作品 =====
function renderGenreSeg(genres) {
  // 按项目实际题材动态渲染分类按钮：没有任何题材 → 只剩「全部」
  const seg = document.getElementById('genreSeg');
  const cur = seg.querySelector('.active')?.dataset.g || '全部';
  seg.innerHTML = '<button class="active" data-g="全部">全部</button>' +
    genres.map(g => `<button data-g="${escAttr(g)}">${esc(g)}</button>`).join('');
  // 当前选中的题材还在 → 保持；不在了（如题材被删）→ 回退「全部」
  const target = [...seg.querySelectorAll('button')].find(b => b.dataset.g === cur);
  if (target) {
    seg.querySelectorAll('button').forEach(x => x.classList.remove('active'));
    target.classList.add('active');
  }
}
function loadWorks() {
  apiGet('/api/novels').then(data => {
    const novels = data.novels || [];
    // 题材按钮数据源：作品列表里实际出现过的题材（去重、非空、保持顺序）
    const seen = [];
    novels.forEach(n => { if (n.genre && !seen.includes(n.genre)) seen.push(n.genre); });
    renderGenreSeg(seen);
    const g = document.querySelector('#genreSeg .active').dataset.g;
    const s = document.querySelector('#statusSeg .active').dataset.s;
    const filtered = novels.filter(n =>
      (g === '全部' || n.genre === g) && (s === '全部' || n.status === s)
    );
    const wrap = document.getElementById('worksContent');
    if (filtered.length === 0) {
      wrap.innerHTML = `<div class="empty-state"><img src="/static/xiaoshuomao-official.svg"><br>还没有作品，点击「新建小说」开始你的第一本</div>`;
      return;
    }
    wrap.innerHTML = `
      <div class="works-grid">
        ${filtered.map(n => `
          <div class="novel-card" onclick="enterNovel(${n.id}, '${escAttr(n.title)}')">
            <div class="tags"><span class="tag">${esc(n.genre || '未分类')}</span><button class="tag tag-edit" title="编辑题材" onclick="event.stopPropagation(); openGenreEdit(${n.id}, '${escAttr(n.genre || '')}', '${escAttr(n.title)}')">✎</button><span class="tag">${esc(n.status || '连载中')}</span><span class="date">${esc(n.updated_at ? n.updated_at.slice(5, 10) : '')}</span></div>
            <h3>${esc(n.title)}</h3>
            <p>${esc(n.description || '还没有简介，开始写第一章吧。')}</p>
            <div class="meta">第 ${n.chapter_count || 0} 章 · ${(n.total_words || 0).toLocaleString()} 字</div>
          </div>`).join('')}
      </div>
      ${filtered[0] ? `
      <div class="work-row">
        <div class="cover">📖</div>
        <div class="info">
          <div class="tags"><span class="tag solid">热门</span><span class="tag">连载</span></div>
          <h3>${esc(filtered[0].title)}</h3>
          <div class="desc">第 ${filtered[0].chapter_count || 0} 章 · ${(filtered[0].total_words || 0).toLocaleString()} 字 · 上次编辑 ${timeAgo(filtered[0].updated_at)}</div>
        </div>
        <div class="actions">
          <button class="btn-pill small" onclick="enterNovel(${filtered[0].id}, '${escAttr(filtered[0].title)}')">继续写</button>
        </div>
      </div>` : ''}
    `;
  }).catch(e => {
    document.getElementById('worksContent').innerHTML = `<div class="empty-state">加载失败: ${esc(e.message)}<br><br>请确认后端已启动（localhost:8000）</div>`;
  });
}
// 题材/状态筛选：题材按钮是动态生成的，用事件委托绑定（页面加载只绑一次，新按钮自动生效）
try {
  document.getElementById('genreSeg').addEventListener('click', e => {
    const b = e.target.closest('button');
    if (!b) return;
    document.querySelectorAll('#genreSeg button').forEach(x => x.classList.remove('active'));
    b.classList.add('active');
    loadWorks();
  });
  document.querySelectorAll('#statusSeg button').forEach(b => b.addEventListener('click', () => {
    document.querySelectorAll('#statusSeg button').forEach(x => x.classList.remove('active'));
    b.classList.add('active');
    loadWorks();
  }));
} catch (e) { console.warn('作品筛选绑定失败', e); }
