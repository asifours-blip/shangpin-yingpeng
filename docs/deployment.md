# 本地部署边界

此说明只面向**空数据库的本机隔离启动**。公开快照不含原私有仓库历史、0013 发布 checkout、备份、密钥或停流证明，不能用它直接重演原私有 0013→0014 升级。旧库迁移须由持有匹配发布版本和完整备份的运营者在受控环境另行处理，不能运行 `up` 试探升级。

## 首次启动

需要 PowerShell 7.4+、Docker Compose v2、可用端口与持久卷。在仓库根目录：

```powershell
./deploy/stack.ps1 init
# 检查 deploy/stack.env 和 deploy/secrets/ 的访问权限、项目名、端口及本地 URL。
./deploy/stack.ps1 up
./deploy/stack.ps1 smoke
```

`init` 只填补缺失的配置与随机秘密文件，不覆盖已有文件。`up` 先启动 PostgreSQL、Redis、MinIO，再把空库迁移到 `0014_operation_plans` 并启动 API/前端；遇到其他 schema 版本会拒绝。`smoke` 检查依赖、schema 和前端 HTML，**不证明**登录、签名媒体、生成、OAuth 或发布可用。前端端口默认只绑定宿主 `127.0.0.1`；此栈未配置公网 HTTPS。

空用户库会建立固定演示账号，`APP_ENV=production` 不会跳过。不能把本地栈直接开放到公网；先改造账号初始化、凭据管理、权限、HTTPS、监控与恢复流程，并完成真实上游验收。不要提交 `deploy/stack.env`、`deploy/secrets/` 或任何真实 token。

## 服务与恢复限制

默认服务为 PostgreSQL、Redis、MinIO、API、前端。`migrate` 是工具 profile；`generation-worker`、`legacy-generation-worker`、`publish-worker` 位于 workers profile，`operation-scheduler` 位于 scheduler profile，均不随 `up` 自动启动。Compose 固定 `PUBLISH_LIVE=0`、`DOUYIN_OAUTH_ENABLED=0` 和空模型密钥；没有连接时活动会停在待连接。

`stack.ps1` 与 `restore.ps1` 保留同版本备份/恢复校验代码，但恢复要求**备份记录的 Git commit、部署配置、镜像和独立密钥与干净发布 checkout 完全匹配**。导出器不携带原 Git 历史；公开快照的备份/恢复尚未演练，不能借用私有版本的测试结论。公开快照没有 0013 升级所需先决材料，不提供可执行的历史升级步骤。

`GET /health` 只证明 API 进程响应。媒体还依赖 MinIO 对象与数据库资产记录配套保存、`/objects/` 签名代理、FFmpeg 与中文字体；模型、来源、平台应用授权和发布能力仍需各自连接与验收。
