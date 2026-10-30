# API reference

The backend is a FastAPI app; interactive docs are at `/docs` on a running server (http://localhost:8000/docs locally). When auth is on, send HTTP Basic credentials (`SORA_WORKFLOW_USERNAME`/`SORA_WORKFLOW_PASSWORD`) or `Authorization: Bearer $SORA_WORKFLOW_API_TOKEN`.

## Session and settings

- `GET /api/auth/session` — check credentials
- `GET /api/providers/active` — report the default backend configured for new workloads
- `GET /api/prompts/workflow` / `PUT /api/prompts/workflow` — read or edit the workflow prompt template
- `POST /api/refine/workflow`, `POST /api/refine/single` — polish a storyboard or single-clip prompt with the reasoning model

## Storyboard workflows

- `POST /api/workflows` — start a new video job with a storyboard script
- `GET /api/workflows` — list recent jobs with progress snapshots
- `GET /api/workflows/{job_id}` — poll job status, including messages, scene metadata, and final MP4/JSON manifest links
- `DELETE /api/workflows/{job_id}` — remove a job and its assets
- `GET /api/workflows/{job_id}/assets.zip` — download every scene asset
- `POST /api/workflows/{job_id}/scenes/{scene_order}/regenerate` — rerender a scene, optionally with a new prompt
- `POST /api/workflows/{job_id}/scenes/{scene_order}/remix` — remix a completed scene by supplying a new prompt against the previous Azure video ID (Sora 2 only)
- `POST /api/workflows/{job_id}/scenes/{scene_order}/select` — choose which scene version should be used when composing the final cut
- `POST /api/workflows/{job_id}/recompose` — regenerate the final video using the currently selected scene versions

## Single clips

- `POST /api/single-videos` — generate one clip
- `GET /api/single-videos` — list single-clip jobs
- `GET /api/single-videos/{job_id}` — poll a single-clip job
- `DELETE /api/single-videos/{job_id}` — remove a single-clip job
- `POST /api/single-videos/{job_id}/remix` — remix a clip (Sora 2 only)
- `POST /api/single-videos/{job_id}/select` — choose the preferred version

## Video insight

- `POST /api/video-insights` — upload a video for Azure Content Understanding analysis
- `GET /api/video-insights` — list recent insight jobs with progress snapshots
- `GET /api/video-insights/{job_id}` — poll the key-frame/segment results and download links for a specific job
- `DELETE /api/video-insights/{job_id}` — remove a completed/failed insight job and its workspace artifacts

## Video reproduce

- `POST /api/reproduce` — upload a reference video and start the Kling reproduce workflow
- `GET /api/reproduce` — list reproduce jobs with cached segments, prompts, and output links
- `GET /api/reproduce/{job_id}` — inspect a specific reproduce job (segments, stylized frames, Kling versions, and merged output)
- `POST /api/reproduce/{job_id}/segments/{segment_order}/regenerate` — supply a custom prompt to rerender a single segment
- `POST /api/reproduce/{job_id}/rerun` — resume a failed reproduce job without re-calling the reasoning model, gpt-image-2.5, or Kling for cached artifacts
- `DELETE /api/reproduce/{job_id}` — remove a reproduce job and its workspace assets

## Static files

- `GET /static/...` — generated assets (final video, scene clips, logs)
