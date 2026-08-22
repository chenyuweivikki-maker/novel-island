function enterNovel(id, title) {
  showView('chat');
  selectNovel(id, title);
}
function homeAsk(q) {
  if (!q) return;
  document.getElementById('homeInput').value = '';
  autoResizeInput(document.getElementById('homeInput'));
  clearDraft();  // 消息已发出，清空该会话草稿
  setWelcomeVisible(false);  // 发出消息 → 隐藏吉祥物欢迎页，进入对话模式
  appendMsg('user', q, null, 'homeMsgList');
  // 主流 AI 产品模式：发出消息的瞬间，左侧列表立刻出现本会话（先本地占位，再后端建占位）
  pendingSessionTitle = q.slice(0, 12);
  renderChatSessions();
  apiCall('/api/chat/session/ensure', { scope: 'home', session_id: sessionId, title: pendingSessionTitle })
    .then(() => renderChatSessions())
    .catch(() => {});
  // 流式打字机（与创作页一致）：SSE 逐 token 渲染；支持停止生成
  const list = document.getElementById('homeMsgList');
  const bubble = document.createElement('div');
  bubble.className = 'msg typing';
  bubble.innerHTML = '<div class="avatar"><img src="/static/xiaoshuomao-official.svg" alt="猫"></div><div class="body">思考中…</div>';
  list.appendChild(bubble);
  list.scrollTop = 99999;
  const sendBtn = document.getElementById('homeSend');
  const btnOrig = sendBtn.textContent;
  sendBtn.textContent = '…';
  sendBtn.disabled = true;
  let full = '', lastUsage = null;
  const ctrl = new AbortController();
  if (window.__homeAbort) { try { window.__homeAbort.abort(); } catch (e) {} }
  window.__homeAbort = ctrl;
  // 停止按钮：生成中可点，停止后保留已生成部分
  const stopBtn = document.createElement('button');
  stopBtn.className = 'stop-gen';
  stopBtn.textContent = '■ 停止';
  stopBtn.addEventListener('click', function () { try { ctrl.abort(); } catch (e) {} });
  bubble.querySelector('.body').appendChild(stopBtn);
  function finish() {
    sendBtn.textContent = btnOrig;
    sendBtn.disabled = false;
    bubble.remove();
    showUsageBar(lastUsage);
    if (!full) {
      // 流式无输出（降级路径）→ 非流式兜底
      return apiCall('/api/kb/ask', Object.assign({ query: q, top_k: 5, session_id: sessionId }, agentSettingsBody())).then(function (data) {
        appendMsg('agent', data.answer, { companion: /抱抱|陪陪|歇一歇|抱抱你/.test(data.answer || ''), query: q, tools: data.tools_used || [] }, 'homeMsgList');
        pendingSessionTitle = null;
        renderChatSessions();
      });
    }
    appendMsg('agent', full, { companion: /抱抱|陪陪|歇一歇|抱抱你/.test(full), query: q }, 'homeMsgList');
    // 一轮对话结束 → 清占位、刷新左侧会话列表（新会话入库后立刻出现在「对话」分组）
    pendingSessionTitle = null;
    renderChatSessions();
  }
  fetch(API_BASE + '/api/kb/ask', {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(Object.assign({ query: q, top_k: 5, stream: true, session_id: sessionId }, agentSettingsBody())),
    signal: ctrl.signal
  }).then(function (resp) {
    if (!resp.ok) throw new Error('HTTP ' + resp.status);
    const reader = resp.body.getReader();
    const decoder = new TextDecoder('utf-8');
    let buf = '';
    function pump() {
      return reader.read().then(function (r) {
        if (r.done) { finish(); return; }
        buf += decoder.decode(r.value, { stream: true });
        let sep;
        while ((sep = buf.indexOf('\n\n')) >= 0) {
          const raw = buf.slice(0, sep); buf = buf.slice(sep + 2);
          if (!raw.startsWith('data: ')) continue;
          let payload;
          try { payload = JSON.parse(raw.slice(6)); } catch (e) { continue; }
          if (payload.type === 'token') {
            full += payload.data;
            bubble.classList.remove('typing');
            bubble.querySelector('.body').innerHTML = esc(full).replace(/\n/g, '<br>');
            list.scrollTop = list.scrollHeight;
          } else if (payload.type === 'done') { finish(); return; }
        }
        return pump();
      });
    }
    return pump();
  }).catch(function (e) {
    sendBtn.textContent = btnOrig;
    sendBtn.disabled = false;
    bubble.remove();
    // 用户主动停止不算错误：保留已生成内容
    if (e && e.name === 'AbortError') {
      if (full) { appendMsg('agent', full, { query: q }, 'homeMsgList'); }
      pendingSessionTitle = null;
      renderChatSessions();
      return;
    }
    appendMsg('agent', '出错了：' + e.message, null, 'homeMsgList');
    pendingSessionTitle = null;
    renderChatSessions();
  });
}
// 关键交互绑定段：各自 try-catch 隔离——任何一段在个别浏览器环境抛错，都不连累其他功能
try {
  document.getElementById('homeSend').addEventListener('click', () => {
    homeAsk(document.getElementById('homeInput').value.trim());
  });
} catch (e) { console.warn('homeSend 绑定失败', e); }
// 输入框自动高度（多行自适应，超宽自动换行不再向左延伸）
function autoResizeInput(el) {
  if (!el) return;
  el.style.height = 'auto';
  el.style.height = Math.min(el.scrollHeight, 120) + 'px';
}
try {
  ['homeInput', 'qaInput', 'waInput'].forEach(function (id) {
    var el = document.getElementById(id);
    if (el) el.addEventListener('input', function () { autoResizeInput(el); });
  });
  document.getElementById('homeInput').addEventListener('keydown', e => {
    if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); document.getElementById('homeSend').click(); }
  });
} catch (e) { console.warn('输入框绑定失败', e); }
try {
  document.querySelectorAll('#view-home .chip, .chips-row .chip').forEach(chip => {
  chip.addEventListener('click', () => {
    const q = chip.dataset.q;
    // 「帮我创建一本新书」→ 直接走建书流程（新建小说弹窗 → 创建后 Agent 引导建库）
    if (q && q.indexOf('创建一本新书') >= 0) { openNewNovel(); return; }
    // 「看看我的灵感库」→ 跳灵感库页
    if (q && q.indexOf('看看我的灵感库') >= 0) { showView('inspiration'); return; }
    if (document.getElementById('view-home').classList.contains('active')) {
      homeAsk(q);
    } else {
      document.getElementById('qaInput').value = q;
      ask();
    }
  });
});
} catch (e) { console.warn('chip 绑定失败', e); }

// ===== 多会话切换（对话历史分组 / 对比不同对话效果）=====
async function renderChatSessions() {
  try {
    const data = await apiGet('/api/chat/sessions');
    const list = document.getElementById('chatSessionList');
    // 只看首页闲聊组（scope=home），项目对话按书走项目侧边栏
    const homeSessions = (data.sessions || []).filter(s => s.scope === 'home');
    // 当前会话是否已在后端列表里（有真实记录或 ensure 占位）
    const inList = homeSessions.some(s => s.session_id === sessionId);
    if (!homeSessions.length && !pendingSessionTitle) {
      list.innerHTML = '<div class="side-item hide-when-collapsed" style="cursor:default;color:var(--text-3)">还没有对话，点下方＋新建</div>';
      return;
    }
    const itemHtml = (s, archived) => `
      <div class="side-item ${s.session_id === sessionId ? 'active' : ''} ${archived ? 'archived' : ''}" data-session="${escAttr(s.session_id)}" title="${escAttr(s.last_msg || '')}">
        <span class="hide-when-collapsed side-title">${esc((s.title || s.last_msg || '新对话').slice(0, 14))}</span>
        <span class="hide-when-collapsed meta">${s.pinned ? '📌' : ''}</span>
        <span class="hide-when-collapsed session-ops">
          <button class="del-session side-menu-btn" data-menu="${escAttr(s.session_id)}" title="管理对话">⋮</button>
        </span>
      </div>`;
    // 分组：置顶 + 活跃在前，归档移到底部「归档」分组
    const active = homeSessions.filter(s => !s.archived);
    const archived = homeSessions.filter(s => s.archived);
    const sortActive = [...active].sort((a, b) => (b.pinned ? 1 : 0) - (a.pinned ? 1 : 0));
    let itemsHtml = sortActive.map(s => itemHtml(s, false)).join('');
    if (archived.length) {
      itemsHtml += '<div class="side-group hide-when-collapsed">归档</div>' +
        archived.map(s => itemHtml(s, true)).join('');
    }
    // 本地占位：新会话还没落库（ensure 未回 / 失败）也要立刻出现在列表顶部，置顶高亮
    if (pendingSessionTitle && !inList) {
      itemsHtml = `<div class="side-item active" data-session="${escAttr(sessionId)}" title="${escAttr(pendingSessionTitle)}">
        <span class="hide-when-collapsed side-title">${esc(pendingSessionTitle.slice(0, 14))}</span>
      </div>` + itemsHtml;
    }
    list.innerHTML = itemsHtml;
    list.querySelectorAll('.side-item[data-session]').forEach(item => {
      item.addEventListener('click', e => {
        if (e.target.classList.contains('del-session') || e.target.classList.contains('side-menu-btn')) return;
        switchChatSession(item.dataset.session);
      });
      // 双击改名
      item.addEventListener('dblclick', e => {
        if (e.target.classList.contains('del-session')) return;
        renameSessionDialog(item.dataset.session);
      });
    });
    // ⋮ 管理菜单（仿 DSH：重命名 / 置顶 / 归档 / 导出 / 删除）
    list.querySelectorAll('.side-menu-btn').forEach(btn => {
      btn.addEventListener('click', e => {
        e.stopPropagation();
        const sid = btn.dataset.menu;
        const item = list.querySelector('.side-item[data-session="' + sid + '"]');
        const s = homeSessions.find(x => x.session_id === sid) || {};
        openSessionMenu(btn, sid, s);
      });
    });
  } catch (e) { console.error(e); }
}
let sessionMenuEl = null;
function closeSessionMenu() {
  if (sessionMenuEl) { sessionMenuEl.remove(); sessionMenuEl = null; }
  document.removeEventListener('click', closeSessionMenu);
}
function openSessionMenu(anchor, sid, s) {
  closeSessionMenu();
  const menu = document.createElement('div');
  menu.className = 'session-menu';
  const item = (label, fn) => {
    const b = document.createElement('button');
    b.textContent = label;
    b.addEventListener('click', e => {
      e.stopPropagation();
      closeSessionMenu();
      fn();
    });
    return b;
  };
  menu.appendChild(item('🗑 删除对话', () => deleteSessionDialog(sid)));
  menu.appendChild(item(s.pinned ? '📌 取消置顶' : '📌 置顶', () =>
    apiCall('/api/chat/session/flag', { scope: 'home', session_id: sid, pinned: !s.pinned })
      .then(renderChatSessions).catch(err => alert('操作失败: ' + err.message))));
  menu.appendChild(item('✏️ 重命名', () => renameSessionDialog(sid)));
  menu.appendChild(item(s.archived ? '📂 取消归档' : '🗄 归档', () =>
    apiCall('/api/chat/session/flag', { scope: 'home', session_id: sid, archived: !s.archived })
      .then(() => { if (sid === sessionId) newChatSession(); renderChatSessions(); })
      .catch(err => alert('操作失败: ' + err.message))));
  menu.appendChild(item('⤓ 导出对话', () => exportSession(sid)));
  document.body.appendChild(menu);
  // 定位在按钮下方（右对齐）
  const r = anchor.getBoundingClientRect();
  menu.style.position = 'fixed';
  menu.style.left = Math.min(r.right - 160, window.innerWidth - 180) + 'px';
  menu.style.top = (r.bottom + 4) + 'px';
  sessionMenuEl = menu;
  setTimeout(() => document.addEventListener('click', closeSessionMenu), 0);
}
function renameSessionDialog(sid) {
  const item = document.querySelector('.side-item[data-session="' + sid + '"]');
  const cur = item ? (item.querySelector('.side-title') || {}).textContent : '';
  const name = prompt('重命名这段对话：', cur);
  if (name == null) return;
  const trimmed = name.trim();
  if (!trimmed) return;
  apiCall('/api/chat/session/rename', { scope: 'home', session_id: sid, title: trimmed })
    .then(() => renderChatSessions())
    .catch(err => alert('重命名失败: ' + err.message));
}
function deleteSessionDialog(sid) {
  if (!confirm('删除这段对话？历史记录将清空。')) return;
  fetch(`${API_BASE}/api/chat/session?scope=home&session_id=${encodeURIComponent(sid)}`, { method: 'DELETE' })
    .then(() => {
      if (sid === sessionId) newChatSession();
      renderChatSessions();
    })
    .catch(err => alert('删除失败: ' + err.message));
}


function setWelcomeVisible(show) {
  // 欢迎块与消息区互斥：显示欢迎块时隐藏消息区（反之亦然），避免争抢空间
  const hw = document.getElementById('homeWelcome');
  if (hw) hw.hidden = !show;
  const list = document.getElementById('homeMsgList');
  if (list) list.classList.toggle('visible', !show);
}
function switchChatSession(sid) {
  if (!sid) return;
  sessionId = sid;
  try { localStorage.setItem('novel_island_session', sessionId); } catch (e) {}
  pendingSessionTitle = null;  // 切换到已有会话，清掉占位
  // 清空首页消息区，加载该会话历史（欢迎块是独立节点，不受影响）
  const list = document.getElementById('homeMsgList');
  list.innerHTML = '';
  loadChatHistory();  // 内部会 restoreDraft（恢复该会话草稿）
  renderChatSessions();
}
function newChatSession() {
  sessionId = 'web-' + Date.now().toString(36) + '-' + Math.random().toString(36).slice(2, 8);
  try { localStorage.setItem('novel_island_session', sessionId); } catch (e) {}
  const list = document.getElementById('homeMsgList');
  list.innerHTML = '';
  setWelcomeVisible(true);
  // 本地占位「新对话」（不发后端请求、不落库，刷新即消失）：
  // 点「＋新建对话」立刻在列表看到反馈，避免误以为没开成功、消息悄悄进旧会话；
  // 发消息时 homeAsk 会覆盖成消息内容并 ensure 建真实会话（未发送仍不建会话）
  pendingSessionTitle = '新对话';
  renderChatSessions();
  restoreDraft();  // 恢复该会话输入框草稿（未发送内容保留）
  showView('home');
}
// ===== 输入框草稿（未发送内容本地保存，刷新/切会话不丢）=====
function draftKey() { return 'novel_island_draft_' + sessionId; }
function saveDraft() {
  try {
    const el = document.getElementById('homeInput');
    localStorage.setItem(draftKey(), el ? el.value : '');
  } catch (e) {}
}
function restoreDraft() {
  try {
    const el = document.getElementById('homeInput');
    if (!el) return;
    el.value = localStorage.getItem(draftKey()) || '';
    autoResizeInput(el);
  } catch (e) {}
}
function clearDraft() {
  try { localStorage.removeItem(draftKey()); } catch (e) {}
}
async function loadChatHistory() {
  try {
    const data = await apiGet(`/api/chat/history?scope=home&session_id=${encodeURIComponent(sessionId)}`);
    const hist = data.history || [];
    const list = document.getElementById('homeMsgList');
    if (!hist.length) {
      setWelcomeVisible(true);
      restoreDraft();  // 空会话：恢复草稿
      return;
    }
    list.innerHTML = '';
    setWelcomeVisible(false);
    hist.forEach(m => {
      appendMsg(m.role === 'user' ? 'user' : 'agent', m.content, null, 'homeMsgList');
    });
    list.scrollTop = list.scrollHeight;
    restoreDraft();  // 有历史的会话：也恢复草稿
  } catch (e) { console.error(e); }
}
// 导出会话对话为 markdown/txt
async function exportSession(sid) {
  try {
    const data = await apiGet(`/api/chat/history?scope=home&session_id=${encodeURIComponent(sid)}&limit=1000`);
    const hist = data.history || [];
    if (!hist.length) { alert('这段对话还没有内容可导出'); return; }
    const title = (hist.find(m => m.role === 'user') || {}).content || '对话';
    const lines = ['# 小说岛 · 对话导出', '', `会话：${title.slice(0, 30)}`, `时间：${new Date().toLocaleString()}`, ''];
    hist.forEach(m => {
      const who = m.role === 'user' ? '**我**' : '**小说猫**';
      lines.push(`### ${who}  ${new Date((m.created_at || 0) * 1000).toLocaleString()}`);
      lines.push('', m.content, '');
    });
    const blob = new Blob([lines.join('\n')], { type: 'text/markdown;charset=utf-8' });
    const a = document.createElement('a');
    a.href = URL.createObjectURL(blob);
    a.download = `小说岛-${(title || '对话').slice(0, 20)}.md`;
    a.click();
    URL.revokeObjectURL(a.href);
  } catch (e) { alert('导出失败: ' + e.message); }
}
document.getElementById('btnNewChatSession').addEventListener('click', newChatSession);
// 输入框草稿：输入即保存（未发送内容跨刷新/切会话保留）
try {
  const hi = document.getElementById('homeInput');
  if (hi) hi.addEventListener('input', saveDraft);
} catch (e) {}
// 初始化时只渲染会话列表。
// 默认进入首页显示吉祥物欢迎页（main.js 的 showView('home') 已重置消息区）；
// 点左侧会话列表（switchChatSession）才加载对应会话历史（含草稿）。
renderChatSessions();
