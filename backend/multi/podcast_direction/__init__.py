"""
EchoPress 整档播客内容方向诊断工具

独立子模块，目录约定：

    podcast_direction/
      __init__.py        # 本文件（包标识）
      DESIGN.md          # 完整方案文档（产品 / Prompt 设计 / 边界）
      prompt.py          # PROMPTS dict（system + user 模板）
      parser.py          # 工作流四：纯规则降噪 + warnings 合并
      runner.py          # CLI 主入口（python -m workbench.podcast_direction.runner）
      examples/
        input_example.json
        output_example.json

不参与 workbench/prompts/__init__.py 的 get_workbench_prompts() 覆盖机制
（这个工具不是 C 端节点替身，是独立诊断工具）。
"""
