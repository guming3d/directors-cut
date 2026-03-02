import {
  DurationOption,
  ImageGeneratorOption,
  Language,
  ReferenceFrameMode,
  ReproduceSegmentationOption,
  TabId,
} from "@/types/dashboard";
import {
  ArrowPathIcon,
  CheckCircleIcon,
  ClockIcon,
  DocumentTextIcon,
  ExclamationTriangleIcon,
  FilmIcon,
  PlayCircleIcon,
} from "@heroicons/react/24/outline";

export const STATUS_TOKENS: Record<string, { label: string; className: string }> = {
  queued: {
    label: "Queued",
    className: "text-zinc-600 dark:text-slate-300",
  },
  running: {
    label: "In Progress",
    className: "text-zinc-600 dark:text-slate-300",
  },
  completed: {
    label: "Completed",
    className: "text-emerald-600 dark:text-emerald-400",
  },
  failed: {
    label: "Failed",
    className: "text-rose-600 dark:text-rose-400",
  },
};

export const stageIcons: Record<string, typeof DocumentTextIcon> = {
  queued: ClockIcon,
  initializing: ArrowPathIcon,
  planning: DocumentTextIcon,
  rendering: FilmIcon,
  composing: PlayCircleIcon,
  running: ArrowPathIcon,
  completed: CheckCircleIcon,
  failed: ExclamationTriangleIcon,
};

export const terminalStatuses = new Set(["completed", "failed"]);

export const TABS: { id: TabId; label: string }[] = [
  { id: "combined", label: "Storyboard workspace" },
  { id: "single", label: "Single clip console" },
  { id: "insight", label: "Video insight" },
  { id: "reproduce", label: "Video reproduce" },
];

export const LANGUAGE_OPTIONS: Language[] = ["en", "zh"];

export const SORA_DURATION_OPTIONS = ["4", "8", "12"] as const;
export const KLING_DURATION_OPTIONS = ["5", "10"] as const;
export const resolutionOptions = ["720x1280", "1280x720", "1920x1080"] as const;
export const VIDEO_PROVIDER_OPTIONS = ["sora2", "kling", "jimeng"] as const;

export const IMAGE_GENERATOR_OPTIONS: { value: ImageGeneratorOption; label: string }[] = [
  { value: "gpt-image-1", label: "Azure gpt-image-1" },
  { value: "kling", label: "Kling image generation" },
  { value: "original", label: "Use original frames (no stylization)" },
];

export const SEGMENTATION_OPTIONS: { value: ReproduceSegmentationOption; label: string }[] = [
  { value: "content_understanding", label: "Azure Content Understanding" },
  { value: "opencv", label: "OpenCV (PySceneDetect)" },
];

export const REFERENCE_FRAME_OPTIONS: { value: ReferenceFrameMode; label: string }[] = [
  { value: "start_end", label: "Start + end frames" },
  { value: "start_only", label: "Start frame only" },
];

export const getDurationOptions = (provider: string | null): readonly DurationOption[] =>
  provider === "kling" || provider === "jimeng" ? KLING_DURATION_OPTIONS : SORA_DURATION_OPTIONS;

export const getImageGeneratorLabel = (value: string): string => {
  const option = IMAGE_GENERATOR_OPTIONS.find((item) => item.value === value);
  return option ? option.label : value;
};

export const getSegmentationLabel = (value: string): string => {
  const option = SEGMENTATION_OPTIONS.find((item) => item.value === value);
  return option ? option.label : value;
};

export const getReferenceModeLabel = (value: string): string => {
  const option = REFERENCE_FRAME_OPTIONS.find((item) => item.value === value);
  return option ? option.label : value;
};

export const AUTH_STORAGE_KEY = "sora2-authenticated";
export const AUTH_HEADER_STORAGE_KEY = "sora2-auth-header";
export const DEFAULT_WORKFLOW_PROMPT_TEMPLATE = [
  "**Scene 1 (Scene 1 title)**",
  "Scene 1 kling prompt content",
  "",
  "**Scene 2 (Scene 2 title)**",
  "Scene 2 kling prompt content",
  "",
  "**Scene 3 (Scene 3 title) **",
  "Scene 3 kling prompt content",
].join("\n");
