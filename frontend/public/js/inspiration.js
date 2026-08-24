// ===== 灵感库（P12）=====
const INSP_NOVEL = 0; // 默认收件库（不绑定具体小说）；「自动分类」tab 查全部
let inspCategory = 'auto'; // auto=自动分类（默认页，显示所有+输入框）| 具体分类名=只看该分类
let inspCatNames = [];

async function loadInspiration() {
  await Promise.all([loadInspCats(), loadInspItems(), loadInspNovelSel()]);
}
async function loadInspNovelSel() {
  try {
    const data = await apiGet('/api/novels');
    const sel = document.getElementById('inspNovelSel');
    if (!sel) return;
    const cur = sel.value;
    sel.innerHTML = '<option value="0">✨ 自动分类</option>' +
      (data.novels || []).map(n => `<option value="${n.id}">📕 ${esc(n.title)}</option>`).join('');
    sel.value = cur || '0';
  } catch (e) { console.error(e); }
}
async function loadInspCats() {
  try {
    const data = await apiGet(`/api/inspiration/categories?novel_id=${INSP_NOVEL}`);
    inspCatNames = (data.categories || []).map(c => c.name);
    const list = document.getElementById('inspCatList');
    list.innerHTML = (data.categories || []).map(c => `
      <div class="insp-cat ${inspCategory === c.name ? 'active' : ''}" data-cat="${escAttr(c.name)}">
        <span>${esc(c.name)}</span>
        <span class="cnt">${c.count}</span>
        <button class="more" title="分类管理">⋯</button>
      </div>`).join('');
    list.querySelectorAll('.insp-cat').forEach(el => {
      el.addEventListener('click', e => {
        if (e.target.classList.contains('more')) { openInspMenu(e, el.dataset.cat); return; }
        selectInspCat(el.dataset.cat);
      });
    });
    // 自动分类（静态元素）：同步 active 态（inspCategory='auto' 时高亮）
    const autoEl = document.querySelector('#inspCats .insp-cat[data-cat="auto"]');
    if (autoEl) autoEl.classList.toggle('active', inspCategory === 'auto');
    const cntAll = (data.categories || []).reduce((s, c) => s + c.count, 0);
    document.getElementById('inspCntAll').textContent = cntAll;
  } catch (e) { console.error(e); }
}
// 选中某个分类（自动分类或具体分类）：统一处理 active 高亮 + 刷新条目
function selectInspCat(cat) {
  inspCategory = cat;
  document.querySelectorAll('#inspCats .insp-cat').forEach(x => x.classList.remove('active'));
  const els = document.querySelectorAll('#inspCats .insp-cat');
  for (let i = 0; i < els.length; i++) { if (els[i].dataset.cat === cat) { els[i].classList.add('active'); break; } }
  loadInspItems();
}
// 「自动分类」是静态元素，之前没绑点击，导致点了其他分类就回不去 —— 这里补上
document.getElementById('inspCatAuto').addEventListener('click', () => selectInspCat('auto'));

async function loadInspItems() {
  const list = document.getElementById('inspItems');
  // 输入框/分类中提示只在「自动分类」tab 显示（其他分类是确定类目，直接浏览）
  const isAuto = inspCategory === 'auto';
  document.getElementById('inspInputBox').style.display = isAuto ? 'flex' : 'none';
  document.getElementById('inspClassifying').style.display = 'none';
  try {
    // 「自动分类」tab：显示所有书 + 默认收件库的全部灵感；具体分类 tab：只看该分类（所有书）
    const cat = isAuto ? '' : inspCategory;
    const url = `/api/inspirations?category=${encodeURIComponent(cat)}`;
    const data = await apiGet(url);
    const items = data.inspirations || [];
    // 小说标题映射（用于「属于哪本书」标签）
    var novelTitles = {};
    try {
      const ns = await apiGet('/api/novels');
      (ns.novels || []).forEach(n => { novelTitles[n.id] = n.title; });
    } catch (e) {}
    if (!items.length) {
      // 自动分类 tab：加号卡片引导记灵感；具体分类 tab：直接空状态
      list.innerHTML = isAuto
        ? `<div class="insp-item add-card" onclick="openNewInsp()">
             <div><div class="plus">＋</div><div class="sub">记一条灵感</div></div>
           </div>
           <div class="empty-state" style="grid-column:1/-1">还没有灵感。想到什么就写什么，AI 会自动帮你分类整理。</div>`
        : `<div class="empty-state" style="grid-column:1/-1">这个分类还没有灵感。</div>`;
      return;
    }
    list.innerHTML = items.map(it => {
      const bookTag = it.novel_id && novelTitles[it.novel_id] != null
        ? `<span class="tag book">📕 ${esc(novelTitles[it.novel_id])}</span>` : '';
      return `
      <div class="insp-item">
        <div class="content">${esc(it.content)}</div>
        <div class="meta">
          <span class="tag">${esc(it.category)}</span>
          ${bookTag}
          <span>${timeAgo(it.created_at * 1000)}</span>
          <select onchange="changeInspCat(${it.id}, this.value)">
            ${inspCatNames.map(n => `<option ${n === it.category ? 'selected' : ''}>${esc(n)}</option>`).join('')}
            <option>其他</option>
          </select>
          <button class="share-btn" data-insp-id="${it.id}" data-insp-content="${escAttr(it.content)}">分享</button>
          <button class="del" onclick="deleteInsp(${it.id})">删除</button>
        </div>
      </div>`;}).join('') + (isAuto ? `
      <div class="insp-item add-card" onclick="openNewInsp()">
        <div><div class="plus">＋</div><div class="sub">添加分类</div></div>
      </div>` : '');
  } catch (e) {
    list.innerHTML = `<div class="empty-state">加载失败: ${esc(e.message)}</div>`;
  }
}
function openNewInsp() {
  const box = document.getElementById('inspInputBox');
  if (box) { box.scrollIntoView({ behavior: 'smooth', block: 'center' }); }
  const ta = document.getElementById('inspInput');
  if (ta) ta.focus();
}
async function changeInspCat(id, cat) {
  try {
    await apiCall(`/api/inspiration/${id}/category`, { insp_id: id, category: cat });
    loadInspiration();
  } catch (e) { alert('改分类失败: ' + e.message); }
}
async function deleteInsp(id) {
  if (!confirm('删除这条灵感？')) return;
  try {
    await fetch(`${API_BASE}/api/inspiration/${id}`, { method: 'DELETE' });
    loadInspiration();
  } catch (e) { alert('删除失败: ' + e.message); }
}
// 上传灵感（AI 自动分类）
document.getElementById('btnInspSend').addEventListener('click', async () => {
  const input = document.getElementById('inspInput');
  const text = input.value.trim();
  if (!text) return;
  input.value = '';
  const classifying = document.getElementById('inspClassifying');
  classifying.style.display = 'block';
  try {
    const sel = document.getElementById('inspNovelSel');
    const novelId = sel ? parseInt(sel.value) || 0 : INSP_NOVEL;
    const data = await apiCall('/api/inspirations', { novel_id: novelId, content: text });
    classifying.style.display = 'none';
    if (data.error) { alert(data.error); return; }
    document.getElementById('inspHint').textContent = data.auto
      ? `✓ 已入库，AI 分类为「${data.category}」`
      : '✓ 已入库';
    loadInspiration();
    setTimeout(() => document.getElementById('inspHint').textContent = '灵感库：想到什么就记什么，写的时候来找', 3000);
  } catch (e) {
    classifying.style.display = 'none';
    alert('上传失败: ' + e.message);
  }
});
document.getElementById('inspInput').addEventListener('keydown', e => {
  if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); document.getElementById('btnInspSend').click(); }
});
// 导出
document.getElementById('btnInspExport').addEventListener('click', async () => {
  try {
    const data = await apiGet(`/api/inspirations/export?novel_id=${INSP_NOVEL}`);
    const blob = new Blob([data.text], { type: 'text/plain;charset=utf-8' });
    const a = document.createElement('a');
    a.href = URL.createObjectURL(blob);
    a.download = '小说岛-灵感库.txt';
    a.click();
    URL.revokeObjectURL(a.href);
  } catch (e) { alert('导出失败: ' + e.message); }
});
// 分类管理（⋯ 浮动菜单：改名/上移/下移/删除）
let inspMenuEl = null;
function openInspMenu(e, name) {
  e.stopPropagation();
  closeInspMenu();
  const menu = document.createElement('div');
  menu.className = 'insp-menu';
  menu.innerHTML = `
    <button onclick="renameInspCat('${escAttr(name)}')">✏️ 改名</button>
    <button onclick="moveInspCat('${escAttr(name)}','up')">↑ 上移</button>
    <button onclick="moveInspCat('${escAttr(name)}','down')">↓ 下移</button>
    <button class="danger" onclick="delInspCat('${escAttr(name)}')">🗑 删除</button>`;
  menu.style.left = Math.min(e.clientX, window.innerWidth - 150) + 'px';
  menu.style.top = Math.min(e.clientY, window.innerHeight - 160) + 'px';
  document.body.appendChild(menu);
  inspMenuEl = menu;
}
function closeInspMenu() { if (inspMenuEl) { inspMenuEl.remove(); inspMenuEl = null; } }
document.addEventListener('click', closeInspMenu);
async function moveInspCat(name, dir) {
  closeInspMenu();
  try {
    await apiCall('/api/inspiration/category/move', { novel_id: INSP_NOVEL, name, direction: dir });
    loadInspCats();
  } catch (e) { alert('移动失败: ' + e.message); }
}
async function delInspCat(name) {
  closeInspMenu();
  if (!confirm(`删除分类「${name}」？其中的灵感会归入「其他」。`)) return;
  try {
    await fetch(`${API_BASE}/api/inspiration/category?novel_id=${INSP_NOVEL}&name=${encodeURIComponent(name)}`, { method: 'DELETE' });
    if (inspCategory === name) inspCategory = 'auto';
    loadInspiration();
  } catch (e) { alert('删除失败: ' + e.message); }
}
async function renameInspCat(name) {
  closeInspMenu();
  const newName = prompt('新的分类名：', name);
  if (!newName || newName === name) return;
  try {
    await apiCall('/api/inspiration/category/rename', { novel_id: INSP_NOVEL, old_name: name, new_name: newName });
    if (inspCategory === name) inspCategory = newName;
    loadInspiration();
  } catch (e) { alert('改名失败: ' + e.message); }
}
document.getElementById('btnInspAddCat').addEventListener('click', () => {
  const name = prompt('新分类名：');
  if (!name) return;
  apiCall('/api/inspiration/category', { novel_id: INSP_NOVEL, name }).then(loadInspCats).catch(e => alert('添加失败: ' + e.message));
});
// 侧栏灵感库分组已移除（灵感库是独立 tab，有自己顶部分类栏）
