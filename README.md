# Sora Agent Workflow

Full-stack toolkit for running the Azure Sora2/Kling multi-agent video workflow with a FastAPI backend and a Next.js dashboard. The backend orchestrates Director → Sora → Composer agents to generate per-scene clips, monitor progress, and stitch the final video. The frontend lets users paste storyboard scripts, launch jobs, and watch live status updates, scene previews, and the completed compilation.

## Prerequisites

- Python 3.10+
- Node.js 18.17+ (or 20+) and npm
- `ffmpeg`/`ffprobe` available on your system `PATH`
- Azure Sora/OpenAI credentials (and optional Kling credentials) expected by `backend/sora-openai-client.py`
- Optional environment variables:
  - `SORA_WORKFLOW_ALLOWED_ORIGINS` — comma-separated list of origins permitted by CORS
  - `SORA_WORKFLOW_THREADS` — concurrent render jobs (default: `3`)
  - `SORA_BLOCK_RETRIES` — retries for moderated scenes (default: `3`)
  - `VIDEO_DOWNLOAD_VARIANT` — override the variant name sent to the Sora client

### Switching video backends (Sora2 vs. Kling)

Set `VIDEO_PROVIDER` to `sora2` (default) or `kling` to decide which API generates clips. When targeting Kling you must configure `KLING_API_BASE_URL`, `KLING_ACCESS_KEY`, and `KLING_SECRET_KEY` so the workflow can mint short-lived JWTs. Optional knobs such as `KLING_MODEL_NAME`, `KLING_MODE`, `KLING_CFG_SCALE`, `KLING_POLL_INTERVAL`, `KLING_POLL_TIMEOUT`, and `KLING_TOKEN_TTL` still apply, and a legacy `KLING_API_KEY` fallback remains available for staging environments. Run `python verify_kling_token.py` to confirm your keys produce a valid token and inspect its expiration. The workflow reuses the same Director/Composer agents, and the dashboard now surfaces a “Video backend” tag so operators can immediately see which engine is active. For Kling image-to-video runs you can optionally drop files named `scene1_tail.png`, `scene2_tail.png`, etc., into your `scene_start_screen/` directory to provide last-frame guidance alongside the usual `scene#.png` head images. Remix endpoints remain Sora-only; Kling jobs support regeneration but not Azure remix APIs.

## Backend (FastAPI) Setup

```bash
cd /home/minggu/projects_code/sora2-workflow-agent
python -m venv .venv
source .venv/bin/activate
pip install -U agent_framework openai python-dotenv pillow fastapi uvicorn[standard]
```

Run the workflow API:

```bash
uvicorn video_agent_workflow:fastapi_app --host 0.0.0.0 --port 8000
```

### Available API routes

- `POST /api/workflows` — start a new video job with a storyboard script
- `GET /api/workflows` — list recent jobs with progress snapshots
- `GET /api/workflows/{job_id}` — poll job status, including messages, scene metadata, and final MP4/JSON manifest links
- `POST /api/workflows/{job_id}/scenes/{scene_order}/remix` — remix a completed scene by supplying a new prompt against the previous Azure video ID
- `POST /api/workflows/{job_id}/scenes/{scene_order}/select` — choose which scene version should be used when composing the final cut
- `POST /api/workflows/{job_id}/recompose` — regenerate the final video using the currently selected scene versions
- `GET /api/providers/active` — report the default backend (`sora2` or `kling`) configured for new workloads
- `POST /api/video-insights` — upload a video for Azure Content Understanding analysis
- `GET /api/video-insights` — list recent insight jobs with progress snapshots
- `GET /api/video-insights/{job_id}` — poll the key-frame/segment results and download links for a specific job
- `DELETE /api/video-insights/{job_id}` — remove a completed/failed insight job and its workspace artifacts
- `POST /api/reproduce` — upload a reference video and start the Kling reproduce workflow
- `GET /api/reproduce` — list reproduce jobs with cached segments, prompts, and output links
- `GET /api/reproduce/{job_id}` — inspect a specific reproduce job (segments, stylized frames, Kling versions, and merged output)
- `POST /api/reproduce/{job_id}/segments/{segment_order}/regenerate` — supply a custom prompt to rerender a single segment
- `POST /api/reproduce/{job_id}/rerun` — resume a failed reproduce job without re-calling GPT-5, gpt-image-1, or Kling for cached artifacts
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
