// ===== 登录 / 注册（UI 先行，接口三期） =====
document.getElementById('btnLoginHeader').addEventListener('click', () => {
  document.getElementById('btnLoginHeader').style.display = 'none';
  document.getElementById('userName').style.display = 'inline';
  showView('home');
});
function toggleLoginMode() {
  loginMode = loginMode === 'login' ? 'register' : 'login';
  document.getElementById('loginPwd2Field').style.display = loginMode === 'register' ? 'block' : 'none';
  document.getElementById('btnLogin').textContent = loginMode === 'register' ? '注册' : '登录';
  document.getElementById('loginSwitch').innerHTML = loginMode === 'register'
    ? '已有账号？<a id="loginToggle">直接登录</a>' : '还没有账号？<a id="loginToggle">立即注册</a>';
  document.getElementById('loginToggle').addEventListener('click', toggleLoginMode);
}
document.getElementById('loginToggle').addEventListener('click', toggleLoginMode);
document.getElementById('btnLogin').addEventListener('click', () => {
  const email = document.getElementById('loginEmail').value;
  document.getElementById('loginErr').style.display = email.includes('@') ? 'none' : 'block';
  if (!email.includes('@')) return;
  document.getElementById('btnLoginHeader').style.display = 'none';
  document.getElementById('userName').style.display = 'inline';
  showView('home');
});

// ===== 新建引导 =====
function showAgentWelcome(title) {
  appendMsg('agent', `恭喜创建《${title}》！知识库已就绪（空库也能问答）。\n\n接下来可以按这个顺序开始：\n① 直接在这里告诉我你的主角是谁、故事发生在哪——我边聊边帮你建库\n② 或在「写作编辑器」保存第一章（自动生成章纲、抽取人物、更新知识库）\n③ 素材也可以直接拖进输入框，我自动解析入库`);
}

// 「看看我的灵感库」chip → 灵感库页
document.querySelectorAll('.chip[data-q="看看我的灵感库"]').forEach(c => {
  c.addEventListener('click', () => showView('inspiration'));
});

// ===== 弹窗通用关闭（Esc / 点遮罩）=====
document.querySelectorAll('.modal-mask').forEach(mask => {
  mask.addEventListener('click', e => { if (e.target === mask) mask.classList.remove('show'); });
});
document.addEventListener('keydown', e => {
  if (e.key === 'Escape') document.querySelectorAll('.modal-mask.show').forEach(m => m.classList.remove('show'));
});


  // 自诊断版本徽标（看到它 = JS 正常执行）
  var vb = document.createElement('div');
  vb.id = 'versionBadge';
  vb.style.cssText = 'position:fixed;right:12px;bottom:8px;font-size:11px;color:#9c9c9c;z-index:9998;font-family:monospace';
  document.body.appendChild(vb);
  updateBadge();
  fetch(API_BASE + '/api/health').then(function (r) { return r.json(); })
    .then(function (d) { badgeBackend = '✓ 后端已连接 (' + (d.model || 'ok') + ')'; updateBadge(); })
    .catch(function () { badgeBackend = '✗ 后端未连接'; updateBadge(); });
// ===== Agent 设置（模型 / 温度 / 人设语气）=====
var agentSettings = { model: '', complexModel: 'kimi-k2.6', temperature: 0.8, persona: '', presets: [], theme: 'default' };
try {
  var saved = JSON.parse(localStorage.getItem('novel_island_agent') || '{}');
  if (saved && typeof saved === 'object') agentSettings = Object.assign(agentSettings, saved);
  if (!Array.isArray(agentSettings.presets)) agentSettings.presets = [];
} catch (e) {}
// 主题应用（默认 default 明亮）
function applyTheme(theme) {
  document.body.setAttribute('data-theme', theme || 'default');
  var cards = document.querySelectorAll('#themeGrid .theme-card');
  cards.forEach(function (c) { c.classList.toggle('active', c.dataset.theme === (theme || 'default')); });
}
function agentSettingsBody() {
  // 组装 ask 请求的 agent 设置字段（persona 拼进 query 前的系统层，由后端透传）
  var b = {};
  if (agentSettings.model) b.model = agentSettings.model;
  if (agentSettings.complexModel && agentSettings.complexModel !== 'kimi-k2.6') b.complex_model = agentSettings.complexModel;
  if (agentSettings.temperature != null) b.temperature = Number(agentSettings.temperature);
  if (agentSettings.persona && agentSettings.persona.trim()) b.persona = agentSettings.persona.trim();
  return b;
}
function saveAgentSettings() {
  try { localStorage.setItem('novel_island_agent', JSON.stringify(agentSettings)); } catch (e) {}
  applyTheme(agentSettings.theme);
}
function renderAgentPresets() {
  const wrap = document.getElementById('agentPresetList');
  if (!wrap) return;
  if (!agentSettings.presets.length) { wrap.innerHTML = '<span style="font-size:12px;color:var(--text-3)">暂无预设</span>'; return; }
  wrap.innerHTML = agentSettings.presets.map((pr, i) => `
    <span class="agent-preset-chip ${pr.persona === agentSettings.persona ? 'on' : ''}" data-i="${i}" title="${escAttr(pr.persona || '')}">
      ${esc(pr.name)} <b class="preset-del" data-i="${i}">×</b>
    </span>`).join('');
  wrap.querySelectorAll('.agent-preset-chip').forEach(ch => {
    ch.addEventListener('click', e => {
      if (e.target.classList.contains('preset-del')) return;
      const pr = agentSettings.presets[Number(ch.dataset.i)];
      if (!pr) return;
      agentSettings.persona = pr.persona || '';
      document.getElementById('agentPersonaInput').value = agentSettings.persona;
      saveAgentSettings();
      renderAgentPresets();
    });
    ch.querySelector('.preset-del').addEventListener('click', e => {
      e.stopPropagation();
      agentSettings.presets.splice(Number(ch.dataset.i), 1);
      saveAgentSettings();
      renderAgentPresets();
    });
  });
}
document.getElementById('btnAgentSettings').addEventListener('click', () => {
  document.getElementById('agentModelSel').value = agentSettings.model || '';
  document.getElementById('agentComplexSel').value = agentSettings.complexModel || 'kimi-k2.6';
  document.getElementById('agentTempSlider').value = String(agentSettings.temperature != null ? agentSettings.temperature : 0.8);
  document.getElementById('agentTempVal').textContent = String(agentSettings.temperature != null ? agentSettings.temperature : 0.8);
  document.getElementById('agentPersonaInput').value = agentSettings.persona || '';
  renderAgentPresets();
  applyTheme(agentSettings.theme);
  // 默认回到 Agent tab
  document.querySelectorAll('#settingsTabs .settings-tab').forEach(function (t) { t.classList.remove('active'); });
  document.querySelector('#settingsTabs .settings-tab[data-pane="agent"]').classList.add('active');
  document.querySelectorAll('.settings-pane').forEach(function (pn) { pn.classList.remove('active'); });
  document.getElementById('pane-agent').classList.add('active');
  document.getElementById('agentSettingsModal').classList.add('show');
});
// 设置 tab 切换
document.getElementById('settingsTabs').addEventListener('click', e => {
  const btn = e.target.closest('.settings-tab');
  if (!btn) return;
  document.querySelectorAll('#settingsTabs .settings-tab').forEach(t => t.classList.remove('active'));
  btn.classList.add('active');
  document.querySelectorAll('.settings-pane').forEach(pn => pn.classList.remove('active'));
  document.getElementById('pane-' + btn.dataset.pane).classList.add('active');
});
// 主题选择（即时生效）
document.getElementById('themeGrid').addEventListener('click', e => {
  const card = e.target.closest('.theme-card');
  if (!card) return;
  applyTheme(card.dataset.theme);
});
document.getElementById('btnSavePreset').addEventListener('click', () => {
  const name = document.getElementById('agentPresetName').value.trim();
  const persona = document.getElementById('agentPersonaInput').value.trim();
  if (!name || !persona) { alert('预设名称和人设内容都要填'); return; }
  agentSettings.presets = agentSettings.presets.filter(pr => pr.name !== name);
  agentSettings.presets.push({ name: name, persona: persona });
  saveAgentSettings();
  document.getElementById('agentPresetName').value = '';
  renderAgentPresets();
});
document.getElementById('agentTempSlider').addEventListener('input', e => {
  document.getElementById('agentTempVal').textContent = e.target.value;
});
document.getElementById('btnAgentSettingsSave').addEventListener('click', () => {
  agentSettings.model = document.getElementById('agentModelSel').value || '';
  agentSettings.complexModel = document.getElementById('agentComplexSel').value || 'kimi-k2.6';
  agentSettings.temperature = parseFloat(document.getElementById('agentTempSlider').value) || 0.8;
  agentSettings.persona = document.getElementById('agentPersonaInput').value.trim();
  const activeTheme = document.querySelector('#themeGrid .theme-card.active');
  agentSettings.theme = activeTheme ? activeTheme.dataset.theme : 'default';
  saveAgentSettings();
  document.getElementById('agentSettingsModal').classList.remove('show');
});
document.getElementById('btnAgentSettingsReset').addEventListener('click', () => {
  agentSettings = { model: '', complexModel: 'kimi-k2.6', temperature: 0.8, persona: '', presets: agentSettings.presets || [], theme: 'default' };
  saveAgentSettings();
  document.getElementById('agentSettingsModal').classList.remove('show');
});
document.getElementById('agentSettingsModal').addEventListener('click', e => {
  if (e.target === document.getElementById('agentSettingsModal')) document.getElementById('agentSettingsModal').classList.remove('show');
});
// 初始化应用已保存主题
applyTheme(agentSettings.theme);
