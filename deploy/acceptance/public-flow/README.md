# 本机隔离主流程验收

此目录只用于**独立、可丢弃的本机合成活动**。Compose 项目固定为 `studio-public-showcase-20260928`，唯一宿主端口为 `127.0.0.1:18260`；PostgreSQL、Redis、MinIO 和外部 Chat 替身仅在 Docker 内网，三个数据卷只属于此项目。`PUBLISH_LIVE=0`、`DOUYIN_OAUTH_ENABLED=0`，没有常驻 Worker 或调度器，也不注入真实模型或平台凭据。准备脚本生成的秘密文件位于 Git 忽略的 `deploy/secrets/public-flow/`，不要打印或提交。

以下从**全新项目卷**开始，在仓库根目录 PowerShell 7 执行。先检查 Docker 项目名、18260 端口和现有卷；不要对旧业务卷执行 `down -v`。

```powershell
./deploy/acceptance/public-flow/prepare.ps1
docker build -f backend/Dockerfile -t studio-public-showcase-backend:local .
docker build -f deploy/minio/Dockerfile -t studio-public-showcase-minio:local .
Push-Location frontend
npm ci
npm run build
docker build -f ../deploy/acceptance/public-flow/Dockerfile.frontend -t studio-public-showcase-frontend:local .
Pop-Location
docker compose -f deploy/acceptance/public-flow/compose.yaml up -d --wait postgres redis minio mock-upstream
docker compose -f deploy/acceptance/public-flow/compose.yaml run --rm migrate
docker compose -f deploy/acceptance/public-flow/compose.yaml up -d --wait api frontend
python deploy/acceptance/public-flow/run_flow.py prepare
docker compose -f deploy/acceptance/public-flow/compose.yaml run --rm --no-deps tick
python deploy/acceptance/public-flow/run_flow.py edit
docker compose -f deploy/acceptance/public-flow/compose.yaml run --rm --no-deps tick
python deploy/acceptance/public-flow/run_flow.py finish
Push-Location deploy/acceptance/public-flow
npm ci
node browser_flow.mjs edit
Pop-Location
docker compose -f deploy/acceptance/public-flow/compose.yaml run --rm --no-deps tick
Push-Location deploy/acceptance/public-flow
node browser_flow.mjs finish
node browser_flow.mjs capture
Pop-Location
python deploy/acceptance/public-flow/verify_download.py
```

浏览器脚本使用独立 headless Chrome 会话，拒绝非 `127.0.0.1:18260` 请求；需要本机安装 Chrome。它通过真实 DOM 点击运营总览、活动详情、编辑、批准及导出；`capture` 只读。`run_flow.py` 通过真实 API 建合成商品/活动并检验版本、ZIP 清单与哈希；`tick` 调用真实流水线，外部 Chat 只由本地 `mock_upstream.py` 应答，媒体仍走本仓 FFmpeg/MinIO。请分别记录 API 与 UI 的结果，不能把前者冒充后者。未审核版本、真实平台授权和发布不在此流程内。

上面的 `docker build` 在首次构建时需要下载公开依赖与固定上游源码；这属于构建依赖，不是模型或平台调用。本轮 MinIO 无网络重建因缺 Alpine APK 索引失败，实际复用了已有、与本仓 Dockerfile 对应的本机缓存镜像 ID，并给它专属 showcase tag；不能将它写成“本轮从源码新构建”。公开仓库读者在可联网的构建环境应按上方 Dockerfile 命令构建。

结束后只停止本项目、保留三个卷便于复核：

```powershell
docker compose -f deploy/acceptance/public-flow/compose.yaml stop
```

三镜头审核按钮的前端回归可单独运行，不依赖上面的 Docker 流程：

```powershell
node deploy/acceptance/public-flow/storyboard_review_buttons.mjs
```

前提：本机安装 Chrome，并在仓库根目录执行 `npm ci --prefix frontend` 和 `npm ci --prefix deploy/acceptance/public-flow`。脚本启动临时本地 Vite 和 headless Chrome，渲染实际 Vue 页面，并用脚本模拟全部 `/api` 响应；非本地请求会被拦截。脚本核对首帧、视频通过/退回按钮的状态和请求参数，以及排队、运行、未知、失败、取消、缺失任务、锁定、旧版本、已审核和提交中禁用。通过仅证明前端条件与请求构造，不代表真实后端、存储或 Ark 生成已验收。

2026-10-01 实测记录：基线 `13f2f1b5d055d2efe45d23fe01c584b171d00852`。在最终有效阶段 fixture 下，临时恢复四处原始状态比较后执行同一脚本，报 `AssertionError: 首帧通过 enabled=true`、实际 `false`，脚本退出码 1；随后在 `finally` 中恢复修复文件。修复后的脚本退出码 0，输出 `storyboard_review_buttons: PASS (rendered Vue, mocked /api, no backend)`；在 `frontend/` 执行 `npm run build` 退出码 0（Vue 类型检查及 Vite 构建通过，仅有现存的 500 kB 大 chunk 提示）。本次未调用真实后端、存储或 Ark 服务。
