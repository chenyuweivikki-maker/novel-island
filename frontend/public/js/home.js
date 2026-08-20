function enterNovel(id, title) {
  showView('chat');
  selectNovel(id, title);
}
function homeAsk(q) {
  if (!q) return;
  document.getElementById('homeInput').value = '';
  autoResizeInput(document.getElementById('homeInput'));
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
    let itemsHtml = homeSessions.map(s => `
      <div class="side-item ${s.session_id === sessionId ? 'active' : ''}" data-session="${escAttr(s.session_id)}" title="${escAttr(s.last_msg || '')}">
        <span class="hide-when-collapsed side-title">${esc((s.title || s.last_msg || '新对话').slice(0, 14))}</span>
        <span class="hide-when-collapsed session-ops">
          <button class="del-session" data-export="${escAttr(s.session_id)}" title="导出对话">⤓</button>
          <button class="del-session" data-del="${escAttr(s.session_id)}" title="删除该对话">×</button>
        </span>
      </div>`).join('');
    // 本地占位：新会话还没落库（ensure 未回 / 失败）也要立刻出现在列表顶部，置顶高亮
    if (pendingSessionTitle && !inList) {
      itemsHtml = `<div class="side-item active" data-session="${escAttr(sessionId)}" title="${escAttr(pendingSessionTitle)}">
        <span class="hide-when-collapsed side-title">${esc(pendingSessionTitle.slice(0, 14))}</span>
      </div>` + itemsHtml;
    }
    list.innerHTML = itemsHtml;
    list.querySelectorAll('.side-item[data-session]').forEach(item => {
      item.addEventListener('click', e => {
        if (e.target.classList.contains('del-session')) return;
        switchChatSession(item.dataset.session);
      });
      // 双击改名
      item.addEventListener('dblclick', e => {
        if (e.target.classList.contains('del-session')) return;
        const sid = item.dataset.session;
        const curEl = item.querySelector('.side-title');
        const cur = curEl ? curEl.textContent : '';
        const name = prompt('重命名这段对话：', cur);
        if (name == null) return;
        const trimmed = name.trim();
        if (!trimmed) return;
        apiCall('/api/chat/session/rename', { scope: 'home', session_id: sid, title: trimmed })
          .then(() => renderChatSessions())
          .catch(err => alert('重命名失败: ' + err.message));
      });
    });
    list.querySelectorAll('.del-session[data-export]').forEach(btn => {
      btn.addEventListener('click', e => {
        e.stopPropagation();
        exportSession(btn.dataset.export);
      });
    });
    list.querySelectorAll('.del-session').forEach(btn => {
      btn.addEventListener('click', async e => {
        e.stopPropagation();
        const sid = btn.dataset.del;
        if (!confirm('删除这段对话？历史记录将清空。')) return;
        try {
          await fetch(`${API_BASE}/api/chat/session?scope=home&session_id=${encodeURIComponent(sid)}`, { method: 'DELETE' });
          if (sid === sessionId) newChatSession();
          renderChatSessions();
        } catch (err) { alert('删除失败: ' + err.message); }
      });
    });
  } catch (e) { console.error(e); }
}
function switchChatSession(sid) {
  if (!sid) return;
  sessionId = sid;
  try { localStorage.setItem('novel_island_session', sessionId); } catch (e) {}
  pendingSessionTitle = null;  // 切换到已有会话，清掉占位
  // 清空首页消息区，加载该会话历史
  const list = document.getElementById('homeMsgList');
  list.innerHTML = '';
  const hw = document.getElementById('homeWelcome');
  if (hw) hw.style.display = 'none';
  loadChatHistory();
  renderChatSessions();
}
function newChatSession() {
  sessionId = 'web-' + Date.now().toString(36) + '-' + Math.random().toString(36).slice(2, 8);
  try { localStorage.setItem('novel_island_session', sessionId); } catch (e) {}
  const list = document.getElementById('homeMsgList');
  list.innerHTML = '';
  const hw = document.getElementById('homeWelcome');
  if (hw) { hw.style.display = 'flex'; }
  // 新建即占位（主流 AI 产品模式）：列表立刻出现「新对话」项置顶高亮；
  // 后端 ensure 建 system 占位行，成功后用真实记录替换（刷新也不丢）
  pendingSessionTitle = '新对话';
  renderChatSessions();
  apiCall('/api/chat/session/ensure', { scope: 'home', session_id: sessionId, title: '新对话' })
    .then(() => renderChatSessions())
    .catch(() => {});
  showView('home');
}
async function loadChatHistory() {
  try {
    const data = await apiGet(`/api/chat/history?scope=home&session_id=${encodeURIComponent(sessionId)}`);
    const hist = data.history || [];
    const list = document.getElementById('homeMsgList');
    const hw = document.getElementById('homeWelcome');
    if (!hist.length) {
      if (hw) hw.style.display = 'flex';
      return;
    }
    if (hw) hw.style.display = 'none';
    list.innerHTML = '';
    hist.forEach(m => {
      appendMsg(m.role === 'user' ? 'user' : 'agent', m.content, null, 'homeMsgList');
    });
    list.scrollTop = list.scrollHeight;
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
// 初始化时渲染会话列表 + 恢复当前会话历史
renderChatSessions();
loadChatHistory();
