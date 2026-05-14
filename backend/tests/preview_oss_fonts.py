"""
开源字体替换 · 本地预览。

用于在 Mac 本地预览"把 Hiragino Sans GB / Palatino / Baskerville / Times New Roman
等未授权字体替换为开源等价字体"后的 PDF 效果——字体链改为"开源在前"，
所以即使 Mac 系统里有 Hiragino / Palatino，也会先选中开源替身，
从而模拟线上 Docker 容器里 **只有开源字体** 时的真实渲染。

替换规则：
  页码 italic 链：
    Palatino, Baskerville, Times New Roman, EB Garamond, Noto Serif CJK SC
      → EB Garamond, Palatino, Baskerville, Times New Roman, Noto Serif CJK SC
  章节 / 前置页标题链：
    Hiragino Sans GB, PingFang SC, Noto Sans CJK SC, Noto Sans SC
      → Source Han Sans SC, Hiragino Sans GB, PingFang SC, Noto Sans CJK SC, Noto Sans SC

字体文件位于 /tmp/pod_to_book_oss_fonts/：
  - SourceHanSansSC-Bold.otf        （思源黑体 SC Bold, weight 700，OFL）
  - EBGaramond-VF.ttf               （EB Garamond 可变字重, OFL）
  - EBGaramond-Italic-VF.ttf        （EB Garamond Italic 可变字重, OFL）

用法：
  cd backend
  python -m tests.preview_oss_fonts <task_storage_dir>
  # 例：python -m tests.preview_oss_fonts storage/b5df08ed-7925-4938-b6b2-22d3d0306528
"""
import os
import re
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

OSS_FONT_DIR = Path("/tmp/pod_to_book_oss_fonts")
REQUIRED_FONTS = [
    "SourceHanSansSC-Bold.otf",
    "EBGaramond-VF.ttf",
    "EBGaramond-Italic-VF.ttf",
]


def ensure_oss_fonts() -> None:
    missing = [f for f in REQUIRED_FONTS if not (OSS_FONT_DIR / f).exists()]
    if missing:
        print(f"缺少字体文件：{missing}")
        print(f"请先把它们放到 {OSS_FONT_DIR}/ 下。")
        sys.exit(1)


def swap_font_chains(body: str) -> tuple[str, int, int]:
    # 页码 italic 链：把 EB Garamond 挪到首位
    old_serif = (
        '("Palatino", "Baskerville", "Times New Roman", '
        '"EB Garamond", "Noto Serif CJK SC", "Noto Serif SC")'
    )
    new_serif = (
        '("EB Garamond", "Palatino", "Baskerville", "Times New Roman", '
        '"Noto Serif CJK SC", "Noto Serif SC")'
    )
    body, serif_n = re.subn(re.escape(old_serif), new_serif, body)

    # 章节 / 前置页标题链：把 Source Han Sans SC 挪到首位
    old_sans = (
        '("Hiragino Sans GB", "PingFang SC", "Noto Sans CJK SC", "Noto Sans SC")'
    )
    new_sans = (
        '("Source Han Sans SC", "Hiragino Sans GB", "PingFang SC", '
        '"Noto Sans CJK SC", "Noto Sans SC")'
    )
    body, sans_n = re.subn(re.escape(old_sans), new_sans, body)

    return body, serif_n, sans_n


def main() -> None:
    if len(sys.argv) < 2:
        print("用法: python -m tests.preview_oss_fonts <task_storage_dir>")
        sys.exit(1)
    ensure_oss_fonts()

    task_dir = Path(sys.argv[1]).resolve()
    if not task_dir.is_dir():
        print(f"目录不存在: {task_dir}")
        sys.exit(1)

    # 取最近一次 book_*.typ（优先 repatch，因为已经包含最新 preamble）
    candidates = sorted(task_dir.glob("book_*.typ"))
    if not candidates:
        print(f"未找到 book_*.typ in {task_dir}")
        sys.exit(1)
    latest_typ = candidates[-1]
    print(f"[preview-oss] 源 Typst: {latest_typ.name}")

    source = latest_typ.read_text(encoding="utf-8")
    new_source, serif_n, sans_n = swap_font_chains(source)
    print(f"[preview-oss] 字体链替换：serif(页码) x{serif_n}, sans(标题) x{sans_n}")
    if serif_n == 0 and sans_n == 0:
        print("  ⚠️  没有命中任何字体链。此源文件可能过旧，先跑 repatch_typeset.py 再来。")

    ts = time.strftime("%Y%m%d_%H%M%S")
    out_typ = task_dir / f"book_preview_oss_{ts}.typ"
    out_pdf = task_dir / f"book_preview_oss_{ts}.pdf"
    out_typ.write_text(new_source, encoding="utf-8")
    print(f"[preview-oss] 新 Typst 源码: {out_typ}")

    import typst as typst_lib

    bundle_fonts = (
        Path(__file__).resolve().parent.parent
        / "app" / "workflow" / "knowledge" / "assets" / "fonts"
    )
    font_paths = [str(bundle_fonts), str(OSS_FONT_DIR)]
    print(f"[preview-oss] font_paths = {font_paths}")

    t0 = time.perf_counter()
    typst_lib.compile(str(out_typ), output=str(out_pdf), font_paths=font_paths)
    elapsed = time.perf_counter() - t0
    size_kb = out_pdf.stat().st_size / 1024
    print(f"[preview-oss] PDF 已编译: {out_pdf} ({size_kb:.0f} KB, {elapsed:.1f}s)")

    try:
        subprocess.run(["open", str(out_pdf)], check=False)
    except Exception:
        pass


if __name__ == "__main__":
    main()
