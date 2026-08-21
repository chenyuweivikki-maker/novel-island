# 小说岛 · Agent 能力诊断与补足方案

> 日期：2026-08-21 · 诊断方式：读代码 + 对运行中后端做真实问答实测（15 个测试点）
> 结论：**Agent 能力"未达预期"的主因不是模型不行，而是数据链路断了 + 知识库本身残缺**

---

## 一、三分钟结论

| # | 问题 | 根因 | 状态 |
|---|------|------|------|
| 1 | 首页没有吉祥物欢迎页 | `homeWelcome` 是消息列表子元素，清空列表时被连带删除 | ✅ 已修（已合并 main） |
| 2 | Agent 问答全部"知识库信息不足" | 入库只更新向量库，TF-IDF 检索器永远落后；Agent 工具/空库判断都用 TF-IDF | ✅ 已修（已合并 main） |
| 3 | 知识库内容残缺 | 项目 21 只有 7 个对话式抽取的短块（大量"未知"占位），39KB 真实设定从未入库 | 🔄 已建库（见下文） |
| 4 | 检索实现七处各写各的 | 向量优先/TF-IDF/混合检索三种写法散落各处 | ⚠️ 已修工具层，架构债待收敛 |
| 5 | 代码管理与记忆维护 | 检索入口不一致、TF-IDF/向量双索引不同步、main.py 业务逻辑混杂 | ⚠️ 已修关键，建议见文末 |

---

## 二、问题 1：首页没有吉祥物欢迎页（已修复）

### 现象
- 打开首页只有输入框 + 底部文字，没有吉祥物/标题/引导 chips
- 用户需求：点左上角图标或"首页"→ 吉祥物首页；点左侧对话列表 → 显示会话内容；底部文字"你写作的时候，我会一直在"

### 根因（代码级）
`index.html` 里 `homeWelcome`（吉祥物欢迎块）是 `homeMsgList`（消息列表）的**子元素**：

```html
<div class="home-msgs" id="homeMsgList">
  <div class="home-welcome" id="homeWelcome">...</div>  <!-- 子元素 -->
</div>
```

`home.js` 的 `loadChatHistory()` 和 `switchChatSession()` 用 `list.innerHTML = ''` 清空消息区时，**把欢迎块一起删了**。`nav.js` 的 `showView('home')` 里虽然有恢复逻辑（`hw.style.display = 'flex'`），但此时 `hw` 已被删除为 null，恢复失效。于是刷新后首页永远没有吉祥物。

### 修复（已合并 main，前端静态文件即时生效）
1. **欢迎块移出消息列表**，成为独立兄弟节点 → `innerHTML=''` 不再误删
2. **CSS**：欢迎块 `flex:1; justify-content:center` 垂直居中占位，有消息时自动让位
3. **统一 `hidden` 属性控制显隐**（不再混用 style.display）
4. **初始化不再自动加载历史**：首次进入首页 = 欢迎页（main.js 的 showView('home') 重置）；点左侧会话才加载
5. `homeAsk` 发消息时隐藏欢迎块，进入对话模式

### 验证（Playwright 实测 localhost:8000）
- ✅ 首次打开 → 吉祥物 200px + "小说岛" + 4 chips + 底部文案
- ✅ 点左侧会话 → 显示该会话历史（10 条消息），欢迎块隐藏
- ✅ 点"首页" → 回到吉祥物欢迎页
- ✅ 点左上角 Logo → 回到吉祥物欢迎页
- ✅ 底部文字"你写作的时候，我会一直在。"

---

## 三、问题 2：Agent 问答全部"知识库信息不足"（已修复）

### 现象（实测 5 个典型问题全部失败）
```
问"唐嘉措的宠物是什么" → "知识库中目前没有关于唐嘉措宠物的具体信息"
问"男主是做什么工作的" → "无法确认小说中是否存在'男主'这一角色"
问"卡文了，后面怎么写" → "当前知识库信息不足，无法给出具体灵感建议"
问"唐嘉措的人设崩了吗" → "当前知识库信息不足，无法检查人设一致性"
问"帮我检查逻辑矛盾" → "当前知识库信息不足，无法检查逻辑矛盾"
```

### 根因（代码级）
**检索双索引不同步**：
- 对话式建库/整稿建库只更新**向量库**（`vs.add_chunks()`），**从不重建 TF-IDF 检索器**
- 而 `/api/kb/ask` 的空库判断（`if not r.is_ready`）和 Agent 的 `search_kb` 工具都用 **TF-IDF**（`r.search`）
- 结果：入库内容向量有、TF-IDF 查不到 → 问答退化成"空库引导"

复现证据：
```
启动时 restore → novel 21 chunks=7（向量库 7 块）
运行中 /api/kb/status?novel_id=21 → chunks=1（TF-IDF 只 1 块）
```
（后端 8/20 启动，vector_21.npz 8/21 被更新，TF-IDF 未跟随）

### 修复（已合并 main，重启后端后生效）
1. **`services/kb.py` 新增 `_rebuild_tfidf()`**：入库后从向量库全量重建该项目 TF-IDF（数据量小，重建便宜）
2. **`ingest_material()` 和 `/api/kb/build`** 入库后都调用 `_rebuild_tfidf()`
3. **`tools/kb_tools.py` 的 `execute_search_kb` / `brainstorm_plot_ideas`** 改用 `hybrid_search`（向量+TF-IDF 合并），不再单依赖 TF-IDF

### 验证（重启后）
- ✅ 后端重启 → `novel 21 chunks=7`（restore 正常）
- ✅ 问"唐嘉措的宠物是什么" → 精确属性检索命中（不再"信息不足"）

---

## 四、问题 3：知识库内容残缺（已建库）

### 现象
项目 21 的图谱里唐嘉措的 persona 全是"未知"占位（职业/性格/家庭/经历/创伤/动机/宠物/年龄/外貌/物品全"未知"），只有对话式抽取的 7 个短块。

### 根因
39KB 的《观南嘉措资料》（真实 13608 字人设+大纲+前三章）在 `data/sample/` 里放着，**从未通过 `/api/kb/build` 完整入库**。之前只靠首页对话自动抽取了几个零散设定。

### 处理
2026-08-21 用 `/api/kb/build`（mode=init, novel_id=21）完整建库（见建库脚本输出）。

---

## 五、问题 4：检索实现七处各写各的（架构债）

### 现状（改一处漏两处的高风险区）
| 位置 | 实现 |
|------|------|
| `main.py:524-536` | 向量优先 → TF-IDF 兜底（手写） |
| `main.py:662` | 纯 TF-IDF |
| `tools/kb_tools.py` | ✅ 已改 hybrid_search |
| `tools/consistency_tools.py:83-90` | 向量 + TF-IDF 分离各查一次 |
| `hybrid_retriever.py` | 标准混合（向量+TF-IDF 合并去重） |
| `qa_nodes.py:398` | 纯 TF-IDF（多跳灵感 Hop2） |
| `qa_nodes.py:72` | RetrieveNode 用 `get_retriever_for().search()` 纯 TF-IDF |

### 建议（不紧急，下次大改时做）
统一所有检索入口到 `hybrid_search()`。改动点：
1. `qa_nodes.py` 的 `RetrieveNode` / `MultiHopInspirationNode` Hop2 → 用 `hybrid_search`
2. `main.py:662`（/api/kb/retrieve）→ 用 `hybrid_search`
3. `consistency_tools.py` → 用 `hybrid_search`
4. 删除 `main.py` 里手写的"向量优先 TF-IDF 兜底"（:524-536），统一走 `hybrid_search`

---

## 六、问题 5：代码管理与记忆维护（建议）

### 现状问题
1. **检索入口不统一**（见上）→ 本次就踩了：工具层改 hybrid、主链路还是 TF-IDF，差一点漏
2. **TF-IDF 与向量库双索引不同步** → 本次核心 bug。根治需 `_rebuild_tfidf` 成为入库链路固定环节
3. **main.py 仍 1387 行**：空库引导（`EMPTY_KB_SYSTEM_PROMPT` / `_llm_empty_kb_reply`）和 `services/chat.py` 的 `NO_PROJECT_*` 逻辑高度相似但各自实现，行为可能漂移
4. **开发记忆维护**：`docs/开发记忆.md` 是好实践（每次改动更新），但记忆里的"当前进度"部分落后于实际（记忆 8/10 写的里程碑 18，实际已到 8/21 的 20+ 提交）
5. **前端模块边界**：9 个 js 文件按域拆分合理，但 `home.js` 里混合了会话管理 + 草稿 + 导出，可再拆

### 建议的维护规则（防"改一处影响别处"）
1. **检索必须单入口**：所有"查知识库"都走 `hybrid_search()`，删除各处的分离实现
2. **入库必须双索引同步**：`ingest_material` / `build_kb` / `save_chapter` 之后固定调 `_rebuild_tfidf()`
3. **main.py 瘦身**：空库引导逻辑统一收进 `services/`，main.py 只留路由
4. **记忆更新**：每次里程碑合并后更新 `docs/开发记忆.md` + 会话记忆的"当前进度"，别让记忆落后两个里程碑
5. **前端全局变量**：`var` 声明跨文件（state.js），新增模块注意命名冲突；`window.__*` 探针保留（诊断有用）

---

## 七、本次改动清单

| commit | 内容 |
|--------|------|
| `fcacb44` | fix(home): 恢复吉祥物首页——欢迎块独立于消息列表，点首页/Logo重置欢迎页，点会话才显示内容 |
| `d2f5e83` | fix(agent): 修复知识库检索链路——入库同步重建TF-IDF + 工具层改用混合检索 |

均已在 worktree（novel-island-setup）提交并 fast-forward 合并回 main（主文件夹）。
