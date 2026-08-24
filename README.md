# 小说岛 · Novel Island

> 网文创作者的 **AI 第二大脑** —— 知识管理 / 一致性校验 / 灵感拓展 / 情感陪伴

一个面向中长篇网文作者的 AI Agent 平台：**和 AI 聊设定，它帮你把项目记忆自动建起来**；再基于这些记忆做问答、查一致性、出大纲、润色、分析策略，还能在你卡文时陪你聊。

---

## ✨ 核心特性

- **对话式建库**：跟 AI 聊人设、关系、剧情，系统自动抽取成**项目记忆**（人物 / 关系 / 事件），并在**人物关系图**、**人设卡片**里实时呈现。*聊到什么，才记什么。*
- **不编造的映射**：抽取人物 / 关系 / 属性时做**原文证据校验**，凡是聊天里没出现过、或找不到原话支撑的内容，一律不写入记忆——AI 只做忠实记录，不做脑补创作。
- **知识库问答**：基于项目自己的记忆做问答，带**防幻觉质检**（LLM-as-Judge 打回重试），并做设定一致性校验。
- **创作工作台**：写作编辑器（三栏：章节 + 编辑区 + AI 对话联动）、人设卡片、人物关系图、时间线、章纲、背景资料。
- **自动大纲**：对话里提到梗概 / 主题 / 主线 / 冲突 / 结局 → 自动捕获进大纲；也可一键「✨ 生成大纲」。
- **灵感库**：素材上传 → AI 自动分类（人设 / 剧情 / 金句 / 世界观）→ 分类浏览 / 管理 / 分享到社区。
- **润色 Review**：选中文本对比"原文 vs 修改"，采纳 / 放弃会沉淀为偏好。
- **创作策略报告**：DataAnalyst 分析章节 + 图谱 + 大纲 → 结构化报告。
- **情感陪伴**：卡文、疲惫时的陪伴模式（保留"猫"式的温和语气）。
- **多项目隔离**：每个作品独立的图谱、知识库、会话，互不污染。

## 🧠 设计理念

> **对话 = 项目记忆的唯一来源。** 入库只取作者原话，抽取的实体名 / 属性 / 关系必须能在原文里找到证据（原文逐字出现 + 非通用词过滤），防止 LLM 从"都市 / 百合 / 两位女主角"这种零星信息里**脑补出不存在的角色、关系和人设**。作者随后在自己确认、修改的基础上写正文——AI 是作者的记录员与参谋，不是代写者。

## 🛠 技术栈

| 层 | 技术 | 职责 |
|----|------|------|
| 编排 | [LangGraph](https://langchain-ai.github.io/langgraph/) | 状态机、条件路由、并行抽取、防幻觉回边 |
| API | [FastAPI](https://fastapi.tiangolo.com/) | 后端接口（单端口同时托管前端） |
| LLM | DeepSeek（主力）+ Kimi（高阶）等 | 问答 / 抽取 / 质检 / 摘要 / 陪伴 |
| 检索 | TF-IDF (scikit-learn) + 向量库 **混合检索** | 项目知识库查询 |
| 图谱 | 内存 + JSON 持久化 | 实体 / 关系 / 事件，按项目隔离 |
| 前端 | 原生 HTML / CSS / JS（无框架） | 首页 / 工作台 / 灵感库 / 社区 |

## 📁 目录结构

```
novel-island/
├── backend/
│   ├── app/
│   │   ├── core/        # 配置/分块/检索/LLM客户端/记忆/模型路由/图谱/混合检索
│   │   ├── nodes/       # LangGraph 节点（建库抽取/问答）
│   │   ├── graphs/      # LangGraph 状态机（build_graph/qa_graph）
│   │   ├── services/    # 业务服务（自动建书/知识库入库/会话同步/无项目对话）
│   │   ├── tools/       # 工具集（search_kb 等）
│   │   ├── models/      # 状态定义
│   │   └── main.py      # FastAPI 入口 + 路由
│   └── requirements.txt
├── frontend/public/     # 前端（index.html + styles.css + js/ 按域模块）
├── data/                # 运行时数据（不入库）
├── docs/                # 项目开发手册 / 设计文档
├── Dockerfile
└── docker-compose.yml
```

## 🚀 快速开始（本地跑）

```bash
# 1. 克隆
git clone https://github.com/chenyuweivikki-maker/novel-island.git
cd novel-island

# 2. 后端（建议虚拟环境）
cd backend
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

# 3. 配置密钥（复制示例 → 填入你的 DeepSeek API Key）
cp ../.env.example .env

# 4. 启动（单端口同时托管前端）
uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload

# 打开 http://localhost:8000
```

> 默认用 `deepseek-chat`；高阶任务可用 Kimi。在 `.env` 里按示例填对应 API Key 即可。

## 🔌 主要接口

| 接口 | 功能 |
|------|------|
| `POST /api/novel` / `GET /api/novels` | 创建 / 列出作品（多项目） |
| `POST /api/kb/build` | 构建 / 增量入库（状态机并行抽取） |
| `POST /api/kb/ask` | 基于项目知识问答（意图路由→工具→质检→记忆） |
| `GET /api/graph` / `.../neighbors` / `.../path` | 人物关系图 / 邻居查询 / 多跳推理 |
| `GET /api/timeline` | 故事脉络（正文章节事件） |
| `POST /api/novel/{id}/outline` 等 | 大纲读写 / 生成 / 对话自动捕获 |
| `POST /api/chapter` | 保存章节（冲突检测 + 章纲 + 增量入库） |
| `POST /api/polish` | 润色（编辑器 Review） |
| `POST /api/analysis/report` | 创作策略报告 |
| `POST /api/inspirations` 等 | 灵感库 / 自动分类 |
| `POST /api/community/*` | 社区（发帖 / 点赞 / 评论 / 分享） |
| `GET /api/cost` / `GET /api/health` | 成本 / 健康检查 |

## 🗺 当前进度 & 路线图

> 基于开发手册（`docs/项目开发手册.md`），综合完成度约 **75%**。

**已完成**：对话式建库、问答状态机、防幻觉质检、并行抽取、三层记忆、模型路由 + 成本、知识图谱 + 多跳 + 多项目隔离、人物关系可视化、自动大纲、润色、创作策略报告、灵感库、社区、情感陪伴、前端五视图闭环。

**进行中 / 待办**：
- [ ] 检索入口统一到 `hybrid_search`（重构多套各自为政的检索逻辑）
- [ ] `main.py` 瘦身（路由编排进一步收进 `services/`）
- [ ] 埋点补齐 → 情绪曲线 / 节奏分析
- [ ] OutlineConsistencyNode（大纲一致性校验）
- [ ] 文本备份 / 版本管理
- [ ] 图片 OCR
- [ ] 付费墙
- [ ] 知识图谱迁移到 Neo4j

## ⚠️ 说明

- 仓库不包含任何 `.env` 密钥、用户数据库、虚拟环境等运行时敏感 / 体积文件（见 `.gitignore`）。
- 项目以作者为核心的设计理念正在逐步落地；欢迎基于 `docs/` 里的开发手册和设计文档了解实现细节。
