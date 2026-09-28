# 快照定向验证

2026-09-28 在本地独立候选目录执行。以下仅证明改写过的模板、合成媒体与前端可构建；未启动数据库、对象存储、Worker、调度器或真实平台连接，也没有运行完整后端套件或远端 CI。

| 检查 | 命令或入口 | 结果 |
| --- | --- | --- |
| 前端依赖 | 候选 `frontend/` 内 `npm ci` | exit 0，安装 73 个包 |
| 前端构建 | 候选 `frontend/` 内 `npm run build` | exit 0，Vite 处理 1683 个模块，19.26 秒；仅有大于 500 kB 的 chunk 提示 |
| 营销提示词与事实边界 | 候选 `backend/` 内 `python -m pytest -q tests/test_copywriting.py::test_creation_direction_is_separate_from_product_facts tests/test_copywriting.py::test_source_reference_reaches_prompt_without_becoming_product_fact tests/test_copywriting.py::test_copy_prompts_load_from_markdown tests/test_copywriting.py::test_risk_lexicon_single_char_zui_is_not_a_hit` | 4 passed，exit 0；使用已有本机虚拟环境解释器，但工作目录和被测应用均为候选 |
| 合成媒体与抖音 HTTP 契约 | 候选 `backend/` 内 `python scripts/verify_public_media.py`，在已有 backend 镜像的一次性只读容器中执行，`--network none`，候选 backend 只读挂载 | exit 0；Pillow 可解码合成 JPEG，ffprobe 确认 1 秒视频，真实 `DouyinPublishAdapter` 经 `httpx.MockTransport` 完成封面、视频、创建三阶段 |

媒体验证所用本机缓存镜像 ID 为 `sha256:d97e34d49b4c844f8810811c7c02bd67765c2d00b87eb7de3862ac5a401a2e74`；它只提供 Python 依赖、Pillow 与 ffprobe，不包含本快照业务代码。候选代码以只读挂载执行。合成 MP4 的 SHA-256 为 `92673A69558B784453BF48A6C117148D86BAD22C22FC4A0E8902C5F17E4F0533`，几何 JPEG 为 `D790C799526C84F7357C6F0AB1B1753C1860E6FB21024B3C52916D69C3BE2FBA`。

运行脚本不会发送真实 HTTP；`MockTransport` 只在进程内检查请求正文和响应解析。这里没有证明 Docker Compose 首次启动、真实 OAuth、模型生成、真实发布或公开环境安全性。
