# Sora Agent Workflow

Full-stack toolkit for running the Azure Sora2 multi-agent video workflow with a FastAPI backend and a Next.js dashboard. The backend orchestrates the Director → Sora → Composer agents to generate per-scene clips, monitor progress, and stitch the final video. The frontend lets users paste storyboard scripts, launch jobs, and watch live status updates, scene previews, and the completed compilation.

## Prerequisites

- Python 3.10+
- Node.js 18.17+ (or 20+) and npm
- `ffmpeg`/`ffprobe` available on your system `PATH`
- Access to the Azure Sora/OpenAI credentials expected by `sora-openai-client.py`
- Optional environment variables:
  - `SORA_WORKFLOW_ALLOWED_ORIGINS` — comma-separated list of origins permitted by CORS
  - `SORA_WORKFLOW_THREADS` — default concurrent Sora render jobs (fallback: `3`)
  - `VIDEO_DOWNLOAD_VARIANT` — override the variant name sent to the Sora client

## Backend (FastAPI) Setup

```bash
cd /home/minggu/projects_code/sora2-workflow-agent
python -m venv .venv
source .venv/bin/activate
pip install -U agent_framework openai python-dotenv pillow fastapi uvicorn[standard]
```

Run the workflow API:

```bash
uvicorn sora_agent_workflow:fastapi_app --host 0.0.0.0 --port 8000
```

Key routes:

- `POST /api/workflows` — start a new video job with a storyboard script
- `GET /api/workflows` — list recent jobs with progress snapshots
- `GET /api/workflows/{job_id}` — poll job status, including messages, scene metadata, and final MP4/JSON manifest links
- `POST /api/workflows/{job_id}/scenes/{scene_order}/remix` — remix a completed scene by supplying a new prompt against the previous Azure video ID
- `POST /api/workflows/{job_id}/scenes/{scene_order}/select` — choose which scene version should be used when composing the final cut
- `POST /api/workflows/{job_id}/recompose` — regenerate the final video using the currently selected scene versions
- `GET /static/...` — static hosting of generated assets (final video, scene clips, logs)

Generated artifacts are stored under `output/<job_id>/`.

## Frontend (Next.js) Setup

```bash
cd /home/minggu/projects_code/sora2-workflow-agent/frontend
```

Create a `.env.local` with the backend URL (adjust host/port as needed):

```bash
NEXT_PUBLIC_API_BASE_URL=http://127.0.0.1:8000
```

Install dependencies (if not already installed) and launch the dev server:

```bash
npm install
npm run dev
```

Then open http://localhost:3000/ to access the dashboard. The app will automatically poll the configured API for new and in-progress jobs.

For production builds:

```bash
npm run build
npm run start
```

## Typical Workflow

1. Start the FastAPI service (`uvicorn ...` command above).
2. Ensure your Azure/OpenAI credentials and any required environment variables for `sora-openai-client.py` are set.
3. Run the Next.js UI (`npm run dev`) and paste a storyboard script using `**Scene N (Title)**` style headings.
4. Choose `Parallel` or `Sequence` mode before launching: sequence mode seeds the first scene with a single image and chains the remaining scenes via last-frame references for tighter continuity.
5. Monitor progress in the dashboard: planning, rendering, and composing phases update in real time; remix individual scenes, pick the preferred version, and regenerate the final MP4 directly from the UI.

## Troubleshooting

- Missing clips or prompts usually indicate misformatted script headers; verify they follow `**Scene 1 (Title)**` syntax.
- If the UI cannot reach the backend, confirm `NEXT_PUBLIC_API_BASE_URL` points to the FastAPI origin and CORS allows the frontend host.
- Scene rendering requires the prepared `scene_start_screen/scene#.png` assets; the workflow will fail if counts mismatch.
