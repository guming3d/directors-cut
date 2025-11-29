import pytest
from pydantic import ValidationError

from backend.models import (
    SingleVideoPayload,
    StartWorkflowPayload,
    VideoWorkflowRequest,
)


def test_kling_workflow_payload_rejects_sora_duration():
    with pytest.raises(ValidationError):
        StartWorkflowPayload(
            script="**Scene 1 (Test)**\nDescription",
            video_provider="kling",
            scene_duration=4,
        )


def test_kling_workflow_payload_accepts_valid_duration():
    payload = StartWorkflowPayload(
        script="**Scene 1 (Test)**\nDescription",
        video_provider="kling",
        scene_duration=5,
    )
    assert payload.scene_duration == 5


def test_single_video_payload_validates_kling_duration():
    with pytest.raises(ValidationError):
        SingleVideoPayload(
            prompt="Describe a futuristic city skyline at dusk.",
            video_provider="kling",
            duration=8,
        )


def test_video_workflow_request_rejects_invalid_kling_duration():
    with pytest.raises(ValidationError):
        VideoWorkflowRequest(
            video_provider="kling",
            scene_duration=4,
        )
