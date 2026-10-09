"""Dataclasses and Pydantic models shared across the workflow."""

from __future__ import annotations

import os
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .config import (
    DEFAULT_IMAGE_GENERATOR,
    DEFAULT_REPRODUCE_REFERENCE_MODE,
    DEFAULT_REPRODUCE_SEGMENTATION,
    DEFAULT_SORA_RESOLUTION,
    DEFAULT_VIDEO_PROVIDER,
    FINAL_VIDEO_PATH,
    OUTPUT_DIR,
    PROMPTS_DIR,
    SCENE_IMAGE_DIR,
    SCENES_DIR,
    SCRIPT_PATH,
    SORA_CLIENT,
    SORA_CLIP_DURATION_SECONDS,
    SORA_BLOCK_RETRIES,
    TMP_DIR,
    allowed_durations_for_provider,
)
from .utils import utc_now


VideoProviderName = Literal["sora2", "kling", "jimeng", "seedance"]


@dataclass
class CharacterReference:
    """A character extracted from the storyboard with an optional reference image."""

    name: str
    description: str
    image_path: Optional[str] = None


class EncodedImagePayload(BaseModel):
    """Base64-encoded image sent from the frontend."""

    model_config = ConfigDict(populate_by_name=True, extra="ignore")

    filename: str
    data_url: str = Field(
        ..., alias="dataUrl", description="data URL string containing base64 image content"
    )
    content_type: Optional[str] = Field(default=None, alias="contentType")


class VideoWorkflowRequest(BaseModel):
    """Configuration for the multi-agent video workflow."""

    model_config = ConfigDict(populate_by_name=True)

    script_path: str = Field(
        default=str(SCRIPT_PATH), description="Path to the master video script."
    )
    video_provider: VideoProviderName = Field(
        default=DEFAULT_VIDEO_PROVIDER,
        alias="videoProvider",
        description="Video backend to use for rendering.",
    )
    output_dir: str = Field(default=str(OUTPUT_DIR), description="Root output folder.")
    prompt_dir: str = Field(
        default=str(PROMPTS_DIR),
        description="Directory for generated prompt text files.",
    )
    scenes_dir: str = Field(
        default=str(SCENES_DIR), description="Directory for downloaded scene MP4s."
    )
    tmp_dir: str = Field(
        default=str(TMP_DIR), description="Directory for intermediate artifacts."
    )
    final_video_path: str = Field(
        default=str(FINAL_VIDEO_PATH), description="Final stitched MP4 path."
    )
    sora_client_path: str = Field(
        default=str(SORA_CLIENT), description="Path to sora-openai-client.py."
    )
    scene_image_dir: str = Field(
        default=str(SCENE_IMAGE_DIR),
        description="Directory containing scene start screen images (scene1.png, scene2.png, ...).",
    )
    use_scene_images: bool = Field(
        default=True,
        description="Whether to include scene start screen images when rendering Sora clips.",
    )
    generate_ai_first_frame: bool = Field(
        default=False,
        description="Whether to generate AI first-frame images for each scene before calling Sora.",
    )
    first_frame_dir: Optional[str] = Field(
        default=None,
        description="Directory to store AI-generated first-frame stills for each scene.",
    )
    resolution: str = Field(
        default=DEFAULT_SORA_RESOLUTION,
        description="Clip resolution string, e.g. 720x1280.",
    )
    scene_duration: int = Field(
        default=SORA_CLIP_DURATION_SECONDS,
        description="Scene duration in seconds (Sora: 4/8/12, Kling/Jimeng: 5/10, Seedance: 4/5/8/10/12).",
    )
    thread_count: int = Field(
        default=int(os.getenv("SORA_WORKFLOW_THREADS", "3")),
        ge=1,
        le=10,
        description="Parallel Sora job count.",
    )
    blocked_retry_attempts: int = Field(
        default=SORA_BLOCK_RETRIES,
        ge=1,
        description="How many times to retry a scene when Sora blocks a request.",
    )
    download_variant: str = Field(
        default=os.getenv("VIDEO_DOWNLOAD_VARIANT", "video"),
        description="Variant name passed to sora-openai-client.",
    )
    job_id: Optional[str] = Field(
        default=None,
        description="Server-assigned workflow job identifier used for progress tracking.",
    )
    generation_mode: Literal["parallel", "sequence"] = Field(
        default="parallel",
        description="Parallel renders scenes concurrently; sequence enforces frame-to-frame chaining.",
    )
    sequence_initial_image: Optional[str] = Field(
        default=None,
        description="Optional prepared PNG used to seed the first clip when generation_mode=sequence.",
    )
    refine_with_gpt5: bool = Field(
        default=False,
        description="Indicates whether GPT-6.1-sol storyboard refinement was requested.",
    )
    character_ref_dir: Optional[str] = Field(
        default=None,
        description="Directory to store generated character reference images.",
    )
    visual_style: str = Field(
        default="3d_animation",
        description="Visual style for all generated images and video.",
    )

    @model_validator(mode="after")
    def validate_scene_duration(self) -> "VideoWorkflowRequest":
        allowed = allowed_durations_for_provider(self.video_provider)
        if self.scene_duration not in allowed:
            allowed_str = ", ".join(str(value) for value in allowed)
            raise ValueError(
                f"{self.video_provider.upper()} supports scene durations of {allowed_str} seconds."
            )
        return self


@dataclass
class ProgressMessage:
    timestamp: datetime
    stage: str
    text: str


@dataclass
class SceneVersionEntry:
    version_id: str
    video_path: str
    label: str = ""
    log_path: Optional[str] = None
    client_log_path: Optional[str] = None
    video_id: Optional[str] = None
    prompt: Optional[str] = None
    prompt_path: Optional[str] = None
    source: str = "original"
    created_at: datetime = field(default_factory=utc_now)
    last_frame_path: Optional[str] = None
    first_frame_path: Optional[str] = None


@dataclass
class SceneStatus:
    order: int
    label: str
    duration_seconds: int
    prompt_path: str
    video_path: str
    log_path: Optional[str] = None
    client_log_path: Optional[str] = None
    selected_version_id: Optional[str] = None
    versions: list[SceneVersionEntry] = field(default_factory=list)
    last_frame_path: Optional[str] = None
    first_frame_path: Optional[str] = None

    def set_selected_version(self, version: SceneVersionEntry) -> None:
        self.selected_version_id = version.version_id
        self.video_path = version.video_path
        self.log_path = version.log_path
        self.client_log_path = version.client_log_path
        self.last_frame_path = version.last_frame_path
        self.first_frame_path = version.first_frame_path

    def get_version(self, version_id: Optional[str]) -> SceneVersionEntry:
        if version_id:
            for candidate in self.versions:
                if candidate.version_id == version_id:
                    return candidate
        if self.versions:
            return self.versions[-1]
        raise KeyError(f"Scene {self.order} has no recorded versions.")


@dataclass
class WorkflowJobState:
    job_id: str
    status: str = "queued"
    stage: Optional[str] = None
    progress: float = 0.0
    resolution: str = DEFAULT_SORA_RESOLUTION
    video_provider: str = DEFAULT_VIDEO_PROVIDER
    script: str = ""
    script_path: Optional[str] = None
    started_at: datetime = field(default_factory=utc_now)
    completed_at: Optional[datetime] = None
    error: Optional[str] = None
    final_video_path: Optional[str] = None
    manifest: Optional[dict[str, Any]] = None
    total_scenes: int = 0
    completed_scenes: int = 0
    messages: list[ProgressMessage] = field(default_factory=list)
    scenes: list[SceneStatus] = field(default_factory=list)
    download_variant: str = field(default_factory=lambda: os.getenv("VIDEO_DOWNLOAD_VARIANT", "video"))
    generation_mode: str = "parallel"
    scene_duration: int = SORA_CLIP_DURATION_SECONDS
    generate_ai_first_frame: bool = False
    character_ref_paths: list[str] = field(default_factory=list)
    visual_style: str = "3d_animation"

    def add_message(self, stage: str, text: str) -> None:
        self.messages.append(ProgressMessage(timestamp=utc_now(), stage=stage, text=text))


@dataclass
class SingleVideoVersionEntry:
    version_id: str
    label: str
    prompt: str
    prompt_path: str
    video_path: str
    log_path: Optional[str] = None
    client_log_path: Optional[str] = None
    video_id: Optional[str] = None
    source: str = "original"
    created_at: datetime = field(default_factory=utc_now)


@dataclass
class SingleVideoJobState:
    job_id: str
    prompt: str
    resolution: str
    duration: int
    download_variant: str
    video_provider: str = DEFAULT_VIDEO_PROVIDER
    status: str = "queued"
    progress: float = 0.0
    started_at: datetime = field(default_factory=utc_now)
    completed_at: Optional[datetime] = None
    error: Optional[str] = None
    video_path: Optional[str] = None
    log_path: Optional[str] = None
    client_log_path: Optional[str] = None
    video_id: Optional[str] = None
    messages: list[ProgressMessage] = field(default_factory=list)
    versions: list[SingleVideoVersionEntry] = field(default_factory=list)
    selected_version_id: Optional[str] = None

    def add_message(self, stage: str, text: str) -> None:
        self.messages.append(ProgressMessage(timestamp=utc_now(), stage=stage, text=text))

    def set_active_version(self, version: SingleVideoVersionEntry) -> None:
        self.selected_version_id = version.version_id
        self.video_path = version.video_path
        self.log_path = version.log_path
        self.client_log_path = version.client_log_path
        self.video_id = version.video_id


@dataclass
class SingleVideoJobConfig:
    job_id: str
    prompt_path: Path
    prompt_text: str
    resolution: str
    duration: int
    download_variant: str
    video_path: Path
    reference_image_path: Optional[Path]
    job_dir: Path
    video_provider: str = DEFAULT_VIDEO_PROVIDER
    reference_image_paths: list[Path] = field(default_factory=list)


@dataclass
class VideoInsightKeyFrame:
    frame_id: Optional[str]
    timestamp: Optional[str]
    description: Optional[str] = None
    confidence: Optional[float] = None
    tags: list[str] = field(default_factory=list)


@dataclass
class VideoInsightSegment:
    segment_id: Optional[str]
    label: Optional[str]
    start: Optional[str]
    end: Optional[str]
    summary: Optional[str] = None
    tags: list[str] = field(default_factory=list)
    start_seconds: Optional[float] = None
    end_seconds: Optional[float] = None
    start_frame_path: Optional[str] = None
    end_frame_path: Optional[str] = None


@dataclass
class VideoInsightJobState:
    job_id: str
    filename: str
    status: str = "queued"
    progress: float = 0.0
    started_at: datetime = field(default_factory=utc_now)
    completed_at: Optional[datetime] = None
    error: Optional[str] = None
    video_path: Optional[str] = None
    result_path: Optional[str] = None
    original_content_type: Optional[str] = None
    notes: Optional[str] = None
    messages: list[ProgressMessage] = field(default_factory=list)
    key_frames: list[VideoInsightKeyFrame] = field(default_factory=list)
    segments: list[VideoInsightSegment] = field(default_factory=list)

    def add_message(self, stage: str, text: str) -> None:
        self.messages.append(ProgressMessage(timestamp=utc_now(), stage=stage, text=text))


@dataclass
class ReproduceSegmentVersion:
    version_id: str
    prompt: str
    status: str = "queued"
    video_path: Optional[str] = None
    log_path: Optional[str] = None
    client_log_path: Optional[str] = None
    task_id: Optional[str] = None
    error: Optional[str] = None
    prompt_path: Optional[str] = None
    created_at: datetime = field(default_factory=utc_now)


@dataclass
class VideoReproduceSegment:
    order: int
    label: str
    description: str
    start_seconds: float
    end_seconds: float
    duration_seconds: float
    kling_duration_seconds: int
    segment_id: Optional[str] = None
    tags: list[str] = field(default_factory=list)
    start_frame_path: Optional[str] = None
    end_frame_path: Optional[str] = None
    stylized_start_frame_path: Optional[str] = None
    stylized_end_frame_path: Optional[str] = None
    image_prompt_path: Optional[str] = None
    original_video_path: Optional[str] = None
    status: str = "queued"
    versions: list[ReproduceSegmentVersion] = field(default_factory=list)
    selected_version_id: Optional[str] = None

    def select_version(self, version: ReproduceSegmentVersion) -> None:
        self.selected_version_id = version.version_id

    def get_version(self, version_id: Optional[str]) -> ReproduceSegmentVersion:
        if version_id:
            for candidate in self.versions:
                if candidate.version_id == version_id:
                    return candidate
        if self.versions:
            return self.versions[-1]
        raise KeyError(f"Segment {self.order} has no versions recorded.")


@dataclass
class VideoReproduceJobState:
    job_id: str
    filename: str
    content_type: Optional[str] = None
    status: str = "queued"
    progress: float = 0.0
    started_at: datetime = field(default_factory=utc_now)
    completed_at: Optional[datetime] = None
    error: Optional[str] = None
    video_path: Optional[str] = None
    final_video_path: Optional[str] = None
    notes: Optional[str] = None
    thread_count: int = 3
    resolution: str = DEFAULT_SORA_RESOLUTION
    image_generation_backend: str = DEFAULT_IMAGE_GENERATOR
    segmentation_backend: str = DEFAULT_REPRODUCE_SEGMENTATION
    reference_mode: str = DEFAULT_REPRODUCE_REFERENCE_MODE
    messages: list[ProgressMessage] = field(default_factory=list)
    segments: list[VideoReproduceSegment] = field(default_factory=list)

    def add_message(self, stage: str, text: str) -> None:
        self.messages.append(ProgressMessage(timestamp=utc_now(), stage=stage, text=text))


class JobMessageModel(BaseModel):
    timestamp: datetime
    stage: str
    text: str


class SceneVersionModel(BaseModel):
    version_id: str
    label: str
    source: str
    prompt: Optional[str]
    prompt_path: Optional[str]
    video_path: str
    video_url: Optional[str]
    log_path: Optional[str]
    client_log_path: Optional[str]
    video_id: Optional[str]
    created_at: datetime
    last_frame_path: Optional[str] = None
    last_frame_url: Optional[str] = None
    first_frame_path: Optional[str] = None
    first_frame_url: Optional[str] = None


class SceneStatusModel(BaseModel):
    order: int
    label: str
    duration_seconds: int
    prompt_path: str
    video_path: str
    video_url: Optional[str] = None
    log_path: Optional[str] = None
    client_log_path: Optional[str] = None
    selected_version_id: Optional[str] = None
    versions: list[SceneVersionModel] = Field(default_factory=list)
    last_frame_path: Optional[str] = None
    last_frame_url: Optional[str] = None
    first_frame_path: Optional[str] = None
    first_frame_url: Optional[str] = None


class JobStatusResponse(BaseModel):
    job_id: str
    status: str
    stage: Optional[str]
    progress: float
    video_provider: str = DEFAULT_VIDEO_PROVIDER
    resolution: str
    started_at: datetime
    completed_at: Optional[datetime]
    error: Optional[str]
    final_video_path: Optional[str]
    final_video_url: Optional[str]
    manifest: Optional[dict[str, Any]]
    script: Optional[str] = None
    script_path: Optional[str] = None
    messages: list[JobMessageModel]
    scenes: list[SceneStatusModel]
    generation_mode: str = "parallel"
    scene_duration: int = SORA_CLIP_DURATION_SECONDS
    generate_ai_first_frame: bool = False
    character_ref_paths: list[str] = Field(default_factory=list)
    character_ref_urls: list[str] = Field(default_factory=list)
    visual_style: str = "3d_animation"


class SingleVideoVersionModel(BaseModel):
    version_id: str
    label: str
    source: str
    prompt: str
    prompt_path: str
    video_path: str
    video_url: Optional[str]
    log_path: Optional[str]
    client_log_path: Optional[str]
    video_id: Optional[str]
    created_at: datetime


class SingleVideoJobResponse(BaseModel):
    job_id: str
    status: str
    progress: float
    prompt: str
    video_provider: str = DEFAULT_VIDEO_PROVIDER
    resolution: str
    duration: int
    download_variant: str
    started_at: datetime
    completed_at: Optional[datetime]
    error: Optional[str]
    video_path: Optional[str]
    video_url: Optional[str]
    log_path: Optional[str]
    log_url: Optional[str]
    client_log_path: Optional[str]
    video_id: Optional[str]
    selected_version_id: Optional[str]
    versions: list[SingleVideoVersionModel] = Field(default_factory=list)
    messages: list[JobMessageModel]


class VideoInsightKeyFrameModel(BaseModel):
    frame_id: Optional[str]
    timestamp: Optional[str]
    description: Optional[str]
    confidence: Optional[float]
    tags: list[str] = Field(default_factory=list)


class VideoInsightSegmentModel(BaseModel):
    segment_id: Optional[str]
    label: Optional[str]
    start: Optional[str]
    end: Optional[str]
    start_seconds: Optional[float] = None
    end_seconds: Optional[float] = None
    summary: Optional[str]
    tags: list[str] = Field(default_factory=list)
    start_frame_path: Optional[str] = None
    start_frame_url: Optional[str] = None
    end_frame_path: Optional[str] = None
    end_frame_url: Optional[str] = None


class VideoInsightJobResponse(BaseModel):
    job_id: str
    status: str
    progress: float
    filename: str
    content_type: Optional[str]
    started_at: datetime
    completed_at: Optional[datetime]
    error: Optional[str]
    video_path: Optional[str]
    video_url: Optional[str]
    result_path: Optional[str]
    result_url: Optional[str]
    notes: Optional[str]
    messages: list[JobMessageModel]
    key_frames: list[VideoInsightKeyFrameModel] = Field(default_factory=list)
    segments: list[VideoInsightSegmentModel] = Field(default_factory=list)


class ReproduceSegmentVersionModel(BaseModel):
    version_id: str
    prompt: str
    status: str
    video_path: Optional[str]
    video_url: Optional[str]
    log_path: Optional[str]
    log_url: Optional[str]
    client_log_path: Optional[str]
    client_log_url: Optional[str]
    task_id: Optional[str]
    error: Optional[str]
    created_at: datetime


class VideoReproduceSegmentModel(BaseModel):
    segment_id: Optional[str]
    order: int
    label: str
    description: str
    start_seconds: float
    end_seconds: float
    duration_seconds: float
    kling_duration_seconds: int
    tags: list[str] = Field(default_factory=list)
    start_frame_path: Optional[str] = None
    start_frame_url: Optional[str] = None
    end_frame_path: Optional[str] = None
    end_frame_url: Optional[str] = None
    stylized_start_frame_path: Optional[str] = None
    stylized_start_frame_url: Optional[str] = None
    stylized_end_frame_path: Optional[str] = None
    stylized_end_frame_url: Optional[str] = None
    image_prompt_path: Optional[str] = None
    image_prompt_url: Optional[str] = None
    original_video_path: Optional[str] = None
    original_video_url: Optional[str] = None
    status: str
    selected_version_id: Optional[str]
    versions: list[ReproduceSegmentVersionModel] = Field(default_factory=list)


class VideoReproduceJobResponse(BaseModel):
    job_id: str
    filename: str
    content_type: Optional[str]
    status: str
    progress: float
    thread_count: int
    resolution: str
    started_at: datetime
    completed_at: Optional[datetime]
    error: Optional[str]
    notes: Optional[str]
    video_path: Optional[str]
    video_url: Optional[str]
    final_video_path: Optional[str]
    final_video_url: Optional[str]
    image_generation_backend: str
    segmentation_backend: str
    reference_mode: str
    messages: list[JobMessageModel]
    segments: list[VideoReproduceSegmentModel] = Field(default_factory=list)


class StartWorkflowPayload(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    script: str = Field(
        ..., description="Storyboard script content for the Director agent to process."
    )
    video_provider: VideoProviderName = Field(
        default=DEFAULT_VIDEO_PROVIDER,
        alias="videoProvider",
        description="Video backend to use when rendering scenes.",
    )
    threads: Optional[int] = Field(
        default=None, ge=1, le=10, description="Override concurrent Sora render threads."
    )
    resolution: Optional[str] = Field(
        default=None,
        description="Optional override for render resolution in WIDTHxHEIGHT format.",
    )
    use_scene_images: Optional[bool] = Field(
        default=None,
        alias="useSceneImages",
        description="Set to false to skip providing scene start screen images.",
    )
    generate_ai_first_frame: bool = Field(
        default=False,
        alias="generateAiFirstFrame",
        description="Generate AI first-frame still images for each scene before calling Sora2.",
    )
    download_variant: Optional[str] = Field(
        default=None,
        description="Optional override for the VIDEO_DOWNLOAD_VARIANT sent to the Sora client.",
    )
    scene_images: Optional[list[EncodedImagePayload]] = Field(
        default=None,
        alias="sceneImages",
        description="Optional list of uploaded scene start screen images (data URLs).",
    )
    generation_mode: Literal["parallel", "sequence"] = Field(
        default="parallel",
        alias="generationMode",
        description="Choose between parallel (default) or sequence rendering with chained references.",
    )
    refine_with_gpt5: bool = Field(
        default=False,
        alias="refineWithGpt5",
        description="When true, runs the submitted script through GPT-6.1-sol before planning scenes.",
    )
    scene_duration: int = Field(
        default=SORA_CLIP_DURATION_SECONDS,
        alias="sceneDuration",
        description="Scene runtime in seconds (Sora: 4/8/12, Kling/Jimeng: 5/10, Seedance: 4/5/8/10/12).",
    )
    visual_style: Literal["2d_animation", "3d_animation", "realistic"] = Field(
        default="3d_animation",
        alias="visualStyle",
        description="Visual style for all generated images and video: 2d_animation, 3d_animation, or realistic.",
    )

    @model_validator(mode="after")
    def validate_scene_duration(self) -> "StartWorkflowPayload":
        allowed = allowed_durations_for_provider(self.video_provider)
        if self.scene_duration not in allowed:
            allowed_str = ", ".join(str(value) for value in allowed)
            raise ValueError(
                f"{self.video_provider.upper()} supports scene durations of {allowed_str} seconds."
            )
        uploads = self.scene_images or []
        if len(uploads) > 32:
            raise ValueError("A maximum of 32 scene reference images can be uploaded per workflow.")
        if self.generation_mode == "sequence" and len(uploads) > 1:
            raise ValueError("Sequence mode accepts at most one uploaded reference image.")
        return self


class SingleVideoPayload(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    prompt: str = Field(..., description="Prompt text for a single video generation.")
    resolution: str = Field(
        default=DEFAULT_SORA_RESOLUTION,
        description="Output resolution (e.g. 720x1280).",
    )
    video_provider: VideoProviderName = Field(
        default=DEFAULT_VIDEO_PROVIDER,
        alias="videoProvider",
        description="Video backend to use for this single clip.",
    )
    duration: int = Field(
        default=8,
        description="Video duration in seconds (Sora: 4/8/12, Kling/Jimeng: 5/10, Seedance: 4/5/8/10/12).",
    )
    download_variant: Optional[str] = Field(
        default=None,
        alias="downloadVariant",
        description="Optional override for VIDEO_DOWNLOAD_VARIANT when downloading the asset.",
    )
    reference_image: Optional[EncodedImagePayload] = Field(
        default=None,
        alias="referenceImage",
        description="Optional input reference image encoded as a base64 data URL.",
    )
    reference_images: Optional[list[EncodedImagePayload]] = Field(
        default=None,
        alias="referenceImages",
        description="Optional list of additional reference still images (used when Kling multi-image runs are desired).",
    )
    refine_with_gpt5: bool = Field(
        default=False,
        alias="refineWithGpt5",
        description="When true, uses GPT-6.1-sol to refine the prompt before submitting to Sora2.",
    )

    @model_validator(mode="after")
    def validate_duration(self) -> "SingleVideoPayload":
        allowed = allowed_durations_for_provider(self.video_provider)
        if self.duration not in allowed:
            allowed_str = ", ".join(str(value) for value in allowed)
            raise ValueError(
                f"{self.video_provider.upper()} supports single clip durations of {allowed_str} seconds."
            )
        upload_count = len(self.reference_images or []) + (1 if self.reference_image else 0)
        if upload_count > 4:
            raise ValueError("A maximum of 4 reference images can be uploaded per request.")
        if self.video_provider != "kling" and upload_count > 1:
            raise ValueError(
                f"{self.video_provider.upper()} supports at most one reference image per request."
            )
        return self


class SceneRemixPayload(BaseModel):
    prompt: str = Field(
        ..., description="New descriptive prompt to remix the previously generated scene."
    )


class SceneRegeneratePayload(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="ignore")

    prompt: Optional[str] = Field(
        default=None,
        description="Optional updated prompt used when regenerating the scene.",
    )


class SingleVideoRemixPayload(BaseModel):
    prompt: str = Field(
        ..., description="New descriptive prompt for remixing the previous single-video output."
    )


class SceneSelectionPayload(BaseModel):
    version_id: str = Field(
        ..., alias="versionId", description="Identifier of the scene version to use in the final cut."
    )


class ReproduceSegmentRegeneratePayload(BaseModel):
    prompt: str


class SingleVideoSelectionPayload(BaseModel):
    version_id: str = Field(
        ..., alias="versionId", description="Identifier of the single-video version to activate."
    )


class RefineWorkflowPayload(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    script: str
    video_provider: VideoProviderName = Field(
        default=DEFAULT_VIDEO_PROVIDER,
        alias="videoProvider",
        description="Video backend that controls which GPT-6.1-sol template is used while refining.",
    )


class RefineSinglePayload(BaseModel):
    prompt: str


class WorkflowPromptTemplateResponse(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    video_provider: VideoProviderName = Field(
        default=DEFAULT_VIDEO_PROVIDER,
        alias="videoProvider",
    )
    template: str


class WorkflowPromptTemplateUpdate(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    template: str
    video_provider: VideoProviderName = Field(
        default=DEFAULT_VIDEO_PROVIDER,
        alias="videoProvider",
    )


class RefinedScriptResponse(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    refined_script: str = Field(..., alias="refinedScript")


class RefinedPromptResponse(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    refined_prompt: str = Field(..., alias="refinedPrompt")


@dataclass
class SceneDraft:
    order: int
    scene_number: int
    title: str
    description_lines: list[str]


@dataclass
class ScenePlan:
    order: int
    label: str
    duration_seconds: int
    description: str
    prompt_path: str
    prompt_text: str


@dataclass
class ScenePlanBatch:
    plans: list[ScenePlan]
    assigned_total_seconds: int


@dataclass
class SceneRender:
    order: int
    label: str
    duration_seconds: int
    prompt_path: str
    video_path: str
    log_path: str | None
    client_log_path: str | None
    prompt_text: Optional[str] = None
    version_id: str = field(default_factory=lambda: uuid.uuid4().hex)
    version_label: str = "Original"
    video_id: Optional[str] = None
    version_source: str = "original"
    last_frame_path: Optional[str] = None
    first_frame_path: Optional[str] = None


@dataclass
class SceneRenderBatch:
    renders: list[SceneRender]
    assigned_total_seconds: int


def get_active_single_version(job: SingleVideoJobState) -> Optional[SingleVideoVersionEntry]:
    if job.selected_version_id:
        for version in job.versions:
            if version.version_id == job.selected_version_id:
                return version
    if job.versions:
        return job.versions[-1]
    return None


__all__ = [
    "CharacterReference",
    "EncodedImagePayload",
    "JobMessageModel",
    "JobStatusResponse",
    "ProgressMessage",
    "ReproduceSegmentVersion",
    "ReproduceSegmentVersionModel",
    "SceneDraft",
    "ScenePlan",
    "ScenePlanBatch",
    "SceneRemixPayload",
    "SceneRegeneratePayload",
    "SceneRender",
    "SceneRenderBatch",
    "ReproduceSegmentRegeneratePayload",
    "SceneSelectionPayload",
    "SceneStatus",
    "SceneStatusModel",
    "SceneVersionEntry",
    "SceneVersionModel",
    "SingleVideoJobConfig",
    "SingleVideoJobResponse",
    "SingleVideoJobState",
    "SingleVideoPayload",
    "SingleVideoRemixPayload",
    "SingleVideoVersionEntry",
    "SingleVideoVersionModel",
    "VideoInsightJobResponse",
    "VideoInsightJobState",
    "VideoInsightKeyFrame",
    "VideoInsightKeyFrameModel",
    "VideoInsightSegment",
    "VideoInsightSegmentModel",
    "VideoReproduceJobResponse",
    "VideoReproduceJobState",
    "VideoReproduceSegment",
    "VideoReproduceSegmentModel",
    "VideoWorkflowRequest",
    "WorkflowJobState",
    "RefineWorkflowPayload",
    "RefineSinglePayload",
    "RefinedPromptResponse",
    "RefinedScriptResponse",
    "WorkflowPromptTemplateResponse",
    "WorkflowPromptTemplateUpdate",
    "get_active_single_version",
]
