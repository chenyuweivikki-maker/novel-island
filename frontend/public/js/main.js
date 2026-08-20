// ===== 初始化 =====
window.__jsStage = 'end';  // 诊断探针：脚本完整执行到末尾
window.addEventListener('load', () => {
  showView('home');
  renderSidebar();
  bindGraphCanvasEvents();
});