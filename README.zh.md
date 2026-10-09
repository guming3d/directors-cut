# Sora Agent Workflow

这是一个用于运行 Azure Sora2/Kling/Jimeng/Seedance 多智能体视频工作流的全栈工具包，包含 FastAPI 后端和 Next.js 仪表盘。后端负责编排 Director → Sora → Composer 智能体以生成分场景的片段、监控进度并拼接最终视频。前端支持粘贴分镜脚本、发起任务、查看实时状态、场景预览以及最终成片。

## 前置要求

- Python 3.10+
- Node.js 18.17+（或 20+）与 npm
- 系统 `PATH` 中可用的 `ffmpeg`/`ffprobe`
- `backend/sora-openai-client.py` 所需的 Azure Sora/OpenAI 凭据（如需 Kling 或 Jimeng 也要提供对应凭据）
- 可选环境变量：
  - `SORA_WORKFLOW_ALLOWED_ORIGINS` — 允许的 CORS 来源列表（逗号分隔）
  - `SORA_WORKFLOW_THREADS` — 并发渲染任务数量（默认 `3`）
  - `SORA_BLOCK_RETRIES` — 因审核拦截的场景的重试次数（默认 `3`）
  - `VIDEO_DOWNLOAD_VARIANT` — 覆盖传给 Sora 客户端的 variant 名称
  - `VIDEO_PROVIDER` — 选择后端：`sora2`（默认）、`kling`、`jimeng` 或 `seedance`

## 项目结构

- `sora_agent_workflow.py`：CLI 入口与 FastAPI 应用
- `backend/sora-openai-client.py`：单次渲染的直接 Sora 客户端
- `sora_workflow/`：核心智能体、服务、提示词与配置
- `frontend/`：Next.js 仪表盘（App Router，TypeScript）
- `Chapters/`：分镜脚本示例
- `scene_start_screen/` 与 `images_references/`：可选的起始帧与参考图
- `output/`：生成的片段、日志、清单与 TinyDB 状态（不提交版本库）

## 后端安装与使用

1. 创建虚拟环境并安装依赖：
   ```bash
   python -m venv .venv
   source .venv/bin/activate
   pip install -r requirements.lock
   ```
   `requirements.lock` 锁定了全部传递依赖（Docker 镜像也从它安装）。`agent-framework` 尚未 1.0，新版本会破坏现有代码，因此在 `requirements.txt` 中精确锁定。修改 `requirements.txt` 后，请重新生成锁文件并检查差异：
   ```bash
   uv pip compile requirements.txt -o requirements.lock --python-version 3.11 \
     --python-platform x86_64-unknown-linux-gnu --prerelease=allow --no-header
   ```
2. 将 `.env.example` 复制为 `.env`，填写凭据（Azure/OpenAI 密钥、`SORA_WORKFLOW_ALLOWED_ORIGINS`、`VIDEO_PROVIDER`，如用 Kling 再补充 `KLING_API_BASE_URL`、`KLING_ACCESS_KEY`、`KLING_SECRET_KEY` 等）。
3. 启动 FastAPI 服务：
   ```bash
   uvicorn sora_agent_workflow:fastapi_app --host 0.0.0.0 --port 8000
   ```
4. 如需一条命令同时启动后端和前端，在仓库根目录运行：
   ```bash
   ./start-dev.sh
   # 或 npm run dev:all
   ```
   若未配置任何鉴权变量，脚本会仅在本地开发场景下自动设置 `SORA_WORKFLOW_REQUIRE_AUTH=0`。
5. 生成的文件位于 `output/<job_id>/`，并通过 `/static/...` 提供访问。

### 切换 Sora2、Kling、Jimeng

设置 `VIDEO_PROVIDER=sora2`（默认）、`VIDEO_PROVIDER=kling` 或 `VIDEO_PROVIDER=jimeng` 选择后端。使用 Kling 时需配置 `KLING_API_BASE_URL`、`KLING_ACCESS_KEY`、`KLING_SECRET_KEY`（可选 `KLING_API_KEY` 仅用于旧环境），其余参数如 `KLING_MODEL_NAME`、`KLING_MODE`、`KLING_CFG_SCALE`、`KLING_POLL_INTERVAL`、`KLING_POLL_TIMEOUT`、`KLING_TOKEN_TTL` 仍可调，`python verify_kling_token.py` 可用于检查令牌有效期。使用 Jimeng 时需提供 `JIMENG_ACCESS_KEY_ID`、`JIMENG_SECRET_ACCESS_KEY`（如有 STS 令牌则填 `JIMENG_SECURITY_TOKEN`），默认调用 `https://visual.volcengineapi.com`，Region/Service 固定为 `cn-north-1` / `cv`，支持 5s/10s 时长，可选首帧参考图。Kling/Jimeng/Seedance 目前仅支持重新生成，不支持 remix。

使用 Seedance 2.5（火山方舟，模型 `doubao-seedance-2-5-260628`）时需设置 `ARK_API_KEY`，支持 4/5/8/10/12 秒，默认生成音频（`SEEDANCE_GENERATE_AUDIO=false` 可关闭）。方舟仅接受 URL 形式的参考图，因此首帧会先上传到公开的 Cloudflare R2 存储桶，需配置 `R2_ACCOUNT_ID`、`R2_BUCKET`、`R2_PUBLIC_BASE_URL`、`R2_ACCESS_KEY_ID`、`R2_SECRET_ACCESS_KEY`（上传的帧会公开可读）。Seedance 按生成秒数计费，创建任务不会自动重试，测试时请使用 4 秒场景。

文本与视觉推理统一使用 `AZURE_OPENAI_REASONING_DEPLOYMENT`（默认 `gpt-6.1-sol`，`reasoning.effort=medium`）；若部署在其他资源上，可设置 `AZURE_OPENAI_REASONING_ENDPOINT`/`AZURE_OPENAI_REASONING_API_KEY`。剧本缺少 `**Scene N (Title)**` 场景标题时，推理模型会自动补充后继续执行。图像生成使用 Foundry `gpt-image-2.5-sunburst` 部署（`FOUNDRY_GPT_IMAGE_ENDPOINT`、`FOUNDRY_GPT_IMAGE_API_KEY`）。

### API 路由

- `POST /api/workflows` — 使用分镜脚本创建新的视频任务
- `GET /api/workflows` — 列出近期任务及进度快照
- `GET /api/workflows/{job_id}` — 轮询任务状态，包含消息、场景元数据、最终 MP4/JSON 清单链接
- `POST /api/workflows/{job_id}/scenes/{scene_order}/remix` — 基于上一版 Azure 视频 ID，提交新提示词对已完成的场景进行 remix
- `POST /api/workflows/{job_id}/scenes/{scene_order}/select` — 选择某个场景版本用于最终剪辑
- `POST /api/workflows/{job_id}/recompose` — 按当前选择的版本重新生成最终视频
- `GET /api/providers/active` — 返回默认配置的后端
- `POST /api/reproduce` — 上传参考视频并启动 Kling reproduce 工作流
- `GET /api/reproduce` — 列出 reproduce 任务及其分段、提示与输出
- `GET /api/reproduce/{job_id}` — 查看指定 reproduce 任务的分段、风格化帧与 Kling 版本
- `POST /api/reproduce/{job_id}/segments/{segment_order}/regenerate` — 为某个分段提供自定义提示并重新渲染
- `POST /api/reproduce/{job_id}/rerun` — 在失败后复用本地缓存（GPT-6.1-sol、gpt-image-2.5、Kling）的产物继续执行
- `DELETE /api/reproduce/{job_id}` — 删除 reproduce 任务及其工作区文件
- `GET /static/...` — 提供生成资产（最终视频、片段、日志）的静态访问

## 前端安装与使用

1. 安装依赖：
   ```bash
   cd frontend
   npm install
   ```
2. 创建 `frontend/.env.local` 指向后端地址（按需调整主机/端口）：
   ```bash
   NEXT_PUBLIC_API_BASE_URL=http://127.0.0.1:8000
   ```
3. 启动开发服务器：
   ```bash
   npm run dev
   ```
   打开 http://localhost:3000/ 访问仪表盘，应用会自动轮询 API 获取新任务与进行中的任务。
4. 生产环境构建：
   ```bash
   npm run build
   npm run start
   ```

## 典型流程

1. 准备分镜文件（如 `Chapters/video_script.txt`），采用 `**Scene N (Title)**` 标题格式。
2. 可选：在 `scene_start_screen/scene#.png` 放置起始帧，在 `images_references/` 放置参考图。
3. 启动 FastAPI 服务与前端开发服务器。
4. 在仪表盘粘贴分镜，选择 `Parallel` 或 `Sequence` 模式（顺序模式会用上一场景的末帧串联），然后发起任务。
5. 在界面监控进度：规划、渲染、合成阶段实时更新；可对单独场景进行 remix、选择版本，并直接重新生成最终 MP4。

## 测试

在仓库根目录运行：
```bash
pytest -q
```

## 部署（Azure Container Apps）

1. 在 `.env` 中填入 Azure 订阅、区域、镜像仓库与 Container App 名称（示例配置见 `.env.example`）。
2. 部署前后端：
   ```bash
   ./deploy.sh
   ```
3. 更新镜像时：
   ```bash
   ./update.sh
   ```
   可使用 `--component backend` 或 `--component frontend` 限制更新范围，或用 `--image-tag <tag>` 覆盖默认标签（当前 Git SHA/时间戳）。

## 常见问题

- 分镜标题未按 `**Scene 1 (Title)**` 格式书写时，容易导致缺失片段或提示词。
- UI 无法访问后端时，检查 `NEXT_PUBLIC_API_BASE_URL` 与 CORS（`SORA_WORKFLOW_ALLOWED_ORIGINS`）。
- 使用顺序模式时需确保存在对应数量的 `scene_start_screen/scene#.png`，数量不匹配会导致失败。
- 审核拦截的场景会按 `SORA_BLOCK_RETRIES` 进行重试；也可重新生成某个场景或使用 **Compose Available Scenes** 拼接已完成片段。
- Kling 报错包含 `/v1/videos/...` 通常代表缺少密钥、令牌有效期或基地址。请用 `python verify_kling_token.py` 验证。Kling 目前不支持 remix，请使用 regenerate，并按需提供 `_tail` 图引导最终帧。
- Jimeng 返回 code≠10000 或缺少视频链接时，优先检查 Access Key/Secret、签名参数以及时长（仅支持 5/10 秒）；Jimeng 也不支持 remix，只能重新生成。
