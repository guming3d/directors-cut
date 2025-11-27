# Repository Guidelines

## Project Structure & Module Organization
- Backend entrypoints live at the repo root: `sora_agent_workflow.py` exposes the CLI plus the FastAPI `fastapi_app`, and `sora-openai-client.py` handles single Sora renders (resizing references, downloading MP4/JSON logs). Core orchestration sits in `sora_workflow/` (agents, API wiring, services, prompts, images, stores, models); defaults and paths are centralized in `sora_workflow/config.py`.
- The dashboard is a Next.js (app router) app under `frontend/` using TypeScript/React 19; `frontend/Dockerfile` builds it with `NEXT_PUBLIC_API_BASE_URL`.
- Storyboard inputs live in `Chapters/`, start-screen assets in `scene_start_screen/scene#.png`, and optional references in `images_references/`. Generated artifacts and TinyDB state land in `output/` (`output/db` holds workflow and single-video job records).
- Prompt refinement is optional and routed through `sora_workflow/prompts.py`; `refine_with_gpt5` flags use the Azure Responses API via `AZURE_OPENAI_GPT5_DEPLOYMENT`/`DIRECTOR_REFINEMENT_DEPLOYMENT`/`AZURE_OPENAI_API_VERSION` env vars when present.
- Deployment tooling: `Dockerfile.backend` builds the FastAPI image; `deploy.sh` provisions Azure Container Apps and pushes backend/frontend images; `update.sh` refreshes images/tags using the existing environment. Sample values stay in `.env.example`.
- Tests are under `tests/` (pytest + pytest-asyncio + Pillow) covering prompt parsing, image prep, and stubbed workflow runs.

## Build, Test, and Development Commands
- Backend setup: `python -m venv .venv && source .venv/bin/activate && pip install -r requirements.txt`.
- Run the API: `uvicorn sora_agent_workflow:fastapi_app --host 0.0.0.0 --port 8000` (requires `ffmpeg`/`ffprobe` on PATH).
- CLI workflow: `python sora_agent_workflow.py --threads 3 --scene-duration 8 --resolution 720x1280 --final-video output/sora_story_compilation.mp4 [--devui]` (resolution is locked to `DEFAULT_SORA_RESOLUTION`).
- Direct Sora client: `python sora-openai-client.py --prompt-file Chapters/video_script.txt --input-image images_references/screenshot-002.png --resolution 720x1280 --duration 8` (writes MP4/JSON under `output/` unless `VIDEO_OUTPUT_*` overrides).
- Frontend: `cd frontend && npm install && npm run dev` for local work; `npm run build && npm run start` for production; `npm run lint` to check Next/ESLint rules.
- Tests: `pytest -q` from the repo root; async workflow tests monkeypatch Sora calls—follow that pattern for new networked code.

## Coding Style & Naming Conventions
- Target Python 3.10+ with PEP 8/4-space indents and type hints on public functions. Keep constants upper-snake and centralized in `sora_workflow/config.py`; reuse helpers like `normalize_script_text`, `prepare_scene_start_images`, and `resolution_to_size` instead of hard-coded paths or sizes.
- CLI flags stay kebab-case and default to portrait `720x1280` with 4/8/12 second durations; preserve prompt/scene filename patterns (`scene_##_*.txt`, `scene#.png`).
- Frontend uses Next.js app router with TypeScript; favor functional components and hooks, keep translation maps and option lists aligned with existing shapes, and avoid hard-coding API URLs (use `NEXT_PUBLIC_API_BASE_URL`).

## Testing Guidelines
- Update or add pytest cases when changing script parsing (`tests/test_prompts.py`), image prep (`tests/test_images.py`), or workflow orchestration (`tests/test_workflow_integration.py`). Use temporary directories and monkeypatch `_invoke_sora`/`run_subprocess` or Azure clients to avoid real Sora calls.
- For new API routes or job-state changes, assert TinyDB-backed stores emit expected snapshots and that static URLs stay rooted in `output/`.

## Deployment & Operations
- `.env` drives local runs and Azure deployment; keep `.env.example` in sync when adding env vars (API keys, deployment names, CORS origins, thread limits, moderation retry counts, download variants). The frontend uses `.env.local` for `NEXT_PUBLIC_API_BASE_URL`.
- `output/` is runtime storage for prompts, scenes, final videos, logs, and TinyDB (`workflow_jobs.json`, `single_video_jobs.json`); avoid committing generated artifacts.
- FastAPI serves static assets from `output/` under `/static`; keep generated files within that tree so dashboard links resolve.
- Sequence mode chains clips via the first start image; ensure `scene_start_screen` counts match scene totals or handle uploads via the API.

## Security & Configuration Tips
- Never commit live Azure/OpenAI keys or user content. Use `AZURE_OPENAI_API_KEY`, `AZURE_OPENAI_ENDPOINT`/`ENDPOINT_URL`, `DEPLOYMENT_NAME`, `VIDEO_DOWNLOAD_VARIANT`, `SORA_WORKFLOW_ALLOWED_ORIGINS`, etc., via environment variables.
- When sharing logs or manifests, strip job IDs and personal scripts. Remix/regenerate endpoints rely on stored `video_id` metadata—keep those JSON files intact in `output/` when debugging.
