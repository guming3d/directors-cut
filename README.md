# Sora Agent Workflow

Full-stack toolkit for running the Azure Sora2/Kling multi-agent video workflow with a FastAPI backend and a Next.js dashboard. The backend orchestrates Director → Sora → Composer agents to generate per-scene clips, monitor progress, and stitch the final video. The frontend lets users paste storyboard scripts, launch jobs, and watch live status updates, scene previews, and the completed compilation.

## Prerequisites

- Python 3.10+
- Node.js 18.17+ (or 20+) and npm
- `ffmpeg`/`ffprobe` available on your system `PATH`
- Azure Sora/OpenAI credentials (and optional Kling credentials) expected by `sora-openai-client.py`
- Optional environment variables:
  - `SORA_WORKFLOW_ALLOWED_ORIGINS` — comma-separated list of origins permitted by CORS
  - `SORA_WORKFLOW_THREADS` — concurrent render jobs (default: `3`)
  - `SORA_BLOCK_RETRIES` — retries for moderated scenes (default: `3`)
  - `VIDEO_DOWNLOAD_VARIANT` — override the variant name sent to the Sora client
  - `VIDEO_PROVIDER` — `sora2` (default) or `kling` to choose the backend

## Project layout

- `sora_agent_workflow.py`: CLI entrypoint and FastAPI application
- `sora-openai-client.py`: direct Sora client for single renders
- `sora_workflow/`: core agents, services, prompts, and configuration
- `frontend/`: Next.js dashboard (app router, TypeScript)
- `Chapters/`: storyboard script samples
- `scene_start_screen/` and `images_references/`: optional start and reference images for scenes
- `output/`: generated clips, logs, manifests, and TinyDB state (not committed)

## Backend setup and usage

1. Create a virtual environment and install dependencies:
   ```bash
   python -m venv .venv
   source .venv/bin/activate
   pip install -r requirements.txt
   ```
2. Copy `.env.example` to `.env` and supply your credentials (Azure/OpenAI keys, `SORA_WORKFLOW_ALLOWED_ORIGINS`, `VIDEO_PROVIDER`, and optionally Kling values such as `KLING_API_BASE_URL`, `KLING_ACCESS_KEY`, and `KLING_SECRET_KEY`).
3. Start the FastAPI service:
   ```bash
   uvicorn sora_agent_workflow:fastapi_app --host 0.0.0.0 --port 8000
   ```
4. Generated artifacts live under `output/<job_id>/` and are served via `/static/...`.

### Switching between Sora2 and Kling

Set `VIDEO_PROVIDER=sora2` (default) or `VIDEO_PROVIDER=kling` to pick the backend. For Kling, configure `KLING_API_BASE_URL`, `KLING_ACCESS_KEY`, and `KLING_SECRET_KEY`; optional knobs (`KLING_MODEL_NAME`, `KLING_MODE`, `KLING_CFG_SCALE`, `KLING_POLL_INTERVAL`, `KLING_POLL_TIMEOUT`, `KLING_TOKEN_TTL`, legacy `KLING_API_KEY`) remain available. Run `python verify_kling_token.py` to confirm your token setup and expiration. Kling image-to-video runs can include optional `scene1_tail.png`, `scene2_tail.png`, etc., in `scene_start_screen/` to guide the final frame.

### Available API routes

- `POST /api/workflows` — start a new video job with a storyboard script
- `GET /api/workflows` — list recent jobs with progress snapshots
- `GET /api/workflows/{job_id}` — poll job status, including messages, scene metadata, and final MP4/JSON manifest links
- `POST /api/workflows/{job_id}/scenes/{scene_order}/remix` — remix a completed scene by supplying a new prompt against the previous Azure video ID
- `POST /api/workflows/{job_id}/scenes/{scene_order}/select` — choose which scene version should be used when composing the final cut
- `POST /api/workflows/{job_id}/recompose` — regenerate the final video using the currently selected scene versions
- `GET /api/providers/active` — report the default backend (`sora2` or `kling`) configured for new workloads
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
