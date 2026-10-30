# Configuration

All settings come from environment variables, usually through `.env` (copy `.env.example`, which lists every option with placeholders).

## General

- `SORA_WORKFLOW_ALLOWED_ORIGINS` — comma-separated list of origins permitted by CORS
- `SORA_WORKFLOW_THREADS` — concurrent render jobs (default: `3`)
- `SORA_BLOCK_RETRIES` — retries for moderated scenes (default: `3`)
- `VIDEO_DOWNLOAD_VARIANT` — override the variant name sent to the Sora client
- `SORA_WORKFLOW_USERNAME` / `SORA_WORKFLOW_PASSWORD` or `SORA_WORKFLOW_API_TOKEN` — dashboard and API credentials

## Models

All text and vision reasoning (storyboard refinement, scene splitting, first-frame prompts, character extraction, segment descriptions) uses `AZURE_OPENAI_REASONING_DEPLOYMENT` (default `gpt-6.1-sol`) with `reasoning.effort=medium`; set `AZURE_OPENAI_REASONING_ENDPOINT`/`AZURE_OPENAI_REASONING_API_KEY` if it lives on a different resource than Sora. When a workflow script has no `**Scene N (Title)**` headers, the reasoning model inserts them automatically and the pipeline continues. Images come from the Foundry `gpt-image-2.5-sunburst` deployment (`FOUNDRY_GPT_IMAGE_ENDPOINT`, `FOUNDRY_GPT_IMAGE_API_KEY`).

## Video backends

Set `VIDEO_PROVIDER` to `sora2` (default), `kling`, `jimeng`, or `seedance` to choose the default engine for new jobs; the dashboard can override it per job and shows which engine each job used. All backends share the same Director and Composer agents. Remix is Sora-only; Kling, Jimeng, and Seedance jobs support regeneration instead.

| Backend | Clip lengths |
|---|---|
| Sora 2 | 4, 8, 12 s |
| Kling | 5, 10 s |
| Jimeng 3.0 Pro | 5, 10 s |
| Seedance 2.5 | 4, 5, 8, 10, 12 s |

### Sora 2 (Azure OpenAI)

Sora rendering and remix sign in with Microsoft Entra ID by default (`AZURE_OPENAI_AUTH=entra`) through `DefaultAzureCredential`: your `az login` locally, or a managed identity in Azure. That identity needs the "Cognitive Services OpenAI User" role (or equivalent) on the Sora resource. Set `AZURE_OPENAI_AUTH=key` to send `AZURE_OPENAI_API_KEY` instead; that fails with `AuthenticationTypeDisabled` when the resource has key-based authentication disabled.

### Kling

Configure `KLING_API_BASE_URL`, `KLING_ACCESS_KEY`, and `KLING_SECRET_KEY` so the workflow can mint short-lived JWTs. Optional knobs such as `KLING_MODEL_NAME`, `KLING_MODE`, `KLING_CFG_SCALE`, `KLING_POLL_INTERVAL`, `KLING_POLL_TIMEOUT`, and `KLING_TOKEN_TTL` still apply, and a legacy `KLING_API_KEY` fallback remains available for staging environments.

Kling 3.0 (`KLING_MODEL_NAME=kling-v3`) generates speech, sound effects, and ambience with each clip by default; set `KLING_SOUND=off` for silent clips.

### Jimeng

Provide `JIMENG_ACCESS_KEY_ID` and `JIMENG_SECRET_ACCESS_KEY` (STS token optional via `JIMENG_SECURITY_TOKEN`); the client signs requests against `JIMENG_API_BASE_URL` (defaults to `https://visual.volcengineapi.com`) using the fixed `JIMENG_REGION=cn-north-1` and `JIMENG_SERVICE=cv` unless overridden. Jimeng supports 5s/10s clips and accepts an optional reference image as the first frame. Jimeng 3.0 Pro has no audio option, so Jimeng clips are always silent.

### Seedance

For Seedance 2.5 (Volcengine Ark, model `doubao-seedance-2-5-260628`), set `ARK_API_KEY`. Clips can be 4, 5, 8, 10, or 12 seconds and include generated audio unless `SEEDANCE_GENERATE_AUDIO=false`. Ark only accepts reference images by URL, so start frames are uploaded to a public Cloudflare R2 bucket first: configure `R2_ACCOUNT_ID`, `R2_BUCKET`, `R2_PUBLIC_BASE_URL`, `R2_ACCESS_KEY_ID`, and `R2_SECRET_ACCESS_KEY` (uploaded frames become publicly readable). Seedance is billed per generated second and task creation is never retried automatically; use 4-second scenes while testing.

## Video insight (Azure Content Understanding)

The **Video insight** tab uploads an MP4/MOV/WEBM file to Azure Content Understanding, then shows the extracted key-frame timestamps and segment summaries. Each job stores the raw JSON manifest under `/static` with download links for the analyzed clip and the insight payload.

- `AZURE_CONTENT_UNDERSTANDING_ENDPOINT` — the region-specific Cognitive Service endpoint.
- `AZURE_CONTENT_UNDERSTANDING_API_KEY` — API key for the content understanding resource.
- `AZURE_CONTENT_UNDERSTANDING_API_VERSION` — default `2025-05-01-preview`.
- `AZURE_CONTENT_UNDERSTANDING_ANALYZER_ID` — defaults to `prebuilt-videoAnalyzer`.
- `CONTENT_UNDERSTANDING_MAX_UPLOAD_MB`, `CONTENT_UNDERSTANDING_POLL_INTERVAL`, `CONTENT_UNDERSTANDING_POLL_TIMEOUT` — optional overrides for upload size and polling cadence.
- `AZURE_SPEECH_ENDPOINT` / `AZURE_SPEECH_KEY` — optional, reserved for future speech-to-text expansion.

## Output storage

Locally, jobs and media live in `output/`. `OUTPUT_STORAGE=blob` mirrors `output/` to Azure Blob Storage so jobs survive redeploys; the deploy scripts turn this on in Azure. See [Deployment](deployment.md#persistent-storage).
