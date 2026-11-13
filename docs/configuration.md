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

Set `VIDEO_PROVIDER` to `sora2` (default), `kling`, `jimeng`, `seedance`, or one of the Foundry Fal models (`fal-kling-v3-pro`, `fal-ltx-2.5-pro`, `fal-minimax-h3-max`, `fal-minimax-h3-max-turbo`) to choose the default engine for new jobs; the dashboard can override it per job and shows which engine each job used. All backends share the same Director and Composer agents. Remix is Sora-only; all other backends support regeneration instead.

| Backend | Clip lengths |
|---|---|
| Sora 2 | 4, 8, 12 s |
| Kling | 5, 10 s |
| Jimeng 3.0 Pro | 5, 10 s |
| Seedance 2.5 | 4, 5, 8, 10, 12 s |
| Kling V3 Pro (Foundry) | 4, 5, 8, 10 s (configurable, 3-15) |
| LTX 2.5 Pro (Foundry) | 6, 8, 10 s |
| MiniMax H3 Max / Max Turbo (Foundry) | 4, 6, 8, 10 s (configurable, 1-15) |

### Sora 2 (Azure OpenAI)

Sora rendering and remix sign in with Microsoft Entra ID by default (`AZURE_OPENAI_AUTH=entra`) through `DefaultAzureCredential`: your `az login` locally, or a managed identity in Azure. That identity needs the "Cognitive Services OpenAI User" role (or equivalent) on the Sora resource. Set `AZURE_OPENAI_AUTH=key` to send `AZURE_OPENAI_API_KEY` instead; that fails with `AuthenticationTypeDisabled` when the resource has key-based authentication disabled.

### Kling

Configure `KLING_API_BASE_URL`, `KLING_ACCESS_KEY`, and `KLING_SECRET_KEY` so the workflow can mint short-lived JWTs. Optional knobs such as `KLING_MODEL_NAME`, `KLING_MODE`, `KLING_CFG_SCALE`, `KLING_POLL_INTERVAL`, `KLING_POLL_TIMEOUT`, and `KLING_TOKEN_TTL` still apply, and a legacy `KLING_API_KEY` fallback remains available for staging environments.

Kling 3.0 (`KLING_MODEL_NAME=kling-v3`) generates speech, sound effects, and ambience with each clip by default; set `KLING_SOUND=off` for silent clips.

### Jimeng

Provide `JIMENG_ACCESS_KEY_ID` and `JIMENG_SECRET_ACCESS_KEY` (STS token optional via `JIMENG_SECURITY_TOKEN`); the client signs requests against `JIMENG_API_BASE_URL` (defaults to `https://visual.volcengineapi.com`) using the fixed `JIMENG_REGION=cn-north-1` and `JIMENG_SERVICE=cv` unless overridden. Jimeng supports 5s/10s clips and accepts an optional reference image as the first frame. Jimeng 3.0 Pro has no audio option, so Jimeng clips are always silent.

### Seedance

For Seedance 2.5 (Volcengine Ark, model `doubao-seedance-2-5-260628`), set `ARK_API_KEY`. Clips can be 4, 5, 8, 10, or 12 seconds and include generated audio unless `SEEDANCE_GENERATE_AUDIO=false`. Ark only accepts reference images by URL, so start frames are uploaded to a public Cloudflare R2 bucket first: configure `R2_ACCOUNT_ID`, `R2_BUCKET`, `R2_PUBLIC_BASE_URL`, `R2_ACCESS_KEY_ID`, and `R2_SECRET_ACCESS_KEY` (uploaded frames become publicly readable). Seedance is billed per generated second and task creation is never retried automatically; use 4-second scenes while testing.

### Fal models in Microsoft Foundry

Four Fal deployments on one Foundry resource: `Fal-Kling-V3-Pro`, `Fal-LTX-2.5-Pro`, `Fal-MiniMax-H3-Max`, and `Fal-MiniMax-H3-Max-Turbo`. Set `FOUNDRY_FAL_ENDPOINT` to the resource (e.g. `https://testaistudio0307049060.services.ai.azure.com`). Calls go to its provider-native route, `<endpoint>/providers/falai/v1/<model>/{text,image}-to-video`, and the resource's `/providers/falai/v1/docs` lists the routes. They do not go through the OpenAI `/openai/v1/videos` API: that route accepts the Fal model names but fails every create with HTTP 500 ("Failed to parse video generation service error response").

- **Auth:** Entra ID by default (`FOUNDRY_FAL_AUTH=entra`); the identity needs the "Foundry User" role on the resource, which `deploy.sh`/`update.sh` grant to the backend's managed identity. `FOUNDRY_FAL_AUTH=key` sends `FOUNDRY_FAL_API_KEY`.
- **Start images** are sent inline as JPEG data URIs, so no public image hosting is needed. The finished video is an expiring asset on the same resource and is downloaded immediately.
- **Clip lengths** start at 4 seconds, except LTX 2.5 Pro, whose shortest clip is 6 seconds. Override the offered lengths per model with `FAL_<MODEL>_DURATIONS` (see `.env.example`); keep `frontend/src/app/dashboard/constants.ts` in step.
- **Output:** Kling V3 Pro renders 1080p; LTX 2.5 Pro renders 720p, or 1080p for 1920x1080 jobs; MiniMax renders 768P (768x1344 for portrait), or 1080P for 1920x1080 jobs. Kling and LTX generate audio unless `FAL_GENERATE_AUDIO=false`; MiniMax always adds sound.
- **Limits:** each deployment accepts 2 submissions per minute by default. Rate-limited submits (HTTP 429) are retried with backoff; other submit failures are never retried, because an accepted submit is billed. Status checks are not rate-limited. Up to `FAL_MAX_CONCURRENT_TASKS` (default 2) renders run per model.
- **Prompts:** Fal Kling V3 Pro uses the Kling 3.0 templates (a dashboard edit applies to both Kling backends). LTX and MiniMax have their own templates in `prompts/`, and the two MiniMax variants share one. Prompt limits are 2500 characters for Kling and 5000 for LTX.

## Video insight (Azure Content Understanding)

The **Video insight** tab uploads an MP4/MOV/WEBM file to Azure Content Understanding, then shows the extracted key-frame timestamps and segment summaries. Each job stores the raw JSON manifest under `/static` with download links for the analyzed clip and the insight payload.

- `AZURE_CONTENT_UNDERSTANDING_ENDPOINT` — the Foundry (AI Services) resource endpoint, e.g. `https://<name>.cognitiveservices.azure.com/`.
- `AZURE_CONTENT_UNDERSTANDING_AUTH` — `entra` (default) or `key`. `entra` signs in with Microsoft Entra ID (`az login` locally, the managed identity in Azure); the identity needs the **Cognitive Services User** role on the resource. `key` sends `AZURE_CONTENT_UNDERSTANDING_API_KEY` and fails if key-based access is disabled on the resource.
- `AZURE_CONTENT_UNDERSTANDING_API_KEY` — only used when `AZURE_CONTENT_UNDERSTANDING_AUTH=key`.
- `AZURE_CONTENT_UNDERSTANDING_API_VERSION` — default `2025-11-01`. The earlier `2025-05-01-preview` is retired (HTTP 410).
- `AZURE_CONTENT_UNDERSTANDING_ANALYZER_ID` — defaults to `prebuilt-videoSearch`, which needs a completion deployment (for example `gpt-6-luna`) mapped in the resource defaults via `PATCH /contentunderstanding/defaults`, mapping both `prebuilt-analyzer-completion` and `prebuilt-analyzer-completion-mini`.
- `CONTENT_UNDERSTANDING_MAX_UPLOAD_MB`, `CONTENT_UNDERSTANDING_POLL_INTERVAL`, `CONTENT_UNDERSTANDING_POLL_TIMEOUT` — optional overrides for upload size and polling cadence.
- `AZURE_SPEECH_ENDPOINT` / `AZURE_SPEECH_KEY` — optional, reserved for future speech-to-text expansion.

## Output storage

Locally, jobs and media live in `output/`. `OUTPUT_STORAGE=blob` mirrors `output/` to Azure Blob Storage so jobs survive redeploys; the deploy scripts turn this on in Azure. See [Deployment](deployment.md#persistent-storage).
