# 隔离验证入口

此入口只用于合成配置的完整后端回归。启动脚本显式使用空的 `deploy/ci/empty.env`，不读取开发者的 `.env`；运行时网络设为 `internal: true`，PostgreSQL、Redis、MinIO 无宿主端口；不启动 API、生成 Worker、发布 Worker 或调度进程。`PUBLISH_LIVE=0`、OAuth 关闭，模型密钥是固定的测试标志，模型地址指向容器内不可达端口。测试结果若有失败或跳过，命令返回非零；JUnit 保存于忽略的 `deploy/ci/results/backend.xml`。

从仓库根目录构建镜像并执行：

```sh
docker build -f backend/Dockerfile -t studio-public-ci-backend:local .
docker build -f deploy/ci/Dockerfile.test -t studio-public-ci-test:local .
docker build -f deploy/minio/Dockerfile -t studio-public-ci-minio:local .
PUBLIC_CI_PROJECT=studio-public-ci-local bash deploy/ci/run.sh
```

`run.sh` 在结束时仅停止本轮项目，保留卷；再次运行前应使用新的 `PUBLIC_CI_PROJECT` 获得空数据库。GitHub Actions 使用相同入口，并用 Node 20.19.5 执行前端 `npm ci` 与 `npm run build`。本地已有相同源码构建的 MinIO 镜像时可复用该镜像标识，远端 CI 始终从当前候选 Dockerfile 构建。
