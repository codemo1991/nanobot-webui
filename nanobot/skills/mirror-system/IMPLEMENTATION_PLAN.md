# 镜室长期功能实施路线图

本文档描述三个长期功能的实施计划：赏 Qwen-Image、定时任务、镜融合与 profile。

**实施状态**（2025-02）：三项均已实现。

---

## 1. 赏 Qwen-Image 接入

### 现状
- `start_shang()` 返回 `descriptionA`/`descriptionB` 占位文案，`imageA`/`imageB` 为 `None`
- 前端 ShangTab 展示文本描述，无真实图片
- shang-prompts.md 已有 A/B 图生成原则与 Prompt 模板

### 技术方案

**API**：阿里云 DashScope 文生图
- 端点：`POST https://dashscope.aliyuncs.com/api/v1/services/aigc/multimodal-generation/generation`
- 模型：`qwen-image-plus`（艺术风格）或 `qwen-image-max`
- 请求体：`input.messages` 含文本 prompt，`parameters.size` 如 `1024*1024`

**依赖**：
```txt
# 可选：官方 SDK，或直接 requests
dashscope>=1.14.0
```

**实现步骤**：
1. 新建 `nanobot/providers/dashscope_image.py`：封装文生图调用（复用现有 DASHSCOPE_API_KEY）
2. 修改 `mirror_service.start_shang()`：
   - 根据 topic 从 shang-prompts 或 LLM 生成 A/B 图描述
   - 调用 DashScope 生成两张图，得到 URL 或 base64
   - 将图片保存到 `mirror/shang/images/{record_id}_A.png`，record 中存相对路径或 URL
3. 修改 `getShangToday`/`getShangRecords` 返回的 record 含 `imageA`/`imageB` 可访问 URL
4. 前端 ShangTab 用 `<img src="...">` 展示，无图时降级为文本描述

**配置**：复用 `config.providers.dashscope.api_key`，可选增加 `mirror.qwen_image_model`。

---

## 2. 定时任务：悟/辩未封存会话每日自动分析封存

### 现状
- 悟/辩会话需用户手动点击「结束悟道/辩论」才会封存
- 非当日、status=active 的会话不会被自动处理

### 技术方案

**方案 A：CLI 独立命令（推荐）**
- 新增 `nanobot mirror seal-stale` 命令
- 逻辑：遍历 wu/bian 会话，筛选 `status=active` 且 `created_at` 非今日，逐个调用 `seal_session`（含 LLM 分析）
- 用户通过系统 crontab 定时执行：`0 0 * * * nanobot mirror seal-stale`（每日 0 点）

**方案 B：扩展 nanobot agent 的 CronService**
- 增加 `CronPayload.kind = "mirror_seal_stale"`
- `on_cron_job` 中若为该类型，直接调用 mirror 封存逻辑
- 需在 agent 启动时注入 MirrorService（或复用 workspace + sessions）

**推荐**：方案 A 更简洁，不依赖 agent 常驻，适合 web-ui 为主的使用场景。

**实现步骤**：
1. 在 `nanobot/cli/commands.py` 增加 `mirror_app = typer.Typer()`，注册 `seal-stale`
2. `seal-stale` 命令：
   - 加载 config、workspace、sessions
   - 实例化 MirrorService
   - 调用 `list_sessions("wu")` 与 `list_sessions("bian")`，过滤 active 且非今日
   - 对每个会话调用 `_run_mirror_analysis` + `seal_session`
3. 文档中说明如何配置 crontab

**Crontab 配置示例**：

```bash
# 每日 0 点执行镜室封存
0 0 * * * /path/to/nanobot mirror seal-stale
```

若 nanobot 以虚拟环境安装，可使用绝对路径：

```bash
# 示例：conda 环境
0 0 * * * /home/user/miniconda3/envs/nanobot/bin/nanobot mirror seal-stale

# 示例：venv + pip
0 0 * * * /home/user/venv/nanobot/bin/nanobot mirror seal-stale
```

**预演（dry-run）**：执行前可先用 `--dry-run` 查看将被封存的会话，不实际执行：

```bash
nanobot mirror seal-stale --dry-run
# 或
nanobot mirror seal-stale -n
```

---

## 3. 镜融合与 profile 生成

### 现状
- `get_profile()` 仅读取 `mirror/profile.json` 或 `snapshots/*.json`
- 无自动生成逻辑
- jing-prompts.md 已有融合归纳 Prompt 模板

### 数据来源
- 悟：`mirror/wu/*.md`、`mirror/wu/MEMORY.md`
- 辩：`mirror/bian/*.md`、`mirror/bian/MEMORY.md`
- 赏：`mirror/shang/records.json`

### 技术方案

**新增 `mirror_service.generate_profile()`**：
1. 汇总上述三部分数据（日期范围内的 session 分析、赏记录）
2. 从 `references/jing-prompts.md` 加载融合 Prompt
3. 调用 LLM 生成结构化 JSON（bigFive、jungArchetype、drivers、conflicts、suggestions、updateTime）
4. 写入 `mirror/profile.json`，并追加到 `snapshots/{YYYY-MM-DD}.json`

**触发方式**：
- 手动：吾 Tab 增加「生成/刷新画像」按钮，调用新 API `POST /api/v1/mirror/profile/generate`
- 可选：每周定时（结合 cron 或 CLI）自动生成

**实现步骤**：
1. 在 `mirror_service` 中新增 `_load_wu_summary()`、`_load_bian_summary()`、`_load_shang_summary()` 汇总函数
2. 新增 `generate_profile()`：拼接 Prompt、调用 LLM、解析 JSON、写入文件
3. 新增 API `POST /mirror/profile/generate`，返回生成的 profile
4. 吾 Tab 增加「刷新画像」按钮，调用该 API
5. 周汇总、变化追踪：可在 profile 中增加 `prevProfile` 或单独 `mirror/changes/{date}.md`，后续迭代

---

## 实施优先级建议

| 顺序 | 项目 | 工作量 | 依赖 |
|------|------|--------|------|
| 1 | 镜融合与 profile | 中 | 无，可独立完成 |
| 2 | 定时任务 seal-stale | 小 | 无 |
| 3 | 赏 Qwen-Image | 中～大 | DashScope API Key、图片存储 |

建议先做 **镜融合**，让吾 Tab 具备完整闭环；再做 **定时任务** 减轻用户负担；最后做 **赏 Qwen-Image**，需额外配置与测试。
