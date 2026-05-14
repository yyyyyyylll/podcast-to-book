# PodBook 技术架构文档

> 版本：v1.0 (MVP)  
> 更新日期：2026-02-25

---

## 1. 技术栈总览

| 层级 | 技术选型 | 说明 |
|------|----------|------|
| **前端** | React 18 + TypeScript | SPA应用 |
| **UI组件库** | Ant Design 5.x | 企业级UI组件 |
| **前端构建** | Vite | 快速开发构建 |
| **状态管理** | Zustand | 轻量级状态管理 |
| **后端** | Python 3.11 + FastAPI | 异步Web框架 |
| **工作流引擎** | LangGraph | AI Agent编排 |
| **数据库** | PostgreSQL 15 | 主数据库 |
| **ORM** | SQLAlchemy 2.0 | 数据库访问层 |
| **文件存储** | 本地文件系统 | MVP阶段使用本地存储，后续可切换OSS |
| **ASR服务** | 阿里云语音识别 | 音频转文字 |
| **LLM服务** | 通义千问 (qwen-max) | 文本处理 |
| **图片生成** | 通义万相 | 封面、插图生成 |
| **PDF生成** | WeasyPrint / ReportLab | HTML转PDF |
| **任务队列** | 后台线程 + asyncio | MVP阶段简化方案 |
| **部署** | 阿里云 ECS + Docker | 容器化部署 |

---

## 2. 系统架构图

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                              PodBook 系统架构                                │
├─────────────────────────────────────────────────────────────────────────────┤
│                                                                             │
│  ┌─────────────┐                                                           │
│  │   浏览器     │                                                           │
│  │  (React)    │                                                           │
│  └──────┬──────┘                                                           │
│         │ HTTP/REST                                                        │
│         ▼                                                                   │
│  ┌─────────────────────────────────────────────────────────────────────┐   │
│  │                        FastAPI 后端服务                              │   │
│  │  ┌──────────┐  ┌──────────┐  ┌──────────┐  ┌──────────────────┐    │   │
│  │  │ 上传API  │  │ 任务API  │  │ 结果API  │  │ 后台任务处理器    │    │   │
│  │  └──────────┘  └──────────┘  └──────────┘  └────────┬─────────┘    │   │
│  └─────────────────────────────────────────────────────┼───────────────┘   │
│                                                        │                    │
│         ┌──────────────────────────────────────────────┼──────────┐        │
│         │                LangGraph Workflow            │          │        │
│         │  ┌────────┐  ┌────────┐  ┌────────┐  ┌────────┐        │        │
│         │  │ 板块1  │─▶│ 板块2  │─▶│ 板块3  │─▶│ 板块4  │─┐     │        │
│         │  │内容解析│  │内容重组│  │精华提炼│  │文本润色│ │     │        │
│         │  └────────┘  └────────┘  └────────┘  └────────┘ │     │        │
│         │                                                  ▼     │        │
│         │                          ┌────────┐  ┌────────────┐   │        │
│         │                          │ 板块6  │◀─│   板块5    │   │        │
│         │                          │文本修改│  │  内容注释  │   │        │
│         │                          └───┬────┘  └────────────┘   │        │
│         │                              │                         │        │
│         │                              ▼                         │        │
│         │                        ┌──────────┐                   │        │
│         │                        │ 排版生成 │                   │        │
│         │                        └──────────┘                   │        │
│         └────────────────────────────────────────────────────────┘        │
│                                                                             │
│  ┌─────────────────┐  ┌─────────────────┐  ┌─────────────────────────┐    │
│  │   PostgreSQL    │  │   本地文件存储   │  │     阿里云 AI 服务       │    │
│  │   (任务数据)     │  │  (音频/PDF)     │  │ ASR + 通义千问 + 万相   │    │
│  └─────────────────┘  └─────────────────┘  └─────────────────────────┘    │
│                                                                             │
└─────────────────────────────────────────────────────────────────────────────┘
```

---

## 3. 项目目录结构

```
podbook/
├── backend/                    # 后端服务
│   ├── app/
│   │   ├── __init__.py
│   │   ├── main.py            # FastAPI 入口
│   │   ├── config.py          # 配置管理
│   │   ├── database.py        # 数据库连接
│   │   │
│   │   ├── api/               # API 路由
│   │   │   ├── __init__.py
│   │   │   ├── upload.py      # 上传接口
│   │   │   ├── tasks.py       # 任务接口
│   │   │   └── templates.py   # 模板接口
│   │   │
│   │   ├── models/            # 数据模型
│   │   │   ├── __init__.py
│   │   │   ├── task.py
│   │   │   └── template.py
│   │   │
│   │   ├── schemas/           # Pydantic 模型
│   │   │   ├── __init__.py
│   │   │   ├── task.py
│   │   │   └── template.py
│   │   │
│   │   ├── services/          # 业务服务
│   │   │   ├── __init__.py
│   │   │   ├── storage_service.py # 文件存储（本地/OSS可切换）
│   │   │   ├── asr_service.py     # 语音识别
│   │   │   ├── llm_service.py     # 通义千问调用
│   │   │   ├── image_service.py   # 通义万相调用
│   │   │   └── pdf_service.py     # PDF 生成
│   │   │
│   │   ├── workflow/          # LangGraph 工作流
│   │   │   ├── __init__.py
│   │   │   ├── graph.py       # 主工作流定义
│   │   │   ├── state.py       # 状态定义
│   │   │   └── nodes/         # 各板块节点
│   │   │       ├── __init__.py
│   │   │       ├── transcription.py   # 板块1：内容解析
│   │   │       ├── restructure.py     # 板块2：内容重组
│   │   │       ├── extraction.py      # 板块3：精华提炼
│   │   │       ├── polish.py          # 板块4：文本润色
│   │   │       ├── annotation.py      # 板块5：内容注释
│   │   │       ├── feedback.py        # 板块6：模拟反馈与修改
│   │   │       └── layout.py          # 排版生成
│   │   │
│   │   ├── templates/         # 排版模板（HTML/CSS）
│   │   │   ├── business/
│   │   │   ├── elegant/
│   │   │   └── minimal/
│   │   │
│   │   └── utils/             # 工具函数
│   │       ├── __init__.py
│   │       └── helpers.py
│   │
│   ├── tests/                 # 测试
│   ├── alembic/               # 数据库迁移
│   ├── requirements.txt
│   ├── Dockerfile
│   └── .env.example
│
├── frontend/                   # 前端应用
│   ├── src/
│   │   ├── main.tsx
│   │   ├── App.tsx
│   │   ├── vite-env.d.ts
│   │   │
│   │   ├── pages/             # 页面组件
│   │   │   ├── HomePage.tsx       # 首页/上传
│   │   │   ├── ProcessingPage.tsx # 处理中
│   │   │   └── ResultPage.tsx     # 结果页
│   │   │
│   │   ├── components/        # 通用组件
│   │   │   ├── AudioUploader.tsx
│   │   │   ├── TemplateSelector.tsx
│   │   │   ├── ProgressTracker.tsx
│   │   │   ├── ResultTabs.tsx
│   │   │   └── PdfPreview.tsx
│   │   │
│   │   ├── stores/            # 状态管理
│   │   │   └── taskStore.ts
│   │   │
│   │   ├── services/          # API 调用
│   │   │   └── api.ts
│   │   │
│   │   ├── types/             # 类型定义
│   │   │   └── index.ts
│   │   │
│   │   └── styles/            # 样式
│   │       └── global.css
│   │
│   ├── public/
│   ├── index.html
│   ├── package.json
│   ├── tsconfig.json
│   ├── vite.config.ts
│   └── Dockerfile
│
├── storage/                    # 文件存储目录（MVP本地存储）
│   ├── uploads/               # 用户上传的音频
│   └── outputs/               # 生成的PDF和图片
├── docker-compose.yml          # 本地开发环境
├── docker-compose.prod.yml     # 生产环境
└── README.md
```

---

## 4. 核心模块设计

### 4.1 数据库设计

#### tasks 表
```sql
CREATE TABLE tasks (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    title VARCHAR(255) NOT NULL,
    author VARCHAR(255) NOT NULL,
    description TEXT,
    template_id UUID REFERENCES templates(id),
    
    -- 文件路径（本地存储使用相对路径，如 uploads/xxx/audio.mp3）
    audio_path TEXT NOT NULL,
    pdf_path TEXT,
    
    -- 状态
    status VARCHAR(50) DEFAULT 'pending',  -- pending/processing/completed/failed
    current_stage VARCHAR(50),              -- transcription/restructure/extraction/polish/annotation/feedback/layout
    progress INTEGER DEFAULT 0,             -- 0-100
    error_message TEXT,
    
    -- 各板块结果（JSONB存储）
    result_transcription JSONB,   -- 板块1结果
    result_chapters JSONB,        -- 板块2结果
    result_highlights JSONB,      -- 板块3结果
    result_polished JSONB,        -- 板块4结果
    result_annotations JSONB,     -- 板块5结果
    result_feedback JSONB,        -- 板块6结果
    result_final JSONB,           -- 最终文稿
    
    -- 时间戳
    created_at TIMESTAMP DEFAULT NOW(),
    updated_at TIMESTAMP DEFAULT NOW(),
    completed_at TIMESTAMP
);

CREATE INDEX idx_tasks_status ON tasks(status);
CREATE INDEX idx_tasks_created_at ON tasks(created_at DESC);
```

#### templates 表
```sql
CREATE TABLE templates (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name VARCHAR(100) NOT NULL,
    description TEXT,
    preview_image_url TEXT,
    
    -- 模板配置
    config JSONB NOT NULL,  -- 包含字体、颜色、布局等配置
    
    is_active BOOLEAN DEFAULT true,
    sort_order INTEGER DEFAULT 0,
    
    created_at TIMESTAMP DEFAULT NOW(),
    updated_at TIMESTAMP DEFAULT NOW()
);
```

### 4.2 API 接口设计

#### 上传接口
```
POST /api/v1/upload
Content-Type: multipart/form-data

请求参数：
- file: 音频文件 (required)
- title: 书名 (required)
- author: 作者名 (required)
- description: 简介 (optional)
- template_id: 模板ID (required)

响应：
{
    "task_id": "uuid",
    "status": "pending",
    "message": "任务已创建，即将开始处理"
}
```

#### 任务状态查询
```
GET /api/v1/tasks/{task_id}

响应：
{
    "id": "uuid",
    "title": "商业访谈录",
    "author": "张三",
    "status": "processing",
    "current_stage": "extraction",
    "progress": 45,
    "stages": {
        "transcription": "completed",
        "restructure": "completed",
        "extraction": "processing",
        "polish": "pending",
        "annotation": "pending",
        "feedback": "pending",
        "layout": "pending"
    },
    "created_at": "2026-02-25T10:00:00Z"
}
```

#### 任务结果获取
```
GET /api/v1/tasks/{task_id}/result

响应：
{
    "id": "uuid",
    "title": "商业访谈录",
    "status": "completed",
    "results": {
        "transcription": { ... },
        "chapters": { ... },
        "highlights": { ... },
        "polished": { ... },
        "annotations": { ... },
        "feedback": { ... },
        "final": { ... }
    },
    "pdf_url": "/files/outputs/xxx-task-id/book.pdf"
}
```

#### 模板列表
```
GET /api/v1/templates

响应：
{
    "templates": [
        {
            "id": "uuid",
            "name": "商业画册风",
            "preview_image_url": "https://...",
            "description": "适合商业访谈、案例分析类内容"
        },
        ...
    ]
}
```

### 4.3 LangGraph 工作流设计

#### State 定义
```python
from typing import TypedDict, Optional, List
from langgraph.graph import StateGraph

class PodBookState(TypedDict):
    # 输入
    task_id: str
    audio_path: str  # 本地文件路径
    title: str
    author: str
    template_id: str
    
    # 板块1输出
    transcription: Optional[dict]  # {segments: [{speaker, text, start, end}]}
    
    # 板块2输出
    chapters: Optional[dict]  # {title, theme, chapters: [{title, content}]}
    
    # 板块3输出
    highlights: Optional[dict]  # {quotes: [], methodologies: [], mindmap: {}}
    
    # 板块4输出
    polished_content: Optional[dict]  # {chapters: [{title, content, intro}]}
    
    # 板块5输出
    annotations: Optional[List[dict]]  # [{original_text, position, annotation}]
    
    # 板块6输出
    feedback_issues: Optional[List[dict]]  # [{text, issue, suggestion}]
    final_content: Optional[dict]  # 最终文稿
    
    # 排版输出
    pdf_path: Optional[str]  # 本地文件路径
    
    # 状态
    current_stage: str
    error: Optional[str]
```

#### 工作流图
```python
from langgraph.graph import StateGraph, END

def create_workflow():
    workflow = StateGraph(PodBookState)
    
    # 添加节点
    workflow.add_node("transcription", transcription_node)
    workflow.add_node("restructure", restructure_node)
    workflow.add_node("extraction", extraction_node)
    workflow.add_node("polish", polish_node)
    workflow.add_node("annotation", annotation_node)
    workflow.add_node("feedback", feedback_node)
    workflow.add_node("layout", layout_node)
    
    # 定义边（顺序执行）
    workflow.set_entry_point("transcription")
    workflow.add_edge("transcription", "restructure")
    workflow.add_edge("restructure", "extraction")
    workflow.add_edge("extraction", "polish")
    workflow.add_edge("polish", "annotation")
    workflow.add_edge("annotation", "feedback")
    workflow.add_edge("feedback", "layout")
    workflow.add_edge("layout", END)
    
    return workflow.compile()
```

---

## 5. 各板块技术实现细节

### 5.1 板块一：内容解析

```python
# workflow/nodes/transcription.py

async def transcription_node(state: PodBookState) -> PodBookState:
    """
    输入：audio_path (本地文件路径)
    输出：transcription (结构化转写结果)
    
    处理流程：
    1. 读取本地音频文件
    2. 调用阿里云ASR进行转写（支持声纹识别）
    3. 后处理：剔除口语词、合并短句、标记时间戳
    4. 返回结构化结果
    """
    pass
```

**阿里云ASR调用要点：**
- 使用智能语音交互服务（NLS）的录音文件识别接口
- 开启 `enable_speaker_diarization` 进行说话人分离
- 返回格式包含时间戳和说话人标签

**后处理逻辑：**
```python
# 需要剔除的口语词列表
FILLER_WORDS = ["嗯", "啊", "那个", "就是说", "然后", "对对对", ...]

def clean_transcription(segments):
    """清理口语表达"""
    for seg in segments:
        text = seg["text"]
        for word in FILLER_WORDS:
            text = text.replace(word, "")
        seg["text"] = text.strip()
    return segments
```

### 5.2 板块二：内容重组

```python
# workflow/nodes/restructure.py

RESTRUCTURE_PROMPT = """
你是一位专业的书籍编辑。请分析以下播客转写文稿，完成：

1. 判断内容类型（访谈、对谈、独白等）
2. 提炼核心主题（一句话概括）
3. 将内容重组为章节结构，为每章拟定标题
4. 保持原意的同时，让结构更适合书籍阅读

转写文稿：
{transcription}

请以JSON格式输出：
{{
    "content_type": "访谈",
    "core_theme": "...",
    "chapters": [
        {{"title": "第一章 xxx", "content": "...", "key_points": [...]}}
    ]
}}
"""
```

**LLM调用配置：**
- 模型：qwen-max
- temperature: 0.3（保持一致性）
- max_tokens: 8000（长文本输出）

### 5.3 板块三：精华提炼

```python
# workflow/nodes/extraction.py

EXTRACTION_PROMPT = """
请从以下书稿中提取精华内容：

1. 金句：10-20条最有启发性的语句
2. 方法论：总结可复用的思维模型或方法
3. 章节导语：为每章写一段100字左右的导读

书稿内容：
{chapters}

输出JSON格式：
{{
    "quotes": ["金句1", "金句2", ...],
    "methodologies": [
        {{"name": "方法论名称", "description": "描述", "steps": [...]}}
    ],
    "chapter_intros": [
        {{"chapter_title": "...", "intro": "..."}}
    ]
}}
"""
```

### 5.4 板块四：文本润色

```python
# workflow/nodes/polish.py

POLISH_PROMPT = """
你是一位资深的出版编辑。请对以下书稿进行润色：

要求：
1. 统一全书语气风格（正式但不失亲和）
2. 优化段落过渡，增强叙事连贯性
3. 调整阅读节奏（长短句交错，适当留白）
4. 在合适位置增加导语和解释性文字
5. 补充必要的行业背景知识

原始章节内容：
{chapter_content}

章节导语（供参考）：
{chapter_intro}

请输出润色后的完整章节内容。
"""
```

**批处理策略：**
- 逐章节润色，避免单次调用token过长
- 每章润色完成后更新数据库进度

### 5.5 板块五：内容注释

```python
# workflow/nodes/annotation.py

IDENTIFY_TERMS_PROMPT = """
请识别以下文本中需要注释的专有名词、人名、公司名、技术术语：

文本：
{text}

输出JSON数组：
[
    {{"term": "术语", "position": "原文出现位置的前后文", "type": "人名/公司/术语/概念"}}
]
"""

GENERATE_ANNOTATION_PROMPT = """
请为以下术语生成简洁的注释（50-100字）：

术语：{term}
类型：{type}
上下文：{context}

如果需要，可以参考以下搜索结果：
{search_results}

输出注释文本。
"""
```

**联网搜索策略：**
- 对于人名、公司名：调用搜索API获取最新信息
- 对于通用术语：优先使用LLM知识库
- 使用阿里云搜索服务或自建搜索接口

### 5.6 板块六：模拟读者反馈 & 修改

```python
# workflow/nodes/feedback.py

READER_FEEDBACK_PROMPT = """
请以一位普通读者的视角阅读以下文稿，指出：

1. 晦涩难懂的段落（标记位置，说明原因）
2. 逻辑跳跃或不连贯的地方
3. 可能产生误解的表述
4. 信息密度过大需要拆分的部分

文稿：
{content}

输出JSON：
{{
    "issues": [
        {{
            "text": "原文片段",
            "location": "章节/段落位置",
            "issue_type": "晦涩/跳跃/歧义/密集",
            "description": "问题描述",
            "suggestion": "修改建议"
        }}
    ]
}}
"""

FIX_ISSUES_PROMPT = """
请根据以下问题列表修改文稿：

原文：
{original_text}

问题：
{issues}

请输出修改后的文本，确保：
1. 解决所有标记的问题
2. 保持原意不变
3. 行文流畅自然
"""
```

### 5.7 排版生成

```python
# workflow/nodes/layout.py

async def layout_node(state: PodBookState) -> PodBookState:
    """
    输入：final_content, template_id, title, author
    输出：pdf_path (本地文件路径)
    
    处理流程：
    1. 加载模板配置
    2. 生成封面图（通义万相）
    3. 为各章节生成插图（可选）
    4. 渲染HTML
    5. HTML转PDF（WeasyPrint）
    6. 保存PDF到本地outputs目录
    """
    pass
```

**模板配置结构：**
```json
{
    "name": "商业画册风",
    "fonts": {
        "title": "Source Han Serif SC",
        "body": "Source Han Sans SC"
    },
    "colors": {
        "primary": "#1a1a1a",
        "accent": "#d4a574",
        "background": "#fefefe"
    },
    "layout": {
        "page_size": "A5",
        "margins": {"top": 20, "bottom": 25, "left": 20, "right": 20},
        "line_height": 1.8,
        "paragraph_spacing": 12
    },
    "cover": {
        "style": "minimal",
        "image_prompt_template": "专业商业书籍封面，{title}，简约现代风格"
    }
}
```

**PDF生成（WeasyPrint）：**
```python
from weasyprint import HTML, CSS

def generate_pdf(html_content: str, css_content: str) -> bytes:
    html = HTML(string=html_content)
    css = CSS(string=css_content)
    return html.write_pdf(stylesheets=[css])
```

---

## 6. 第三方服务集成

### 6.1 文件存储服务（本地实现）

MVP阶段使用本地文件系统存储，通过抽象接口设计，后续可无缝切换到阿里云OSS。

```python
# services/storage_service.py

import os
import shutil
from abc import ABC, abstractmethod
from pathlib import Path
from app.config import settings

class StorageService(ABC):
    """存储服务抽象基类"""
    
    @abstractmethod
    async def save_file(self, file_path: str, key: str) -> str:
        """保存文件，返回访问路径/URL"""
        pass
    
    @abstractmethod
    async def get_file_path(self, key: str) -> str:
        """获取文件路径"""
        pass
    
    @abstractmethod
    async def delete_file(self, key: str) -> bool:
        """删除文件"""
        pass


class LocalStorageService(StorageService):
    """本地文件存储实现（MVP阶段使用）"""
    
    def __init__(self):
        self.base_dir = Path(settings.STORAGE_DIR)  # 如 ./storage
        self.uploads_dir = self.base_dir / "uploads"
        self.outputs_dir = self.base_dir / "outputs"
        
        # 确保目录存在
        self.uploads_dir.mkdir(parents=True, exist_ok=True)
        self.outputs_dir.mkdir(parents=True, exist_ok=True)
    
    async def save_file(self, file_path: str, key: str) -> str:
        """保存文件到本地，返回相对路径"""
        dest_path = self.base_dir / key
        dest_path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(file_path, dest_path)
        return str(key)
    
    async def save_upload(self, file_content: bytes, filename: str, task_id: str) -> str:
        """保存上传的文件"""
        key = f"uploads/{task_id}/{filename}"
        dest_path = self.base_dir / key
        dest_path.parent.mkdir(parents=True, exist_ok=True)
        dest_path.write_bytes(file_content)
        return key
    
    async def get_file_path(self, key: str) -> str:
        """获取文件的完整本地路径"""
        return str(self.base_dir / key)
    
    async def get_file_url(self, key: str) -> str:
        """获取文件的访问URL（供前端下载）"""
        return f"/files/{key}"
    
    async def delete_file(self, key: str) -> bool:
        """删除文件"""
        file_path = self.base_dir / key
        if file_path.exists():
            file_path.unlink()
            return True
        return False


# 后续可添加 OSSStorageService 实现
# class OSSStorageService(StorageService):
#     """阿里云OSS存储实现"""
#     pass


# 工厂函数，根据配置返回对应实现
def get_storage_service() -> StorageService:
    if settings.STORAGE_TYPE == "local":
        return LocalStorageService()
    # elif settings.STORAGE_TYPE == "oss":
    #     return OSSStorageService()
    else:
        return LocalStorageService()
```

**目录结构：**
```
storage/
├── uploads/           # 用户上传的音频文件
│   └── {task_id}/
│       └── audio.mp3
└── outputs/           # 生成的文件
    └── {task_id}/
        ├── cover.png
        ├── chapter_1.png
        └── book.pdf
```

**FastAPI 静态文件服务：**
```python
# main.py
from fastapi.staticfiles import StaticFiles

app.mount("/files", StaticFiles(directory="storage"), name="files")
```

### 6.2 阿里云 ASR

```python
# services/asr_service.py

from alibabacloud_nls20190201.client import Client
from app.config import settings

class ASRService:
    def __init__(self):
        self.client = Client(
            access_key_id=settings.ALIYUN_ACCESS_KEY,
            access_key_secret=settings.ALIYUN_SECRET_KEY,
            region_id="cn-shanghai"
        )
    
    async def transcribe(self, audio_url: str) -> dict:
        """
        提交录音文件识别任务
        返回：{task_id, segments: [{speaker, text, start_time, end_time}]}
        """
        # 1. 提交任务
        request = CreateAsrTaskRequest()
        request.set_file_link(audio_url)
        request.set_enable_speaker_diarization(True)  # 开启说话人分离
        
        response = self.client.create_asr_task(request)
        task_id = response.task_id
        
        # 2. 轮询结果
        result = await self._poll_result(task_id)
        return result
```

### 6.3 通义千问

```python
# services/llm_service.py

from dashscope import Generation
from app.config import settings

class LLMService:
    def __init__(self):
        self.model = "qwen-max"
    
    async def generate(
        self, 
        prompt: str, 
        system_prompt: str = None,
        temperature: float = 0.7,
        max_tokens: int = 4000
    ) -> str:
        """调用通义千问生成文本"""
        messages = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        messages.append({"role": "user", "content": prompt})
        
        response = Generation.call(
            model=self.model,
            messages=messages,
            temperature=temperature,
            max_tokens=max_tokens,
            result_format="message",
            api_key=settings.DASHSCOPE_API_KEY
        )
        
        return response.output.choices[0].message.content
    
    async def generate_json(self, prompt: str, **kwargs) -> dict:
        """生成JSON格式输出"""
        result = await self.generate(prompt, **kwargs)
        # 解析JSON，处理markdown代码块
        return self._parse_json(result)
```

### 6.4 通义万相

```python
# services/image_service.py

from dashscope import ImageSynthesis
from app.config import settings

class ImageService:
    async def generate_image(
        self, 
        prompt: str,
        size: str = "1024*1024",
        style: str = None
    ) -> str:
        """
        生成图片，返回图片URL
        """
        response = ImageSynthesis.call(
            model="wanx-v1",
            prompt=prompt,
            size=size,
            style=style,
            api_key=settings.DASHSCOPE_API_KEY
        )
        
        if response.status_code == 200:
            return response.output.results[0].url
        else:
            raise Exception(f"图片生成失败: {response.message}")
    
    async def generate_cover(self, title: str, author: str, theme: str) -> str:
        """生成书籍封面"""
        prompt = f"专业书籍封面设计，书名《{title}》，作者{author}，主题：{theme}，简约现代风格，高品质"
        return await self.generate_image(prompt, size="768*1024")
    
    async def generate_chapter_illustration(self, chapter_summary: str) -> str:
        """生成章节插图"""
        prompt = f"书籍插图，{chapter_summary}，简约线条风格，黑白色调"
        return await self.generate_image(prompt, size="1024*768")
```

---

## 7. 后台任务处理

### 7.1 任务执行器（MVP简化版）

```python
# app/background.py

import asyncio
from concurrent.futures import ThreadPoolExecutor
from app.workflow.graph import create_workflow
from app.database import get_db
from app.models.task import Task

executor = ThreadPoolExecutor(max_workers=5)

async def process_task(task_id: str):
    """处理单个任务"""
    db = next(get_db())
    task = db.query(Task).filter(Task.id == task_id).first()
    
    try:
        # 更新状态
        task.status = "processing"
        db.commit()
        
        # 创建并运行工作流
        workflow = create_workflow()
        initial_state = {
            "task_id": task_id,
            "audio_path": task.audio_path,
            "title": task.title,
            "author": task.author,
            "template_id": str(task.template_id),
            "current_stage": "transcription"
        }
        
        # 执行工作流（带状态回调）
        final_state = await workflow.ainvoke(
            initial_state,
            config={"callbacks": [TaskProgressCallback(task_id, db)]}
        )
        
        # 保存结果
        task.status = "completed"
        task.pdf_path = final_state.get("pdf_path")
        task.completed_at = datetime.utcnow()
        db.commit()
        
    except Exception as e:
        task.status = "failed"
        task.error_message = str(e)
        db.commit()
        raise

def start_task_processing(task_id: str):
    """在后台线程中启动任务处理"""
    loop = asyncio.new_event_loop()
    executor.submit(lambda: loop.run_until_complete(process_task(task_id)))
```

### 7.2 进度回调

```python
# app/workflow/callbacks.py

from langgraph.callbacks import BaseCallbackHandler

class TaskProgressCallback(BaseCallbackHandler):
    def __init__(self, task_id: str, db):
        self.task_id = task_id
        self.db = db
        self.stage_progress = {
            "transcription": 15,
            "restructure": 30,
            "extraction": 45,
            "polish": 60,
            "annotation": 75,
            "feedback": 90,
            "layout": 100
        }
    
    def on_chain_start(self, serialized, inputs, **kwargs):
        """节点开始时更新进度"""
        stage = inputs.get("current_stage")
        if stage:
            task = self.db.query(Task).filter(Task.id == self.task_id).first()
            task.current_stage = stage
            task.progress = self.stage_progress.get(stage, 0)
            self.db.commit()
```

---

## 8. 前端关键实现

### 8.1 状态管理

```typescript
// stores/taskStore.ts

import { create } from 'zustand';

interface TaskState {
  currentTask: Task | null;
  isPolling: boolean;
  
  setCurrentTask: (task: Task) => void;
  startPolling: (taskId: string) => void;
  stopPolling: () => void;
}

export const useTaskStore = create<TaskState>((set, get) => ({
  currentTask: null,
  isPolling: false,
  
  setCurrentTask: (task) => set({ currentTask: task }),
  
  startPolling: (taskId) => {
    set({ isPolling: true });
    const poll = async () => {
      if (!get().isPolling) return;
      
      const task = await api.getTask(taskId);
      set({ currentTask: task });
      
      if (task.status === 'processing') {
        setTimeout(poll, 3000); // 每3秒轮询
      } else {
        set({ isPolling: false });
      }
    };
    poll();
  },
  
  stopPolling: () => set({ isPolling: false })
}));
```

### 8.2 文件上传组件

```tsx
// components/AudioUploader.tsx

import { Upload, message } from 'antd';
import { InboxOutlined } from '@ant-design/icons';

const { Dragger } = Upload;

const ALLOWED_TYPES = ['audio/mpeg', 'audio/mp4', 'audio/wav', 'audio/x-m4a'];

export const AudioUploader: React.FC<{
  onFileSelect: (file: File) => void;
}> = ({ onFileSelect }) => {
  
  const beforeUpload = (file: File) => {
    if (!ALLOWED_TYPES.includes(file.type)) {
      message.error('仅支持 MP3、M4A、WAV 格式');
      return false;
    }
    onFileSelect(file);
    return false; // 阻止自动上传
  };

  return (
    <Dragger
      accept=".mp3,.m4a,.wav"
      beforeUpload={beforeUpload}
      showUploadList={false}
    >
      <p className="ant-upload-drag-icon">
        <InboxOutlined />
      </p>
      <p className="ant-upload-text">拖拽音频文件到此处，或点击选择文件</p>
      <p className="ant-upload-hint">支持 MP3、M4A、WAV 格式</p>
    </Dragger>
  );
};
```

---

## 9. 部署架构

### 9.1 本地开发环境

```yaml
# docker-compose.yml

version: '3.8'

services:
  backend:
    build: ./backend
    ports:
      - "8000:8000"
    volumes:
      - ./backend:/app
      - ./storage:/app/storage    # 本地文件存储目录
    environment:
      - DATABASE_URL=postgresql://postgres:postgres@db:5432/podbook
      - STORAGE_TYPE=local
      - STORAGE_DIR=/app/storage
      - DASHSCOPE_API_KEY=${DASHSCOPE_API_KEY}
      - ALIYUN_ACCESS_KEY=${ALIYUN_ACCESS_KEY}
      - ALIYUN_SECRET_KEY=${ALIYUN_SECRET_KEY}
    depends_on:
      - db
    command: uvicorn app.main:app --host 0.0.0.0 --reload

  frontend:
    build: ./frontend
    ports:
      - "3000:3000"
    volumes:
      - ./frontend:/app
      - /app/node_modules
    command: npm run dev

  db:
    image: postgres:15
    environment:
      - POSTGRES_DB=podbook
      - POSTGRES_USER=postgres
      - POSTGRES_PASSWORD=postgres
    volumes:
      - postgres_data:/var/lib/postgresql/data
    ports:
      - "5432:5432"

volumes:
  postgres_data:
```

### 9.2 生产环境（阿里云）

```
┌─────────────────────────────────────────────────────────────┐
│                    阿里云生产环境部署                         │
├─────────────────────────────────────────────────────────────┤
│                                                             │
│  用户 ──▶ [SLB负载均衡] ──▶ [ECS实例]                        │
│                             │                               │
│                             ├── Nginx (静态资源 + 反向代理)   │
│                             │     ├── /api/* → FastAPI       │
│                             │     └── /* → React静态文件     │
│                             │                               │
│                             └── Docker Compose               │
│                                   ├── backend容器            │
│                                   └── frontend构建产物       │
│                                                             │
│  数据服务：                                                  │
│  ├── RDS PostgreSQL (主数据库)                              │
│  ├── 本地磁盘 (文件存储，后续可切换OSS)                       │
│  └── 日志服务 SLS (日志收集，可选)                           │
│                                                             │
└─────────────────────────────────────────────────────────────┘
```

**推荐配置（MVP阶段）：**
| 资源 | 规格 | 预估月费用 |
|------|------|-----------|
| ECS | 2核4G + 100G云盘 | ¥100-200 |
| RDS PostgreSQL | 1核1G | ¥50-100 |
| SLB | 按量付费（可选） | ¥20-50 |
| AI服务 | 按调用量 | 按需 |

> 注：MVP阶段使用ECS本地磁盘存储文件，后续用户量增长时再切换到OSS。

---

## 10. 环境变量配置

```bash
# .env.example

# 数据库
DATABASE_URL=postgresql://user:password@localhost:5432/podbook

# 文件存储（MVP阶段使用本地存储）
STORAGE_TYPE=local
STORAGE_DIR=./storage

# 阿里云 OSS（后续切换时启用）
# OSS_ACCESS_KEY=your_access_key
# OSS_SECRET_KEY=your_secret_key
# OSS_ENDPOINT=oss-cn-hangzhou.aliyuncs.com
# OSS_BUCKET=podbook-files

# 阿里云 AI 服务 (DashScope)
DASHSCOPE_API_KEY=your_dashscope_api_key

# 阿里云 ASR
ALIYUN_ACCESS_KEY=your_access_key
ALIYUN_SECRET_KEY=your_secret_key
ALIYUN_ASR_APP_KEY=your_asr_app_key

# 应用配置
APP_ENV=development
DEBUG=true
SECRET_KEY=your_secret_key_here
```

---

## 11. 开发规范

### 11.1 代码规范
- **Python**: 遵循 PEP8，使用 Black 格式化，Ruff 进行 lint
- **TypeScript**: 使用 ESLint + Prettier
- **Git**: 使用 Conventional Commits 规范

### 11.2 分支策略
- `main`: 生产分支
- `develop`: 开发分支
- `feature/*`: 功能分支
- `fix/*`: 修复分支

### 11.3 测试策略
- 后端：pytest 单元测试 + API 集成测试
- 前端：Vitest 单元测试
- E2E：暂不需要（MVP阶段）

---

## 附录：依赖清单

### 后端 (requirements.txt)
```
fastapi>=0.109.0
uvicorn>=0.27.0
sqlalchemy>=2.0.0
alembic>=1.13.0
asyncpg>=0.29.0
pydantic>=2.5.0
python-multipart>=0.0.6

# LangGraph
langgraph>=0.0.40
langchain>=0.1.0

# 阿里云
dashscope>=1.14.0
alibabacloud-nls20190201>=1.0.0
# oss2>=2.18.0  # 后续切换OSS时启用

# PDF生成
weasyprint>=60.0

# 工具
python-dotenv>=1.0.0
httpx>=0.26.0
```

### 前端 (package.json dependencies)
```json
{
  "dependencies": {
    "react": "^18.2.0",
    "react-dom": "^18.2.0",
    "react-router-dom": "^6.22.0",
    "antd": "^5.14.0",
    "zustand": "^4.5.0",
    "axios": "^1.6.0",
    "@ant-design/icons": "^5.2.0"
  },
  "devDependencies": {
    "@types/react": "^18.2.0",
    "@vitejs/plugin-react": "^4.2.0",
    "typescript": "^5.3.0",
    "vite": "^5.1.0",
    "eslint": "^8.56.0",
    "prettier": "^3.2.0"
  }
}
```
