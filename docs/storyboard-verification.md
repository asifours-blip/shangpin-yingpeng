# 三镜头审核验收记录（2026-09-29）

本地分支 `feat/storyboard-review`，基线 `a293ce2f5311fbec9024629f9b97029d3ad09029`。三镜头是独立素材交付模式，ZIP 保存三组已审首帧/视频与 manifest；未合成为成片，因此发布排期被阻断。原 FFmpeg 成片路线保留。

| 检查 | 结果 | 原始证据 |
| --- | --- | --- |
| 已知媒体失败用例 `pytest -q tests/test_douyin_http.py::test_cover_video_create_send_reviewed_bytes_text_and_distinct_ids` | 1 passed | `%TEMP%\shangpin_storyboard_media_single_20260929.log` |
| 明确无 PostgreSQL 子集，含分镜纯契约、媒体渲染、旧版 HTTP/worker 等 | 83 passed，1 deselected；排除的是 `test_worker_loop.py::test_claim_mutex_semantics_live_pg` | `%TEMP%\shangpin_storyboard_nopg_20260929.log` |
| `ruff check --no-cache`（改动的后端及测试文件） | 通过 | `%TEMP%\shangpin_storyboard_ruff_20260929.log` |
| `npm run build`（Vue 类型检查及 Vite 构建） | 通过；仅有现存大 bundle 提示 | `%TEMP%\shangpin_storyboard_frontend_build_20260929.log` |
| 隔离无头 Chrome，最终构建 + 本地模拟 API | 全高截图已人工检查：三镜头、版本、预算、媒体状态、审核和发布阻断均展示；服务已停 | [界面截图](screenshots/storyboard-review-fixture-desktop.png)、`%TEMP%\shangpin_storyboard_ui_fixture_final_err_20260929.log` |
| 全量 `pytest -q` 诊断 | 在最后一次计费守卫修正前启动；数据库不可达、运行到 51% 时 Ctrl-C 中断，进程退出码 1，无完整汇总。收集顺序映射的第 133 项 F 后由定向测试确认是 Windows 缺 `tzdata` | `%TEMP%\shangpin_storyboard_pytest_full_20260929.log`、`%TEMP%\shangpin_storyboard_collect_20260929.log` |
| 定向 DST 用例 `test_operation_plans.py::test_dst_nonexistent_and_repeated_local_windows` | 原失败：`ZoneInfoNotFoundError(America/New_York)`；增加 Windows 专用 `tzdata==2026.4` 后 1 passed | `%TEMP%\shangpin_storyboard_dst_target_20260929.log`、`%TEMP%\shangpin_storyboard_dst_fixed_20260929.log` |

无 PostgreSQL 子集复现命令（在 `backend/`，先把 `.tools/ffmpeg/ffmpeg-9.0.2-essentials_build/bin` 仅加入当前进程 PATH）：

```powershell
.\.venv\Scripts\python.exe -m pytest -q --tb=short -x tests/test_consumer_qc.py tests/test_deploy_media_url.py tests/test_douyin_credentials.py tests/test_douyin_http.py tests/test_media_render.py tests/test_sources_collect.py tests/test_campaign_pipeline.py tests/test_worker_loop.py tests/test_storyboard_contract.py tests/test_copywriting.py::test_creation_direction_is_separate_from_product_facts tests/test_copywriting.py::test_source_reference_reaches_prompt_without_becoming_product_fact tests/test_copywriting.py::test_copy_prompts_load_from_markdown tests/test_copywriting.py::test_risk_lexicon_single_char_zui_is_not_a_hit tests/test_i2v.py::test_extract_video_url_shapes tests/test_i2v.py::test_run_i2v_uses_product_asset -k 'not claim_mutex_semantics_live_pg'
```

媒体依赖：FFmpeg 官网 [Windows 构建入口](https://ffmpeg.org/download.html) 链至 Gyan 的 `ffmpeg-9.0.2-essentials_build.7z`；[发布者 SHA-256](https://www.gyan.dev/ffmpeg/builds/packages/ffmpeg-9.0.2-essentials_build.7z.sha256) 与下载包一致：`4705843ccaaf54257c16ad90f3e952ece33c17df964ecf7bfdbb0f49c7171077`。仅解出 `ffmpeg.exe`/`ffprobe.exe` 到 Git 忽略的 `.tools/ffmpeg/`，只在测试进程 PATH 使用；未修改系统安装或 PATH。

2026-09-29 当日待验收：新 PostgreSQL 集成测试、MinIO/worker 模拟 provider 联动；当时 Docker 不可用。用户已选择暂缓真实 Ark 图片/视频生成；根目录 `.env` 未提供有效项目配置，本次未调用付费生成。截图使用模拟 API 和仓库测试素材，不代表真实数据库或模型端到端成功。

## 隔离存储与真实后端验收（2026-09-30）

本轮使用独立的 `storyboard_test` PostgreSQL、独立 Redis、独立 MinIO 容器；不连接现有 `wutian-aigc-*` 业务容器。三个临时容器均标记 `codex.task=shangpin-storyboard-20260930`，只监听 127.0.0.1 的 25432/26379/29000 端口。数据迁移到 0014 后，明确使用 `PUBLIC_CI_STRICT=1`，跳过视为失败。所有 Ark 图片/视频函数只在测试进程中替换为合成结果，没有调用远端模型；用户已明确暂缓真实生成。

| 检查 | 结果 | 原始证据 |
| --- | --- | --- |
| 新三镜头 PostgreSQL 集成测试 | 3 passed、0 skipped | `%TEMP%\shangpin_storyboard_pg3_20260930.log` |
| 新真实 MinIO + worker + 审核/锁定/局部重做/六资产 ZIP SHA/发布阻断测试 | 1 passed、0 skipped。真实任务领取、存储读写和 API；仅 Ark provider 返回合成媒体 | `%TEMP%\shangpin_storyboard_real_storage_20260930.log` |
| 相关 PostgreSQL 回归 | 52 passed、0 skipped；命令见下方 | `%TEMP%\shangpin_storyboard_pg_related_20260930.log` |
| 真实 FastAPI + Redis 会话 + Vite + 隔离无头 Chrome | 登录 200；活动 56 从 v17 三镜头全锁定，点击解锁镜头 2 再点击只重做本镜头视频，成为 v19；镜头 1/3 资产 ID 不变，镜头 2 首帧保留、视频引用失效。页面含发布阻断。媒体来自隔离 MinIO，Ark provider 模拟 | `%TEMP%\shangpin_storyboard_browser_cdp_final_20260930.log`、[操作前截图](screenshots/storyboard-review-real-backend-before.png)、[操作后截图](screenshots/storyboard-review-real-backend-after.png) |
| 临时数据与进程清理 | 测试用户 62/活动 56/7 个对象按 owner 核对后删除；只停本轮三个 `--rm` 容器及其独占匿名卷；8000/5173/19222 无监听；Docker Engine 29.8.1 仍运行 | `%TEMP%\shangpin_storyboard_browser_cleanup_20260930.log`、`%TEMP%\shangpin_storyboard_docker_cleanup_20260930.log` |

相关回归命令在 `backend/` 执行，测试进程环境变量指向上面的隔离容器（`POSTGRES_*`、`REDIS_*`、`MINIO_*` 均为合成测试值，`ARK_API_KEY=synthetic-test-no-remote`）：

```powershell
.\.venv\Scripts\python.exe -m pytest -q -ra --tb=short tests/test_storyboard_review.py tests/test_review_versions.py tests/test_publish_jobs.py tests/test_worker_loop.py::test_claim_mutex_semantics_live_pg
```

真实 MinIO 用例并不证明 Ark 图像/视频模型可调用；它证明模拟 provider 结果经本项目真实 worker、对象存储、审核、导出、发布约束的闭环。浏览器截图中的商品图和视频是合成测试素材，不是模型输出。浏览器种子与 CDP 命令原文保留在 `%TEMP%\shangpin_storyboard_browser_seed_20260930.py` 和 `%TEMP%\shangpin_storyboard_browser_cdp_20260930.cjs`；第一次种子脚本路径错误、第一次 CDP 脚本语法错误以及一次页面文案断言错误的原始失败日志也保留，最终通过日志以 `_fixed_` / `_final_` 命名。临时测试资源已清理，复跑需重新创建隔离容器和数据。未再次运行全量测试；上轮无法定位的全量诊断已由 DST 定向用例修复验证。
