// ===== 通用可拖拽分栏（左右调整宽度）=====
// 在相邻两个元素之间插一个拖拽手柄，按住拖动即可调整一侧宽度，并记住用户调过的大小。
function makeResizable(leftEl, rightEl, opts = {}) {
  if (!leftEl || !rightEl) return;
  const { min = 140, max = 640, target = 'left', persistKey = '' } = opts;
  const bar = document.createElement('div');
  bar.className = 'resizer';
  // 手柄悬在两元素之间（插在 rightEl 之前）
  leftEl.parentNode.insertBefore(bar, rightEl);

  bar.addEventListener('mousedown', e => {
    e.preventDefault();
    const startX = e.clientX;
    const startW = target === 'left'
      ? leftEl.getBoundingClientRect().width
      : rightEl.getBoundingClientRect().width;
    document.body.classList.add('resizing');
    const move = ev => {
      let w = startW + (target === 'left' ? (ev.clientX - startX) : -(ev.clientX - startX));
      w = Math.max(min, Math.min(max, w));
      const el = target === 'left' ? leftEl : rightEl;
      el.style.width = w + 'px';
      if (persistKey) try { localStorage.setItem(persistKey, w); } catch (_) {}
    };
    const up = () => {
      document.body.classList.remove('resizing');
      document.removeEventListener('mousemove', move);
      document.removeEventListener('mouseup', up);
    };
    document.addEventListener('mousemove', move);
    document.addEventListener('mouseup', up);
  });

  // 恢复持久化宽度
  if (persistKey) {
    try {
      const saved = parseInt(localStorage.getItem(persistKey), 10);
      if (saved && saved >= min && saved <= max) {
        (target === 'left' ? leftEl : rightEl).style.width = saved + 'px';
      }
    } catch (_) {}
  }
}

function initResizers() {
  // 1) 全局侧边栏 × 主内容区
  const sidebar = document.getElementById('appSidebar');
  const main = document.querySelector('.main');
  if (sidebar && main) {
    makeResizable(sidebar, main, { min: 170, max: 460, target: 'left', persistKey: 'novel-island-sidebar-w' });
  }
  // 2) 写作编辑器三栏：章节列表 × 编辑区 × AI 对话
  const ws = document.querySelector('.write-sidebar');
  const we = document.querySelector('.write-editor');
  const wa = document.querySelector('.write-ai');
  if (ws && we) {
    makeResizable(ws, we, { min: 150, max: 400, target: 'left', persistKey: 'novel-island-writeside-w' });
  }
  if (we && wa) {
    makeResizable(we, wa, { min: 240, max: 560, target: 'right', persistKey: 'novel-island-writeai-w' });
  }
}

document.addEventListener('DOMContentLoaded', initResizers);
