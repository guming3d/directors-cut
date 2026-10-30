# Getting started

This guide runs Director's Cut on your machine: the FastAPI backend on port 8000 and the Next.js dashboard on port 3000.

## Prerequisites

- Python 3.10+
- Node.js 18.17+ (or 20+) and npm
- `ffmpeg`/`ffprobe` available on your system `PATH`
- Credentials for at least one video backend (Azure OpenAI Sora 2 by default; see [Configuration](configuration.md) for Kling, Jimeng, and Seedance)

## 1. Configure

```bash
cp .env.example .env
```

Fill in your Azure OpenAI endpoint and deployments, then the keys for any other backend you plan to use. [Configuration](configuration.md) explains every group of variables.

## 2. Backend

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.lock
uvicorn video_agent_workflow:fastapi_app --host 0.0.0.0 --port 8000
```

`requirements.lock` pins every transitive dependency (the Docker image installs from it too). `agent-framework` is pre-1.0 and newer releases break this code, so its packages are pinned exactly in `requirements.txt`. After editing `requirements.txt`, regenerate the lock and rebuild:

```bash
uv pip compile requirements.txt -o requirements.lock --python-version 3.11 \
  --python-platform x86_64-unknown-linux-gnu --prerelease=allow --no-header
```

Review the diff before committing: an unpinned dependency can move to a breaking release.

API docs are served at http://localhost:8000/docs. The route list is in [API reference](api.md).

## 3. Frontend

```bash
cd frontend
npm install
echo "NEXT_PUBLIC_API_BASE_URL=http://127.0.0.1:8000" > .env.local
npm run dev
```

Open http://localhost:3000/. The dashboard polls the API for new and in-progress jobs. For a production build, run `npm run build` and then `npm run start`.

### Both at once

```bash
./start-dev.sh
# or: npm run dev:all
```

If no auth variables are configured, this script sets `SORA_WORKFLOW_REQUIRE_AUTH=0` for local development only.

## Typical workflow

1. Write a storyboard with `**Scene N (Title)**` headings (samples live in `Chapters/`). Without headings, the reasoning model inserts them for you.
2. Optionally add start-frame images under `scene_start_screen/scene#.png` and reference images under `images_references/`.
3. In the **Storyboard workspace** tab, click **Start Workflow**, paste the storyboard, and choose the backend, resolution, visual style, scene duration, and `Parallel` or `Sequence` mode (sequence chains scenes through last-frame references).
4. Watch the plan, render, and compose stages update live. Regenerate or remix individual scenes, pick the version you prefer, and regenerate the final MP4.

Generated files land in `output/<job_id>/` and are served under `/static/...`.

## Command line

The same workflow runs without the dashboard:

```bash
python video_agent_workflow.py --threads 3 --scene-duration 8 --resolution 720x1280
```

## Project layout

| Path | Contents |
|---|---|
| `video_agent_workflow.py` | CLI entry point and the FastAPI app (`fastapi_app`) |
| `backend/` | Agents, API routes, provider clients (Sora, Kling, Jimeng, Seedance), job stores, prompts, storage |
| `frontend/` | Next.js dashboard (App Router, TypeScript) |
| `prompts/` | Prompt templates, editable from the dashboard |
| `Chapters/` | Sample storyboard scripts |
| `images_references/` | Optional reference images |
| `tests/` | Backend tests (`pytest`) |
| `deploy.sh`, `update.sh`, `deploy_lib.sh` | Azure Container Apps deployment ([Deployment](deployment.md)) |
| `output/` | Generated clips, logs, manifests, and TinyDB job state (not committed) |

## Testing

```bash
pytest -q
```

## Troubleshooting

- Misformatted storyboard headers (not matching `**Scene 1 (Title)**`) often cause missing clips or prompts.
- If the UI cannot reach the backend, confirm `NEXT_PUBLIC_API_BASE_URL` and CORS (`SORA_WORKFLOW_ALLOWED_ORIGINS`).
- Ensure `scene_start_screen/scene#.png` assets exist when using sequence mode; mismatched counts will fail the workflow.
- Moderation blocks are retried up to `SORA_BLOCK_RETRIES`; regenerate a scene or use **Compose Available Scenes** to stitch completed clips.
- Kling errors that reference `/v1/videos/...` usually mean missing keys, token lifetime, or base URL; `pytest tests/test_kling_auth.py` checks token generation. Remix actions stay disabled for Kling—use regenerate instead and provide optional `_tail` images for final-frame guidance.
