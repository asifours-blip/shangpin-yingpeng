# 公开副本验证记录

2026-09-28 在独立公开副本执行。下表先记录导出改写的定向检查；随后在独立数据库和对象存储完成完整后端套件及本地业务主链。[GitHub Actions 运行 36376495278](https://github.com/asifours-blip/shangpin-yingpeng/actions/runs/36376495278) 已成功；真实模型/平台连接与公网部署仍未验证。

| 检查 | 命令或入口 | 结果 |
| --- | --- | --- |
| 前端依赖 | 候选 `frontend/` 内 `npm ci` | exit 0，安装 73 个包 |
| 前端构建 | 候选 `frontend/` 内 `npm run build` | exit 0，Vite 处理 1683 个模块，19.26 秒；仅有大于 500 kB 的 chunk 提示 |
| 营销提示词与事实边界 | 候选 `backend/` 内 `python -m pytest -q tests/test_copywriting.py::test_creation_direction_is_separate_from_product_facts tests/test_copywriting.py::test_source_reference_reaches_prompt_without_becoming_product_fact tests/test_copywriting.py::test_copy_prompts_load_from_markdown tests/test_copywriting.py::test_risk_lexicon_single_char_zui_is_not_a_hit` | 4 passed，exit 0；使用已有本机虚拟环境解释器，但工作目录和被测应用均为候选 |
| 合成媒体与抖音 HTTP 契约 | 候选 `backend/` 内 `python scripts/verify_public_media.py`，在已有 backend 镜像的一次性只读容器中执行，`--network none`，候选 backend 只读挂载 | exit 0；Pillow 可解码合成 JPEG，ffprobe 确认 1 秒视频，真实 `DouyinPublishAdapter` 经 `httpx.MockTransport` 完成封面、视频、创建三阶段 |

媒体验证所用本机缓存镜像 ID 为 `sha256:d97e34d49b4c844f8810811c7c02bd67765c2d00b87eb7de3862ac5a401a2e74`；它只提供 Python 依赖、Pillow 与 ffprobe，不包含本快照业务代码。候选代码以只读挂载执行。合成 MP4 的 SHA-256 为 `92673A69558B784453BF48A6C117148D86BAD22C22FC4A0E8902C5F17E4F0533`，几何 JPEG 为 `D790C799526C84F7357C6F0AB1B1753C1860E6FB21024B3C52916D69C3BE2FBA`。

运行脚本不会发送真实 HTTP；`MockTransport` 只在进程内检查请求正文和响应解析。这里没有证明 Docker Compose 首次启动、真实 OAuth、模型生成、真实发布或公开环境安全性。

## 完整后端回归

后端负责人在独立 `studio-public-ci-20260928` 内网项目的全新 `studio_ci_final` 空库迁移到 `0014_operation_plans` 后，执行公开副本完整后端套件：**267 passed、0 failed、0 skipped、2 warnings，退出码 0，86.82 秒**；JUnit 汇总一致。此前定向回归为 36 passed。首轮完整执行有 17 项因空模型标记触发测试替身门禁而失败，随后仅修隔离测试配置，未修改业务实现，再用全新测试库取得上述最终结果。CI 项目不映射宿主端口，不启动真实平台调用。[远端 CI](https://github.com/asifours-blip/shangpin-yingpeng/actions/runs/36376495278) 在提交 `515d1efeceecb0097cd534cce0b71531eeae9b8f` 上也成功：后端 267 passed、0 skipped、2 warnings（82.24 秒），前端构建处理 1683 个模块（7.09 秒）；这是远端隔离测试与构建结果，不证明真实上游。

## 本机主链（API 与真实服务）

独立 `studio-public-showcase-20260928` 项目使用专属 PostgreSQL、Redis、MinIO 卷及 loopback `127.0.0.1:18260`，业务 API 使用公开副本构建的后端镜像（ID `sha256:4d64ffeac95caf97a71c5e60c5bc376a62090ed0055fb50466e803fa9978371f`）；前端从公开副本源码重建（1683 模块、退出码 0），再装入独立 Nginx 镜像（ID `sha256:185dc2709625f8d338eabf75ec8dacf5c23e48d7a839755724be5de2d4576076`）。仅外部 Chat HTTP 边界接入本地替身，应用仍运行原有 prompt 组装、响应解析、质检、数据库事务及 FFmpeg 媒体处理。`PUBLISH_LIVE=0`、`DOUYIN_OAUTH_ENABLED=0`，没有启动常驻生成 Worker、发布 Worker 或调度器。

真实 API 主链：合成商品图与人工定义的合成事实上传/创建为商品 1、事实版本 1；来源为空仍创建双平台活动 1，重复启动取得相同 run 1；有限 tick 实际执行 6 步。API 编辑抖音产生 v2 后再执行 2 个媒体步骤，双平台审核通过。抖音 v2 ZIP（SHA-256 `a1f61c449b4843e17db5cfd88260574b5111e9c67eb327a50d03419f02172264`）与小红书 v1 ZIP（`1512db2bca6451dea44f64923c720d03519675dad711399823aff3dc73114172`）逐文件核对清单顺序、版本及 SHA-256；发布安排只读查看，未提交或真实发布。

真实浏览器链路另行执行，不能与 API 主链混算：隔离 headless Chrome 从导航点击运营总览→活动详情→审核，在抖音侧**页面保存为 v3**，有限 tick 再重做 2 个媒体步骤，然后在页面点击**通过**与**导出当前已审核版本**。浏览器确实下载 `campaign-1-douyin-v3.zip`（257683 字节）；对下载文件复核 5 个交付文件的顺序与 SHA-256，整个 ZIP 的 SHA-256 为 `75438bb5733c786677f6acc91f8fe7c5a8ddb855a1e41ab4b81d12707a117801`。1440×900 与 390×844 页面均无文档横向溢出；浏览器记录外部请求 0、页面脚本错误 0、API 错误 0。截图： [运营总览桌面](screenshots/public-flow-overview-desktop.png)、[活动详情桌面](screenshots/public-flow-detail-desktop.png)、[素材交付桌面](screenshots/public-flow-delivery-desktop.png)，以及对应[窄屏总览](screenshots/public-flow-overview-narrow.png)、[窄屏详情](screenshots/public-flow-detail-narrow.png)、[窄屏交付](screenshots/public-flow-delivery-narrow.png)。这些页面均读取本次合成活动的真实 API 数据，没有加载旧飞机视频或旧私有截图。

可重演命令和安全边界见[专用验收入口](../deploy/acceptance/public-flow/README.md)。最终运行使用公开源码镜像；最初缓存镜像的 `migrate-0014` 入口不兼容导致一次迁移尝试退出 1，改用其 `migrate` 后仅在本项目空库迁移成功；后续 API 已切换为公开源码镜像。有限 tick 的首两次配置试跑分别在 Python 导入与秘密文件加载前置阶段退出，没有领取步骤；补齐专用 `PYTHONPATH` 和秘密文件 wrapper 后才执行上述 6＋2＋2 个真实步骤。MinIO 本轮无网络源码重建因缺 Alpine APK 索引退出 1，实际复用与本仓 Dockerfile 对应的已核本机缓存镜像并单独标记（ID `sha256:b5027e20949671a349b820e3f3f905917add64ac31c575bccca157713b5cc76c`），不能把它说成此次新构建。读者在可联网环境可按该 Dockerfile 构建。

验收结束后仅对 `studio-public-showcase-20260928` 执行 `docker compose ... stop`，退出码 0；该项目运行容器 0，专属 PostgreSQL、Redis、MinIO 三卷仍在以供复核。没有清除旧项目或日常服务。当前结论限本机合成流程与已执行 CI，不包括真实模型生成、OAuth/发布、公开环境安全或备份恢复。
