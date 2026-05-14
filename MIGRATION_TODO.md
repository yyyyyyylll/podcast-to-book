# 迁移后的手工 TODO

prepare-oss.sh 只做机械替换，下列改动需要人工：

## 后端

- [ ] `backend/single/api/deps.py` —— 删 `get_current_user` 的 auth 依赖（改成 stub 或删除函数）
- [ ] `backend/single/models/*.py` —— 删 `user_id` 外键字段
- [ ] `backend/single/main.py` —— 删除商业 router 注册：`auth, admin, credits, referral, payment_callback, host_routes`
- [ ] `backend/single/api/upload.py` / `tasks.py` —— 删积分扣费、用户绑定逻辑（grep `credit_service` `payment_service` `user_id`）
- [ ] `backend/single/api/internal.py` —— 评估是否保留（如果只服务于商业内部 API 则删）
- [ ] `backend/multi/AGENTS.md` —— 评估保留与否（内部协作文档）

## 前端

- [ ] `frontend/src/App.tsx` —— 路由表里删商业页面引用（已删的页面会编译报错，按报错改）
- [ ] `frontend/src/pages/LandingPage.tsx` —— 替换为极简开源介绍页
- [ ] 检查 `OrdersPage.tsx` / `UsagePage.tsx` 是否保留（取决于用户最终决定）
- [ ] grep `import.*analytics` —— 删埋点调用
- [ ] grep `import.*hostApi` —— 删 host 商业 API 调用
- [ ] grep `useUser` `useAuth` `Login` —— 删登录态相关代码

## 配置

- [ ] 新写 `.env.example`（只保留 LLM_API_KEY / ASR 相关）
- [ ] 新写 `README.md`
- [ ] 加 `LICENSE`（AGPL-3.0）
- [ ] 简化 `.gitignore`（不用排除 keys/ 因为开源版无支付）
- [ ] 检查 `Dockerfile` / `docker-compose.yml` 引用的路径是否还对

## 最后一步

- [ ] `cd <OUT_DIR> && git init && git add -A && git commit -m "Initial commit"`
- [ ] `gh repo create podcast-to-book --public --source=. --push`
