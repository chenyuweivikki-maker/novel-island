// ===== 视图切换 =====
var VIEW_NAMES = { home: '首页', works: '我的作品', chat: '创作', inspiration: '灵感库', community: '社区', login: '登录' };
function showView(view) {
  // 内联样式强制切换：不再依赖 .active 类，杜绝 CSS 层叠失效
  var names = ['home', 'works', 'chat', 'inspiration', 'community', 'login'];
  for (var i = 0; i < names.length; i++) {
    var el = document.getElementById('view-' + names[i]);
    if (!el) continue;
    var show = names[i] === view;
    el.style.display = show ? (names[i] === 'chat' || names[i] === 'home' ? 'flex' : 'block') : 'none';
  }
  document.querySelectorAll('#topNav a').forEach(function (i) {
    i.classList.toggle('active', NAV_VIEW[i.dataset.nav] === view);
  });
  // 项目区：仅创作视图显示（点击项目即进入项目侧边栏）
  document.getElementById('sideProjects').style.display = view === 'chat' ? 'block' : 'none';
  // 对话会话列表：仅首页显示（其他 tab 的侧边栏不需要历史对话）
  document.getElementById('sideChats').style.display = view === 'home' ? 'block' : 'none';
  // 整个侧边栏：仅首页/创作显示；灵感库/我的作品/社区等无侧边栏内容 → 整栏隐藏不占位
  var sideBar = document.getElementById('appSidebar');
  var collapseBtn = document.getElementById('btnSideCollapse');
  var hasSidebar = (view === 'home' || view === 'chat');
  sideBar.style.display = hasSidebar ? 'flex' : 'none';
  if (collapseBtn) collapseBtn.style.display = hasSidebar ? 'block' : 'none';
  if (view === 'works') { loadWorks(); }
  if (view === 'inspiration') { loadInspiration(); }
  if (view === 'community') { loadCommunity(); }
  if (view === 'home') {
    // 点「首页」/左上角 Logo → 回到吉祥物欢迎页（参考 Figma 首页：吉祥物 + 标题 + 引导 chips + 输入框）。
    // 每次进入首页都重置为欢迎页（清空消息区 + 显示欢迎块）；
    // 只有用户点左侧对话列表（switchChatSession）才加载并显示对应会话内容。
    try {
      const homeList = document.getElementById('homeMsgList');
      if (homeList) homeList.innerHTML = '';
      const hw = document.getElementById('homeWelcome');
      if (hw) hw.hidden = false;
    } catch (e) {}
  }
  if (view === 'chat') {
    if (!currentNovelId) selectDefaultChat();
    renderSidebar();
    if (currentNovelId) refreshKBViews();
  }
  setBadge(VIEW_NAMES[view] ? '视图:' + VIEW_NAMES[view] + ' · ' : '');
}
var badgeView = '', badgeBackend = '检测后端…';
function setBadge(t) { badgeView = t; updateBadge(); }
function updateBadge() {
  var vb = document.getElementById('versionBadge');
  if (!vb) return;
  // 诊断探针：JS 执行阶段 + 最近 tab 点击 + switchSubPage 是否抛错 + 当前激活页面（排查"tab 失灵"用）
  var stage = window.__jsStage || '?';
  var tab = window.__lastTab ? ' · tab:' + window.__lastTab : '';
  var tabErr = window.__lastTabErr ? ' · tabErr:' + window.__lastTabErr : '';
  var actPage = '';
  try {
    var ap = document.querySelector('.page.active');
    if (ap) actPage = ap.id;
  } catch (e) {}
  vb.textContent = (window.__buildId || 'v1.2') + ' · ' + badgeView + badgeBackend + ' · JS:' + stage + tab + tabErr
    + ' · page:' + actPage;
}
// 每 2 秒刷新徽标，让 JS 阶段 / tab 点击状态实时可见（诊断用）
setInterval(function () { try { updateBadge(); } catch (e) {} }, 2000);
document.querySelector('.logo').addEventListener('click', () => showView('home'));
var NAV_VIEW = { home: 'home', chat: 'chat', inspiration: 'inspiration', works: 'works', community: 'community' };
document.querySelectorAll('#topNav a').forEach(function (item) {
  item.addEventListener('click', function () {
    showView(NAV_VIEW[item.dataset.nav]);
  });
});

function timeAgo(iso) {
  if (!iso) return '—';
  const d = new Date(iso), now = new Date();
  const h = Math.floor((now - d) / 3600000);
  if (h < 1) return Math.max(1, Math.floor((now - d) / 60000)) + ' 分钟前';
  if (h < 24) return h + ' 小时前';
  return Math.floor(h / 24) + ' 天前';
}