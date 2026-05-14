#!/bin/bash
# 批量并行测试编者序修改效果：13个播客全流程
# 用法: cd backend && bash tests/run_batch_test.sh

cd "$(dirname "$0")/.."

VENV_PYTHON="./venv/bin/python"
BATCH_TS=$(date +%Y%m%d_%H%M%S)
LOG_DIR="tests/artifacts/batch_${BATCH_TS}"
mkdir -p "$LOG_DIR"

URLS=(
  "https://www.xiaoyuzhoufm.com/episode/69adf2bdc8cdeb38c28a1a81"
  "https://www.xiaoyuzhoufm.com/episode/698563a188663289fe80769a"
  "https://www.xiaoyuzhoufm.com/episode/695331cb2db086f897b50ea9"
  "https://www.xiaoyuzhoufm.com/episode/685f6dc8bef90978ec4c1e60"
  "https://www.xiaoyuzhoufm.com/episode/663ac13e13426298925c9853"
  "https://www.xiaoyuzhoufm.com/episode/65e75676d15a20dbcab730ca"
  "https://www.xiaoyuzhoufm.com/episode/699c3925de29766da93f2f74"
  "https://www.xiaoyuzhoufm.com/episode/689dbb17759c1ff652eb6795"
  "https://www.xiaoyuzhoufm.com/episode/6948c33c262481ac732bbd0b"
  "https://www.xiaoyuzhoufm.com/episode/68d101ed2c82c9dcca9c4bbc"
  "https://www.xiaoyuzhoufm.com/episode/696cd009109824f9e125ad1c"
  "https://www.xiaoyuzhoufm.com/episode/6826c6a55ccf03732b24d5a3"
  "https://www.xiaoyuzhoufm.com/episode/67c66c0bb0167b8db954b848"
)

LABELS=(
  "01_商业事件圆桌_AI年度总结"
  "02_商业事件访谈_AICoding东旭"
  "03_商业嘉宾访谈_Manus出售"
  "04_商业嘉宾圆桌_罗永浩AI"
  "05_成长嘉宾访谈_鲁豫张春"
  "06_成长嘉宾圆桌_底层自信"
  "07_成长嘉宾圆桌_真诚无往不利"
  "08_人文事件圆桌_枪炮病菌钢铁"
  "09_人文事件圆桌_盖茨比马斯克"
  "10_人文事件圆桌_预制菜枪击案"
  "11_人文事件访谈_红楼梦金瓶梅"
  "12_人文嘉宾访谈_陈丹青艺术"
  "13_人文嘉宾圆桌_老灵魂坐标系"
)

PIDS=()

echo "=========================================="
echo "批量测试开始: $BATCH_TS"
echo "共 ${#URLS[@]} 个播客，全部并行"
echo "日志目录: $LOG_DIR"
echo "=========================================="

for i in "${!URLS[@]}"; do
  url="${URLS[$i]}"
  label="${LABELS[$i]}"
  logfile="${LOG_DIR}/${label}.log"
  
  echo "[启动] $label"
  $VENV_PYTHON -m tests.run_full_pipeline "$url" > "$logfile" 2>&1 &
  PIDS+=($!)
done

echo ""
echo "全部 ${#URLS[@]} 个任务已启动，PID: ${PIDS[*]}"
echo "等待所有任务完成..."
echo ""

FAILED=0
for i in "${!PIDS[@]}"; do
  pid="${PIDS[$i]}"
  label="${LABELS[$i]}"
  if wait "$pid"; then
    echo "[完成] $label (PID $pid)"
  else
    echo "[失败] $label (PID $pid, exit code $?)"
    FAILED=$((FAILED + 1))
  fi
done

echo ""
echo "=========================================="
echo "批量测试结束"
echo "成功: $((${#URLS[@]} - FAILED)) / ${#URLS[@]}"
echo "失败: $FAILED"
echo "日志: $LOG_DIR"
echo "=========================================="

# 汇总各任务的编者序到一个文件，方便对比查看
SUMMARY="${LOG_DIR}/00_编者序汇总.md"
echo "# 编者序批量测试汇总" > "$SUMMARY"
echo "" >> "$SUMMARY"
echo "运行时间: $BATCH_TS" >> "$SUMMARY"
echo "" >> "$SUMMARY"

for label in "${LABELS[@]}"; do
  logfile="${LOG_DIR}/${label}.log"
  if [ -f "$logfile" ]; then
    # 从日志中提取 artifacts 目录路径
    artifact_dir=$(grep -o 'tests/artifacts/full_pipeline_[^ ]*' "$logfile" | head -1)
    if [ -n "$artifact_dir" ] && [ -f "$artifact_dir/05_editor_preface.md" ]; then
      echo "---" >> "$SUMMARY"
      echo "" >> "$SUMMARY"
      echo "## $label" >> "$SUMMARY"
      echo "" >> "$SUMMARY"
      cat "$artifact_dir/05_editor_preface.md" >> "$SUMMARY"
      echo "" >> "$SUMMARY"
      
      # 也提取 PDF 路径
      pdf_path=$(grep -o 'PDF: [^ ]*' "$logfile" | head -1 | sed 's/PDF: //')
      if [ -n "$pdf_path" ]; then
        echo "*PDF: $pdf_path*" >> "$SUMMARY"
        echo "" >> "$SUMMARY"
      fi
    fi
  fi
done

echo "编者序汇总文件: $SUMMARY"
