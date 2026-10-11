export type JobMessage = {
  timestamp: string;
  stage: string;
  text: string;
};

export type SceneVersion = {
  version_id: string;
  label: string;
  source: string;
  prompt: string | null;
  prompt_path: string | null;
  video_path: string;
  video_url: string | null;
  log_path: string | null;
  client_log_path: string | null;
  video_id: string | null;
  created_at: string;
  last_frame_path?: string | null;
  last_frame_url?: string | null;
  first_frame_path?: string | null;
  first_frame_url?: string | null;
  error?: string | null;
};

export type SceneStatus = {
  order: number;
  label: string;
  duration_seconds: number;
  prompt_path: string;
  video_path: string;
  video_url: string | null;
  log_path: string | null;
  client_log_path: string | null;
  selected_version_id: string | null;
  versions: SceneVersion[];
  last_frame_path?: string | null;
  last_frame_url?: string | null;
  first_frame_path?: string | null;
  first_frame_url?: string | null;
};

export type SingleVideoVersion = {
  version_id: string;
  label: string;
  source: string;
  prompt: string;
  prompt_path: string;
  video_path: string;
  video_url: string | null;
  log_path: string | null;
  client_log_path: string | null;
  video_id: string | null;
  created_at: string;
};

export type JobStatus = {
  job_id: string;
  status: string;
  video_provider: string;
  resolution: string;
  stage: string | null;
  progress: number;
  started_at: string;
  completed_at: string | null;
  error: string | null;
  final_video_path: string | null;
  final_video_url: string | null;
  manifest: Record<string, unknown> | null;
  script?: string | null;
  script_path?: string | null;
  messages: JobMessage[];
  scenes: SceneStatus[];
  generation_mode: "parallel" | "sequence";
  scene_duration?: number;
  generate_ai_first_frame?: boolean;
  character_ref_urls?: string[];
};

export type SingleVideoJob = {
  job_id: string;
  status: string;
  progress: number;
  prompt: string;
  video_provider: string;
  resolution: string;
  duration: number;
  download_variant: string;
  started_at: string;
  completed_at: string | null;
  error: string | null;
  video_path: string | null;
  video_url: string | null;
  log_path: string | null;
  log_url: string | null;
  client_log_path: string | null;
  video_id: string | null;
  selected_version_id: string | null;
  versions: SingleVideoVersion[];
  messages: JobMessage[];
};

export type VideoInsightKeyFrame = {
  frame_id: string | null;
  timestamp: string | null;
  description: string | null;
  confidence: number | null;
  tags: string[];
};

export type VideoInsightSegment = {
  segment_id: string | null;
  label: string | null;
  start: string | null;
  end: string | null;
  start_seconds?: number | null;
  end_seconds?: number | null;
  summary: string | null;
  tags: string[];
  start_frame_path?: string | null;
  start_frame_url?: string | null;
  end_frame_path?: string | null;
  end_frame_url?: string | null;
};

export type VideoInsightJob = {
  job_id: string;
  filename: string;
  status: string;
  progress: number;
  content_type: string | null;
  notes: string | null;
  started_at: string;
  completed_at: string | null;
  error: string | null;
  video_path: string | null;
  video_url: string | null;
  result_path: string | null;
  result_url: string | null;
  key_frames: VideoInsightKeyFrame[];
  segments: VideoInsightSegment[];
  messages: JobMessage[];
};

export type ReproduceSegmentVersion = {
  version_id: string;
  prompt: string;
  status: string;
  video_path: string | null;
  video_url: string | null;
  log_path: string | null;
  log_url: string | null;
  client_log_path: string | null;
  client_log_url: string | null;
  task_id: string | null;
  error: string | null;
  created_at: string;
};

export type ReferenceFrameMode = "start_end" | "start_only";

export type VideoReproduceSegment = {
  segment_id: string | null;
  order: number;
  label: string;
  description: string;
  start_seconds: number;
  end_seconds: number;
  duration_seconds: number;
  kling_duration_seconds: number;
  tags: string[];
  start_frame_path: string | null;
  start_frame_url: string | null;
  end_frame_path: string | null;
  end_frame_url: string | null;
  stylized_start_frame_path: string | null;
  stylized_start_frame_url: string | null;
  stylized_end_frame_path: string | null;
  stylized_end_frame_url: string | null;
  image_prompt_path: string | null;
  image_prompt_url: string | null;
  original_video_path: string | null;
  original_video_url: string | null;
  status: string;
  selected_version_id: string | null;
  versions: ReproduceSegmentVersion[];
};

export type VideoReproduceJob = {
  job_id: string;
  filename: string;
  content_type: string | null;
  status: string;
  progress: number;
  thread_count: number;
  resolution: string;
  image_generation_backend: string;
  started_at: string;
  completed_at: string | null;
  error: string | null;
  notes: string | null;
  video_path: string | null;
  video_url: string | null;
  final_video_path: string | null;
  final_video_url: string | null;
  segmentation_backend: ReproduceSegmentationOption;
  reference_mode: ReferenceFrameMode;
  messages: JobMessage[];
  segments: VideoReproduceSegment[];
};

export type PromptPreview = {
  sceneLabel: string;
  versionLabel: string;
  prompt: string;
};

export type RefinementReview = {
  mode: "workflow" | "single";
  originalText: string;
  refinedText: string;
  videoProvider?: string;
};

export type TabId = "combined" | "single" | "insight" | "reproduce";

export type Language = "en" | "zh";

export type DurationOption = "4" | "5" | "8" | "10" | "12";

export type ImageGeneratorOption = "gpt-image-2.5" | "kling" | "original";

export type ReproduceSegmentationOption = "content_understanding" | "opencv";

export type VisualStyle = "2d_animation" | "3d_animation" | "realistic";
