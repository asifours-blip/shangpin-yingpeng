# 三镜头审核验收记录（2026-09-29）

本地分支 `feat/storyboard-review`，基线 `a293ce2f5311fbec9024629f9b97029d3ad09029`。三镜头是独立素材交付模式，ZIP 保存三组已审首帧/视频与 manifest；未合成为成片，因此发布排期被阻断。原 FFmpeg 成片路线保留。

| 检查 | 结果 | 原始证据 |
| --- | --- | --- |
| 已知媒体失败用例 `pytest -q tests/test_douyin_http.py::test_cover_video_create_send_reviewed_bytes_text_and_distinct_ids` | 1 passed | `%TEMP%\shangpin_storyboard_media_single_20260929.log` |
| 明确无 PostgreSQL 子集，含分镜纯契约、媒体渲染、旧版 HTTP/worker 等 | 83 passed，1 deselected；排除的是 `test_worker_loop.py::test_claim_mutex_semantics_live_pg` | `%TEMP%\shangpin_storyboard_nopg_20260929.log` |
| `ruff check --no-cache`（改动的后端及测试文件） | 通过 | `%TEMP%\shangpin_storyboard_ruff_20260929.log` |
| `npm run build`（Vue 类型检查及 Vite 构建） | 通过；仅有现存大 bundle 提示 | `%TEMP%\shangpin_storyboard_frontend_build_20260929.log` |
| 隔离无头 Chrome，最终构建 + 本地模拟 API | 全高截图已人工检查：三镜头、版本、预算、媒体状态、审核和发布阻断均展示；服务已停 | [界面截图](screenshots/storyboard-review-fixture-desktop.png)、`%TEMP%\shangpin_storyboard_ui_fixture_final_err_20260929.log` |
| 全量 `pytest -q` 诊断 | 在最后一次计费守卫修正前启动；数据库不可达、运行到 51% 时 Ctrl-C 中断，进程退出码 1；原始进度含 1 个未定位 F，无完整汇总，不能宣称全量通过 | `%TEMP%\shangpin_storyboard_pytest_full_20260929.log` |

无 PostgreSQL 子集复现命令（在 `backend/`，先把 `.tools/ffmpeg/ffmpeg-9.0.2-essentials_build/bin` 仅加入当前进程 PATH）：

```powershell
.\.venv\Scripts\python.exe -m pytest -q --tb=short -x tests/test_consumer_qc.py tests/test_deploy_media_url.py tests/test_douyin_credentials.py tests/test_douyin_http.py tests/test_media_render.py tests/test_sources_collect.py tests/test_campaign_pipeline.py tests/test_worker_loop.py tests/test_storyboard_contract.py tests/test_copywriting.py::test_creation_direction_is_separate_from_product_facts tests/test_copywriting.py::test_source_reference_reaches_prompt_without_becoming_product_fact tests/test_copywriting.py::test_copy_prompts_load_from_markdown tests/test_copywriting.py::test_risk_lexicon_single_char_zui_is_not_a_hit tests/test_i2v.py::test_extract_video_url_shapes tests/test_i2v.py::test_run_i2v_uses_product_asset -k 'not claim_mutex_semantics_live_pg'
```

媒体依赖：FFmpeg 官网 [Windows 构建入口](https://ffmpeg.org/download.html) 链至 Gyan 的 `ffmpeg-9.0.2-essentials_build.7z`；[发布者 SHA-256](https://www.gyan.dev/ffmpeg/builds/packages/ffmpeg-9.0.2-essentials_build.7z.sha256) 与下载包一致：`4705843ccaaf54257c16ad90f3e952ece33c17df964ecf7bfdbb0f49c7171077`。仅解出 `ffmpeg.exe`/`ffprobe.exe` 到 Git 忽略的 `.tools/ffmpeg/`，只在测试进程 PATH 使用；未修改系统安装或 PATH。

待验收：新 PostgreSQL 集成测试、MinIO/真实 worker 联动与真实 Ark 图片/视频生成。Docker 当前不可用；本次进程中的 Ark Key 对北京 `/files` 只读端点返回 401，项目 `.env` 尚无经验证的有效图片/视频配置。未调用付费生成。截图使用模拟 API 和仓库测试素材，不代表真实数据库或模型端到端成功。
