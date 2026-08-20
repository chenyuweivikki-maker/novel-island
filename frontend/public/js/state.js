// ═══════════════════════════════════════════════════════════
// 小说岛 · 前端（设计系统 v1.0 重构版）
// ═══════════════════════════════════════════════════════════
var API_BASE = 'http://localhost:8000';

// ═══ 自诊断探针（排查"tab 失灵"用）：徽标显示脚本执行阶段 & 最近一次 tab 点击 ═══
window.__buildId = 'v2-0821d';
window.__jsStage = 'start';      // start → delegation → end（脚本执行到哪一步）
window.__lastTab = '';           // 最近点击的 tab（如 '写作 12:03:45'）
window.__tabHits = 0;            // 委托捕获到的 tab 点击次数

// ===== STATE =====
var KB = { chunks: [], entities: [], characters: [], relationships: [], ready: false };
var currentNovelId = null;
var currentSubPage = 'chat';
var companionMode = false;
var loginMode = 'login'; // login | register
var messages = [];       // 对话消息列表
// 会话 ID（localStorage 持久化：刷新页面不丢，支持对比不同对话的效果）
var sessionId = null;
try { sessionId = localStorage.getItem('novel_island_session') || null; } catch (e) {}
if (!sessionId) {
  sessionId = 'web-' + Date.now().toString(36) + '-' + Math.random().toString(36).slice(2, 8);
  try { localStorage.setItem('novel_island_session', sessionId); } catch (e) {}
}
// 本地占位会话标题（主流 AI 产品模式）：
// 新建对话 / 发出首条消息时，后端会话还没落库，先用这个标题在左侧列表渲染一个置顶项；
// 后端 ensure 成功或一轮回复落库后，用真实数据替换。null = 无占位。
var pendingSessionTitle = null;

// ═══ 关键交互绑定提前注册（防御性重构）═══
// 创作页 sub-tab 用 document 级事件委托，注册后永久生效：
// 即使后续脚本在个别浏览器环境里中断，tab 也不会"点了没反应"。
try {
  document.addEventListener('click', function (e) {
    var el = e.target;
    while (el && el !== document) {
      if (el.classList && el.classList.contains('sub-tab')) {
        window.__tabHits = (window.__tabHits || 0) + 1;
        window.__lastTab = (el.textContent || '').trim() + ' ' + new Date().toLocaleTimeString();
        try {
          switchSubPage(el.getAttribute('data-subpage'));
          window.__lastTabErr = '';
        } catch (err) {
          window.__lastTabErr = String((err && err.message) || err).slice(0, 120);
        }
        return;
      }
      el = el.parentNode;
    }
  });
  window.__jsStage = 'delegation';
} catch (e) {}

trackSessionStart();
function trackSessionStart() {
  try {
    fetch(`${API_BASE}/api/track`, {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ event: 'session_start', session_id: sessionId, props: { entry_point: 'web' } })
    }).catch(() => {});
  } catch (e) {}
}
window.addEventListener('beforeunload', () => {
  try {
    navigator.sendBeacon(`${API_BASE}/api/track`, JSON.stringify({ event: 'session_end', session_id: sessionId, props: { entry_point: 'web' } }));
  } catch (e) {}
});

// ===== API =====
async function apiCall(path, body) {
  const resp = await fetch(`${API_BASE}${path}`, {
    method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body)
  });
  if (!resp.ok) throw new Error(`API ${resp.status}: ${await resp.text()}`);
  return resp.json();
}
async function apiGet(path) {
  const resp = await fetch(`${API_BASE}${path}`);
  if (!resp.ok) throw new Error(`API ${resp.status}: ${await resp.text()}`);
  return resp.json();
}