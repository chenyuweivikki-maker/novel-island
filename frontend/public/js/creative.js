// ===== 侧栏 =====
function renderSidebar() {
  apiGet('/api/novels').then(data => {
    const novels = data.novels || [];
    const list = document.getElementById('projectList');
    if (novels.length === 0) {
      list.innerHTML = '<div class="side-item hide-when-collapsed" style="cursor:default;color:var(--text-3)">还没有项目，点下方＋新建</div>';
      return;
    }
    list.innerHTML = novels.map(n => `
      <div class="side-item ${n.id === currentNovelId ? 'active' : ''}" data-novel-id="${n.id}" draggable="true">
        <span class="hide-when-collapsed side-title">${esc(n.title)}</span>
        <span class="hide-when-collapsed meta">${((n.total_words || 0) / 10000).toFixed(1)}万字</span>
      </div>`).join('');
    list.querySelectorAll('.side-item').forEach(item => {
      item.addEventListener('click', e => {
        if (e.target.classList.contains('side-edit')) return;
        selectNovel(Number(item.dataset.novelId), item.querySelector('.side-title').textContent);
      });
      item.addEventListener('dragstart', e => e.dataTransfer.setData('text/plain', item.dataset.novelId));
      item.addEventListener('dragover', e => e.preventDefault());
      item.addEventListener('drop', e => {
        e.preventDefault();
        reorderProjects(Number(e.dataTransfer.getData('text/plain')), Number(item.dataset.novelId));
      });
    });
  }).catch(() => {});
}
try {
  document.getElementById('btnSideCollapse').addEventListener('click', () => {
    const sb = document.getElementById('appSidebar');
    const collapsed = sb.classList.toggle('collapsed');
    document.getElementById('btnSideCollapse').textContent = collapsed ? '›' : '‹';
  });
} catch (e) { console.warn('侧栏按钮绑定失败', e); }

// ===== 新建小说 =====
function openNewNovel() { document.getElementById('newNovelModal').classList.add('show'); }
try {
  document.getElementById('btnNewProjectWorks').addEventListener('click', openNewNovel);
  document.getElementById('btnNewProjectSide').addEventListener('click', openNewNovel);
  document.getElementById('btnCancelNewNovel').addEventListener('click', () => document.getElementById('newNovelModal').classList.remove('show'));
  document.getElementById('btnConfirmNewNovel').addEventListener('click', async () => {
    const title = document.getElementById('newNovelTitle').value.trim();
    if (!title) { alert('请输入小说名称'); return; }
    const genre = document.getElementById('newNovelGenre').value.trim();
    const expectedWords = parseInt(document.getElementById('newNovelExpectedWords').value) || 0;
    const chapterWords = parseInt(document.getElementById('newNovelChapterWords').value) || 0;
    try {
      const data = await apiCall('/api/novel', { title, genre, expected_words: expectedWords, chapter_words: chapterWords });
      document.getElementById('newNovelModal').classList.remove('show');
      document.getElementById('newNovelGenre').value = '';
      showView('chat');
      renderSidebar();
      selectNovel(data.novel_id, title);
      showAgentWelcome(title);
      trackEvent('create_book', { book_genre: genre, has_outline: false }); // PRD 埋点
    } catch (e) { alert('创建失败: ' + e.message); }
  });
} catch (e) { console.warn('新建小说绑定失败', e); }

// ===== 编辑题材 =====
var genreEditingId = null;
function openGenreEdit(novelId, currentGenre, title) {
  genreEditingId = novelId;
  document.getElementById('genreEditTitle').textContent = title;
  document.getElementById('genreEditInput').value = currentGenre;
  document.getElementById('genreEditModal').classList.add('show');
}
try {
  document.getElementById('btnCancelGenreEdit').addEventListener('click', () => document.getElementById('genreEditModal').classList.remove('show'));
  document.getElementById('btnConfirmGenreEdit').addEventListener('click', async () => {
    if (genreEditingId == null) return;
    const genre = document.getElementById('genreEditInput').value.trim();
    try {
      await apiCall('/api/novel/' + genreEditingId + '/genre', { genre });
      document.getElementById('genreEditModal').classList.remove('show');
      loadWorks(); // 刷新作品列表 + 题材分类按钮
      trackEvent('edit_book_genre', { book_id: genreEditingId, book_genre: genre });
    } catch (e) { alert('保存失败: ' + e.message); }
  });
} catch (e) { console.warn('题材编辑绑定失败', e); }

// ===== 选中小说 / 默认对话 =====
function selectNovel(id, title) {
  currentNovelId = id;
  document.getElementById('subTabs').classList.add('visible');
  document.getElementById('chatTitle').textContent = title;
  document.getElementById('btnReport').style.display = 'inline-block';
  document.getElementById('btnStats').style.display = 'inline-block';
  switchSubPage('chat');
  renderSidebar();
  refreshKBViews();
  loadChapters();
  // 进入小说：对话区空则给欢迎语，避免「没反应」的错觉
  if (document.getElementById('msgList').children.length === 0) {
    appendMsg('agent', '正在基于《' + title + '》工作。可以问我关于设定、逻辑、灵感的问题，或把素材拖进输入框，我自动解析入库。');
  }
}
function selectDefaultChat() {
  currentNovelId = null;
  document.getElementById('subTabs').classList.remove('visible');
  document.getElementById('chatTitle').textContent = '写作Agent';
  document.getElementById('btnReport').style.display = 'none';
  document.getElementById('btnStats').style.display = 'none';
  switchSubPage('chat');
}

// ===== 二级 tab =====
// 点击绑定已在脚本前部用 document 事件委托注册（防御性重构：防止脚本中断导致 tab 失灵）
function switchSubPage(subpage) {
  currentSubPage = subpage;
  document.querySelectorAll('.sub-tab').forEach(t => t.classList.remove('active'));
  document.querySelectorAll('.page').forEach(p => p.classList.remove('active'));
  const tab = document.querySelector(`.sub-tab[data-subpage="${subpage}"]`);
  if (tab) tab.classList.add('active');
  const page = document.getElementById('page-' + subpage);
  if (page) page.classList.add('active');
  if (subpage === 'chars' || subpage === 'graph') refreshKBViews();
  if (subpage === 'timeline') renderTimeline();
  if (subpage === 'chapter_outline') renderChapterOutlines();
  if (subpage === 'backgrounds') loadBackgrounds();
  if (subpage === 'write') loadChapters();
}

// ===== 对话 =====
let msgIdSeq = 0;
function appendMsg(role, text, opts, containerId) {
  opts = opts || {}; containerId = containerId || 'msgList';
  const mid = ++msgIdSeq;
  messages.push({ id: mid, role: role, text: text, query: opts.query || '', container: containerId });
  const list = document.getElementById(containerId);
  const cls = role === 'user' ? 'msg user' : (opts.companion ? 'msg companion' : 'msg');
  const avatar = role === 'user' ? '我' : '<img src="/static/xiaoshuomao-official.svg" alt="猫">';
  const src = opts.src ? `<div class="src">来源：${opts.src}</div>` : '';
  const toolsHtml = (opts.tools && opts.tools.length)
    ? `<div class="src tools">${opts.tools.map(t => `<span class="tool-chip">🛠 ${esc(t.tool)}</span>`).join(' ')}</div>` : '';
  const tag = opts.companion ? `<span class="tag">♡ 陪伴模式</span><br>` : '';
  const actions = (role === 'agent' && !opts.noActions && opts.query)
    ? `<div class="msg-actions">
        <button title="重新生成" onclick="regenerateMsg(${mid}, event)">↻</button>
        <button title="复制" onclick="copyMsg(${mid}, event)">📋</button>
        <button title="有帮助" id="fbGood${mid}" onclick="feedbackMsg(${mid}, 'good', event)">👍</button>
        <button title="不满意" id="fbBad${mid}" onclick="feedbackMsg(${mid}, 'bad', event)">👎</button>
      </div>` : '';
  const div = document.createElement('div');
  div.className = cls;
  div.dataset.mid = mid;
  div.innerHTML = `<div class="avatar">${avatar}</div><div class="body">${tag}${esc(text).replace(/\n/g, '<br>')}${toolsHtml}${src}</div>${actions}`;
  list.appendChild(div);
  list.scrollTop = list.scrollHeight;
  if (containerId === 'homeMsgList') { var hw = document.getElementById('homeWelcome'); if (hw) hw.style.display = 'none'; }
  return mid;
}
// 重新生成：删除该条之后的消息，用同一问题重新问（流式）
async function regenerateMsg(mid, ev) {
  if (ev) ev.stopPropagation();
  const idx = messages.findIndex(m => m.id === mid);
  if (idx < 0) return;
  const target = messages[idx];
  if (!target.query) return;
  const containerId = target.container || 'msgList';
  // 删除该条 AI 及其后（同一容器内）的所有消息
  const list = document.getElementById(containerId);
  const mine = messages.filter(m => m.container === containerId);
  const mineIdx = mine.findIndex(m => m.id === mid);
  if (mineIdx < 0) return;
  for (let i = mineIdx; i < mine.length; i++) {
    const el = list ? list.querySelector(`.msg[data-mid="${mine[i].id}"]`) : null;
    if (el) el.remove();
  }
  messages = messages.filter(m => !(m.container === containerId && mine.findIndex(x => x.id === m.id) >= mineIdx));
  trackEvent('click_regenerate', { agent_type: 'qa', retry_count: 1 });
  if (containerId === 'homeMsgList') {
    // 首页：直接重新流式
    homeAsk(target.query);
  } else {
    document.getElementById('qaInput').value = target.query;
    askStreaming(containerId, target.query);
  }
}
// 上下文窗口指示：从 /api/cost 汇总显示 token 用量与成本（会话累计）
function showUsageBar(u) {
  const bar = document.getElementById('homeUsageBar');
  if (!bar) return;
  if (u && u.total) {
    bar.textContent = ` · 本会话 ${u.total.toLocaleString()} tokens`;
    bar.style.display = 'inline';
    return;
  }
  apiGet('/api/cost').then(function (d) {
    if (d && d.total_calls) {
      bar.textContent = ` · 已用 ${(d.total_input_tokens + d.total_output_tokens).toLocaleString()} tokens / ¥${(d.total_cost || 0).toFixed(3)}`;
      bar.style.display = 'inline';
    }
  }).catch(function () {});
}
// 复制消息文本（Clipboard API，兼容降级）
function copyMsg(mid, ev) {
  if (ev) ev.stopPropagation();
  const m = messages.find(x => x.id === mid);
  if (!m) return;
  const text = m.text || '';
  function done() {
    const btn = document.querySelector(`.msg[data-mid="${mid}"] .msg-actions button[title="复制"]`);
    if (btn) { const o = btn.textContent; btn.textContent = '✓'; setTimeout(() => btn.textContent = o, 1200); }
  }
  if (navigator.clipboard && navigator.clipboard.writeText) {
    navigator.clipboard.writeText(text).then(done).catch(function () { fallbackCopy(text); done(); });
  } else { fallbackCopy(text); done(); }
}
function fallbackCopy(text) {
  try {
    const ta = document.createElement('textarea');
    ta.value = text; ta.style.position = 'fixed'; ta.style.opacity = '0';
    document.body.appendChild(ta); ta.select();
    document.execCommand('copy'); ta.remove();
  } catch (e) {}
}
// 反馈：好评/差评（埋点 accept_suggestion / reject_suggestion）
function feedbackMsg(mid, kind, ev) {
  if (ev) ev.stopPropagation();
  const goodBtn = document.getElementById('fbGood' + mid);
  const badBtn = document.getElementById('fbBad' + mid);
  if (kind === 'good') {
    goodBtn.classList.add('feedback-on'); badBtn.classList.remove('feedback-on');
    trackEvent('accept_suggestion', { suggestion_type: 'qa', agent_source: 'fact_qa' });
    reportFeedback('accept', mid);
  } else {
    badBtn.classList.add('feedback-on'); goodBtn.classList.remove('feedback-on');
    trackEvent('reject_suggestion', { feedback_reason: 'user_feedback', agent_source: 'fact_qa' });
    reportFeedback('reject', mid);
  }
}
// 程序性记忆（PRD 四层记忆之四）：把用户对建议的采纳/拒绝上报，用于优化后续推荐
function reportFeedback(feedback, mid) {
  try {
    const msg = (messages || []).find(m => m.id === mid);
    const suggestion = (msg && msg.text ? msg.text : '').slice(0, 60);
    if (!suggestion) return;
    fetch(API_BASE + '/api/memory/feedback', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        novel_id: currentNovelId, session_id: sessionId,
        suggestion_type: 'suggestion', suggestion: suggestion, feedback: feedback,
      }),
    }).catch(() => {});
  } catch (e) {}
}
// 前端埋点统一入口
function trackEvent(event, props) {
  try {
    fetch(`${API_BASE}/api/track`, {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ event, props: props || {}, session_id: sessionId })
    }).catch(() => {});
  } catch (e) {}
}
function esc(s) { return s == null ? '' : String(s).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;'); }
function escAttr(s) { return esc(s).replace(/'/g, '&#39;'); }

let attachedFile = null;
const inputWrap = document.getElementById('chatInputWrap');
// 素材拖放（.txt/.md 直接读；.docx/.pdf 走后端解析接口）
inputWrap.addEventListener('dragover', e => { e.preventDefault(); inputWrap.classList.add('dragover'); });
inputWrap.addEventListener('dragleave', () => inputWrap.classList.remove('dragover'));
inputWrap.addEventListener('drop', e => {
  e.preventDefault();
  inputWrap.classList.remove('dragover');
  const file = e.dataTransfer.files && e.dataTransfer.files[0];
  if (!file) return;
  if (/\.(txt|md)$/i.test(file.name)) {
    const reader = new FileReader();
    reader.onload = () => {
      attachedFile = { name: file.name, size: file.size, text: String(reader.result).slice(0, 50000) };
      showAttachChip(file.name, file.size);
    };
    reader.readAsText(file);
  } else if (/\.(docx|pdf)$/i.test(file.name)) {
    // 多模态：发后端解析为文本，再走素材入库
    const fd = new FormData();
    fd.append('file', file);
    document.getElementById('attachArea').style.display = 'block';
    document.getElementById('attachArea').innerHTML = '<span class="attach-chip">⏳ 正在解析 ' + esc(file.name) + '…</span>';
    fetch(`${API_BASE}/api/material/parse`, { method: 'POST', body: fd })
      .then(r => r.json())
      .then(d => {
        if (d.error) { document.getElementById('attachArea').innerHTML = ''; document.getElementById('attachArea').style.display = 'none'; alert('解析失败: ' + d.error); return; }
        attachedFile = { name: file.name, size: file.size, text: d.text.slice(0, 50000) };
        showAttachChip(file.name, file.size);
      })
      .catch(err => { document.getElementById('attachArea').innerHTML = ''; document.getElementById('attachArea').style.display = 'none'; alert('解析失败: ' + err.message); });
  } else if (file) {
    alert('支持 .txt / .md / .docx / .pdf 素材');
  }
});
function showAttachChip(name, size) {
  document.getElementById('attachArea').style.display = 'block';
  document.getElementById('attachArea').innerHTML =
    `<span class="attach-chip">📎 ${esc(name)}（${(size / 1024).toFixed(1)}KB）<button onclick="clearAttach()">×</button></span>`;
}
function clearAttach() {
  attachedFile = null;
  document.getElementById('attachArea').style.display = 'none';
  document.getElementById('attachArea').innerHTML = '';
}

// ===== 流式对话（打字机效果） =====
async function askStreaming(containerId, presetQuery) {
  const input = document.getElementById('qaInput');
  const query = (presetQuery != null ? presetQuery : input.value).trim();
  if (!query && !attachedFile) return;
  const body = Object.assign({ query: query || '（素材解析）', top_k: 5, stream: true, session_id: sessionId }, agentSettingsBody());
  if (currentNovelIdNum()) body.novel_id = currentNovelIdNum();
  if (attachedFile) body.material = attachedFile.text;

  appendMsg('user', query || '📎 ' + attachedFile.name, null, containerId);
  clearAttach();
  if (presetQuery == null) { input.value = ''; autoResizeInput(input); }

  var askBtn = document.getElementById('btnAsk');
  var askOrig = askBtn.textContent;
  askBtn.textContent = '…';
  askBtn.disabled = true;

  // 创建打字气泡
  const list = document.getElementById(containerId);
  const bubble = document.createElement('div');
  bubble.className = 'msg typing';
  bubble.innerHTML = '<div class="avatar"><img src="/static/xiaoshuomao-official.svg" alt="猫"></div><div class="body"></div>';
  list.appendChild(bubble);
  list.scrollTop = 99999;

  let full = '', srcLine = '', lastTools = [];
  // 停止生成（AbortController）
  const ctrl = new AbortController();
  if (window.__askAbort) { try { window.__askAbort.abort(); } catch (e) {} }
  window.__askAbort = ctrl;
  const stopBtn = document.createElement('button');
  stopBtn.className = 'stop-gen';
  stopBtn.textContent = '■ 停止';
  stopBtn.addEventListener('click', function () { try { ctrl.abort(); } catch (e) {} });
  bubble.querySelector('.body').appendChild(stopBtn);
  try {
    const resp = await fetch(`${API_BASE}/api/kb/ask`, {
      method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body), signal: ctrl.signal
    });
    if (!resp.ok) throw new Error('HTTP ' + resp.status);
    const reader = resp.body.getReader();
    const decoder = new TextDecoder('utf-8');
    let buf = '';
    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      buf += decoder.decode(value, { stream: true });
      // SSE：按空行切分事件
      let sep;
      while ((sep = buf.indexOf('\n\n')) >= 0) {
        const raw = buf.slice(0, sep); buf = buf.slice(sep + 2);
        if (!raw.startsWith('data: ')) continue;
        let payload;
        try { payload = JSON.parse(raw.slice(6)); } catch (e) { continue; }
        if (payload.type === 'retrieval') {
          srcLine = (payload.data || []).slice(0, 3).map(s => '片段#' + (s.chunk_id + 1)).join(' · #');
        } else if (payload.type === 'token') {
          full += payload.data;
          bubble.classList.remove('typing');
          bubble.querySelector('.body').innerHTML = esc(full).replace(/\n/g, '<br>');
          list.scrollTop = list.scrollHeight;
        } else if (payload.type === 'done') {
          break;
        }
      }
    }
    bubble.remove();
    if (!full) {
      // 流式没输出（可能是缓存/降级路径）→ 用非流式兜底
      const data = await apiCall('/api/kb/ask', Object.assign({ query: query, top_k: 5, novel_id: body.novel_id || null, session_id: sessionId }, agentSettingsBody()));
      full = data.answer || '（无回答）';
      srcLine = (data.sources || []).length ? '片段#' + data.sources.slice(0, 3).map(s => s.chunk_id + 1).join(' · #') : '';
      lastTools = data.tools_used || [];
    }
    const isCompanion = companionMode || /抱抱|陪陪|我在|歇一歇|抱抱你/.test(full);
    const mid = appendMsg('agent', full, { companion: isCompanion, src: srcLine, query: query, tools: lastTools }, containerId);
  } catch (e) {
    bubble.remove();
    // 用户主动停止：保留已生成部分，不算错误
    if (e && e.name === 'AbortError') {
      if (full) {
        const isCompanion2 = companionMode || /抱抱|陪陪|我在|歇一歇|抱抱你/.test(full);
        appendMsg('agent', full, { companion: isCompanion2, src: srcLine, query: query }, containerId);
      }
    } else {
      appendMsg('agent', '出错了：' + e.message + '（请确认后端已启动）', null, containerId);
    }
  } finally {
    askBtn.textContent = askOrig;
    askBtn.disabled = false;
  }
}
async function ask(containerId) {
  return askStreaming(containerId || 'msgList');
}
document.getElementById('btnAsk').addEventListener('click', ask);
document.getElementById('qaInput').addEventListener('keydown', e => {
  if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); ask(); }
});
document.getElementById('pillCompanion').addEventListener('click', () => {
  companionMode = !companionMode;
  const p = document.getElementById('pillCompanion');
  p.classList.toggle('active-pill', companionMode);
  if (companionMode) appendMsg('agent', '陪伴模式已开启。累了就歇一歇，写不下去的时候，我都在。', { companion: true });
});

// ===== 人设卡片（三区：人物列表 + AI人设图 + 六段式） =====
async function fetchCharacters() {
  try {
    const params = currentNovelIdNum() ? `?novel_id=${currentNovelIdNum()}` : '';
    const data = await apiGet('/api/graph' + params);
    return (data.entities || []).map(ent => {
      const name = typeof ent === 'string' ? ent : (ent.name || '');
      const persona = (typeof ent === 'object' && ent.persona) ? ent.persona : {};
      return {
        name, persona,
        relations: (data.relations || []).filter(r => r.source === name || r.target === name)
      };
    });
  } catch (e) { return []; }
}
let charIndex = 0;
let charPortraitState = {}; // name -> 'empty' | 'generating' | 'done'
function renderCharacterCards() {
  const list = document.getElementById('charList');
  const detail = document.getElementById('charDetail');
  if (!KB.characters.length) {
    list.innerHTML = '<div class="cl-item" style="color:var(--text-3)">暂无人物</div>';
    detail.innerHTML = '<div class="empty-state">还没有人物数据。在「写作编辑器」保存章节后，Agent 会自动抽取人物。</div>';
    return;
  }
  const sel = KB.characters[Math.min(charIndex, KB.characters.length - 1)];
  // 主角/配角分组：persona['角色'] 含「主角」为第一组，否则首个实体当主角，其余配角
  var mains = KB.characters.filter(function (c) { return /主角|女主|男主/.test((c.persona || {})['角色'] || ''); });
  var mainNames = mains.length ? mains.map(function (c) { return c.name; }) : [KB.characters[0].name];
  var listHtml = '';
  var mainList = KB.characters.filter(function (c) { return mainNames.indexOf(c.name) >= 0; });
  var supportList = KB.characters.filter(function (c) { return mainNames.indexOf(c.name) < 0; });
  var itemHtml = function (c, i) {
    var idx = KB.characters.indexOf(c);
    return '<div class="cl-item ' + (idx === charIndex ? 'active' : '') + '" onclick="selectChar(' + idx + ')">' + esc(c.name) + '</div>';
  };
  if (mainList.length) listHtml += '<div class="cl-group">主角</div>' + mainList.map(itemHtml).join('');
  if (supportList.length) listHtml += '<div class="cl-group">配角</div>' + supportList.map(itemHtml).join('');
  list.innerHTML = listHtml + '<div class="cl-new">＋ 新建人物</div>';
  const st = charPortraitState[sel.name] || 'empty';
  const p = sel.persona || {};
  var relTags = sel.relations.length ? sel.relations.map(function (r) {
    var other = r.source === sel.name ? r.target : r.source;
    return '<span class="rel-tag" onclick="switchSubPage(\'graph\')">' + esc(other) + ' · ' + esc(r.relation) + '</span>';
  }).join('') : '<span class="sec-none">暂无关系</span>';
  detail.innerHTML = `
    <div class="portrait-col">
      <div class="portrait-card ${st === 'generating' ? 'generating' : st === 'done' ? 'done' : ''}" id="portraitCard">
        ${st === 'done' ? '<span class="edit-tag">编辑</span><div style="font-size:64px">👤</div><div style="font-size:12px;color:var(--text-2)">已生成 · 全身人像</div>'
          : st === 'generating' ? '<div class="spinner"></div><div style="font-size:13px;color:var(--text-2)">正在生成…</div>'
          : `<svg class="silhouette" viewBox="0 0 56 76" fill="none" stroke="#111"><circle cx="28" cy="18" r="12" stroke-width="2"/><path d="M6 72 C6 50 50 50 50 72" stroke-width="2"/></svg>
             <button class="gen-btn" onclick="genPortrait('${escAttr(sel.name)}')">AI 生成全身人设图</button>
             <div class="gen-hint">支持接入 AI 生图模型</div>`}
      </div>
    </div>
    <div class="info-col">
      <div class="char-name-big">${esc(sel.name)}</div>
      <div class="char-role-line">${esc(mainNames.indexOf(sel.name) >= 0 ? '主角' : '配角')}${p['身份'] ? ' · ' + esc(p['身份']) : ''}</div>
      <div class="info-section"><div class="sec-title">年龄</div><div class="sec-body">${esc(p['年龄'] || '暂无记录')}</div></div>
      <div class="info-section"><div class="sec-title">外貌特征</div><div class="sec-body">${esc(p['外貌'] || '暂无记录')}</div></div>
      <div class="info-section"><div class="sec-title">性格内核</div><div class="sec-body">${esc(p['性格'] || '暂无记录')}</div></div>
      <div class="info-section"><div class="sec-title">过往经历</div><div class="sec-body">${esc(p['经历'] || p['家庭'] || '暂无记录')}</div></div>
      <div class="info-section"><div class="sec-title">核心创伤</div><div class="sec-body">${esc(p['创伤'] || p['伤痛'] || '暂无记录')}</div></div>
      <div class="info-section"><div class="sec-title">最重要的动机</div><div class="sec-body">${esc(p['动机'] || '暂无记录')}</div></div>
      <div class="info-section"><div class="sec-title">社会关系</div><div class="rel-tags">${relTags}</div></div>
      <div class="info-section"><div class="sec-title">标志性物品 / 习惯</div><div class="sec-body">${esc(p['物品'] || '暂无记录')}</div></div>
    </div>`;
}
function selectChar(i) { charIndex = i; renderCharacterCards(); }
function genPortrait(name) {
  charPortraitState[name] = 'generating';
  renderCharacterCards();
  setTimeout(() => { charPortraitState[name] = 'done'; renderCharacterCards(); }, 1600); // mock：真实接入生图模型
}

// ===== 关系图（力导向：拖拽/缩放/点击弹人设卡） =====
let graphSim = null;   // 力模拟模型 {nodes, links, tx, ty, scale, raf, running}
let graphPopover = null;

function buildGraphModel() {
  const nodeMap = {};
  const nodes = KB.characters.map((c, i) => {
    const persona = c.persona || {};
    const isMain = /主角|女主|男主/.test(persona['角色'] || '') || i === 0;
    const n = { name: c.name, persona, isMain, x: 0, y: 0, vx: 0, vy: 0, fixed: false };
    nodeMap[c.name] = n;
    return n;
  });
  const N = nodes.length;
  nodes.forEach((n, i) => {
    n.x = 320 + Math.cos(i * 2 * Math.PI / N) * (N > 1 ? 130 : 0);
    n.y = 210 + Math.sin(i * 2 * Math.PI / N) * (N > 1 ? 130 : 0);
  });
  const links = [], seen = {};
  KB.characters.forEach(c => {
    (c.relations || []).forEach(r => {
      const other = r.source === c.name ? r.target : r.source;
      if (!nodeMap[other] || other === c.name) return;
      const key = [c.name, other].sort().join('|');
      if (seen[key]) return;
      seen[key] = 1;
      links.push({ s: nodeMap[c.name], t: nodeMap[other], relation: r.relation, weight: r.weight || 3 });
    });
  });
  return { nodes, links, tx: 0, ty: 0, scale: 1, raf: null, running: false };
}

function simStep(sim) {
  const nodes = sim.nodes;
  const n = nodes.length;
  // 1. 两两斥力
  for (let i = 0; i < n; i++) {
    for (let j = i + 1; j < n; j++) {
      const a = nodes[i], b = nodes[j];
      let dx = b.x - a.x, dy = b.y - a.y;
      let d2 = dx * dx + dy * dy;
      if (d2 < 900) { d2 = 900; } // 最小距离钳制，防飞散
      const d = Math.sqrt(d2);
      const f = 5200 / d2;       // 斥力强度
      dx /= d; dy /= d;
      a.vx -= dx * f; a.vy -= dy * f;
      b.vx += dx * f; b.vy += dy * f;
    }
  }
  // 2. 弹簧（有边节点拉近）
  for (const l of sim.links) {
    const a = l.s, b = l.t;
    const dx = b.x - a.x, dy = b.y - a.y;
    const d = Math.sqrt(dx * dx + dy * dy) || 1;
    const rest = 150;
    const f = (d - rest) * 0.025;
    const ux = dx / d, uy = dy / d;
    a.vx += ux * f; a.vy += uy * f;
    b.vx -= ux * f; b.vy -= uy * f;
  }
  // 3. 向心力（主角偏向中心，整体聚拢）
  for (const nd of nodes) {
    const cx = 320, cy = 210;
    const k = nd.isMain ? 0.06 : 0.012;
    nd.vx += (cx - nd.x) * k;
    nd.vy += (cy - nd.y) * k;
  }
  // 4. 积分 + 阻尼
  for (const nd of nodes) {
    if (nd.fixed) continue;
    nd.vx *= 0.82; nd.vy *= 0.82;
    nd.x += nd.vx; nd.y += nd.vy;
  }
}

function renderGraphFrame(sim) {
  const svg = document.getElementById('graphSvg');
  const W = svg.clientWidth || 800, H = svg.clientHeight || 480;
  const cx = W / 2, cy = H / 2;
  // 画面内主角居中（世界坐标 → 屏幕）
  const main = sim.nodes.find(x => x.isMain) || sim.nodes[0];
  const sx = p => (p.x - main.x) * sim.scale + W / 2 + sim.tx;
  const sy = p => (p.y - main.y) * sim.scale + H / 2 + sim.ty;
  const px = p => sx(p), py = p => sy(p);
  let html = '';
  // 边
  for (const l of sim.links) {
    const x1 = px(l.s), y1 = py(l.s), x2 = px(l.t), y2 = py(l.t);
    const sw = 1 + Math.min((l.weight || 3) * 0.25, 2.2);
    html += `<line x1="${x1}" y1="${y1}" x2="${x2}" y2="${y2}" stroke="#d0d0d0" stroke-width="${sw}"/>`;
    const mx = (x1 + x2) / 2, my = (y1 + y2) / 2;
    html += `<text x="${mx}" y="${my - 5}" text-anchor="middle" fill="#6b6b6b" font-size="10">${esc(l.relation)}</text>`;
  }
  // 节点
  for (const nd of sim.nodes) {
    const x = px(nd), y = py(nd), r = nd.isMain ? 34 : 23;
    html += `<g class="graph-node" data-name="${escAttr(nd.name)}" transform="translate(${x},${y})">
      <circle r="${r}" fill="#fff" stroke="#111" stroke-width="1.5"/>
      ${nd.isMain ? '<circle r="' + (r + 5) + '" fill="none" stroke="#111" stroke-width="1" stroke-dasharray="3 3"/>' : ''}
      <text y="4" text-anchor="middle" fill="#111" font-size="${nd.isMain ? 13 : 11}" font-weight="${nd.isMain ? 600 : 400}">${esc(nd.name)}</text>
    </g>`;
  }
  svg.innerHTML = html;
  bindGraphNodeEvents(sim);
}

function startGraphSim(sim) {
  if (sim.running) return;
  sim.running = true;
  const step = () => {
    simStep(sim);
    renderGraphFrame(sim);
    sim.raf = requestAnimationFrame(step);
  };
  sim.raf = requestAnimationFrame(step);
  // 15 秒后自动收敛停止（省电），交互时重新启动
  setTimeout(() => { if (sim.running) { cancelAnimationFrame(sim.raf); sim.running = false; } }, 15000);
}

function bindGraphNodeEvents(sim) {
  const svg = document.getElementById('graphSvg');
  svg.querySelectorAll('.graph-node').forEach(g => {
    g.addEventListener('mousedown', e => {
      e.stopPropagation();
      const name = g.dataset.name;
      const nd = sim.nodes.find(x => x.name === name);
      if (!nd) return;
      nd.fixed = true;
      // 拖拽跟随
      const move = ev => {
        const rect = svg.getBoundingClientRect();
        const px = ev.clientX - rect.left, py = ev.clientY - rect.top;
        const main = sim.nodes.find(x => x.isMain) || sim.nodes[0];
        nd.x = main.x + (px - rect.width / 2 - sim.tx) / sim.scale;
        nd.y = main.y + (py - rect.height / 2 - sim.ty) / sim.scale;
      };
      const up = () => {
        nd.fixed = false;
        document.removeEventListener('mousemove', move);
        document.removeEventListener('mouseup', up);
        if (!sim.running) startGraphSim(sim);
      };
      document.addEventListener('mousemove', move);
      document.addEventListener('mouseup', up);
    });
    g.addEventListener('click', e => {
      e.stopPropagation();
      showGraphPopover(sim, g.dataset.name, e);
    });
  });
}

function showGraphPopover(sim, name, ev) {
  const nd = sim.nodes.find(x => x.name === name);
  if (!nd) return;
  closeGraphPopover();
  const p = document.createElement('div');
  p.className = 'graph-popover';
  const persona = nd.persona || {};
  var relChar = null;
  for (var ci = 0; ci < KB.characters.length; ci++) { if (KB.characters[ci].name === name) { relChar = KB.characters[ci]; break; } }
  const rels = relChar ? relChar.relations || [] : [];
  p.style.cssText = 'position:fixed;z-index:300;width:300px;background:#fff;border:1px solid #e5e5e5;border-radius:12px;padding:18px 20px;box-shadow:0 8px 40px rgba(0,0,0,.14);font-size:13px;';
  const sec = (t, v) => v ? `<div style="margin-bottom:10px"><div style="font-size:11px;color:#9c9c9c;letter-spacing:1px;margin-bottom:3px">${t}</div><div style="line-height:1.7">${esc(v)}</div></div>` : '';
  p.innerHTML = `
    <div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:10px">
      <span style="font-size:17px;font-weight:700">${esc(name)}</span>
      <span style="font-size:11px;border:1px solid #111;border-radius:999px;padding:2px 10px">${nd.isMain ? '主角' : '配角'}</span>
    </div>
    ${sec('身份', persona['身份'])}${sec('年龄', persona['年龄'])}${sec('外貌特征', persona['外貌'])}
    ${sec('性格内核', persona['性格'])}${sec('过往经历', persona['经历'] || persona['家庭'])}
    ${sec('核心创伤', persona['创伤'])}${sec('动机', persona['动机'])}
    <div style="display:flex;flex-wrap:wrap;gap:6px;margin-top:4px">
      ${rels.length ? rels.map(r => {
        const other = r.source === name ? r.target : r.source;
        return `<span style="font-size:11px;border:1px solid #d0d0d0;border-radius:999px;padding:2px 10px;color:#6b6b6b">${esc(other)} · ${esc(r.relation)}</span>`;
      }).join('') : '<span style="font-size:11px;color:#9c9c9c">暂无关系</span>'}
    </div>
    <button onclick="closeGraphPopover()" style="position:absolute;top:10px;right:14px;border:none;background:none;font-size:15px;color:#9c9c9c;cursor:pointer">×</button>`;
  p.style.left = Math.min(ev.clientX, window.innerWidth - 330) + 'px';
  p.style.top = Math.min(ev.clientY, window.innerHeight - 320) + 'px';
  document.body.appendChild(p);
  graphPopover = p;
}
function closeGraphPopover() { if (graphPopover) { graphPopover.remove(); graphPopover = null; } }

function bindGraphCanvasEvents() {
  const svg = document.getElementById('graphSvg');
  svg.addEventListener('mousedown', e => {
    if (e.target !== svg) return; // 只处理空白处平移
    const startX = e.clientX, startY = e.clientY;
    const origTx = graphSim ? graphSim.tx : 0, origTy = graphSim ? graphSim.ty : 0;
    const move = ev => {
      if (!graphSim) return;
      graphSim.tx = origTx + (ev.clientX - startX);
      graphSim.ty = origTy + (ev.clientY - startY);
      renderGraphFrame(graphSim);
    };
    const up = () => {
      document.removeEventListener('mousemove', move);
      document.removeEventListener('mouseup', up);
    };
    document.addEventListener('mousemove', move);
    document.addEventListener('mouseup', up);
  });
  svg.addEventListener('wheel', e => {
    e.preventDefault();
    if (!graphSim) return;
    const rect = svg.getBoundingClientRect();
    const mx = e.clientX - rect.left, my = e.clientY - rect.top;
    const factor = e.deltaY < 0 ? 1.12 : 0.89;
    const main = graphSim.nodes.find(x => x.isMain) || graphSim.nodes[0];
    // 以鼠标为锚点缩放（世界坐标补偿）
    const wx = main.x + (mx - rect.width / 2 - graphSim.tx) / graphSim.scale;
    const wy = main.y + (my - rect.height / 2 - graphSim.ty) / graphSim.scale;
    graphSim.scale = Math.min(3, Math.max(0.3, graphSim.scale * factor));
    graphSim.tx = mx - rect.width / 2 - (wx - main.x) * graphSim.scale;
    graphSim.ty = my - rect.height / 2 - (wy - main.y) * graphSim.scale;
    renderGraphFrame(graphSim);
  }, { passive: false });
}

function renderRelationshipGraph() {
  const svg = document.getElementById('graphSvg');
  if (!KB.characters.length) {
    svg.innerHTML = '<text x="50%" y="50%" text-anchor="middle" fill="#9c9c9c" font-size="13">还没有人物数据，保存章节后自动生成</text>';
    return;
  }
  closeGraphPopover();
  graphSim = buildGraphModel();
  renderGraphFrame(graphSim);
  startGraphSim(graphSim);
}

// ===== 时间线 / 章纲（思维导图） =====
function renderMindMap(container, items, labelFn, emptyText) {
  if (!items || items.length === 0) {
    container.innerHTML = `<div class="mm-empty">${emptyText || '暂无内容'}</div>`;
    return;
  }
  container.innerHTML = items.map((item, i) => {
    const side = i % 2 === 0 ? 'left' : 'right';
    return `<div class="mm-item ${side}">
      <div class="mm-card">
        <div class="mm-title">${labelFn(item, i)}</div>
        <div>${esc(item.content || item.summary || item.outline || '')}</div>
      </div>
    </div>`;
  }).join('');
}
async function renderTimeline() {
  const container = document.getElementById('timelineList');
  try {
    const params = currentNovelIdNum() ? `?novel_id=${currentNovelIdNum()}` : '';
    const data = await apiGet('/api/timeline' + params);
    const items = data.timeline || [];
    renderMindMap(container, items, (item, i) => {
      const badge = item.chapter_id != null ? `<span class="timeline-badge">第${item.chapter_id}章</span>` : '';
      return `#${i + 1}${badge}`;
    }, '暂无情节记录，先保存章节');
  } catch (e) {
    container.innerHTML = '<div class="mm-empty">时间线加载失败: ' + esc(e.message) + '</div>';
  }
}
async function renderChapterOutlines() {
  const container = document.getElementById('chapterOutlineMap');
  if (!currentNovelId) { container.innerHTML = '<div class="mm-empty">请先选择一本小说</div>'; return; }
  renderForeshadowBoard(); // P2-3：伏笔看板同步刷新
  try {
    const data = await apiGet(`/api/novel/${currentNovelId}/chapter_outlines`);
    const outlines = data.outlines || [];
    renderMindMap(container, outlines, (item, i) => `第${i + 1}章${item.title ? ' · ' + esc(item.title) : ''}`, '保存章节后，章纲将自动生成');
  } catch (e) {
    container.innerHTML = '<div class="mm-empty">章纲加载失败: ' + esc(e.message) + '</div>';
  }
}

// ===== 背景资料 =====
async function loadBackgrounds() {
  const container = document.getElementById('backgroundList');
  if (!currentNovelId) { container.innerHTML = '<div class="empty-state">请先选择一本小说</div>'; return; }
  try {
    const data = await apiGet(`/api/novel/${currentNovelId}/backgrounds`);
    const grouped = data.backgrounds || {};
    const keys = Object.keys(grouped);
    if (!keys.length) { container.innerHTML = '<div class="empty-state">还没有背景资料，点右上角添加</div>'; return; }
    container.innerHTML = keys.map(cat => `
      <div class="bg-category">
        <div class="bg-category-title">${esc(cat)}</div>
        ${grouped[cat].map(bg => `
          <div class="bg-card">
            ${bg.title ? `<div class="bg-card-title">${esc(bg.title)}</div>` : ''}
            <div class="bg-card-content">${esc(bg.content)}</div>
            <button class="bg-card-del" onclick="deleteBackground(${bg.id})">删除</button>
          </div>`).join('')}
      </div>`).join('');
  } catch (e) {
    container.innerHTML = '<div class="empty-state">加载失败: ' + esc(e.message) + '</div>';
  }
}
async function deleteBackground(id) {
  if (!confirm('删除这条背景资料？')) return;
  try {
    await fetch(`${API_BASE}/api/background/${id}`, { method: 'DELETE' });
    loadBackgrounds();
  } catch (e) { alert('删除失败: ' + e.message); }
}
document.getElementById('btnAddBackground').addEventListener('click', () => {
  if (!currentNovelId) { alert('请先选择一本小说'); return; }
  document.getElementById('bgModal').classList.add('show');
});
// 背景资料：上传文件（.txt/.md/.docx/.pdf）→ 后端解析 → 预填弹窗 → 保存入库
document.getElementById('btnUploadBg').addEventListener('click', () => {
  if (!currentNovelId) { alert('请先选择一本小说'); return; }
  document.getElementById('bgFileInput').click();
});
document.getElementById('bgFileInput').addEventListener('change', async e => {
  const file = e.target.files[0];
  e.target.value = '';
  if (!file) return;
  const status = document.getElementById('bgUploadStatus');
  status.textContent = '解析 ' + file.name + ' …';
  try {
    const fd = new FormData();
    fd.append('file', file);
    const resp = await fetch(`${API_BASE}/api/material/parse`, { method: 'POST', body: fd });
    const data = await resp.json();
    if (data.error || !data.text) throw new Error(data.error || '解析失败');
    const text = data.text.trim();
    // 预填弹窗：分类默认「资料」，标题=文件名，内容=解析文本，作者可改后保存
    document.getElementById('bgCategory').value = '资料';
    const base = (file.name || '资料').replace(/\.[^.]+$/, '');
    document.getElementById('bgTitle').value = base.slice(0, 40);
    document.getElementById('bgContent').value = text.slice(0, 8000);
    document.getElementById('bgModal').classList.add('show');
    status.textContent = `✓ 已解析 ${text.length} 字，确认后保存`;
  } catch (err) {
    status.textContent = '解析失败: ' + err.message;
  }
});
document.getElementById('btnCancelBg').addEventListener('click', () => document.getElementById('bgModal').classList.remove('show'));
document.getElementById('btnConfirmBg').addEventListener('click', async () => {
  const category = document.getElementById('bgCategory').value.trim();
  const content = document.getElementById('bgContent').value.trim();
  if (!category || !content) { alert('请填写分类和内容'); return; }
  try {
    await apiCall(`/api/novel/${currentNovelId}/backgrounds`, { category, title: document.getElementById('bgTitle').value.trim(), content });
    document.getElementById('bgModal').classList.remove('show');
    document.getElementById('bgCategory').value = ''; document.getElementById('bgTitle').value = ''; document.getElementById('bgContent').value = '';
    loadBackgrounds();
  } catch (e) { alert('添加失败: ' + e.message); }
});

// ===== 写作编辑器 =====
document.getElementById('btnSaveChapter').addEventListener('click', async () => {
  const content = document.getElementById('chapterText').value.trim();
  if (!content) { alert('请先写章节内容'); return; }
  if (!currentNovelId) { alert('请先创建或选择一个作品'); return; }
  const btn = document.getElementById('btnSaveChapter');
  btn.disabled = true; btn.textContent = '保存并更新中…';
  try {
    var saveBody = { novel_id: currentNovelId, content: content, title: document.getElementById('chapterTitle').value.trim() };
    if (currentChapterId) saveBody.chapter_id = currentChapterId;
    const data = await apiCall('/api/chapter', saveBody);
    let result = `✓ 已保存（章节#${data.chapter_id}），知识库更新${data.knowledge_updated ? '成功' : '失败'}，新增${(data.stats && data.stats.chunks) || 0} 块`;
    const conflicts = data.conflicts || [];
    const gConflicts = data.graph_conflicts || [];
    if (conflicts.length) {
      result += `\n⚠️ 情节冲突 ${conflicts.length} 处：\n` + conflicts.map(c => `· ${c.conflict}`).join('\n');
    }
    if (gConflicts.length) {
      result += `\n🔍 图谱一致性 ${gConflicts.length} 处：\n` + gConflicts.map(c => `· [${c.dimension}] ${c.conflict}`).join('\n');
    }
    document.getElementById('saveResult').textContent = result;
    document.getElementById('chapterText').value = '';
    currentChapterId = null;
    updateWordCount();
    loadChapters(); renderTimeline(); renderChapterOutlines();
    if (data.outline) document.getElementById('saveResult').textContent += `\n\n📝 章纲已生成：\n${data.outline}`;
  } catch (e) {
    document.getElementById('saveResult').textContent = '保存失败: ' + e.message;
  } finally {
    btn.disabled = false; btn.textContent = '保存章节并更新知识库';
  }
});
var currentChapterId = null; // 当前编辑的章节（保存时更新该章）
async function loadChapters() {
  if (!currentNovelId) return;
  try {
    const data = await apiGet(`/api/novel/${currentNovelId}/chapters`);
    const list = document.getElementById('chapterList');
    const chapters = data.chapters || [];
    if (!chapters.length) {
      list.innerHTML = '<div class="ws-empty">保存章节后显示在此</div>';
      return;
    }
    list.innerHTML = chapters.map(c =>
      `<div class="ws-item ${c.id === currentChapterId ? 'active' : ''}" onclick="openChapter(${c.id})" title="${escAttr(c.title || '')}">${esc(c.title || ('第 ' + (c.order || c.id) + ' 章'))}</div>`
    ).join('');
  } catch (e) {}
}
async function openChapter(id) {
  try {
    const data = await apiGet('/api/chapter/' + id);
    if (data.error) { alert(data.error); return; }
    currentChapterId = id;
    document.getElementById('chapterText').value = data.content || '';
    document.getElementById('chapterTitle').value = data.title || '';
    updateWordCount();
    loadChapters(); // 刷新选中态
    document.getElementById('saveResult').textContent = '已载入' + (data.title ? '《' + data.title + '》' : '');
  } catch (e) { alert('加载章节失败: ' + e.message); }
}
function updateWordCount() {
  var v = document.getElementById('chapterText').value;
  var n = v.replace(/\s/g, '').length;
  document.getElementById('wordCount').textContent = n + ' 字';
}
document.getElementById('chapterText').addEventListener('input', updateWordCount);

// ===== 编辑器右栏：AI 对话联动（P5 三栏） =====
let polishSelStart = null, polishSelEnd = null, polishSelText = '';
function waAppend(role, text) {
  const box = document.getElementById('waMsgs');
  const div = document.createElement('div');
  div.className = 'wa-msg ' + role;
  div.textContent = text;
  box.appendChild(div);
  box.scrollTop = box.scrollHeight;
}
async function waAsk(q) {
  if (!q) return;
  waAppend('user', q);
  const box = document.getElementById('waMsgs');
  const typing = document.createElement('div');
  typing.className = 'wa-msg ai typing';
  typing.textContent = '思考中';
  box.appendChild(typing);
  box.scrollTop = box.scrollHeight;
  try {
    const body = Object.assign({ query: q, top_k: 5, session_id: sessionId }, agentSettingsBody());
    if (currentNovelIdNum()) body.novel_id = currentNovelIdNum();
    const data = await apiCall('/api/kb/ask', body);
    typing.remove();
    waAppend('ai', data.answer);
  } catch (e) {
    typing.remove();
    waAppend('ai', '出错了：' + e.message);
  }
}
document.getElementById('btnWaAsk').addEventListener('click', () => {
  const input = document.getElementById('waInput');
  const q = input.value.trim();
  if (!q) return;
  input.value = '';
  autoResizeInput(input);
  waAsk(q);
});
document.getElementById('waInput').addEventListener('keydown', e => {
  if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); document.getElementById('btnWaAsk').click(); }
});

// ===== 润色 Review（P5：选中文本 → AI 润色 → 对比 → 采纳/放弃） =====
let polishTarget = null; // {type:'selection', start, end}
const chapterTextEl = document.getElementById('chapterText');
chapterTextEl.addEventListener('mouseup', () => {
  const btn = document.getElementById('btnPolishSel');
  const s = chapterTextEl.selectionStart, e = chapterTextEl.selectionEnd;
  if (s != null && e != null && e - s >= 8) {
    polishSelText = chapterTextEl.value.slice(s, e).trim();
    if (polishSelText.length >= 8) {
      btn.style.display = 'inline-block';
      btn.textContent = `✨ AI 润色选中（${polishSelText.length}字）`;
      return;
    }
  }
  btn.style.display = 'none';
});
document.getElementById('btnPolishSel').addEventListener('click', () => {
  const s = chapterTextEl.selectionStart, e = chapterTextEl.selectionEnd;
  if (s == null || e == null || e <= s) return;
  polishTarget = { type: 'selection', start: s, end: e };
  doPolish(chapterTextEl.value.slice(s, e), '润色这一段，保持原意');
});
async function doPolish(text, style) {
  const mask = document.getElementById('polishModal');
  document.getElementById('polishOrig').textContent = text;
  document.getElementById('polishNew').textContent = 'AI 正在润色…';
  mask.classList.add('show');
  document.getElementById('btnPolishRegen').style.display = 'none';
  try {
    const data = await apiCall('/api/polish', { text, style, intensity: 0.5 });
    if (data.error) { document.getElementById('polishNew').textContent = data.error; return; }
    document.getElementById('polishNew').textContent = data.polished;
    document.getElementById('btnPolishRegen').style.display = 'inline-block';
  } catch (e) {
    document.getElementById('polishNew').textContent = '润色失败: ' + e.message;
  }
}
// btnPolishCancel 的绑定在下方（含程序性记忆上报）
document.getElementById('btnPolishRegen').addEventListener('click', () => {
  const text = document.getElementById('polishOrig').textContent;
  document.getElementById('polishNew').textContent = 'AI 正在重新润色…';
  apiCall('/api/polish', { text, style: '保持作者原有风格，轻度润色', intensity: 0.8 }).then(d => {
    document.getElementById('polishNew').textContent = d.polished || d.error || '';
  });
});
document.getElementById('btnPolishAccept').addEventListener('click', () => {
  const polished = document.getElementById('polishNew').textContent;
  const mask = document.getElementById('polishModal');
  if (!polished || polished.indexOf('正在') >= 0 || polished.indexOf('失败') >= 0) return;
  const v = chapterTextEl.value;
  if (polishTarget && polishTarget.type === 'selection') {
    chapterTextEl.value = v.slice(0, polishTarget.start) + polished + v.slice(polishTarget.end);
  } else {
    chapterTextEl.value = polished;
  }
  updateWordCount();
  mask.classList.remove('show');
  document.getElementById('saveResult').textContent = '✓ 已采纳润色结果';
  document.getElementById('btnPolishSel').style.display = 'none';
  // 程序性记忆：采纳润色 → 记录偏好（后续润色/推荐参考）
  try {
    fetch(API_BASE + '/api/memory/feedback', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        novel_id: currentNovelId, session_id: sessionId,
        suggestion_type: 'polish', suggestion: '润色风格：' + ((polishTarget && polishTarget.style) || '默认').slice(0, 40),
        feedback: 'accept',
      }),
    }).catch(() => {});
  } catch (e) {}
});
document.getElementById('btnPolishCancel').addEventListener('click', () => {
  // 程序性记忆：放弃润色 → 记录拒绝
  try {
    fetch(API_BASE + '/api/memory/feedback', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        novel_id: currentNovelId, session_id: sessionId,
        suggestion_type: 'polish', suggestion: '润色风格：' + ((polishTarget && polishTarget.style) || '默认').slice(0, 40),
        feedback: 'reject',
      }),
    }).catch(() => {});
  } catch (e) {}
  document.getElementById('polishModal').classList.remove('show');
});

// ===== 卡文检测（P5：编辑停顿超 5 分钟 → 弹窗求助） =====
let kawenTimer = null;
function resetKawenTimer() {
  if (kawenTimer) clearTimeout(kawenTimer);
  kawenTimer = setTimeout(() => {
    if (currentSubPage === 'write' && document.getElementById('chapterText').value.trim().length >= 30) {
      document.getElementById('kawenModal').classList.add('show');
    }
  }, 5 * 60 * 1000);
}
chapterTextEl.addEventListener('input', resetKawenTimer);
document.getElementById('btnKawenLater').addEventListener('click', () => {
  document.getElementById('kawenModal').classList.remove('show');
  resetKawenTimer();
});
document.getElementById('btnKawenHelp').addEventListener('click', () => {
  document.getElementById('kawenModal').classList.remove('show');
  switchSubPage('chat');
  const content = chapterTextEl.value.trim();
  const q = '我卡文了，最近写的是：' + content.slice(-600) + '\n\n请帮我推演后续情节，给我3个具体方向。';
  document.getElementById('qaInput').value = q;
  ask();
});

// ===== 创作策略报告（P3-1 DataAnalyst） =====
document.getElementById('btnReport').addEventListener('click', async () => {
  if (!currentNovelId) return;
  const mask = document.getElementById('reportModal');
  document.getElementById('reportTitle').textContent = '📊 创作策略报告 · 生成中';
  document.getElementById('reportBody').innerHTML = '正在分析你的作品（章节、人物关系、大纲）…<br><br>生成一份报告大约需要 20-60 秒，请稍候。';
  mask.classList.add('show');
  try {
    const data = await apiCall('/api/analysis/report', { novel_id: currentNovelId });
    if (data.error) { document.getElementById('reportBody').innerHTML = '生成失败: ' + esc(data.error); return; }
    const r = data.report || {};
    const list = (arr) => (arr && arr.length ? arr.map(x => `<li>${esc(x)}</li>`).join('') : '<li style="color:#9c9c9c">暂无</li>');
    document.getElementById('reportTitle').textContent = '📊 创作策略报告';
    document.getElementById('reportBody').innerHTML = `
      <div class="report-stats">
        <span>📖 ${r._chapters || 0} 章</span><span>✍️ ${(r._words || 0).toLocaleString()} 字</span><span>🤖 DataAnalyst Agent</span>
      </div>
      <div class="report-summary">${esc(r.summary || '')}</div>
      <div class="report-sec"><h4>作品优势</h4><ul>${list(r.strengths)}</ul></div>
      <div class="report-sec"><h4>短板提示</h4><ul>${list(r.weaknesses)}</ul></div>
      <div class="report-sec"><h4>市场机会</h4><ul>${list(r.market_opportunities)}</ul></div>
      <div class="report-sec"><h4>策略建议</h4><ul>${list(r.strategy)}</ul></div>
      <div class="report-sec"><h4>开篇钩子示例</h4><div class="report-hook">${esc(r.opening_hook || '')}</div></div>`;
  } catch (e) {
    document.getElementById('reportBody').innerHTML = '生成失败: ' + esc(e.message);
  }
});
document.getElementById('reportModal').addEventListener('click', e => { if (e.target === document.getElementById('reportModal')) e.target.classList.remove('show'); });

// ===== 写作数据面板（Tool 7） =====
document.getElementById('btnStats').addEventListener('click', async () => {
  if (!currentNovelId) return;
  const mask = document.getElementById('statsModal');
  mask.classList.add('show');
  document.getElementById('statsBody').innerHTML = '正在统计…';
  try {
    const d = await apiGet(`/api/novel/${currentNovelId}/stats`);
    if (d.error) { document.getElementById('statsBody').innerHTML = '统计失败: ' + esc(d.error); return; }
    const maxWords = Math.max(1, ...(d.chapters || []).map(c => c.words));
    const maxHour = Math.max(1, ...(d.hour_distribution || []).map(h => h.count));
    const maxMention = Math.max(1, ...(d.character_mentions || []).map(c => c.mentions));
    const bar = (label, val, max, unit) => `
      <div class="bar-row">
        <span class="bar-label" title="${esc(label)}">${esc(label)}</span>
        <div class="bar-track"><div class="bar-fill" style="width:${Math.max(2, Math.round(val / max * 100))}%"></div></div>
        <span class="bar-val">${val}${unit || ''}</span>
      </div>`;
    const chaptersBars = (d.chapters || []).length
      ? d.chapters.slice(-12).map(c => bar(c.title || ('第' + c.order + '章'), c.words, maxWords, '字')).join('')
      : '<div style="color:var(--text-3)">还没有章节</div>';
    const hoursBars = (d.hour_distribution || []).map(h => bar(h.hour + '时', h.count, maxHour, '章')).join('');
    const mentionBars = (d.character_mentions || []).slice(0, 8).map(c => bar(c.name, c.mentions, maxMention, '次')).join('') || '<div style="color:var(--text-3)">暂无</div>';
    const words = (d.top_words || []).map(w => `<span class="w"><b>${esc(w.word)}</b> ×${w.count}</span>`).join('');
    document.getElementById('statsBody').innerHTML = `
      <div class="stats-sum">
        <span>📖 ${d.total_chapters || 0} 章</span>
        <span>✍️ ${(d.total_words || 0).toLocaleString()} 字</span>
        <span>平均每章 ${(d.avg_words_per_chapter || 0).toLocaleString()} 字</span>
        ${d.longest_chapter ? `<span>最长：${esc(d.longest_chapter.title)}（${d.longest_chapter.words} 字）</span>` : ''}
      </div>
      <div class="stats-grid">
        <div class="stats-block"><h4>章节字数趋势</h4>${chaptersBars}</div>
        <div class="stats-block"><h4>人物出场统计</h4>${mentionBars}</div>
        <div class="stats-block"><h4>创作时段分布</h4>${hoursBars}</div>
        <div class="stats-block"><h4>高频词（双字）</h4><div class="word-cloud">${words || '<span style="color:var(--text-3)">暂无</span>'}</div></div>
      </div>`;
  } catch (e) {
    document.getElementById('statsBody').innerHTML = '统计失败: ' + esc(e.message);
  }
});
document.getElementById('statsModal').addEventListener('click', e => { if (e.target === document.getElementById('statsModal')) e.target.classList.remove('show'); });

// ===== 伏笔看板（P2-3） =====
async function renderForeshadowBoard() {
  const board = document.getElementById('fhBoard');
  if (!currentNovelId) { board.style.display = 'none'; return; }
  try {
    const data = await apiGet(`/api/novel/${currentNovelId}/foreshadowings`);
    const st = data.stats || { pending: 0, resolved: 0 };
    document.getElementById('fhPending').textContent = st.pending || 0;
    document.getElementById('fhResolved').textContent = st.resolved || 0;
    const items = data.foreshadowings || [];
    const list = document.getElementById('fhList');
    if (!items.length) {
      board.style.display = 'none';
      return;
    }
    board.style.display = 'block';
    list.innerHTML = items.slice(0, 20).map(f => `
      <div class="fh-item ${f.status === 'resolved' ? 'resolved' : ''}">
        <span class="fh-text">${esc(f.text)}</span>
        <span class="fh-from">${f.chapter_title ? '第' + f.chapter_title.replace('第','').replace('章','') + '章' : ''}${f.status === 'resolved' ? ' ✓已解决' : ''}</span>
        ${f.status === 'pending' ? `<button onclick="resolveFh(${f.id})">标记已解决</button>` : ''}
      </div>`).join('');
  } catch (e) { board.style.display = 'none'; }
}
async function resolveFh(id) {
  try {
    await fetch(`${API_BASE}/api/foreshadowing/${id}`, { method: 'PATCH' });
    renderForeshadowBoard();
  } catch (e) { alert('操作失败: ' + e.message); }
}

// ===== 数据刷新 =====
async function refreshKBViews() {
  KB.characters = await fetchCharacters();
  renderCharacterCards();
  renderRelationshipGraph();
  renderTimeline();
}
function currentNovelIdNum() { return currentNovelId || null; }
