"""配置管理 — 从环境变量读取"""
import os
from dotenv import load_dotenv

load_dotenv(os.path.join(os.path.dirname(__file__), "..", "..", ".env"))


class Settings:
    DEEPSEEK_API_KEY: str = os.getenv("DEEPSEEK_API_KEY", "")
    DEEPSEEK_BASE_URL: str = os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com")
    DEEPSEEK_MODEL: str = os.getenv("DEEPSEEK_MODEL", "deepseek-chat")

    # Moonshot (Kimi) — 高阶创作模型（PRD：复杂逻辑纠错/人设深度分析/核心灵感拓展）
    # OpenAI 兼容协议，缺 key 时自动回退 DeepSeek，不影响主流程
    MOONSHOT_API_KEY: str = os.getenv("MOONSHOT_API_KEY", "")
    MOONSHOT_BASE_URL: str = os.getenv("MOONSHOT_BASE_URL", "https://api.moonshot.cn/v1")
    MOONSHOT_MODEL: str = os.getenv("MOONSHOT_MODEL", "kimi-k2-turbo-preview")

    # 腾讯混元 — 高阶创作模型（备选；PRD实测 HY3 续写风格/避雷提醒最强）
    HUNYUAN_API_KEY: str = os.getenv("HUNYUAN_API_KEY", "")
    HUNYUAN_BASE_URL: str = os.getenv("HUNYUAN_BASE_URL", "https://api.hunyuan.cloud.tencent.com/v1")
    HUNYUAN_MODEL: str = os.getenv("HUNYUAN_MODEL", "hunyuan-turbos-latest")

    # SiliconFlow (Embedding)
    SILICONFLOW_API_KEY: str = os.getenv("SILICONFLOW_API_KEY", "")
    SILICONFLOW_BASE_URL: str = os.getenv("SILICONFLOW_BASE_URL", "https://api.siliconflow.cn/v1")
    EMBEDDING_MODEL: str = os.getenv("EMBEDDING_MODEL", "BAAI/bge-m3")

    HOST: str = os.getenv("HOST", "0.0.0.0")
    PORT: int = int(os.getenv("PORT", "8000"))

    # RAG params
    CHUNK_SIZE: int = 400       # 每块目标字数
    CHUNK_OVERLAP: int = 60     # 重叠字数
    TOP_K: int = 5              # 检索返回数量

    # ===== 额度 / 熔断（P3-2，默认 0 = 不限制，.env 里可开启）=====
    DAILY_COST_LIMIT: float = float(os.getenv("DAILY_COST_LIMIT", "0"))      # 单日成本上限(元)，超限自动降级模型
    DAILY_ASK_LIMIT: int = int(os.getenv("DAILY_ASK_LIMIT", "0"))            # 单日对话次数上限，超限熔断提示
    KB_CAPACITY_LIMIT: int = int(os.getenv("KB_CAPACITY_LIMIT", "0"))        # 每本知识库字数上限，超限阻止入库


settings = Settings()
