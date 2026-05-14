# EchoPress 整档播客内容方向诊断工具完整方案

现在这个工具的完整方案应该围绕一个核心来设计：

> 帮助播客主播看清：自己的整档节目到底沉淀在哪些内容方向上，哪些方向最值得优先整理，后续可以用什么标题和主题进行内容呈现。
> 
> 

它不是单期诊断工具，不是样册生成工具，也不是音频处理工具。它是 EchoPress 内部用于前期判断、内容顾问建议和后续成书方向判断的工具。

---

# 一、工具名称

建议名称：

```text
EchoPress 播客内容方向诊断工具
```

也可以在内部叫：

```text
Podcast Direction Diagnosis Workbench
```

对外展示时可以叫得温和一点：

```text
播客内容方向诊断
```

或者：

```text
播客主题沉淀建议
```

---

# 二、工具核心定位

这个工具解决的问题不是：

```text
这一期节目讲什么？
```

而是：

```text
这档播客整体在讲什么？
它有没有清晰的内容方向？
它是一个主方向贯穿，还是多个方向并行？
哪些方向下已经形成了可沉淀的子主题？
哪个方向最适合优先整理？
后续如果做主题样册、内容合集或电子书初稿，可以叫什么？
```

这才是你们真正需要的。

---

# 三、用户输入信息

工具不抓音频，不做转写，只处理文字信息。

## 必填输入

```json
{
  "podcast_name": "播客名称",
  "podcast_intro": "播客简介",
  "episode_list": [
    {
      "title": "节目标题",
      "intro": "节目简介 / 本期导读"
    }
  ]
}
```

## 可选输入

```json
{
  "host_note": "主播补充说明",
  "target_goal": "主播希望用于内容沉淀 / 样册展示 / 个人品牌 / 后续成书判断",
  "known_content_type": "访谈对话式 / 个人表达式 / 混合型 / 未知"
}
```

第一版里，必填只保留三个就够了：

```text
播客名称
播客简介
节目列表
```

不要一开始让用户填太多。

## 自动抓取的内部信息（联系方式模块）

`--job` 模式下（即 runner 从 `fetch_show` 已抓好的 `show.json` 复用），
还会顺手把以下"联系方式相关"的字段读出来，喂给 `contact.extract_contact_info()`：

```json
{
  "podcaster_names": ["主播昵称 1", "主播昵称 2"],
  "subscription_count": 18420,
  "contacts": [
    {"type": "weixin", "name": "公众号名", "note": "订阅公众号", "url": ""}
  ],
  "description": "节目详情完整文本（包括末尾的 / 联系方式块）"
}
```

`--input` 模式下，这些字段是**可选**的——用户在 JSON 里加上就用，不加
就在 `contact_info.extraction_warnings` 里说明，但不影响主流程。

输出会在 final_json 里加一个 `contact_info` 块，给 EchoPress 内部联系
主播用，**不出现在客户可见 markdown 主体**（只在 markdown 末尾的"内部用"区域显示）。

---

# 四、工具最终输出

最终输出应该分成两层：

## 1\. 内部判断结果

给 EchoPress 自己看。

包括：

```text
整体内容判断
内容结构类型
主要内容方向
方向与子主题
优先整理方向
不建议优先整理方向
内部提醒
```

## 2\. 客户可见结果

可以直接整理后发给主播。

包括：

```text
200—300 字内容诊断
建议优先整理方向
可沉淀子主题
建议标题
主要风险
```

---

# 五、核心工作流设计

建议工具保留 4 个工作流，而不是 3 个。
因为加一个“质量检查工作流”会让结果更稳。

```text
工作流一：整档内容方向识别
工作流二：方向与子主题梳理
工作流三：标题建议与内容诊断生成
工作流四：结果校验与降噪
```

---

# 工作流一：整档内容方向识别

## 目标

判断这档播客整体在讲什么。

它需要回答：

```text
这档播客是一个主方向贯穿吗？
还是多个方向并行？
主要内容方向有哪些？
这些方向是来自真实节目内容，还是只是表面标签？
```

## 输出

```json
{
  "overall_content_judgment": "",
  "content_structure_type": "",
  "main_directions": [],
  "diagnosis_reason": "",
  "internal_note": ""
}
```

## 内容结构类型

只允许四种：

```text
单一主方向型
多方向并行型
主方向清晰但有若干分支型
方向较散，暂不适合系统整理型
```

## 判断标准

```text
单一主方向型：
整档节目长期围绕一个大主题，比如女性成长、读书、商业访谈、心理疗愈。

多方向并行型：
节目里有几个稳定板块，比如读书、成长、女性主义、亲密关系并列存在。

主方向清晰但有若干分支型：
有一个核心气质或主线，但下面展开了不同分支，比如“女性自我理解”下面有读书、关系、成长、职业选择。

方向较散型：
节目标题和简介之间关联较弱，很难判断稳定内容方向。
```

---

# 工作流二：方向与子主题梳理

## 目标

把“主要内容方向”拆成可以继续整理的子主题。

例如：

```text
女性成长
  - 自我意识
  - 关系边界
  - 身体经验
  - 职业选择
  - 女性主义阅读

个人成长
  - 自我认知
  - 长期主义
  - 情绪管理
  - 生活节奏
  - 成长中的选择
```

## 输出

```json
{
  "structure_type": "",
  "directions": [
    {
      "direction_name": "",
      "direction_description": "",
      "sub_topics": [],
      "episode_evidence": [],
      "editing_value": "高 / 中 / 低"
    }
  ],
  "priority_direction": "",
  "priority_reason": "",
  "not_priority_direction": "",
  "not_priority_reason": ""
}
```

## 关键原则

这一层一定要有“取舍”。

不要把所有方向都说成适合整理。
工具必须敢于判断：

```text
哪个方向最值得优先做；
哪个方向虽然出现过，但不适合优先整理；
哪个方向只是偶尔出现，不能作为主线。
```

---

# 工作流三：标题建议与内容诊断生成

## 目标

把内部判断转成用户能理解的结果。

这一层输出两类内容：

## 1\. 建议标题

标题不是单期标题，也不是正式出版书名，而是后续主题样册、内容整理、电子书初稿可以使用的方向标题。

分三类：

```text
克制专业型
文艺表达型
清晰说明型
```

例如一档女性成长类播客：

```text
克制专业型：
《女性成长中的自我理解》
《关系、选择与主体意识》

文艺表达型：
《在生活里重新长出自己》
《把自己重新放回中心》

清晰说明型：
《关于女性成长与自我选择的内容整理》
《从阅读、关系到自我理解》
```

## 2\. 客户可见诊断

控制在 200—300 字，不要分点。

它应该包括：

```text
整档播客的整体方向
内容结构判断
最适合优先整理的方向
这个方向下的子主题
后续整理时需要注意的问题
```

---

# 工作流四：结果校验与降噪

## 为什么需要这个工作流

AI 很容易出现两个问题：

```text
1. 把播客拔高成很大的概念；
2. 明明资料里没有，却生成听起来很高级的主题。
```

所以建议加一个校验步骤。

## 校验目标

检查输出结果是否存在：

```text
单期节目诊断倾向
过度拔高
凭空创造主题
标题过度出版化
承诺出版或传播效果
所有方向都说适合
```

## 可以设计成轻量规则，不一定再调一次 AI

例如 parser 或 runner 里做基础检查：

```text
如果出现“正式出版”“公开发行”“销量”“爆款”等词，标记 warning。
如果 priority_direction 不在 directions 里，标记 warning。
如果 stable themes 没有 episode_evidence，标记 warning。
如果 customer_diagnosis 超过 350 字，标记 warning。
```

后续成熟后再加 AI 自检。

---

# 六、最终 JSON 输出结构

建议定成这个版本：

```json
{
  "metadata": {
    "tool_name": "EchoPress 整档播客内容方向诊断工具",
    "version": "v1.0",
    "input_type": "podcast_text_material"
  },
  "overall_diagnosis": {
    "podcast_name": "",
    "overall_content_judgment": "",
    "content_structure_type": "单一主方向型 / 多方向并行型 / 主方向清晰但有若干分支型 / 方向较散，暂不适合系统整理型",
    "main_directions": [],
    "diagnosis_reason": "",
    "internal_note": ""
  },
  "direction_structure": {
    "structure_type": "",
    "directions": [
      {
        "direction_name": "",
        "direction_description": "",
        "sub_topics": [],
        "episode_evidence": [],
        "editing_value": "高 / 中 / 低"
      }
    ],
    "priority_direction": "",
    "priority_reason": "",
    "not_priority_direction": "",
    "not_priority_reason": ""
  },
  "title_suggestions": {
    "professional_titles": [
      {
        "title": "",
        "reason": ""
      }
    ],
    "literary_titles": [
      {
        "title": "",
        "reason": ""
      }
    ],
    "clear_titles": [
      {
        "title": "",
        "reason": ""
      }
    ]
  },
  "customer_diagnosis": {
    "diagnosis_text": "",
    "recommended_direction": "",
    "recommended_sub_topics": [],
    "main_risk": ""
  },
  "quality_check": {
    "warnings": [],
    "confidence_level": "高 / 中 / 低",
    "manual_review_needed": true
  },
  "contact_info": {
    "podcast_name": "节目名",
    "host_names": ["主播昵称 1", "主播昵称 2"],
    "primary_host": "主播昵称 1",
    "subscription_count": 18420,
    "wechat_personal_id": "可加好友的个人微信号（从描述末尾正则抽出）",
    "wechat_personal_id_candidates": ["..."],
    "wechat_official_account": "公众号名（需要在微信里搜索，不是个人号）",
    "structured_contacts": [
      {"type": "weixin", "name": "...", "url": "", "note": "...", "source": "sdk / description"}
    ],
    "raw_contact_text": "节目详情末尾的 / 联系方式块原文（便于人工核对）",
    "extraction_warnings": ["..."]
  }
}
```

---

# 七、建议的文件架构

你现在既然在 \`workbench\` 里做，最方便的结构是：

```text
backend/workbench/
  prompts/
    podcast_direction_diagnosis.py

  parsers/
    podcast_direction_diagnosis_parser.py

  runners/
    podcast_direction_diagnosis_runner.py

  examples/
    podcast_direction_input.json
    podcast_direction_output.json
```

如果后面想做得更稳，可以拆成：

```text
backend/workbench/
  prompts/
    podcast_direction_overall.py
    podcast_direction_structure.py
    podcast_direction_titles.py
    podcast_direction_quality_check.py

  runners/
    podcast_direction_diagnosis_runner.py
```

但第一版不建议拆太多。
现在最适合的是：

```text
一个 prompt 文件
一个 parser 文件
一个 runner 文件
一个 example 文件
```

---

# 八、Prompt 设计方案

第一版建议只进行一次 AI 调用。
因为你的任务不需要太复杂，输入也只是文字资料。

## System Prompt 核心身份

```text
你是 EchoPress 的首席播客内容战略顾问。

你拥有出版策划、内容品牌诊断、播客栏目分析和主题编辑经验。你的任务不是分析某一期节目，也不是生成样册正文，而是根据整档播客已经提供的文字信息，判断这档播客整体呈现出的内容方向、方向结构、可沉淀子主题，并给出后续内容整理的标题建议。
```

## 必须强调的边界

```text
不要做单期节目主题诊断。
不要替某一期节目重新命名。
不要生成样册正文。
不要进行音频转写。
不要承诺正式出版、公开发行、销量或传播效果。
不要凭空创造节目中没有出现过的方向。
不要把零散内容强行包装成宏大主题。
```

## User Prompt 任务结构

```text
请完成以下任务：

1. 判断整档播客整体内容方向；
2. 判断内容结构类型；
3. 梳理主要方向；
4. 为每个方向拆解子主题；
5. 判断最值得优先整理的方向；
6. 给出标题建议；
7. 生成客户可见诊断；
8. 做基础质量检查。
```

---

# 九、诊断结果示例

假设输入是一档同时聊读书、个人成长、女性主义、亲密关系的播客。

## 输出示例

```text
整体内容判断：
这档播客整体以阅读和生活经验为入口，持续讨论女性如何理解自我、关系与成长。

内容结构类型：
主方向清晰但有若干分支型。

主要内容方向：
读书与思想、女性成长、亲密关系、个人成长、生活选择。

优先整理方向：
女性成长与自我理解。

优先原因：
这一方向能够同时承接播客中的阅读经验、关系讨论和个人成长表达，比单纯整理为读书分享更能体现节目气质，也更适合形成后续主题样册。
```

## 客户诊断示例

```text
从整体内容来看，这档播客并不只是单纯的读书分享，而是借由阅读、生活经验和关系讨论，持续延伸到个人成长与女性自我理解。它的内容结构属于“主方向清晰但有若干分支”的类型：读书是入口，个人经验是连接点，而女性成长与自我意识是更适合被沉淀的核心方向。后续如果继续整理，不建议把所有话题都平均展开，而是可以优先围绕“女性成长与自我理解”这个方向，细分出阅读、关系、边界、自我选择等子主题。这样会比单纯按节目顺序整理更有聚合感，也更容易形成一份有阅读路径的主题内容。
```

---

# 十、内部评分可以暂时不做

之前我们提过评分，但现在我建议第一版先不做分数。

原因是：

```text
分数容易让工具显得像机械评估；
你们现阶段更需要内容判断，而不是量化打分；
客户也不需要看到分数。
```

如果一定要保留，可以只保留内部置信度：

```json
{
  "confidence_level": "高 / 中 / 低",
  "manual_review_needed": true
}
```

这个比评分更实用。

---

# 十一、前端展示方案

虽然现在你先做 \`workbench\`，但后续页面可以这样设计。

## 输入区

```text
播客名称
播客简介
节目列表
主播补充说明
```

节目列表可以让用户直接粘贴：

```text
1. 节目标题：……
   节目简介：……

2. 节目标题：……
   节目简介：……
```

不要一开始做复杂表格。

## 输出区

建议分成 5 张卡片：

```text
1. 整体内容判断
2. 主要内容方向
3. 方向与子主题
4. 建议标题
5. 客户可见诊断
```

其中最重要的是第 5 张卡片，可以一键复制。

---

# 十二、版本规划

## v1：Workbench 内部可跑版

只做：

```text
输入 JSON
调用 AI
返回 JSON
本地输出
```

目标：验证 prompt 质量。

## v2：内部页面版

接入 frontend，做一个简单页面。

目标：让你不用改 JSON，直接复制粘贴节目资料。

## v3：结果编辑版

允许人工修改：

```text
优先整理方向
建议标题
客户诊断文案
```

目标：真正用于客户沟通。

## v4：客户报告版

一键导出：

```text
Markdown
PDF
诊断卡片
```

目标：作为 EchoPress 的服务交付前材料。

---

# 十三、这个工具在 EchoPress 业务里的位置

它应该放在这条链路中：

```text
客户对样册感兴趣
↓
客户提供播客主页或节目文字资料
↓
EchoPress 用工具做整档内容方向诊断
↓
判断播客整体内容方向
↓
给出优先整理方向和标题建议
↓
客户理解自己的内容可以如何沉淀
↓
进入样册、主题样册或后续成书服务
```

它不是最终产品本身，而是：

```text
售前判断工具
内容顾问工具
主题整理辅助工具
后续成书诊断工具
```

---

# 十四、最关键的产品边界

你后续写 prompt、写页面、写介绍时，一定要守住这几个边界：

```text
不做单期主题诊断。
不做音频转写。
不生成样册正文。
不承诺出版。
不强行拔高。
不替主播重新定义完全不存在的方向。
```

你们真正提供的是：

```text
从已有播客文字资料中，看清整档节目的内容方向、可沉淀主题和后续整理路径。
```

---

# 十五、最终一句话定义

这个工具可以这样定义：

> EchoPress 整档播客内容方向诊断工具，是一个基于播客简介、节目标题和节目导读的内部内容顾问工具，用于识别整档播客的主要内容方向，梳理可沉淀的子主题，并为后续样册、主题整理或电子书初稿提供标题建议与方向判断。
> 
> 

这就是完整方案。

> （注：文档部分内容可能由 AI 生成）
