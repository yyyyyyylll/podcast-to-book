from pathlib import Path

from dotenv import load_dotenv
from pydantic_settings import BaseSettings
from pydantic_settings import SettingsConfigDict
from typing import Optional

# 将 .env 中所有变量加载到 os.environ，
# 确保 Playwright 等第三方库能读到 PLAYWRIGHT_BROWSERS_PATH 等配置
# 注意：override=False —— 已存在的 os.environ 优先于 .env 文件（12-factor 标准），
# 这样工作台 .env.workbench（在 import core.config 之前已 load）才不会被 C 端 .env 反向覆盖。
load_dotenv(Path(__file__).resolve().parent.parent / ".env", override=False)


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # 应用配置
    APP_NAME: str = "EchoPress"
    APP_ENV: str = "development"
    DEBUG: bool = True
    
    # 数据库
    DATABASE_URL: str = "postgresql+asyncpg://postgres:postgres@localhost:5432/podbook"
    
    # 文件存储
    STORAGE_TYPE: str = "local"
    STORAGE_DIR: str = "./storage"
    
    # 阿里云 AI 服务（保留备用）
    DASHSCOPE_API_KEY: Optional[str] = None
    
    # 腾讯云 ASR
    TENCENT_SECRET_ID: Optional[str] = None
    TENCENT_SECRET_KEY: Optional[str] = None
    COS_BUCKET: str = ""
    COS_REGION: str = ""
    COS_PREFIX: str = "asr-audio"

    # Google Gemini API（保留备用）
    GOOGLE_API_KEY: Optional[str] = None

    # OpenAI API
    OPENAI_API_KEY: Optional[str] = None
    OPENAI_BASE_URL: Optional[str] = None

    # LLM 配置（统一入口：修改这里即全局生效）
    LLM_MODEL: str = "gemini-3-flash-preview"
    LLM_PREMIUM_MODEL: str = "gpt-5.2"
    LLM_SEARCH_MODEL: str = "gpt-5.2"
    LLM_TEMPERATURE: float = 0.7
    LLM_MAX_TOKENS: int = 65536

    # 计费费率（元/百万token）
    # gemini-3-flash-preview: ¥3.750 输入 / ¥22.500 输出
    COST_LLM_INPUT: float = 3.750
    COST_LLM_OUTPUT: float = 22.500
    # gpt-5.2: ¥13.125 输入 / ¥105.0 输出
    COST_LLM_PREMIUM_INPUT: float = 13.125
    COST_LLM_PREMIUM_OUTPUT: float = 105.0
    COST_SEARCH_INPUT: float = 13.125
    COST_SEARCH_OUTPUT: float = 105.0
    COST_SEARCH_PER_CALL: float = 0.03
    COST_ASR_PER_HOUR: float = 2.4

    # AI 图片生成
    IMAGE_GEN_MODEL: str = "doubao-seedream-5.0-lite"
    IMAGE_GEN_FALLBACK_MODEL: str = "doubao-seedream-4.5"
    IMAGE_GEN_SIZE: str = "2K"

    # VLM 审核模型
    VLM_REVIEW_MODEL: str = "qwen-vl-max"

    # 插图数量控制
    ILLUSTRATION_MAX_TOTAL: int = 12
    ILLUSTRATION_MAX_PER_CHAPTER: int = 3

    # Serper.dev Image Search (Google Images)
    SERPER_API_KEY: Optional[str] = None
    ILLUSTRATION_SEARCH_MAX_TOTAL: int = 5
    ILLUSTRATION_SEARCH_MAX_RESULTS: int = 10
    ILLUSTRATION_SEARCH_MAX_PER_CHAPTER: int = 1
    ILLUSTRATION_SEARCH_MIN_WIDTH: int = 800
    ILLUSTRATION_SEARCH_MIN_HEIGHT: int = 600

    # 图片生成/审核计费
    # doubao-seedream-5.0-lite: ¥0.22/张
    COST_IMAGE_GEN_PER_CALL: float = 0.22
    COST_IMAGE_SEARCH_PER_CALL: float = 0.003
    # qwen-vl-max: ¥3.0 输入 / ¥9.0 输出
    COST_VLM_INPUT: float = 3.0
    COST_VLM_OUTPUT: float = 9.0

    # 腾讯云短信（为空则进入开发模式，验证码固定 888888）
    TENCENT_SMS_APP_ID: str = ""
    TENCENT_SMS_SIGN: str = ""
    TENCENT_SMS_TEMPLATE_ID: str = ""

    # JWT 认证
    JWT_SECRET: str = "change-me-in-production"
    JWT_ACCESS_EXPIRE_MINUTES: int = 120
    JWT_REFRESH_EXPIRE_DAYS: int = 30

    # ── 积分体系（阶梯计费） ──
    CREDIT_WELCOME_AMOUNT: int = 5        # 新用户注册赠送
    CREDIT_COST_FIRST_BOOK_GENERATE: int = 5    # 首本书生成（预览半文）
    CREDIT_COST_FIRST_BOOK_UNLOCK: int = 5      # 首本书解锁全文
    CREDIT_COST_BOOK_GENERATE: int = 10         # 第二本起生成（直接全文）
    CREDIT_COST_UNLOCK_EDIT_EXPORT: int = 10    # 解锁编辑+导出（所有书通用）

    # 兼容旧逻辑的别名
    CREDIT_COST_EBOOK_PODCAST: int = 10  # 保留供 /packs 接口引用
    CREDIT_COST_EBOOK_STANDARD: int = 10
    CREDIT_COST_EBOOK_PREMIUM: int = 15
    CREDIT_COST_PRINT_MID: int = 10
    CREDIT_COST_PRINT_HIGH: int = 15

    # ── 邀请系统 ──
    REFERRAL_INVITEE_BONUS: int = 5      # 被邀请者注册即得积分
    REFERRAL_REWARD_CREDITS: int = 5     # 邀请人每次获得积分（被邀请者首次生成书后发放）
    REFERRAL_MAX_REWARDS: int = 10       # 邀请人最多奖励次数

    # 充值页展示定价（不改变单次扣费积分数）
    CREDIT_ORIGINAL_PRICE_YUAN: float = 20.0
    CREDIT_PROMO_PRICE_YUAN: float = 3.9
    CREDIT_PROMO_ACTIVE: bool = True
    CREDIT_PRICE_PER_UNIT_YUAN: float = 0.2

    # ── 积分包定义（JSON 数组，每项: name, credits, price_cents，可选 books） ──
    CREDIT_PACKS: str = '[{"name":"单本尝新","credits":20,"price_cents":390,"books":1},{"name":"三本畅读","credits":60,"price_cents":990,"books":3}]'

    # ── 支付宝 ──
    ALIPAY_APP_ID: str = ""
    ALIPAY_PRIVATE_KEY_PATH: str = ""
    ALIPAY_PUBLIC_KEY_PATH: str = ""
    ALIPAY_NOTIFY_URL: str = ""
    ALIPAY_RETURN_URL: str = ""
    ALIPAY_SANDBOX: bool = True

    # 管理后台访问密码（为空则不启用保护）
    ADMIN_SECRET: str = ""

    # 内部用户手机号白名单（逗号分隔，登录时自动标记 is_internal）
    INTERNAL_PHONES: str = ""

    # 前端静态文件目录
    FRONTEND_DIR: str = ""

    # 排版配置
    TYPESET_PAPER_SIZE: str = "iso-b5"
    TYPESET_FONT_PATHS: Optional[str] = None

    # Playwright 浏览器路径（固定绝对路径，避免沙盒环境 HOME 重定向导致找不到浏览器）
    PLAYWRIGHT_BROWSERS_PATH: str = ""

    # 小红书 agent 对接（为空则不导出）
    XHS_AGENT_CONTENT_DIR: str = ""

    # 内部 API（供 xhs-ops 等外部服务调用，为空则不启用）
    INTERNAL_API_KEY: str = ""
    
settings = Settings()

# Playwright 依赖 os.environ 来定位浏览器，必须强制设置（沙盒环境可能重定向 HOME）
if settings.PLAYWRIGHT_BROWSERS_PATH:
    import os
    os.environ["PLAYWRIGHT_BROWSERS_PATH"] = settings.PLAYWRIGHT_BROWSERS_PATH
