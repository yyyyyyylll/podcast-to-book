#!/usr/bin/env bash
# =============================================================================
# batch_episode.sh — B 端 workbench 单集完整流水线包装
#
# 顺序：transcribe + compose + annotate + assemble  →  highlights  →  illustrations
#       →  name_chapter (bookish)  →  rename_sections (concrete)  →  render PDF
#       →  try merge book PDF
#
# 用法：
#   bash workbench/scripts/batch_episode.sh <ep_num> <job>
# 示例：
#   bash workbench/scripts/batch_episode.sh 1 possibility
#
# 日志：storage/workbench/jobs/<job>/logs/ep<NN>.log
# 产物源头：全部保存在 storage/workbench/jobs/<job>/ 下，便于后续精修。
# 桌面试读：额外复制一份 PDF 到 ~/Desktop，方便快速打开查看。
# =============================================================================
set -uo pipefail

EP_NUM=${1:?"usage: bash batch_episode.sh <ep_num> <job>"}
JOB=${2:-possibility}

JOB_DIR="storage/workbench/jobs/$JOB"
LOG_DIR="$JOB_DIR/logs"
mkdir -p "$LOG_DIR"
EP_PADDED=$(printf "%02d" "$EP_NUM")
LOG="$LOG_DIR/ep${EP_PADDED}.log"
PROGRESS="$LOG_DIR/progress.txt"

log() {
    local msg="[$(date '+%H:%M:%S')] [ep${EP_PADDED}] $1"
    echo "$msg" >> "$LOG"
    echo "$msg" >> "$PROGRESS"
}

run_step() {
    local label=$1; shift
    log "▶ $label"
    if "$@" >> "$LOG" 2>&1; then
        log "✅ $label"
    else
        log "❌ $label FAILED (exit=$?)"
        return 1
    fi
}

START_TS=$(date +%s)
log "===== START ep${EP_PADDED} ====="

run_step "1/6 run_pipeline (transcribe→compose→annotate→assemble)" \
    python3 -m workbench.runners.run_pipeline --job "$JOB" --only "$EP_NUM" || exit 1

run_step "2/6 extract_highlights" \
    python3 -m workbench.runners.extract_highlights --job "$JOB" --only "$EP_NUM" || exit 2

run_step "3/6 search_illustrations" \
    python3 -m workbench.runners.search_illustrations --job "$JOB" --only "$EP_NUM" || exit 3

run_step "4/6 name_chapter (bookish)" \
    python3 -m workbench.runners.name_chapter --job "$JOB" --only "$EP_NUM" \
        --style bookish --apply bookish --apply-pick 1 || exit 4

run_step "5/6 rename_sections (concrete)" \
    python3 -m workbench.runners.rename_sections --job "$JOB" --only "$EP_NUM" \
        --style concrete --apply concrete --apply-pick 1 || exit 5

run_step "6/7 render_chapter_pdf" \
    python3 -m workbench.runners.render_chapter_pdf --job "$JOB" --only "$EP_NUM" || exit 6

PDF_SRC="$JOB_DIR/episodes/ep${EP_PADDED}/chapter_preview.pdf"
TS=$(date +%H%M%S)
PDF_DST="$HOME/Desktop/ep${EP_PADDED}_workbench_${TS}.pdf"
if [ -f "$PDF_SRC" ]; then
    log "📄 PDF saved → $PDF_SRC"
    cp "$PDF_SRC" "$PDF_DST"
    log "📄 preview copied → $PDF_DST"
else
    log "⚠️  PDF 未生成: $PDF_SRC"
fi

run_step "7/7 merge_book_pdf (if complete)" \
    python3 -m workbench.runners.merge_book_pdf --job "$JOB" \
        --copy-desktop --skip-if-incomplete || exit 7

ELAPSED=$(( $(date +%s) - START_TS ))
log "===== DONE ep${EP_PADDED}  耗时 ${ELAPSED}s ====="
