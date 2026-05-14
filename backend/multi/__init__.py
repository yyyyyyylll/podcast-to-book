"""
EchoPress 工作台（Workbench）— B 端自用工具集

设计原则（必读）：
1. workbench 只 import `app.workflow.*` `app.services.*` `app.config`，
   禁止 import `app.api.*` `app.auth.*` `app.payments.*` `app.background.*`。
2. 反向：`app/` 下的任何模块都不许 import `workbench.*`。
3. 工作台运行时必须使用独立的 .env.workbench，禁止连生产数据库 / 生产 bucket。
4. 工作台所有产物落 `storage/workbench/jobs/<job_id>/`，不污染 C 端 storage 结构。
5. 工作台改动不应触发 C 端 CI/CD（参见 .github/workflows/deploy.yml 的 paths-ignore）。

详见 backend/workbench/README.md
"""
