"""
podcast_book_fit.web_app — 本地通用网页：输入播客链接生成成书初判

用法：
    cd backend
    python -m workbench.podcast_book_fit.web_app --port 8765

打开：
    http://127.0.0.1:8765/
"""
from __future__ import annotations

from multi.env import setup
setup()

import argparse
import asyncio
import json
import logging
import threading
import uuid
import webbrowser
from dataclasses import asdict, dataclass, field
from datetime import datetime
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import unquote

from multi.io import JobPaths, slugify
from multi.podcast_book_fit.render import render_book_fit_pdf
from multi.podcast_book_fit.runner import run_book_fit

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("podcast_book_fit_web")


@dataclass
class TaskState:
    task_id: str
    status: str
    url: str
    job_id: str
    created_at: str
    updated_at: str
    message: str = ""
    result: dict[str, Any] | None = None
    error: str = ""
    files: dict[str, str] = field(default_factory=dict)


TASKS: dict[str, TaskState] = {}
TASK_LOCK = threading.Lock()


def render_app_html() -> str:
    return """<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>播客成书初判</title>
  <style>
    :root { --paper:#f7f2e8; --ink:#1e2424; --muted:#68716e; --line:#d9d0bf; --panel:#fffaf0; --teal:#126f68; --rust:#a7472c; --gold:#bd8b2a; --wash:#eee6d7; }
    * { box-sizing:border-box; }
    body { margin:0; background:var(--paper); color:var(--ink); font-family:"Aptos","PingFang SC","Microsoft YaHei",sans-serif; line-height:1.65; }
    body::before { content:""; position:fixed; inset:0; pointer-events:none; background-image:linear-gradient(rgba(30,36,36,.045) 1px, transparent 1px); background-size:100% 9px; mix-blend-mode:multiply; }
    main { max-width:1080px; margin:0 auto; padding:38px 22px 70px; }
    .hero { display:grid; grid-template-columns:minmax(0,1.1fr) minmax(280px,.9fr); gap:18px; border-bottom:2px solid var(--ink); padding-bottom:26px; }
    .stamp { background:var(--ink); color:var(--paper); min-height:300px; padding:25px; display:flex; flex-direction:column; justify-content:space-between; }
    .kicker { color:#f0c48e; font-size:13px; font-weight:800; }
    h1 { margin:8px 0; font-size:clamp(34px,4.8vw,64px); line-height:1; letter-spacing:0; white-space:nowrap; }
    h2 { margin:28px 0 12px; font-size:22px; letter-spacing:0; }
    .panel { border:1px solid var(--line); background:var(--panel); padding:18px; }
    label { display:block; font-weight:800; margin-bottom:8px; }
    input { width:100%; border:2px solid var(--ink); background:#fffdf6; padding:14px; font-size:16px; color:var(--ink); }
    button { margin-top:12px; border:0; background:var(--teal); color:white; padding:13px 18px; font-size:16px; font-weight:800; cursor:pointer; }
    button:disabled { opacity:.55; cursor:not-allowed; }
    .status { margin-top:14px; color:var(--muted); }
    .result { display:none; margin-top:22px; }
    .summary { border-left:5px solid var(--teal); font-size:18px; }
    .grid { display:grid; grid-template-columns:repeat(auto-fit,minmax(260px,1fr)); gap:12px; margin-top:12px; }
    .chip-row { display:flex; flex-wrap:wrap; gap:8px; }
    .chip-row span { border:1px solid rgba(18,111,104,.28); color:var(--teal); background:#eef6f2; padding:5px 10px; }
    a { color:var(--teal); font-weight:800; }
    .muted { color:var(--muted); }
    @media(max-width:760px){ .hero{grid-template-columns:1fr;} .stamp{min-height:220px;} }
  </style>
</head>
<body>
  <main>
    <section class="hero">
      <div class="stamp">
        <div>
          <div class="kicker">EchoPress Workbench</div>
          <h1>播客成书初判</h1>
        </div>
        <p>输入播客链接，生成 200-300 字的主播成书沟通判断。</p>
      </div>
      <section class="panel">
        <label for="podcast-url">播客链接</label>
        <input id="podcast-url" placeholder="https://www.xiaoyuzhoufm.com/podcast/..." autocomplete="off">
        <button id="run">开始生成</button>
        <div id="status" class="status">等待输入链接。</div>
      </section>
    </section>

    <section id="result" class="result">
      <h2>初判结果</h2>
      <div class="panel summary" id="summary"></div>
      <div class="grid">
        <div class="panel"><strong id="fit-level"></strong><div class="muted">沟通判断</div></div>
        <div class="panel"><strong id="episode-count"></strong><div class="muted">资料覆盖</div></div>
      </div>
      <h2>主播内容气质</h2>
      <div class="chip-row" id="tags"></div>
      <h2>主题 / 标题方向</h2>
      <div class="grid" id="directions"></div>
      <h2>产物</h2>
      <div class="panel" id="files"></div>
    </section>
  </main>
  <script>
    const input = document.getElementById('podcast-url');
    const button = document.getElementById('run');
    const statusEl = document.getElementById('status');
    const resultEl = document.getElementById('result');
    const sleep = ms => new Promise(resolve => setTimeout(resolve, ms));

    function setStatus(text) { statusEl.textContent = text; }

    async function poll(taskId) {
      while (true) {
        const res = await fetch(`/api/tasks/${taskId}`);
        const task = await res.json();
        setStatus(task.message || task.status);
        if (task.status === 'done') {
          renderResult(task);
          if (task.files && task.files.html) {
            window.location.href = task.files.html;
          }
          button.disabled = false;
          return;
        }
        if (task.status === 'error') {
          setStatus(`失败：${task.error}`);
          button.disabled = false;
          return;
        }
        await sleep(1500);
      }
    }

    function renderResult(task) {
      const data = task.result;
      resultEl.style.display = 'block';
      document.getElementById('summary').textContent = data.book_fit_summary || '';
      document.getElementById('fit-level').textContent = data.fit_level || '';
      document.getElementById('episode-count').textContent = `${data.metadata?.episode_count || '-'} 集`;
      document.getElementById('tags').innerHTML = (data.host_profile_tags || []).map(x => `<span>${escapeHtml(x)}</span>`).join('');
      document.getElementById('directions').innerHTML = (data.possible_book_directions || []).map(x => {
        const sections = (x.topic_sections || []).slice(0, 3).map(section => {
          const eps = (section.episode_titles || []).slice(0, 4).map(t => `<li>${escapeHtml(t)}</li>`).join('');
          return `<div class="panel"><strong>${escapeHtml(section.section_title || '可先收录的节目')}</strong><p>${escapeHtml(section.section_reason || '')}</p><ol>${eps}</ol></div>`;
        }).join('');
        return `<article class="panel"><span class="muted">${escapeHtml(x.direction || '')}</span><br><strong>${escapeHtml(x.suggested_title || x.direction || '')}</strong><p>${escapeHtml(x.reason || '')}</p><div class="grid">${sections}</div></article>`;
      }).join('');
      document.getElementById('files').innerHTML = `
        <p><a href="${task.files.html}" target="_blank">打开成书初判页</a></p>
        ${task.files.pdf ? `<p><a href="${task.files.pdf}" target="_blank">下载 PDF</a></p>` : ''}
        <p><a href="${task.files.json}" target="_blank">打开 JSON</a></p>
      `;
      window.scrollTo({ top: resultEl.offsetTop - 20, behavior: 'smooth' });
    }

    function escapeHtml(str) {
      return String(str).replace(/[&<>"']/g, ch => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[ch]));
    }

    button.addEventListener('click', async () => {
      const url = input.value.trim();
      if (!url) { setStatus('请先输入播客链接。'); return; }
      button.disabled = true;
      resultEl.style.display = 'none';
      setStatus('已提交，正在创建任务...');
      const res = await fetch('/api/run', { method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({ url }) });
      const task = await res.json();
      if (!res.ok) {
        setStatus(task.error || '提交失败');
        button.disabled = false;
        return;
      }
      poll(task.task_id);
    });
  </script>
</body>
</html>
"""


def _json_response(handler: BaseHTTPRequestHandler, data: Any, status: int = 200) -> None:
    body = json.dumps(data, ensure_ascii=False, default=str).encode("utf-8")
    handler.send_response(status)
    handler.send_header("Content-Type", "application/json; charset=utf-8")
    handler.send_header("Content-Length", str(len(body)))
    handler.end_headers()
    handler.wfile.write(body)


def _html_response(handler: BaseHTTPRequestHandler, html: str) -> None:
    body = html.encode("utf-8")
    handler.send_response(200)
    handler.send_header("Content-Type", "text/html; charset=utf-8")
    handler.send_header("Content-Length", str(len(body)))
    handler.end_headers()
    handler.wfile.write(body)


def _maybe_generate_missing_pdf(path: Path, *, renderer: Any | None = None) -> bool:
    if path.suffix != ".pdf":
        return False
    if path.exists() and path.is_file():
        return True
    html_path = path.with_suffix(".html")
    if not html_path.exists() or not html_path.is_file():
        return False

    render = renderer or render_book_fit_pdf
    try:
        result = render(html_path, path)
        if hasattr(result, "__await__"):
            result = asyncio.run(result)
        return bool(result) and path.exists() and path.is_file()
    except Exception as exc:
        logger.warning("按需生成 PDF 失败：%s", exc)
        return False


def _file_response(handler: BaseHTTPRequestHandler, path: Path) -> None:
    _maybe_generate_missing_pdf(path)
    if not path.exists() or not path.is_file():
        handler.send_error(HTTPStatus.NOT_FOUND)
        return
    if path.suffix == ".html":
        content_type = "text/html; charset=utf-8"
    elif path.suffix == ".pdf":
        content_type = "application/pdf"
    else:
        content_type = "application/json; charset=utf-8"
    body = path.read_bytes()
    handler.send_response(200)
    handler.send_header("Content-Type", content_type)
    if path.suffix == ".pdf":
        handler.send_header("Content-Disposition", f'attachment; filename="{path.name}"')
    handler.send_header("Content-Length", str(len(body)))
    handler.end_headers()
    handler.wfile.write(body)


def _update_task(task_id: str, **changes: Any) -> None:
    with TASK_LOCK:
        task = TASKS[task_id]
        for key, value in changes.items():
            setattr(task, key, value)
        task.updated_at = datetime.now().isoformat(timespec="seconds")


def _run_task(task_id: str, url: str, job_id: str) -> None:
    try:
        paths = JobPaths.create(job_id=job_id)
        _update_task(task_id, status="running", message="正在抓取节目资料与 RSS 全集...")
        result = asyncio.run(run_book_fit(url=url, paths=paths, premium=False))
        _update_task(
            task_id,
            status="done",
            message="完成",
            result=result,
            files={
                "html": f"/files/{paths.job_id}/book_fit_judgment.html",
                "json": f"/files/{paths.job_id}/book_fit_judgment.json",
                "pdf": f"/files/{paths.job_id}/book_fit_judgment.pdf"
                if (paths.root / "book_fit_judgment.pdf").exists()
                else "",
            },
        )
    except Exception as exc:
        logger.exception("任务失败")
        _update_task(task_id, status="error", error=str(exc), message="任务失败")


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt: str, *args: Any) -> None:
        logger.info("%s - %s", self.address_string(), fmt % args)

    def do_GET(self) -> None:
        if self.path == "/":
            _html_response(self, render_app_html())
            return
        if self.path.startswith("/api/tasks/"):
            task_id = self.path.rsplit("/", 1)[-1]
            with TASK_LOCK:
                task = TASKS.get(task_id)
            if not task:
                _json_response(self, {"error": "task not found"}, 404)
                return
            _json_response(self, asdict(task))
            return
        if self.path.startswith("/files/"):
            _, _, rest = self.path.partition("/files/")
            parts = [unquote(p) for p in rest.split("/") if p]
            if len(parts) != 2:
                self.send_error(HTTPStatus.NOT_FOUND)
                return
            job_id, filename = parts
            paths = JobPaths.open(job_id)
            _file_response(self, paths.root / filename)
            return
        self.send_error(HTTPStatus.NOT_FOUND)

    def do_POST(self) -> None:
        if self.path != "/api/run":
            self.send_error(HTTPStatus.NOT_FOUND)
            return
        length = int(self.headers.get("Content-Length") or 0)
        payload = json.loads(self.rfile.read(length).decode("utf-8") or "{}")
        url = (payload.get("url") or "").strip()
        if not url.startswith("http"):
            _json_response(self, {"error": "请输入合法播客链接"}, 400)
            return
        task_id = uuid.uuid4().hex[:12]
        hint = slugify(url.rsplit("/", 1)[-1] or "podcast", max_len=18)
        job_id = f"{hint}__codex__bookfit__{datetime.now().strftime('%Y%m%d-%H%M%S')}"
        now = datetime.now().isoformat(timespec="seconds")
        task = TaskState(
            task_id=task_id,
            status="queued",
            url=url,
            job_id=job_id,
            created_at=now,
            updated_at=now,
            message="任务已排队",
        )
        with TASK_LOCK:
            TASKS[task_id] = task
        thread = threading.Thread(target=_run_task, args=(task_id, url, job_id), daemon=True)
        thread.start()
        _json_response(self, asdict(task), 202)


def main() -> None:
    parser = argparse.ArgumentParser(description="播客成书初判本地网页")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--open", action="store_true", help="启动后自动打开浏览器")
    args = parser.parse_args()
    server = ThreadingHTTPServer((args.host, args.port), Handler)
    url = f"http://{args.host}:{args.port}/"
    logger.info("成书初判网页已启动：%s", url)
    if args.open:
        webbrowser.open(url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        logger.info("停止服务")


if __name__ == "__main__":
    main()
