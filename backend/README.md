# 后端

FastAPI API 使用 SQLAlchemy、Alembic、PostgreSQL、Redis 和 MinIO；当前迁移 head 为 `0014_operation_plans`。活动生成、旧生图、发布和运营调度是独立进程，API 启动不等于启动它们。推荐按[根目录说明](../README.md#本地起步)使用隔离 Compose 栈。

空用户库启动会写入固定演示账号，即使 `APP_ENV=production` 也是如此；安全初始化改造前不可公网暴露。`GET /health` 只说明 API 进程响应，不覆盖数据库、对象存储、登录、媒体或外部服务。当前 Compose 固定 `PUBLISH_LIVE=0`、`DOUYIN_OAUTH_ENABLED=0`，不注入真实模型密钥；Worker 与调度器默认关闭。抖音 HTTP/OAuth 已有隔离契约实现，真实授权、上传与发布待联调；小红书服务端发布待接入。

需要直接调试源码时，应先在独立可丢弃环境准备 PostgreSQL、Redis、MinIO，并按根目录 [`.env.example`](../.env.example) 的**变量名**填写自己的 Git 忽略配置。只对空开发库执行迁移，不把下列命令用于现有 0013 数据：

```powershell
cd backend
py -3 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m alembic upgrade head
.\.venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

公开快照中的 JPEG 是几何合成图，上传契约测试的 MP4 是独立生成的 1 秒纯色视频；真实媒体生成路径仍需要 FFmpeg 和字体。测试须连接独立的测试数据库，历史私有库数据不能用来宣称本快照已验收。
