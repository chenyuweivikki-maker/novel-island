// ===== 我的作品 =====
let WORKS_ALL = [];  // 全部作品：拖拽排序时保持未筛选项的相对顺序
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
function novelCardHtml(n) {
  return `
    <div class="novel-card" draggable="true" data-id="${n.id}" onclick="enterNovel(${n.id}, '${escAttr(n.title)}')">
      <div class="nc-head">
        <div class="tags">
          <span class="tag">${esc(n.genre || '未分类')}</span>
          <span class="tag">${esc(n.status || '连载中')}</span>
          <span class="date">${esc(n.updated_at ? n.updated_at.slice(5, 10) : '')}</span>
        </div>
        <button class="nc-more" title="管理" onclick="event.stopPropagation(); openNovelMenu(event, ${n.id}, '${escAttr(n.title)}', '${escAttr(n.genre || '')}', '${escAttr(n.status || '连载中')}')">⋯</button>
      </div>
      <h3>${esc(n.title)}</h3>
      <p>${esc(n.description || '还没有简介，开始写第一章吧。')}</p>
      <div class="meta">第 ${n.chapter_count || 0} 章 · ${(n.total_words || 0).toLocaleString()} 字</div>
    </div>`;
}
function loadWorks() {
  apiGet('/api/novels').then(data => {
    const novels = data.novels || [];
    WORKS_ALL = novels;
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
    wrap.innerHTML = `<div class="works-grid">${filtered.map(novelCardHtml).join('')}</div>`;
    bindWorksDrag();
  }).catch(e => {
    document.getElementById('worksContent').innerHTML = `<div class="empty-state">加载失败: ${esc(e.message)}<br><br>请确认后端已启动（localhost:8000）</div>`;
  });
}
// ===== 作品卡片管理（⋯ 菜单：重命名/编辑题材/删除）=====
let novelMenuEl = null;
function openNovelMenu(e, id, title, genre, status) {
  e.stopPropagation();
  if (novelMenuEl) novelMenuEl.remove(); novelMenuEl = null;
  const menu = document.createElement('div');
  menu.className = 'novel-menu';
  menu.innerHTML = `
    <button onclick="renameNovel(${id}, '${escAttr(title)}')">✏️ 重命名</button>
    <button onclick="openGenreEdit(${id}, '${escAttr(genre)}', '${escAttr(title)}')">🏷 编辑题材</button>
    <button class="danger" onclick="deleteNovel(${id}, '${escAttr(title)}')">🗑 删除</button>`;
  document.body.appendChild(menu);
  const r = e.target.getBoundingClientRect();
  menu.style.position = 'fixed';
  menu.style.left = Math.min(r.right - menu.offsetWidth, window.innerWidth - 150) + 'px';
  menu.style.top = (r.bottom + 4) + 'px';
  novelMenuEl = menu;
}
document.addEventListener('click', () => { if (novelMenuEl) { novelMenuEl.remove(); novelMenuEl = null; } });
async function renameNovel(id, cur) {
  if (novelMenuEl) novelMenuEl.remove(); novelMenuEl = null;
  const name = prompt('给这本作品重新命名：', cur);
  if (name == null || !name.trim()) return;
  try {
    await apiCall(`/api/novel/${id}/rename`, { title: name.trim() });
    loadWorks();
  } catch (e) { alert('重命名失败: ' + e.message); }
}
async function deleteNovel(id, title) {
  if (novelMenuEl) novelMenuEl.remove(); novelMenuEl = null;
  if (!confirm(`确定删除《${title}》？将同时删除它的章节、背景、知识库，且不可恢复。`)) return;
  try {
    await fetch(`${API_BASE}/api/novel/${id}`, { method: 'DELETE' });
    loadWorks();
  } catch (e) { alert('删除失败: ' + e.message); }
}
// ===== 拖拽排序（仅在「全部/全部」非筛选视图下有意义，保持未筛选项相对顺序）=====
let _dragId = null;
function bindWorksDrag() {
  document.querySelectorAll('#worksContent .novel-card').forEach(card => {
    card.addEventListener('dragstart', e => {
      _dragId = card.dataset.id;
      e.dataTransfer.effectAllowed = 'move';
      card.classList.add('dragging');
    });
    card.addEventListener('dragend', () => card.classList.remove('dragging'));
    card.addEventListener('dragover', e => { e.preventDefault(); card.classList.add('drag-over'); });
    card.addEventListener('dragleave', () => card.classList.remove('drag-over'));
    card.addEventListener('drop', e => {
      e.preventDefault(); card.classList.remove('drag-over');
      const targetId = card.dataset.id;
      if (_dragId && _dragId !== targetId) reorderNovels(_dragId, targetId);
      _dragId = null;
    });
  });
}
async function reorderNovels(dragId, targetId) {
  const ids = WORKS_ALL.map(n => n.id);
  const from = ids.indexOf(Number(dragId));
  const to = ids.indexOf(Number(targetId));
  if (from < 0 || to < 0) return;
  const [moved] = ids.splice(from, 1);
  ids.splice(to, 0, moved);
  try {
    await apiCall('/api/novels/reorder', { ordered_ids: ids });
    loadWorks();
  } catch (e) { alert('排序失败: ' + e.message); }
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
