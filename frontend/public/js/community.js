// ===== 社区（作者互动：帖子 / 点赞 / 收藏 / 评论）=====
var commFilter = '';
var pendingShareInsp = null; // { id, content } 灵感分享来源

function postTypeName(t) {
  return { post: '📝 分享', inspiration: '💡 灵感分享', idea: '✨ 使用心得', question: '❓ 提问' }[t] || '📝 分享';
}
async function loadCommunity() {
  try {
    const data = await apiGet(`/api/community/posts?post_type=${encodeURIComponent(commFilter)}`);
    const list = document.getElementById('commPosts');
    const posts = data.posts || [];
    if (!posts.length) {
      list.innerHTML = `<div class="pd-empty">社区还空着，来发第一条帖子吧 —— 分享灵感、使用心得或提问。</div>`;
      return;
    }
    list.innerHTML = posts.map(p => `
      <div class="comm-post" data-id="${p.id}">
        <div class="p-head">
          <span class="p-type">${postTypeName(p.post_type)}</span>
          ${p.novel_title ? `<span class="p-type">📕 ${esc(p.novel_title)}</span>` : ''}
          <span class="p-meta"><span class="author">${esc(p.author)}</span></span>
          <span class="p-meta">${timeAgo(p.created_at * 1000)}</span>
          <span class="p-actions">
            <button class="p-act" onclick="togglePostLike(${p.id}, this)">👍 <span>${p.like_count}</span></button>
            <button class="p-act" onclick="togglePostFav(${p.id}, this)">⭐ <span>${p.favorite_count}</span></button>
            <button class="p-act" onclick="openPostDetail(${p.id})">💬 <span>${p.comment_count}</span></button>
          </span>
        </div>
        <div class="p-title" onclick="openPostDetail(${p.id})">${esc(p.title)}</div>
        <div class="p-content" onclick="openPostDetail(${p.id})">${esc(p.content)}</div>
        ${p.inspiration_content ? `<div class="p-insp-quote">💡 ${esc(p.inspiration_content)}</div>` : ''}
      </div>`).join('');
  } catch (e) {
    document.getElementById('commPosts').innerHTML = `<div class="pd-empty">加载失败: ${esc(e.message)}</div>`;
  }
}
async function togglePostLike(id, btn) {
  try {
    const d = await apiCall(`/api/community/post/${id}/like`, {});
    btn.classList.toggle('on', d.liked);
    btn.querySelector('span').textContent = d.like_count;
  } catch (e) { alert('操作失败: ' + e.message); }
}
async function togglePostFav(id, btn) {
  try {
    const d = await apiCall(`/api/community/post/${id}/favorite`, {});
    btn.classList.toggle('on', d.favorited);
    btn.querySelector('span').textContent = d.favorite_count;
  } catch (e) { alert('操作失败: ' + e.message); }
}
async function openPostDetail(id) {
  try {
    const d = await apiGet(`/api/community/post/${id}`);
    const p = d.post;
    const cs = d.comments || [];
    document.getElementById('postDetailBody').innerHTML = `
      <div class="pd-post">
        <div class="p-type" style="margin-bottom:6px">${postTypeName(p.post_type)}</div>
        <div class="pd-title">${esc(p.title)}</div>
        <div class="pd-content">${esc(p.content)}</div>
        ${p.inspiration_content ? `<div class="p-insp-quote" style="margin-top:8px">💡 ${esc(p.inspiration_content)}</div>` : ''}
        <div class="p-meta" style="margin-top:8px">${esc(p.author)} · ${timeAgo(p.created_at * 1000)} · 👍 ${p.like_count} · ⭐ ${p.favorite_count}</div>
      </div>
      <div class="pd-comments" id="pdComments">
        ${cs.length ? cs.map(c => `
          <div class="pd-comment">
            <div class="c-author">${esc(c.author)}<span class="c-time">${timeAgo(c.created_at * 1000)}</span></div>
            <div class="c-content">${esc(c.content)}</div>
          </div>`).join('') : `<div class="pd-empty">还没有评论，来抢沙发～</div>`}
      </div>`;
    document.getElementById('commentInput').value = '';
    document.getElementById('commentInput').dataset.postId = id;
    document.getElementById('postDetailModal').classList.add('show');
  } catch (e) { alert('加载失败: ' + e.message); }
}
async function submitComment() {
  const input = document.getElementById('commentInput');
  const postId = input.dataset.postId;
  const text = input.value.trim();
  if (!postId || !text) return;
  try {
    await apiCall('/api/community/comment', { post_id: parseInt(postId), content: text });
    openPostDetail(parseInt(postId));
  } catch (e) { alert('评论失败: ' + e.message); }
}
// 灵感卡片「分享到社区」：事件委托（避免内联中文转义问题）
document.getElementById('inspItems').addEventListener('click', e => {
  const btn = e.target.closest('.share-btn');
  if (!btn) return;
  const id = parseInt(btn.dataset.inspId) || 0;
  const content = btn.dataset.inspContent || '';
  openPostModal({ id, content });
});
// 发帖弹窗
function openPostModal(shareInsp) {
  pendingShareInsp = shareInsp || null;
  document.getElementById('postTitle').value = '';
  document.getElementById('postContent').value = '';
  if (pendingShareInsp) {
    // 灵感分享：预填内容和类型
    document.querySelectorAll('#postTypeRow .post-type').forEach(b => b.classList.remove('active'));
    document.querySelector('#postTypeRow .post-type[data-type="inspiration"]').classList.add('active');
    document.getElementById('postTitle').value = '分享一条灵感';
    document.getElementById('postContent').value = pendingShareInsp.content;
    document.getElementById('postInspSrc').style.display = 'flex';
    document.getElementById('postInspPreview').textContent = pendingShareInsp.content;
  } else {
    document.getElementById('postInspSrc').style.display = 'none';
  }
  document.getElementById('postModal').classList.add('show');
}
function getSelectedPostType() {
  const el = document.querySelector('#postTypeRow .post-type.active');
  return el ? el.dataset.type : 'post';
}
async function submitPost() {
  const title = document.getElementById('postTitle').value.trim();
  const content = document.getElementById('postContent').value.trim();
  if (!title || !content) { alert('标题和内容都要填'); return; }
  try {
    await apiCall('/api/community/post', {
      title, content,
      post_type: getSelectedPostType(),
      inspiration_id: pendingShareInsp ? pendingShareInsp.id : 0,
    });
    document.getElementById('postModal').classList.remove('show');
    pendingShareInsp = null;
    loadCommunity();
  } catch (e) { alert('发布失败: ' + e.message); }
}
// 事件绑定
document.getElementById('commTabs').addEventListener('click', e => {
  const btn = e.target.closest('.comm-tab');
  if (!btn) return;
  document.querySelectorAll('#commTabs .comm-tab').forEach(t => t.classList.remove('active'));
  btn.classList.add('active');
  commFilter = btn.dataset.type;
  loadCommunity();
});
document.getElementById('btnNewPost').addEventListener('click', () => openPostModal(null));
document.getElementById('btnCancelPost').addEventListener('click', () => {
  document.getElementById('postModal').classList.remove('show');
  pendingShareInsp = null;
});
document.getElementById('btnSubmitPost').addEventListener('click', submitPost);
document.getElementById('postTypeRow').addEventListener('click', e => {
  const btn = e.target.closest('.post-type');
  if (!btn) return;
  document.querySelectorAll('#postTypeRow .post-type').forEach(b => b.classList.remove('active'));
  btn.classList.add('active');
});
document.getElementById('btnSubmitComment').addEventListener('click', submitComment);
document.getElementById('commentInput').addEventListener('keydown', e => {
  if (e.key === 'Enter') submitComment();
});
document.getElementById('postModal').addEventListener('click', e => {
  if (e.target === document.getElementById('postModal')) document.getElementById('postModal').classList.remove('show');
});
document.getElementById('postDetailModal').addEventListener('click', e => {
  if (e.target === document.getElementById('postDetailModal')) document.getElementById('postDetailModal').classList.remove('show');
});
