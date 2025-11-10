# Repository Guidelines

## Project Structure & Module Organization
Three runnable entrypoints live at the repo root: `magentic.py` drives the multi-agent Magentic demo, `sora-openai-client.py` is the Azure OpenAI video client, and `sora_agent_workflow.py` composes the director → Sora2 → composer workflow outlined in `workflow-diagram.png`. Narrative assets that feed prompts stay in `Chapters/`, and visual references or screenshots belong in `images_references/`; keep the existing filenames because the Sora client expects explicit paths. Generated media and logs default to an `output/` folder you create on demand. Store API secrets in `.env`, isolate dependencies in `.venv/`, and leave both untracked.

## Build, Test, and Development Commands
- `python -m venv .venv && source .venv/bin/activate` initializes the local environment; regenerate it whenever dependencies change.
- `pip install -U agent_framework openai python-dotenv` pulls the libraries used by the two scripts; pin exact versions before releasing.
- `python magentic.py` runs the sample multi-agent workflow and streams orchestrator/agent events to the console.
- `python sora-openai-client.py --prompt-file Chapters/video_script.txt --input-image images_references/screenshot-002.png --resolution 1280x720 --duration 8` submits a Sora job using the bundled prompt materials.
- `python sora_agent_workflow.py --threads 3 --resolution 1280x720` runs the three-agent pipeline that splits the script into 4/8/12-second scenes, launches concurrent Sora renders, and stitches them with `ffmpeg` (ensure `ffmpeg`/`ffprobe` are on PATH).

## Coding Style & Naming Conventions
Target Python 3.10+ and follow PEP 8 with 4-space indentation. Keep type hints on public functions (`async def main() -> None`) and prefer descriptive agent names (`ResearcherAgent`, `CoderAgent`). Place configuration constants in ALL_CAPS near the top of the module, keep CLI options in kebab-case, and name new assets with lowercase-hyphenated descriptors to match the existing pattern.

## Testing Guidelines
There is no dedicated test suite yet, so add one under `tests/` when introducing non-trivial logic. Use `pytest` (recommended) or `python -m unittest` for smaller modules, and cover: CLI option parsing, environment fallbacks, and any utility methods added around the OpenAI clients. Mirror filenames (`tests/test_sora_client.py`) and keep fixtures small so contributors can run `pytest -q` before submitting.

## Commit & Pull Request Guidelines
Commits should use short, imperative subjects similar to the initial `first commit` (e.g., “Add Sora download logging”). When opening a PR, include: a one-paragraph summary, reproduction steps or sample commands, links to tracked issues, and screenshots or video snippets when the change affects generated media. Reference any credential or configuration updates explicitly so reviewers can test with their own `.env` safely.

## Security & Configuration Tips
Never check secrets into Git; rely on `.env` keys such as `AZURE_OPENAI_API_KEY`, `ENDPOINT_URL`, `DEPLOYMENT_NAME`, and the `VIDEO_*` variables read inside `sora-openai-client.py`. Rotate keys after demos, and prefer temporary SAS URLs for shared assets. When sharing logs, redact job IDs returned by `client.videos.create_and_poll`.
