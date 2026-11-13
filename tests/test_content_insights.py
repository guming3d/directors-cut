"""Unit tests covering content insight parsing and store helpers."""

from __future__ import annotations

import shutil

import pytest

from backend.config import OUTPUT_DIR
from backend.content_insights.client import AzureContentUnderstandingClient
from backend.content_insights.parser import summarize_content_understanding_result
from backend.models import VideoInsightKeyFrame, VideoInsightSegment
from backend.stores import VideoInsightJobStore


def test_summarize_content_understanding_result_extracts_key_frames_and_segments() -> None:
    payload = {
        "status": "succeeded",
        "result": {
            "insights": {
                "keyFrames": [
                    {
                        "id": "kf-1",
                        "timestamp": {"seconds": 1.25},
                        "text": "Hero pose",
                        "confidence": 0.98,
                        "tags": ["hero", {"name": "pose"}],
                    }
                ],
                "segments": [
                    {
                        "segmentId": "seg-1",
                        "label": "Intro",
                        "start": "PT0S",
                        "end": "PT10S",
                        "summary": "Opening scene",
                        "categories": ["establishing"],
                    }
                ],
            }
        },
    }

    key_frames, segments = summarize_content_understanding_result(payload)

    assert len(key_frames) == 1
    assert key_frames[0].frame_id == "kf-1"
    assert key_frames[0].timestamp in {"1.25s", "PT1.25S", "1.25s"}
    assert "hero" in key_frames[0].tags

    assert len(segments) == 1
    assert segments[0].segment_id == "seg-1"
    assert segments[0].label == "Intro"
    assert segments[0].start == "PT0S"
    assert segments[0].start_seconds == 0.0
    assert segments[0].end_seconds == 10.0
    assert "establishing" in segments[0].tags


def test_extract_segments_handles_value_objects() -> None:
    payload = {
        "fields": {
            "Segments": {
                "type": "array",
                "valueArray": [
                    {
                        "type": "object",
                        "valueObject": {
                            "SegmentId": {"type": "string", "valueString": "1"},
                            "StartTimeMs": {"type": "integer", "valueInteger": 1500},
                            "EndTimeMs": {"type": "integer", "valueInteger": 4200},
                            "Description": {
                                "type": "string",
                                "valueString": "Segment summary text.",
                            },
                        },
                    }
                ],
            }
        }
    }

    _, segments = summarize_content_understanding_result(payload)

    assert len(segments) == 1
    assert segments[0].segment_id == "1"
    assert segments[0].start_seconds == 1.5
    assert segments[0].end_seconds == 4.2
    assert segments[0].summary == "Segment summary text."


@pytest.mark.asyncio
async def test_aiter_file_chunks_streams_payload(tmp_path) -> None:
    video_path = tmp_path / "clip.mp4"
    payload = b"abc" * 1024
    video_path.write_bytes(payload)

    chunks = [chunk async for chunk in AzureContentUnderstandingClient._aiter_file_chunks(video_path, chunk_size=512)]

    assert b"".join(chunks) == payload
    assert all(len(chunk) <= 512 for chunk in chunks)
    assert len(chunks) > 1


@pytest.mark.asyncio
async def test_video_insight_store_snapshot_includes_artifacts() -> None:
    store = VideoInsightJobStore()
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    job_id = "job-test-123"
    await store.create_job(job_id, "clip.mp4", "video/mp4", "notes")

    job_dir = OUTPUT_DIR / job_id
    video_path = job_dir / "clip.mp4"
    video_path.parent.mkdir(parents=True, exist_ok=True)
    video_path.touch()
    await store.set_video_path(job_id, str(video_path))

    result_path = job_dir / "analysis.json"
    start_frame = job_dir / "seg_start.jpg"
    end_frame = job_dir / "seg_end.jpg"
    start_frame.touch()
    end_frame.touch()
    await store.set_result(
        job_id,
        str(result_path),
        [VideoInsightKeyFrame(frame_id="kf", timestamp="1s", description="frame", confidence=0.9, tags=["tag"])],
        [
            VideoInsightSegment(
                segment_id="seg",
                label="Intro",
                start="0s",
                end="5s",
                start_seconds=0.0,
                end_seconds=5.0,
                summary="hi",
                tags=["intro"],
                start_frame_path=str(start_frame),
                end_frame_path=str(end_frame),
            )
        ],
    )
    await store.mark_completed(job_id)

    snapshot = await store.snapshot(job_id)
    assert snapshot.filename == "clip.mp4"
    assert snapshot.status == "completed"
    assert snapshot.key_frames[0].frame_id == "kf"
    assert snapshot.segments[0].label == "Intro"
    assert snapshot.segments[0].start_seconds == 0.0
    assert snapshot.segments[0].start_frame_url and snapshot.segments[0].start_frame_url.startswith("/static/")
    assert snapshot.video_url and job_id in snapshot.video_url

    shutil.rmtree(job_dir, ignore_errors=True)


@pytest.mark.asyncio
async def test_begin_analyze_file_uploads_through_async_client(tmp_path, monkeypatch) -> None:
    import httpx

    from backend.content_insights import client as client_module

    video_path = tmp_path / "clip.mp4"
    payload = b"\x00\x01video" * 300_000
    video_path.write_bytes(payload)
    received: dict = {}

    def _handler(request: httpx.Request) -> httpx.Response:
        received["body"] = request.read()
        received["content_length"] = request.headers.get("content-length")
        return httpx.Response(202, headers={"operation-location": "https://cu.example/operations/1"})

    real_client = httpx.AsyncClient

    def _client(*args, **kwargs):
        kwargs["transport"] = httpx.MockTransport(_handler)
        return real_client(*args, **kwargs)

    monkeypatch.setattr(client_module.httpx, "AsyncClient", _client)
    cu = AzureContentUnderstandingClient(
        endpoint="https://cu.example", auth="key", api_key="test-key", api_version="2025-11-01"
    )

    operation = await cu._begin_analyze_file(video_path, "video/mp4")

    assert (operation, received["body"], received["content_length"]) == (
        "https://cu.example/operations/1",
        payload,
        str(len(payload)),
    )


def _client_with_transport(monkeypatch, handler, **client_kwargs):
    import httpx

    from backend.content_insights import client as client_module

    real_client = httpx.AsyncClient

    def _client(*args, **kwargs):
        kwargs["transport"] = httpx.MockTransport(handler)
        return real_client(*args, **kwargs)

    monkeypatch.setattr(client_module.httpx, "AsyncClient", _client)
    options = {"auth": "key", "api_key": "k"} | client_kwargs
    return AzureContentUnderstandingClient(
        endpoint="https://gone.cognitiveservices.azure.com", api_version="2025-11-01", **options
    )


@pytest.mark.asyncio
async def test_unreachable_endpoint_error_names_endpoint_and_setting(tmp_path, monkeypatch) -> None:
    import httpx

    from backend.content_insights.client import ContentUnderstandingError

    def _handler(request):
        raise httpx.ConnectError("[Errno -2] Name or service not known", request=request)

    cu = _client_with_transport(monkeypatch, _handler)
    video = tmp_path / "clip.mp4"
    video.write_bytes(b"v")

    with pytest.raises(ContentUnderstandingError, match="AZURE_CONTENT_UNDERSTANDING_ENDPOINT") as info:
        await cu._begin_analyze_file(video, "video/mp4")
    assert "gone.cognitiveservices.azure.com" in str(info.value)


@pytest.mark.asyncio
async def test_rejected_key_error_points_to_api_key_setting(tmp_path, monkeypatch) -> None:
    import httpx

    from backend.content_insights.client import ContentUnderstandingError

    cu = _client_with_transport(monkeypatch, lambda request: httpx.Response(401, json={"error": {"code": "401"}}))
    video = tmp_path / "clip.mp4"
    video.write_bytes(b"v")

    with pytest.raises(ContentUnderstandingError, match="AZURE_CONTENT_UNDERSTANDING_API_KEY"):
        await cu._begin_analyze_file(video, "video/mp4")


def test_extract_segments_reads_ga_audio_visual_contents() -> None:
    payload = {
        "status": "Succeeded",
        "result": {
            "analyzerId": "prebuilt-videoSearch",
            "contents": [
                {
                    "kind": "audioVisual",
                    "startTimeMs": 800,
                    "endTimeMs": 3200,
                    "KeyFrameTimesMs": [800, 1600],
                    "markdown": "# Video: 00:00.800 => 00:03.200",
                    "fields": {"Summary": {"type": "string", "valueString": "A person frames a coastal sunset."}},
                },
                {
                    "kind": "audioVisual",
                    "startTimeMs": 3200,
                    "endTimeMs": 6000,
                    "fields": {"Summary": {"type": "string", "valueString": "The camera pans to the ocean."}},
                },
            ],
        },
    }

    _, segments = summarize_content_understanding_result(payload)

    assert [(s.start_seconds, s.end_seconds, s.summary) for s in segments] == [
        (0.8, 3.2, "A person frames a coastal sunset."),
        (3.2, 6.0, "The camera pans to the ocean."),
    ]


def test_ga_contents_are_ignored_when_explicit_segments_exist() -> None:
    payload = {
        "result": {
            "contents": [
                {
                    "kind": "audioVisual",
                    "startTimeMs": 0,
                    "endTimeMs": 9000,
                    "fields": {
                        "Segments": {
                            "type": "array",
                            "valueArray": [
                                {
                                    "type": "object",
                                    "valueObject": {
                                        "StartTimeMs": {"type": "integer", "valueInteger": 0},
                                        "EndTimeMs": {"type": "integer", "valueInteger": 4000},
                                    },
                                }
                            ],
                        }
                    },
                }
            ]
        }
    }

    _, segments = summarize_content_understanding_result(payload)

    assert [(s.start_seconds, s.end_seconds) for s in segments] == [(0.0, 4.0)]


@pytest.mark.asyncio
async def test_entra_mode_sends_bearer_token_instead_of_key(tmp_path, monkeypatch) -> None:
    import httpx

    seen: dict = {}

    def _handler(request: httpx.Request) -> httpx.Response:
        seen["authorization"] = request.headers.get("authorization")
        seen["subscription_key"] = request.headers.get("ocp-apim-subscription-key")
        return httpx.Response(202, headers={"operation-location": "https://cu.example/operations/1"})

    async def _token() -> str:
        return "entra-token"

    cu = _client_with_transport(monkeypatch, _handler, auth="entra", api_key=None, token_provider=_token)
    video = tmp_path / "clip.mp4"
    video.write_bytes(b"v")

    await cu._begin_analyze_file(video, "video/mp4")

    assert (seen["authorization"], seen["subscription_key"]) == ("Bearer entra-token", None)


@pytest.mark.asyncio
async def test_binary_upload_posts_to_analyze_binary_route(tmp_path, monkeypatch) -> None:
    import httpx

    seen: dict = {}

    def _handler(request: httpx.Request) -> httpx.Response:
        seen["path"] = request.url.path
        return httpx.Response(202, headers={"operation-location": "https://cu.example/operations/1"})

    cu = _client_with_transport(monkeypatch, _handler, analyzer_id="prebuilt-videoSearch")
    video = tmp_path / "clip.mp4"
    video.write_bytes(b"v")

    await cu._begin_analyze_file(video, "video/mp4")

    assert seen["path"] == "/contentunderstanding/analyzers/prebuilt-videoSearch:analyzeBinary"


@pytest.mark.asyncio
async def test_rejected_entra_token_error_names_required_role(tmp_path, monkeypatch) -> None:
    import httpx

    from backend.content_insights.client import ContentUnderstandingError

    async def _token() -> str:
        return "entra-token"

    cu = _client_with_transport(
        monkeypatch,
        lambda request: httpx.Response(403, json={"error": {"code": "PermissionDenied"}}),
        auth="entra",
        api_key=None,
        token_provider=_token,
    )
    video = tmp_path / "clip.mp4"
    video.write_bytes(b"v")

    with pytest.raises(ContentUnderstandingError, match="Cognitive Services User"):
        await cu._begin_analyze_file(video, "video/mp4")


@pytest.mark.asyncio
async def test_missing_entra_sign_in_raises_readable_error(tmp_path, monkeypatch) -> None:
    import httpx
    from azure.core.exceptions import ClientAuthenticationError

    from backend.content_insights.client import ContentUnderstandingError

    async def _token() -> str:
        raise ClientAuthenticationError("DefaultAzureCredential failed to retrieve a token")

    cu = _client_with_transport(
        monkeypatch, lambda request: httpx.Response(202), auth="entra", api_key=None, token_provider=_token
    )
    video = tmp_path / "clip.mp4"
    video.write_bytes(b"v")

    with pytest.raises(ContentUnderstandingError, match="az login"):
        await cu._begin_analyze_file(video, "video/mp4")


def test_key_mode_without_api_key_fails_fast() -> None:
    from backend.content_insights.client import ContentUnderstandingError

    with pytest.raises(ContentUnderstandingError, match="AZURE_CONTENT_UNDERSTANDING_API_KEY"):
        AzureContentUnderstandingClient(endpoint="https://cu.example", auth="key", api_key="", api_version="2025-11-01")


def test_entra_mode_is_ready_without_api_key(monkeypatch) -> None:
    from backend.content_insights import service

    monkeypatch.setattr(service, "AZURE_CONTENT_UNDERSTANDING_ENDPOINT", "https://cu.example")
    monkeypatch.setattr(service, "AZURE_CONTENT_UNDERSTANDING_API_KEY", None)
    monkeypatch.setattr(service, "AZURE_CONTENT_UNDERSTANDING_AUTH", "entra")

    service.ensure_content_understanding_ready()
