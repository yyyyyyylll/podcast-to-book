# multi — 多期播客 → 一本书

把同一档播客的多期合并成一整本可印刷的书。CLI 工具集，每个步骤一个 runner，便于精细控制和断点续跑。

这是 podcast-to-book 项目的高阶玩法。如果你只想把"一期播客 → 一本小册子"，用前端 Web 版即可（`http://localhost:3000/create`）。多期合书的复杂度（人工选篇、跨集去重、统一书名/章节、整书排版）目前需要 CLI 精细操作。

---

## 快速上手（约 1 小时跑完一本 13 章的书）

### 1. 准备环境

```bash
cd backend
python -m venv venv && source venv/bin/activate
pip install -r requirements.txt

# 复制 env 模板，填好 LLM key + 腾讯云 ASR key + COS bucket
cp .env.example .env
$EDITOR .env
```

### 2. 拉节目元数据

```bash
python -m multi.runners.fetch_show \
    --url https://www.xiaoyuzhoufm.com/podcast/<podcast_id> \
    --job possibility
```

输出：`storage/workbench/jobs/possibility/`
- `show.json` —— 节目级元数据（标题、封面、主播、简介）
- `episodes_index.json` —— 全部单集列表
- `episodes/epNN/meta.json` —— 每集元数据 + audio_url

### 3. 单集端到端（每集 ~5 分钟）

用封装好的 shell 脚本一口气跑完单集的 7 步：

```bash
# 跑第 1 集
bash multi/scripts/batch_episode.sh 1 possibility

# 批量跑
for i in 1 2 3 4 5 6 7 8 9 10 11 12 13; do
  bash multi/scripts/batch_episode.sh $i possibility
done
```

每集产物：`storage/workbench/jobs/possibility/episodes/epNN/`
- `transcript_raw.json` —— 腾讯云 ASR 原始转录
- `composed.json` —— LLM 重组后的章节内容
- `highlights.json` —— 金句
- `inline_images/` —— 章内插图（搜图，非 AI 生成）
- `chapter_preview.pdf` —— 单集章节预览 PDF

### 4. 整书装订（约 5 分钟）

```bash
# 给整本书起名（LLM 基于全 13 集内容）
python -m multi.runners.name_book --job possibility

# 渲染整书前置内容（封面、版权页、目录）
python -m multi.runners.render_book_front_matter --job possibility

# 合并所有章节，渲染最终整书 PDF
python -m multi.runners.render_grouped_book_pdf --job possibility

# (可选) 拼接 front matter + 章节，得到送印用的整书
python -m multi.runners.merge_book_pdf --job possibility
```

最终 PDF：`storage/workbench/jobs/possibility/book.pdf`

---

## Runner 索引

```
runners/
├── fetch_show.py              # 拉小宇宙节目元数据
├── setup_grouped_job.py       # 建合书任务（按章节分组）
├── run_pipeline.py            # 单集主管线：transcribe → compose → annotate → assemble
├── run_transcription.py       # 仅跑 ASR（被 run_pipeline 引用，也可单跑）
├── extract_highlights.py      # 单集金句提取
├── search_illustrations.py    # 单集配图（Serper 搜图，非 AI 生成）
├── name_chapter.py            # 命名单集对应的章节
├── rename_sections.py         # 命名章节内的小节
├── render_chapter_pdf.py      # 渲染单集预览 PDF
├── name_book.py               # 命名整本书
├── render_book_front_matter.py # 渲染封面/版权/目录
├── render_grouped_book_pdf.py # 合并所有章节为整书 PDF
└── merge_book_pdf.py          # PDF 文件层面的合并工具
```

每个 runner 都有 `--help`：

```bash
python -m multi.runners.run_pipeline --help
python -m multi.runners.name_chapter --help
```

---

## 设计原则

### 一、每步落盘 JSON

每个 runner 的输入是上一步的 JSON 路径，输出也是 JSON。中间状态全部保存在 `storage/workbench/jobs/<job>/`。这样可以：
- 任何一步失败，从该步重跑即可
- 单独替换某一步的实现，不影响其他步骤
- 看每一步的中间结果做人工检查

### 二、`--job` 是命名空间

所有 runner 都接受 `--job <id>` 参数。同一个 job 下的产物全部落到 `storage/workbench/jobs/<job_id>/`，互不污染。

### 三、`--only` 断点续跑

大多数单集 runner 支持 `--only 1,3,5` 或 `--only 1-13` 来指定子集，方便：
- 单独重跑某一集
- 调试新增逻辑时只跑一集试错
- 批量处理时断点续跑

### 四、`--force` 跳过缓存

ASR / LLM 结果会缓存，相同输入直接复用。`--force` 强制重新调用 API（费钱，慎用）。

---

## 关于"克制启用"

播客转书时，部分节点会过度生成内容。这套管线对几个节点做了"克制"：

| 节点 | 默认行为 | 克制后 |
|---|---|---|
| 注释 (annotation) | 每小节 2-3 条 | 全章 ≤5 条，只标书名/人物/术语首次出现 |
| 金句 (highlights) | 每集 5-10 条 | 每章 0-2 条，主播原话且能脱离上下文独立成立 |
| 插图 (illustration) | AI 生图 + 搜图 | **只搜图、不 AI 生成**（Serper 搜 5-10 张候选 → VLM 审 → 取第一张通过）|
| 编者序 | 每集一篇 | 删掉（合书层用整书 preface 代替）|
| 封面 | AI 生成 | 由 `render_book_front_matter` 用预设模板生成 |

如果你想恢复任意一项的默认行为，在对应 runner 的 prompt / 参数里调整即可。

---

## 输出目录结构

```
storage/workbench/jobs/possibility/
├── show.json
├── episodes_index.json
├── episodes/
│   ├── ep01/
│   │   ├── meta.json
│   │   ├── transcript_raw.json
│   │   ├── composed.json
│   │   ├── highlights.json
│   │   ├── inline_images/
│   │   └── chapter_preview.pdf
│   ├── ep02/
│   └── ...
├── merged/
│   ├── book_name.json
│   └── front_matter.pdf
├── logs/
│   └── ep01.log, ep02.log, ...
└── book.pdf                    # 最终整书
```

---

## 常见问题

**Q：跑一本 13 章的书需要多少 API 费用？**

主要消耗在 LLM (compose + annotate + name_chapter + rename_sections + name_book) 和 ASR。
- DeepSeek + 腾讯云 ASR：约 ¥15-30（13 集，每集 30-60 分钟）
- OpenAI gpt-4o + 腾讯云 ASR：约 ¥150-300

**Q：能用 Markdown 输出吗？**

当前只渲染 PDF。Markdown 输出 runner 在早期开发中被删除了，因为 typst 排版才能保证印刷级质量。如果你需要 Markdown，自己加一个 runner 读 `composed.json` 即可。

**Q：能用其他播客平台的 URL 吗？**

`fetch_show.py` 目前只支持小宇宙 (xiaoyuzhoufm.com)。Apple Podcasts / Spotify / YouTube 需要新增 parser。欢迎提 PR。
