# Sora Agent Workflow

Full-stack toolkit for running the Azure Sora2/Kling/Jimeng/Seedance multi-agent video workflow with a FastAPI backend and a Next.js dashboard. The backend orchestrates Director → Sora → Composer agents to generate per-scene clips, monitor progress, and stitch the final video. The frontend lets users paste storyboard scripts, launch jobs, and watch live status updates, scene previews, and the completed compilation.

## Prerequisites

- Python 3.10+
- Node.js 18.17+ (or 20+) and npm
- `ffmpeg`/`ffprobe` available on your system `PATH`
- Azure Sora/OpenAI credentials (and optional Kling or Jimeng credentials) expected by `backend/sora-openai-client.py`
- Optional environment variables:
  - `SORA_WORKFLOW_ALLOWED_ORIGINS` — comma-separated list of origins permitted by CORS
  - `SORA_WORKFLOW_THREADS` — concurrent render jobs (default: `3`)
  - `SORA_BLOCK_RETRIES` — retries for moderated scenes (default: `3`)
  - `VIDEO_DOWNLOAD_VARIANT` — override the variant name sent to the Sora client

### Switching video backends (Sora2, Kling, Jimeng, or Seedance)

Set `VIDEO_PROVIDER` to `sora2` (default), `kling`, `jimeng`, or `seedance` to decide which API generates clips. When targeting Kling you must configure `KLING_API_BASE_URL`, `KLING_ACCESS_KEY`, and `KLING_SECRET_KEY` so the workflow can mint short-lived JWTs. Optional knobs such as `KLING_MODEL_NAME`, `KLING_MODE`, `KLING_CFG_SCALE`, `KLING_POLL_INTERVAL`, `KLING_POLL_TIMEOUT`, and `KLING_TOKEN_TTL` still apply, and a legacy `KLING_API_KEY` fallback remains available for staging environments. Run `python verify_kling_token.py` to confirm your keys produce a valid token and inspect its expiration. For Jimeng, provide `JIMENG_ACCESS_KEY_ID` and `JIMENG_SECRET_ACCESS_KEY` (STS token optional via `JIMENG_SECURITY_TOKEN`); the client signs requests against `JIMENG_API_BASE_URL` (defaults to `https://visual.volcengineapi.com`) using the fixed `JIMENG_REGION=cn-north-1` and `JIMENG_SERVICE=cv` unless overridden. Jimeng supports 5s/10s clips and accepts an optional reference image as the first frame. The workflow reuses the same Director/Composer agents, and the dashboard surfaces a “Video backend” tag so operators can immediately see which engine is active. Remix endpoints remain Sora-only; Kling, Jimeng, and Seedance jobs support regeneration but not Azure remix APIs.

Kling 3.0 (`KLING_MODEL_NAME=kling-v3`) generates speech, sound effects, and ambience with each clip by default; set `KLING_SOUND=off` for silent clips. Jimeng 3.0 Pro has no audio option, so Jimeng clips are always silent.

For Seedance 2.5 (Volcengine Ark, model `doubao-seedance-2-5-260628`), set `ARK_API_KEY`. Clips can be 4, 5, 8, 10, or 12 seconds and include generated audio unless `SEEDANCE_GENERATE_AUDIO=false`. Ark only accepts reference images by URL, so start frames are uploaded to a public Cloudflare R2 bucket first: configure `R2_ACCOUNT_ID`, `R2_BUCKET`, `R2_PUBLIC_BASE_URL`, `R2_ACCESS_KEY_ID`, and `R2_SECRET_ACCESS_KEY` (uploaded frames become publicly readable). Seedance is billed per generated second and task creation is never retried automatically; use 4-second scenes while testing.

### Azure sign-in for Sora

Sora rendering and remix sign in with Microsoft Entra ID by default (`AZURE_OPENAI_AUTH=entra`) through `DefaultAzureCredential`: your `az login` locally, or a managed identity in Azure. That identity needs the "Cognitive Services OpenAI User" role (or equivalent) on the Sora resource. Set `AZURE_OPENAI_AUTH=key` to send `AZURE_OPENAI_API_KEY` instead; that fails with `AuthenticationTypeDisabled` when the resource has key-based authentication disabled.

### Persistent cloud storage

Locally, jobs and media live in `output/`. In Azure, `update.sh` and `deploy.sh` set `OUTPUT_STORAGE=blob`: the backend mirrors `output/` to a private blob container (storage account `<registry>out`, created with shared-key and public access disabled) through its managed identity, which gets "Storage Blob Data Contributor". On startup it restores the job database before loading jobs, then fetches videos and images on first use, so redeploys no longer erase history. The backend runs as a single replica, because jobs are held in memory. `update.sh` refuses to deploy while cloud jobs are running; add `--force` to deploy anyway.

### Models

All text and vision reasoning (storyboard refinement, scene splitting, first-frame prompts, character extraction, segment descriptions) uses `AZURE_OPENAI_REASONING_DEPLOYMENT` (default `gpt-6.1-sol`) with `reasoning.effort=medium`; set `AZURE_OPENAI_REASONING_ENDPOINT`/`AZURE_OPENAI_REASONING_API_KEY` if it lives on a different resource than Sora. When a workflow script has no `**Scene N (Title)**` headers, the reasoning model inserts them automatically and the pipeline continues. Images come from the Foundry `gpt-image-2.5-sunburst` deployment (`FOUNDRY_GPT_IMAGE_ENDPOINT`, `FOUNDRY_GPT_IMAGE_API_KEY`).

## Backend (FastAPI) Setup

```bash
cd /home/minggu/projects_code/sora2-workflow-agent
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.lock
```

`requirements.lock` pins every transitive dependency (the Docker image installs from it too). `agent-framework` is pre-1.0 and newer releases break this code, so its packages are pinned exactly in `requirements.txt`. After editing `requirements.txt`, regenerate the lock and rebuild:

```bash
uv pip compile requirements.txt -o requirements.lock --python-version 3.11 \
  --python-platform x86_64-unknown-linux-gnu --prerelease=allow --no-header
```

Review the diff before committing: an unpinned dependency can move to a breaking release.

Run the workflow API:

```bash
uvicorn video_agent_workflow:fastapi_app --host 0.0.0.0 --port 8000
```

Or start backend + frontend together from the repo root:

```bash
./start-dev.sh
# or: npm run dev:all
```
If no auth variables are configured, this script sets `SORA_WORKFLOW_REQUIRE_AUTH=0` for local development only.

### Available API routes

- `POST /api/workflows` — start a new video job with a storyboard script
- `GET /api/workflows` — list recent jobs with progress snapshots
- `GET /api/workflows/{job_id}` — poll job status, including messages, scene metadata, and final MP4/JSON manifest links
- `POST /api/workflows/{job_id}/scenes/{scene_order}/remix` — remix a completed scene by supplying a new prompt against the previous Azure video ID
- `POST /api/workflows/{job_id}/scenes/{scene_order}/select` — choose which scene version should be used when composing the final cut
- `POST /api/workflows/{job_id}/recompose` — regenerate the final video using the currently selected scene versions
- `GET /api/providers/active` — report the default backend configured for new workloads
- `POST /api/video-insights` — upload a video for Azure Content Understanding analysis
- `GET /api/video-insights` — list recent insight jobs with progress snapshots
- `GET /api/video-insights/{job_id}` — poll the key-frame/segment results and download links for a specific job
- `DELETE /api/video-insights/{job_id}` — remove a completed/failed insight job and its workspace artifacts
- `POST /api/reproduce` — upload a reference video and start the Kling reproduce workflow
- `GET /api/reproduce` — list reproduce jobs with cached segments, prompts, and output links
- `GET /api/reproduce/{job_id}` — inspect a specific reproduce job (segments, stylized frames, Kling versions, and merged output)
- `POST /api/reproduce/{job_id}/segments/{segment_order}/regenerate` — supply a custom prompt to rerender a single segment
- `POST /api/reproduce/{job_id}/rerun` — resume a failed reproduce job without re-calling GPT-6.1-sol, gpt-image-2.5, or Kling for cached artifacts
- `DELETE /api/reproduce/{job_id}` — remove a reproduce job and its workspace assets
- `GET /static/...` — static hosting of generated assets (final video, scene clips, logs)

## Frontend setup and usage

1. Install dependencies:
   ```bash
   cd frontend
   npm install
   ```
2. Create `frontend/.env.local` with your backend URL (adjust host/port as needed):
   ```bash
   NEXT_PUBLIC_API_BASE_URL=http://127.0.0.1:8000
   ```
3. Start the dev server:
   ```bash
   npm run dev
   ```
   Open http://localhost:3000/ to access the dashboard. The app automatically polls the API for new and in-progress jobs.
4. For production builds:
   ```bash
   npm run build
   npm run start
   ```

## Typical workflow

1. Prepare a storyboard file (e.g., `Chapters/video_script.txt`) using `**Scene N (Title)**` headings.
2. Optionally add start-frame images under `scene_start_screen/scene#.png` and reference images under `images_references/`.
3. Start the FastAPI service and the frontend dev server.
4. Paste your storyboard into the dashboard, choose `Parallel` or `Sequence` mode (sequence chains scenes via last-frame references), and launch the job.
5. Monitor progress in the UI: plan, render, and compose phases update in real time. Remix individual scenes, pick preferred versions, and regenerate the final MP4 directly from the dashboard.

## Video insight analysis

The dashboard now includes a **Video insight** tab beside the storyboard and single-clip consoles. Upload an MP4/MOV/WEBM file to generate an Azure Content Understanding job, then review the extracted key-frame timestamps and segmented summaries directly inside the UI. Each job stores the raw JSON manifest under `/static` and exposes download links for both the analyzed clip and the insight payload.

Configure the following environment variables (see `.env.example` for placeholders) before enabling this feature:

- `AZURE_SPEECH_ENDPOINT` / `AZURE_SPEECH_KEY` — optional, reserved for future speech-to-text expansion.
- `AZURE_CONTENT_UNDERSTANDING_ENDPOINT` — the region-specific Cognitive Service endpoint.
- `AZURE_CONTENT_UNDERSTANDING_API_KEY` — API key for the content understanding resource.
- `AZURE_CONTENT_UNDERSTANDING_API_VERSION` — default `2025-05-01-preview`.
- `AZURE_CONTENT_UNDERSTANDING_ANALYZER_ID` — defaults to `prebuilt-videoAnalyzer`.
- `CONTENT_UNDERSTANDING_MAX_UPLOAD_MB`, `CONTENT_UNDERSTANDING_POLL_INTERVAL`, `CONTENT_UNDERSTANDING_POLL_TIMEOUT` — optional overrides for upload size and polling cadence.

Once configured, the backend exposes `/api/video-insights` CRUD endpoints and asynchronously polls Azure until analysis completes. The frontend automatically refreshes active jobs, surfaces delete controls, and renders responsive tables for both key frames and video segments.

## Testing

Run the backend tests from the repo root:
```bash
pytest -q
```

## Deployment (Azure Container Apps)

1. Populate `.env` with your Azure subscription ID, target region, registry, and Container App names. Sample values are included in `.env.example`.
2. Deploy both frontend and backend:
   ```bash
   ./deploy.sh
   ```
3. Roll out new images when updating:
   ```bash
   ./update.sh
   ```
   Use `--component backend` or `--component frontend` to limit the update, or `--image-tag <tag>` to override the default tag (current Git SHA fallback timestamp).

## Troubleshooting

- Misformatted storyboard headers (not matching `**Scene 1 (Title)**`) often cause missing clips or prompts.
- If the UI cannot reach the backend, confirm `NEXT_PUBLIC_API_BASE_URL` and CORS (`SORA_WORKFLOW_ALLOWED_ORIGINS`).
- Ensure `scene_start_screen/scene#.png` assets exist when using sequence mode; mismatched counts will fail the workflow.
- Moderation blocks are retried up to `SORA_BLOCK_RETRIES`; regenerate a scene or use **Compose Available Scenes** to stitch completed clips.
- Kling errors that reference `/v1/videos/...` usually mean missing keys, token lifetime, or base URL. Verify credentials with `python verify_kling_token.py`. Remix actions stay disabled for Kling—use regenerate instead and provide optional `_tail` images for final-frame guidance.
