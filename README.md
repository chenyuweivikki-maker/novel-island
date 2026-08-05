# Novel Island - 小说岛：创作者的第二大脑

> AI Agent平台，为中长篇网文创作者提供知识管理、一致性校验、灵感拓展和情感陪伴

## 项目结构

```
novel-island/
├── backend/                 # Python后端
│   ├── app/
│   │   ├── core/           # 核心配置（数据库连接、LLM客户端）
│   │   ├── nodes/          # LangGraph节点定义
│   │   ├── agents/         # 四大子Agent（FactQA/Logic/Inspiration/Companion）
│   │   ├── tools/          # LangChain工具集（CRUD操作）
│   │   ├── models/         # 数据模型（State Schema、ORM模型）
│   │   └── routers/        # API路由
│   └── tests/
├── frontend/               # 前端
│   └── public/            # 静态页面（原型阶段）
├── config/                # 配置文件
├── data/                  # 数据目录
│   ├── sample/           # 示例小说文本
│   └── sessions/         # 会话数据
└── docs/                  # 文档
```

## 技术栈

| 层 | 技术 | 职责 |
|----|------|------|
| 编排层 | LangGraph | 状态机、节点编排、条件路由 |
| 执行层 | LangChain | 工具定义、Agent调用 |
| 数据层 | LlamaIndex | 文档解析、分块、向量化 |
| 存储层 | Qdrant + Neo4j + PostgreSQL + Redis | 向量/图/关系/缓存 |

## 模型策略

| 级别 | 模型 | 用途 |
|------|------|------|
| 高阶 | Claude Opus / DeepSeek V4pro | 复杂推理、核心灵感拓展 |
| 主力 | DeepSeek-V4-Flash / Qwen3-turbo | 日常对话、摘要、续写 |
| 兜底 | DeepSeek-V4-Flash | 数据预处理、意图识别 |

## 实施路线

- [x] Phase 0: 项目骨架搭建
- [ ] Phase 1: 数据管线 & 基础RAG（MVP）
- [ ] Phase 2: 知识图谱构建
- [ ] Phase 3: Agent编排 & 记忆系统
- [ ] Phase 4: 前端 & 可视化
- [ ] Phase 5: 商业化 & 运营

## 快速开始

```bash
# 后端
cd backend
pip install -r requirements.txt
uvicorn app.main:app --reload

# 前端原型
cd frontend/public
python3 -m http.server 8080
```
