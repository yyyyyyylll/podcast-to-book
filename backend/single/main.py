"""FastAPI 入口 —— 开源版：删除了商业层 router 注册（auth/admin/credits/payment/referral/host）。"""
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from contextlib import asynccontextmanager
import os

from sqlalchemy import select

from core.config import settings
from single.api import upload, tasks, feedback, events, rating, edit, internal
from single.database import init_db, async_session
from single.models.task import Task
from single.models.feedback import Feedback  # noqa: F401
from single.models.rating import Rating  # noqa: F401
from single.models.event import Event  # noqa: F401

_default_frontend = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), "frontend", "dist")
FRONTEND_DIR = settings.FRONTEND_DIR or _default_frontend


async def _recover_stale_tasks():
    async with async_session() as session:
        result = await session.execute(
            select(Task).where(Task.status.in_(["pending", "processing"]))
        )
        stale = result.scalars().all()
        for task in stale:
            task.status = "failed"
            task.error_message = "服务重启，任务中断，请重新提交"

        result2 = await session.execute(
            select(Task).where(Task.status == "regenerating")
        )
        regen_stale = result2.scalars().all()
        for task in regen_stale:
            task.status = "completed"

        if stale or regen_stale:
            await session.commit()
        if stale:
            print(f"[startup] 已恢复 {len(stale)} 个残留任务")
        if regen_stale:
            print(f"[startup] 已恢复 {len(regen_stale)} 个残留 regenerating 任务为 completed")


@asynccontextmanager
async def lifespan(app: FastAPI):
    print(f"🚀 {settings.APP_NAME} 启动中...")
    await init_db()
    print("📦 数据库已初始化")
    await _recover_stale_tasks()
    yield
    print(f"👋 {settings.APP_NAME} 关闭")


app = FastAPI(
    title=settings.APP_NAME,
    description="podcast-to-book — 把播客变成书",
    version="0.1.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

os.makedirs(settings.STORAGE_DIR, exist_ok=True)
app.mount("/files", StaticFiles(directory=settings.STORAGE_DIR), name="files")

app.include_router(upload.router, prefix="/api/v1", tags=["上传"])
app.include_router(tasks.router, prefix="/api/v1", tags=["任务"])
app.include_router(feedback.router, prefix="/api/v1", tags=["反馈"])
app.include_router(events.router, prefix="/api/v1", tags=["埋点"])
app.include_router(rating.router, prefix="/api/v1", tags=["评分"])
app.include_router(edit.router, prefix="/api/v1", tags=["编辑"])
app.include_router(internal.router, prefix="/api/internal", tags=["内部接口"])


@app.get("/health")
async def health_check():
    return {"status": "healthy"}


if os.path.isdir(FRONTEND_DIR):
    app.mount("/assets", StaticFiles(directory=os.path.join(FRONTEND_DIR, "assets")), name="frontend-assets")

    @app.get("/{full_path:path}")
    async def serve_spa(request: Request, full_path: str):
        file_path = os.path.join(FRONTEND_DIR, full_path)
        if full_path and os.path.isfile(file_path):
            return FileResponse(file_path)
        return FileResponse(os.path.join(FRONTEND_DIR, "index.html"))
else:
    @app.get("/")
    async def root():
        return {
            "name": settings.APP_NAME,
            "version": "0.1.0",
            "status": "running",
            "message": "前端未构建，请先运行 cd frontend && npm run build",
        }
