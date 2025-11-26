"use client";

import { ChangeEvent, FormEvent, useCallback, useEffect, useMemo, useState, useRef } from "react";
import Modal from "@/components/Modal";
import {
  ArrowPathIcon,
  CheckCircleIcon,
  ClockIcon,
  DocumentTextIcon,
  ExclamationTriangleIcon,
  FilmIcon,
  PaperAirplaneIcon,
  PlayCircleIcon,
  TrashIcon,
  SunIcon,
  MoonIcon,
} from "@heroicons/react/24/outline";
import Image from "next/image";

const API_BASE = (process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://localhost:8000").replace(/\/$/, "");

type JobMessage = {
  timestamp: string;
  stage: string;
  text: string;
};

type SceneVersion = {
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
};

type SceneStatus = {
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
};

type SingleVideoVersion = {
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

type JobStatus = {
  job_id: string;
  status: string;
  resolution: string;
  stage: string | null;
  progress: number;
  started_at: string;
  completed_at: string | null;
  error: string | null;
  final_video_path: string | null;
  final_video_url: string | null;
  manifest: Record<string, unknown> | null;
  messages: JobMessage[];
  scenes: SceneStatus[];
  generation_mode: "parallel" | "sequence";
  scene_duration?: number;
};

type SingleVideoJob = {
  job_id: string;
  status: string;
  progress: number;
  prompt: string;
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

type PromptPreview = {
  sceneLabel: string;
  versionLabel: string;
  prompt: string;
};

const STATUS_TOKENS: Record<string, { label: string; className: string }> = {
  queued: {
    label: "Queued",
    className: "text-slate-600 dark:text-slate-300",
  },
  running: {
    label: "In Progress",
    className: "text-slate-600 dark:text-slate-300",
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

const stageIcons: Record<string, typeof DocumentTextIcon> = {
  queued: ClockIcon,
  initializing: ArrowPathIcon,
  planning: DocumentTextIcon,
  rendering: FilmIcon,
  composing: PlayCircleIcon,
  running: ArrowPathIcon,
  completed: CheckCircleIcon,
  failed: ExclamationTriangleIcon,
};

const terminalStatuses = new Set(["completed", "failed"]);

type TabId = "combined" | "single";

type Language = "en" | "zh";

const TABS: { id: TabId; label: string }[] = [
  { id: "combined", label: "Storyboard workspace" },
  { id: "single", label: "Single clip console" },
];
const LANGUAGE_OPTIONS: Language[] = ["en", "zh"];

const durationOptions = ["4", "8", "12"] as const;
const resolutionOptions = ["720x1280", "1280x720", "1920x1080"] as const;

const AUTH_USERNAME = "user";
const AUTH_PASSWORD = "sora2isgreat";
const AUTH_STORAGE_KEY = "sora2-authenticated";

const zhTranslations: Record<string, string> = {
  "Across storyboard & single clips": "涵盖分镜与单条视频",
  "Active workflows": "运行中的工作流",
  "Accepted formats: PNG, JPG. Images remain local until submission.": "支持 PNG、JPG，文件在提交前仅存于本地。",
  "API target:": "API 地址：",
  "API ·": "API ·",
  "Available scenes": "可用场景",
  "Awaiting version refresh...": "正在刷新版本…",
  "Chinese": "中文",
  "Clip pending...": "片段准备中…",
  "Completed jobs": "已完成的任务",
  "Completed runs": "完成的运行",
  "Completed": "已完成",
  "Composing final video": "合成最终视频",
  "Composition manifest:": "合成清单：",
  "Delete Selected": "删除选中项",
  "Delete workflows": "删除工作流",
  "Download final video": "下载最终视频",
  "Download logs": "下载日志",
  "Duration (seconds)": "时长（秒）",
  "Generation mode": "生成模式",
  "Enable scene reference images": "启用场景参考图",
  "Enter password": "输入密码",
  "Enter prompt for remix": "输入重制提示词",
  "Enter script or storyboard prompt": "输入剧本或分镜提示词",
  "Enter username": "输入用户名",
  "English": "英文",
  "Failed to refresh single-video status. Confirm the backend is reachable.": "无法刷新单视频状态，请确认后端可访问。",
  "Failed to refresh workflow status. Check that the FastAPI server is reachable.": "无法刷新工作流状态，请检查 FastAPI 服务是否可访问。",
  "Failed to regenerate this scene. Confirm the backend is reachable.": "无法重新生成该场景，请确认后端可用。",
  "Failed": "失败",
  "Final Video": "最终视频",
  "Generate a clip to see it listed here.": "先生成一个片段即可在此查看。",
  "Generate video": "生成视频",
  "Generating video": "生成视频中",
  "Hint: user / sora2isgreat": "提示：user / sora2isgreat",
  "Idle": "空闲",
  "In Progress": "进行中",
  "Invalid username or password.": "用户名或密码不正确。",
  "Initializing single job...": "正在初始化单视频任务…",
  "Initializing workflow...": "正在初始化工作流…",
  "Job Timeline": "任务时间线",
  "Job ID": "任务 ID",
  "Language": "语言",
  "Mode": "模式",
  "No scene references": "不使用参考图",
  "Live jobs": "正在运行的任务",
  "Log file": "日志文件",
  "Mode:": "模式：",
  "Monitor orchestrator agents, track scene deliveries, and keep your queue aligned with Sora best practices.": "监控调度代理、跟踪场景交付，并让队列遵循 Sora 最佳实践。",
  "Orchestrate multi-scene storyboards, monitor scene renders, and review delivery timelines. The workspace syncs live with your FastAPI backend.": "编排多场景分镜，监控渲染并审阅交付时间线。该工作区与 FastAPI 后端实时同步。",
  "Parallel": "并行",
  "Paste your storyboard with scene headings.": "请按场景标题粘贴分镜。",
  "Password": "密码",
  "Plan scenes in parallel": "并行规划场景",
  "Planning scenes": "规划场景",
  "Preparing workflow": "准备工作流",
  "Progress": "进度",
  "Production Workspace": "制作工作区",
  "Quick actions": "快捷操作",
  "Queued": "排队中",
  "Recent Single Jobs": "最近的单视频任务",
  "Render scenes concurrently": "并行渲染场景",
  "Chain scenes sequentially": "串行衔接场景",

  "Remix prompt": "重制提示词",
  "Remix Scene": "重制场景",
  "Selecting...": "正在选择…",
  "Rendering video clips": "渲染视频片段",
  "Resolution": "分辨率",
  "Scene images enabled": "已启用场景参考图",
  "Scene reference": "场景参考图",
  "Scene references": "场景参考图",
  "Scene duration": "场景时长",
  "Scene Renders": "场景渲染",
  "Scene": "场景",
  "seconds": "秒",
  "Scenes this run": "本次场景数",
  "Scenes this workflow": "当前工作流场景数",
  "Current storyboard": "当前分镜",
  "Script-to-Video Control Room": "脚本到视频操控室",
  "System prompt": "系统提示词",
  "Since session start": "自会话开始以来",
  "Select workflows": "选择工作流",
  "Selected": "已选择",
  "Sequence": "串行",
  "Sign in to continue": "登录以继续",
  "Sign in": "登录",
  "Sign out": "退出登录",
  "Single clip console": "单条视频面板",
  "Single Sora2 Video": "单个 Sora2 视频",
  "Single queue": "单视频队列",
  "Single video prompt": "单视频提示词",
  "Single Video Status": "单视频状态",
  "Sora Agent Production Dashboard": "Sora 代理制作看板",
  "Sora Agent Workflow logo": "Sora 工作流徽标",
  "Storyboard Script": "分镜脚本",
  "Sora2 Studio": "Sora2 工作室",
  "Start Workflow": "启动工作流",
  "Starting Workflow...": "正在启动工作流…",
  "Start a workflow before regenerating scenes.": "请先启动工作流再重新生成场景。",
  "Start single video": "启动单视频",
  "Started": "开始时间",
  "Storyboard Workflow": "分镜工作流",
  "Storyboard workspace": "分镜工作区",
  "Status": "状态",
  "The Director agent will refine prompts following Sora2 best practices.": "导演代理会按照 Sora2 最佳实践优化提示词。",
  "Regenerate Final Video": "重新生成最终视频",
  "Regenerate Scene": "重新生成场景",
  "Regenerating...": "重新生成中…",
  "Open MP4 in new tab": "在新标签页打开 MP4",
  "Your browser does not support HTML5 video.": "您的浏览器不支持 HTML5 视频。",
  "Submit reference image": "上传参考图",
  "Submit remix": "提交重制",
  "Tracked workstreams": "追踪的工作项",
  "Tap a row to view job details.": "点击任意行以查看任务详情。",
  "Upload reference stills": "上传参考画面",
  "Use the provided username and password to access the dashboard.": "使用提供的用户名和密码登录控制面板。",
  "Use this version": "使用该版本",
  "Start your first workflow to see it listed here.": "启动第一个工作流后即可在此查看。",
  "Uses previous video ID:": "继承上一段视频 ID：",
  "Username": "用户名",
  "Video ID:": "视频 ID：",
  "Version video unavailable.": "版本视频暂不可用。",
  "Versions": "版本列表",
  "Describe how you want to remix this scene...": "描述你希望如何重制此场景…",
  "Prompt:": "提示词：",
  "Azure video ID:": "Azure 视频 ID：",
  "Waiting to start": "等待开始",
  "Workflow Status": "工作流状态",
  "Workflow Runs": "工作流记录",
  "Workflow Timeline": "工作流时间线",
  "Workflow complete": "工作流完成",
  "Workflow failed": "工作流失败",
  "Workflow health": "工作流健康情况",
  "Sequence mode accepts a single starting still, then reuses the last frame of each clip to guide the next scene for smoother continuity.": "串行模式使用单个起始画面，并复用上一段的最后一帧来引导下一个场景，实现更平滑的衔接。",
  "running or queued": "运行或排队中",
  "Video unavailable.": "视频暂不可用。",
  "Unavailable": "不可用",
  "Generated Video": "已生成视频",
  "Video pending...": "视频生成中…",
  "Generate Single Clip": "创建单条视频",
  "Submit a single prompt directly to the Sora2 API. Optionally attach a still reference, select the render resolution, and choose a supported clip duration.": "将单条提示直接提交给 Sora2 API，可附加参考图、选择分辨率并设置片段时长。",
  "Describe the scene, motion, cinematography, and mood for Sora2...": "描述场景、运动、镜头语言与情绪…",
  "Duration": "时长",
  "Generate Clip": "生成片段",
  "Reference image (optional)": "参考图（可选）",
  "Reference preview": "参考图预览",
  "Refine script with GPT-5": "使用 GPT-5 优化剧本",
  "Use GPT-5 to polish the storyboard before planning scenes.": "在规划场景前，用 GPT-5 打磨分镜文案。",
  "Refine prompt with GPT-5": "使用 GPT-5 优化提示词",
  "Let GPT-5 tighten the single-scene prompt before submitting.": "在提交前，让 GPT-5 精炼单条视频的提示词。",
  "Enabled": "已启用",
  "Disabled": "已关闭",
  "Last frame reference": "最后一帧参考图",
  "Remix Clip": "重制片段",
  "Remix Single Video": "重制单条视频",
  "Describe how you want to remix this clip...": "描述你希望如何重制该片段…",
  "Enter a prompt to remix this clip.": "请填写重制提示词。",
  "Unable to submit single-video remix. Confirm the backend endpoint is reachable.": "无法提交单视频重制请求，请确认后端可访问。",
  "Start a single-video job before requesting a remix.": "请先创建单视频任务再尝试重制。",
  "Sending Prompt...": "正在发送提示词…",
  "s": "秒",
  "View system prompt": "查看系统提示词",
};

const translateValue = (language: Language, value: string) => (language === "zh" ? zhTranslations[value] ?? value : value);

const readFileAsDataUrl = (file: File): Promise<string> =>
  new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => {
      if (typeof reader.result === "string") {
        resolve(reader.result);
      } else {
        reject(new Error("Unable to read file."));
      }
    };
    reader.onerror = () => reject(reader.error ?? new Error("Unable to read file."));
    reader.readAsDataURL(file);
  });

const stageLabel = (stage: string | null, language: Language) => {
  if (!stage) return translateValue(language, "Waiting to start");
  const normalized = stage.toLowerCase();
  switch (normalized) {
    case "planning":
      return translateValue(language, "Planning scenes");
    case "rendering":
      return translateValue(language, "Rendering video clips");
    case "composing":
      return translateValue(language, "Composing final video");
    case "initializing":
      return translateValue(language, "Preparing workflow");
    case "running":
      return translateValue(language, "Generating video");
    case "completed":
      return translateValue(language, "Workflow complete");
    case "failed":
      return translateValue(language, "Workflow failed");
    case "queued":
      return translateValue(language, "Queued");
    default:
      return translateValue(language, normalized.charAt(0).toUpperCase() + normalized.slice(1));
  }
};

const formatTimestamp = (value: string) => {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) {
    return value;
  }
  return `${date.toLocaleDateString()} ${date.toLocaleTimeString()}`;
};

const formatTimeDistance = (value: string | null) => {
  if (!value) return "—";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) {
    return "—";
  }
  return date.toLocaleString();
};

const buildAssetUrl = (path: string | null | undefined) => {
  if (!path) return null;
  return `${API_BASE}${path}`;
};

const getStatusToken = (status: string | undefined, language: Language) => {
  if (!status) {
    return {
      label: translateValue(language, "Idle"),
      className: "border border-slate-200 dark:border-slate-800 bg-white dark:bg-slate-900 text-slate-500",
    };
  }
  const token = STATUS_TOKENS[status];
  if (token) {
    return { ...token, label: translateValue(language, token.label) };
  }
  return {
    label: translateValue(language, status.charAt(0).toUpperCase() + status.slice(1)),
    className: "border border-slate-200 dark:border-slate-800 bg-white dark:bg-slate-900 text-slate-500",
  };
};

export default function Home() {
  const [activeTab, setActiveTab] = useState<TabId>("combined");
    const [theme, setTheme] = useState<"light" | "dark">("light");

  useEffect(() => {
    if (theme === "dark") {
      document.documentElement.classList.add("dark");
    } else {
      document.documentElement.classList.remove("dark");
    }
  }, [theme]);

  const ThemeToggle = () => (
    <button
      type="button"
      onClick={() => setTheme(theme === "dark" ? "light" : "dark")}
      className="rounded-full border border-slate-300 bg-white p-1.5 text-slate-600 transition hover:bg-slate-100 hover:text-slate-900 dark:border-slate-700 dark:bg-slate-800 dark:text-slate-400 dark:hover:bg-slate-700 dark:hover:text-slate-200"
      title={theme === "dark" ? "Switch to light mode" : "Switch to dark mode"}
    >
      {theme === "dark" ? <SunIcon className="h-4 w-4" /> : <MoonIcon className="h-4 w-4" />}
    </button>
  );

  const [language, setLanguage] = useState<Language>("en");
  const [isAuthenticated, setIsAuthenticated] = useState(false);
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [authError, setAuthError] = useState<string | null>(null);
  const translate = useCallback((value: string) => translateValue(language, value), [language]);
  const LanguageToggle = () => (
    <div className="flex items-center gap-2">
      <span className="text-[11px] font-semibold uppercase tracking-[0.3em] text-slate-500">{translate("Language")}</span>
      <div className="flex overflow-hidden rounded-full border border-slate-300 dark:border-slate-700">
        {LANGUAGE_OPTIONS.map((locale) => {
          const isActive = language === locale;
          return (
            <button
              key={locale}
              type="button"
              onClick={() => setLanguage(locale)}
              className={`px-3 py-1 text-xs font-semibold transition ${isActive ? "bg-slate-100 text-slate-900" : "bg-slate-100 dark:bg-slate-800 text-slate-500 dark:text-slate-400 hover:bg-slate-200 dark:bg-slate-700"}`}
            >
              {translate(locale === "en" ? "English" : "Chinese")}
            </button>
          );
        })}
      </div>
    </div>
  );

  useEffect(() => {
    if (typeof window === "undefined") return;
    const storedLanguage = window.localStorage.getItem("sora2-language");
    if (storedLanguage === "en" || storedLanguage === "zh") {
      setLanguage(storedLanguage);
    }
  }, []);

  useEffect(() => {
    if (typeof window === "undefined") return;
    window.localStorage.setItem("sora2-language", language);
  }, [language]);

  // Combined workflow state
  const [script, setScript] = useState("");
  const [useSceneImages, setUseSceneImages] = useState(false);
  const [generationMode, setGenerationMode] = useState<"parallel" | "sequence">("parallel");
  const [combinedResolution, setCombinedResolution] = useState<(typeof resolutionOptions)[number]>(resolutionOptions[0]);
  const [sceneDuration, setSceneDuration] = useState<(typeof durationOptions)[number]>(durationOptions[1]);
  const [sceneImageFiles, setSceneImageFiles] = useState<File[]>([]);
  const [sceneImagePreviews, setSceneImagePreviews] = useState<string[]>([]);
  const [jobs, setJobs] = useState<JobStatus[]>([]);
  const [activeJobId, setActiveJobId] = useState<string | null>(null);
  const [job, setJob] = useState<JobStatus | null>(null);
  const [isSubmittingCombined, setIsSubmittingCombined] = useState(false);
  const [combinedError, setCombinedError] = useState<string | null>(null);
  const [selectedCombinedJobs, setSelectedCombinedJobs] = useState<Set<string>>(new Set());
  const [remixPrompts, setRemixPrompts] = useState<Record<number, string>>({});
  const [remixSubmitting, setRemixSubmitting] = useState<Record<number, boolean>>({});
  const [selectionSubmitting, setSelectionSubmitting] = useState<Record<string, boolean>>({});
  const [isRecomposing, setIsRecomposing] = useState(false);
  const [refineWorkflowWithGpt5, setRefineWorkflowWithGpt5] = useState(false);
  const [sceneRegenerating, setSceneRegenerating] = useState<Record<number, boolean>>({});

  // Single video state
  const [singlePrompt, setSinglePrompt] = useState("");
  const [singleResolution, setSingleResolution] = useState<(typeof resolutionOptions)[number]>(resolutionOptions[0]);
  const [singleDuration, setSingleDuration] = useState<(typeof durationOptions)[number]>(durationOptions[1]);
  const [singleReferenceFile, setSingleReferenceFile] = useState<File | null>(null);
  const [singleReferencePreview, setSingleReferencePreview] = useState<string | null>(null);
  const [singleJobs, setSingleJobs] = useState<SingleVideoJob[]>([]);
  const [activeSingleJobId, setActiveSingleJobId] = useState<string | null>(null);
  const [singleJob, setSingleJob] = useState<SingleVideoJob | null>(null);
  const [isSubmittingSingle, setIsSubmittingSingle] = useState(false);
  const [singleError, setSingleError] = useState<string | null>(null);
  const [selectedSingleJobs, setSelectedSingleJobs] = useState<Set<string>>(new Set());
  const [refineSingleWithGpt5, setRefineSingleWithGpt5] = useState(false);
  const [singleRemixPrompt, setSingleRemixPrompt] = useState("");
  const [singleRemixSubmitting, setSingleRemixSubmitting] = useState(false);
  const [singleSelectionSubmitting, setSingleSelectionSubmitting] = useState<Record<string, boolean>>({});
  const [promptPreview, setPromptPreview] = useState<PromptPreview | null>(null);

  // Modal states
  const [isCreateWorkflowModalOpen, setIsCreateWorkflowModalOpen] = useState(false);
  const [isCreateSingleVideoModalOpen, setIsCreateSingleVideoModalOpen] = useState(false);
  const [isJobDetailModalOpen, setIsJobDetailModalOpen] = useState(false);

  const loadCombinedJobs = useCallback(async () => {
    try {
      const response = await fetch(`${API_BASE}/api/workflows`);
      if (!response.ok) {
        throw new Error(await response.text());
      }
      const data: JobStatus[] = await response.json();
      setJobs(data);
      if (data.length === 0) {
        setJob(null);
        setActiveJobId(null);
      } else {
        setJob((current) =>
          current && data.some((item) => item.job_id === current.job_id) ? current : data[0],
        );
        setActiveJobId((current) =>
          current && data.some((item) => item.job_id === current) ? current : data[0].job_id,
        );
      }
      setSelectedCombinedJobs((previous) => {
        if (previous.size === 0) return previous;
        const next = new Set<string>();
        data.forEach((item) => {
          if (previous.has(item.job_id)) {
            next.add(item.job_id);
          }
        });
        return next;
      });
      setCombinedError(null);
    } catch (err) {
      console.error(err);
      setCombinedError(
        "Unable to load existing workflows. Start one below or verify the API URL.",
      );
    }
  }, []);

  const loadSingleJobs = useCallback(async () => {
    try {
      const response = await fetch(`${API_BASE}/api/single-videos`);
      if (!response.ok) {
        throw new Error(await response.text());
      }
      const data: SingleVideoJob[] = await response.json();
      setSingleJobs(data);
      if (data.length === 0) {
        setSingleJob(null);
        setActiveSingleJobId(null);
      } else {
        setSingleJob((current) =>
          current && data.some((item) => item.job_id === current.job_id) ? current : data[0],
        );
        setActiveSingleJobId((current) =>
          current && data.some((item) => item.job_id === current) ? current : data[0].job_id,
        );
      }
      setSelectedSingleJobs((previous) => {
        if (previous.size === 0) return previous;
        const next = new Set<string>();
        data.forEach((item) => {
          if (previous.has(item.job_id)) {
            next.add(item.job_id);
          }
        });
        return next;
      });
      setSingleError(null);
    } catch (err) {
      console.error(err);
      setSingleError("Unable to load single-video jobs. Verify the API endpoint.");
    }
  }, []);

  useEffect(() => {
    if (!isAuthenticated) {
      setJobs([]);
      setJob(null);
      setActiveJobId(null);
      setSingleJobs([]);
      setSingleJob(null);
      setActiveSingleJobId(null);
      return;
    }
    void loadCombinedJobs();
    void loadSingleJobs();
  }, [isAuthenticated, loadCombinedJobs, loadSingleJobs]);

  useEffect(() => {
    if (!activeJobId || !isAuthenticated) return;
    let disposed = false;
    let timeout: ReturnType<typeof setTimeout> | undefined;

    const scheduleNext = (delay: number) => {
      if (disposed) return;
      timeout = setTimeout(fetchStatus, delay);
    };

    const fetchStatus = async () => {
      try {
        const response = await fetch(`${API_BASE}/api/workflows/${activeJobId}`);
        if (!response.ok) {
          throw new Error(await response.text());
        }
        const data: JobStatus = await response.json();
        if (disposed) {
          return;
        }

        setJob(data);
        setJobs((prev) => {
          const index = prev.findIndex((item) => item.job_id === data.job_id);
          if (index !== -1) {
            const next = [...prev];
            next[index] = data;
            return next;
          }
          return [data, ...prev];
        });
        setCombinedError(null);
        const delay = terminalStatuses.has(data.status) ? 12000 : 4000;
        scheduleNext(delay);
      } catch (err) {
        console.error(err);
        if (!disposed) {
          setCombinedError(
            "Failed to refresh workflow status. Check that the FastAPI server is reachable.",
          );
          scheduleNext(8000);
        }
      }
    };

    void fetchStatus();

    return () => {
      disposed = true;
      if (timeout) {
        clearTimeout(timeout);
      }
    };
  }, [activeJobId, isAuthenticated]);

  useEffect(() => {
    if (!activeSingleJobId || !isAuthenticated) return;
    let disposed = false;
    let interval: ReturnType<typeof setInterval> | undefined;

    const fetchStatus = async () => {
      try {
        const response = await fetch(`${API_BASE}/api/single-videos/${activeSingleJobId}`);
        if (!response.ok) {
          throw new Error(await response.text());
        }
        const data: SingleVideoJob = await response.json();
        if (disposed) {
          return;
        }

        setSingleJob(data);
        setSingleJobs((prev) => {
          const index = prev.findIndex((item) => item.job_id === data.job_id);
          if (index !== -1) {
            const next = [...prev];
            next[index] = data;
            return next;
          }
          return [data, ...prev];
        });
        setSingleError(null);
        if (terminalStatuses.has(data.status) && interval) {
          clearInterval(interval);
          interval = undefined;
        }
      } catch (err) {
        console.error(err);
        if (!disposed) {
          setSingleError("Failed to refresh single-video status. Confirm the backend is reachable.");
        }
      }
    };

    void fetchStatus();
    interval = setInterval(fetchStatus, 4000);

    return () => {
      disposed = true;
      if (interval) {
        clearInterval(interval);
      }
    };
  }, [activeSingleJobId, isAuthenticated]);

  const combinedProgressPercent = useMemo(() => {
    if (!job) return 0;
    const value = Number.isFinite(job.progress) ? job.progress : 0;
    return Math.max(0, Math.min(100, Math.round(value * 100)));
  }, [job]);

  const singleProgressPercent = useMemo(() => {
    if (!singleJob) return 0;
    const value = Number.isFinite(singleJob.progress) ? singleJob.progress : 0;
    return Math.max(0, Math.min(100, Math.round(value * 100)));
  }, [singleJob]);

  const finalVideoUrl = buildAssetUrl(job?.final_video_url);
  const canRecompose = Boolean(job?.scenes?.length && job?.status === "completed");
  const currentStatusToken = getStatusToken(job?.status, language);
  const showSequenceReferences = job?.generation_mode === "sequence";

  const singleVideoUrl = buildAssetUrl(singleJob?.video_url);
  const singleStatusToken = getStatusToken(singleJob?.status, language);


  const panelCardClass =
    "rounded-md border border-slate-200 dark:border-slate-800 bg-white/80 dark:bg-slate-900/50 p-6 shadow-2xl backdrop-blur-xl";
  const pillButtonClass =
    "rounded-full border border-slate-300 dark:border-slate-700 bg-slate-100/50 dark:bg-slate-100 dark:bg-slate-800/50 px-3 py-1.5 text-xs font-semibold uppercase tracking-wide text-slate-500 dark:text-slate-400";
  const displayApiBase = API_BASE.replace(/^https?:\/\//, "");
  const scrollToWorkspaceTop = () => {
    if (typeof window !== "undefined") {
      window.scrollTo({ top: 0, behavior: "smooth" });
    }
  };



  useEffect(() => {
    if (typeof window === "undefined") return;
    const stored = window.localStorage.getItem(AUTH_STORAGE_KEY);
    if (stored === "true") {
      setIsAuthenticated(true);
    }
  }, []);

  const handleLogin = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    if (username.trim() === AUTH_USERNAME && password === AUTH_PASSWORD) {
      setIsAuthenticated(true);
      setAuthError(null);
      if (typeof window !== "undefined") {
        window.localStorage.setItem(AUTH_STORAGE_KEY, "true");
      }
    } else {
      setAuthError("Invalid username or password.");
    }
  };

  const handleLogout = () => {
    setIsAuthenticated(false);
    if (typeof window !== "undefined") {
      window.localStorage.removeItem(AUTH_STORAGE_KEY);
    }
    setUsername("");
    setPassword("");
  };

  const handleSceneImagesChange = async (event: ChangeEvent<HTMLInputElement>) => {
    if (!useSceneImages) {
      setSceneImageFiles([]);
      setSceneImagePreviews([]);
      return;
    }
    let files = event.target.files ? Array.from(event.target.files) : [];
    if (generationMode === "sequence" && files.length > 1) {
      setCombinedError("Sequence mode accepts only one reference image; only the first file was kept.");
      files = files.slice(0, 1);
    }
    setSceneImageFiles(files);
    if (!files.length) {
      setSceneImagePreviews([]);
      return;
    }
    try {
      const previews = await Promise.all(files.map(readFileAsDataUrl));
      setSceneImagePreviews(previews);
    } catch (err) {
      console.error(err);
      setSceneImagePreviews([]);
      setCombinedError("Unable to preview uploaded images. Please try different files.");
    }
  };

  const handleReferenceImageChange = async (event: ChangeEvent<HTMLInputElement>) => {
    const file = event.target.files?.[0] ?? null;
    setSingleReferenceFile(file);
    if (!file) {
      setSingleReferencePreview(null);
      return;
    }
    try {
      const preview = await readFileAsDataUrl(file);
      setSingleReferencePreview(preview);
    } catch (err) {
      console.error(err);
      setSingleReferencePreview(null);
      setSingleError("Unable to preview the reference image. Try a different file.");
    }
  };

  const submitRemix = async (sceneOrder: number) => {
    if (!job?.job_id) {
      setCombinedError("Start a workflow before remixing scenes.");
      return;
    }
    const prompt = (remixPrompts[sceneOrder] ?? "").trim();
    if (!prompt) {
      setCombinedError("Enter a prompt to remix this scene.");
      return;
    }
    setCombinedError(null);
    setRemixSubmitting((prev) => ({ ...prev, [sceneOrder]: true }));
    try {
      const response = await fetch(`${API_BASE}/api/workflows/${job.job_id}/scenes/${sceneOrder}/remix`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ prompt }),
      });
      if (!response.ok) {
        throw new Error(await response.text());
      }
      setRemixPrompts((prev) => ({ ...prev, [sceneOrder]: "" }));
    } catch (err) {
      console.error(err);
      setCombinedError("Unable to submit remix. Confirm the remix endpoint is available and try again.");
    } finally {
      setRemixSubmitting((prev) => ({ ...prev, [sceneOrder]: false }));
    }
  };

  const handleRemixSubmit = async (event: FormEvent<HTMLFormElement>, sceneOrder: number) => {
    event.preventDefault();
    await submitRemix(sceneOrder);
  };

  const handleRegenerateScene = async (sceneOrder: number) => {
    if (!job?.job_id) {
      setCombinedError("Start a workflow before regenerating scenes.");
      return;
    }
    setCombinedError(null);
    setSceneRegenerating((prev) => ({ ...prev, [sceneOrder]: true }));
    try {
      const response = await fetch(`${API_BASE}/api/workflows/${job.job_id}/scenes/${sceneOrder}/regenerate`, {
        method: "POST",
      });
      if (!response.ok) {
        throw new Error(await response.text());
      }
      const data: JobStatus = await response.json();
      setJob(data);
      setJobs((prev) => {
        const others = prev.filter((item) => item.job_id !== data.job_id);
        return [data, ...others];
      });
    } catch (err) {
      console.error(err);
      setCombinedError("Failed to regenerate this scene. Confirm the backend is reachable.");
    } finally {
      setSceneRegenerating((prev) => ({ ...prev, [sceneOrder]: false }));
    }
  };

  const handleSelectVersion = async (sceneOrder: number, versionId: string) => {
    if (!job?.job_id) {
      setCombinedError("Start a workflow before selecting scene versions.");
      return;
    }
    setCombinedError(null);
    setSelectionSubmitting((prev) => ({ ...prev, [versionId]: true }));
    try {
      const response = await fetch(`${API_BASE}/api/workflows/${job.job_id}/scenes/${sceneOrder}/select`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ versionId }),
      });
      if (!response.ok) {
        throw new Error(await response.text());
      }
    } catch (err) {
      console.error(err);
      setCombinedError("Unable to update the selected version. Please try again.");
    } finally {
      setSelectionSubmitting((prev) => ({ ...prev, [versionId]: false }));
    }
  };

  const handleRecompose = async () => {
    if (!job?.job_id || !canRecompose) {
      return;
    }
    setCombinedError(null);
    setIsRecomposing(true);
    try {
      const response = await fetch(`${API_BASE}/api/workflows/${job.job_id}/recompose`, {
        method: "POST",
      });
      if (!response.ok) {
        throw new Error(await response.text());
      }
    } catch (err) {
      console.error(err);
      setCombinedError("Unable to regenerate the final video. Confirm the backend recompose endpoint is running.");
    } finally {
      setIsRecomposing(false);
    }
  };

  const handleCombinedSubmit = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    const trimmed = script.trim();
    if (!trimmed) {
      setCombinedError("Please provide a storyboard script before starting the workflow.");
      return;
    }

    setIsSubmittingCombined(true);
    setCombinedError(null);
    try {
      const payload: Record<string, unknown> = { script: trimmed };
      payload.useSceneImages = useSceneImages;
      payload.resolution = combinedResolution;
      payload.generationMode = generationMode;
      payload.refineWithGpt5 = refineWorkflowWithGpt5;
      payload.sceneDuration = Number(sceneDuration);
      if (useSceneImages && sceneImageFiles.length) {
        const uploads = await Promise.all(
          sceneImageFiles.map(async (file) => ({
            filename: file.name,
            dataUrl: await readFileAsDataUrl(file),
            contentType: file.type,
          })),
        );
        payload.sceneImages = uploads;
      }

      const response = await fetch(`${API_BASE}/api/workflows`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });
      if (!response.ok) {
        throw new Error(await response.text());
      }
      const data: JobStatus = await response.json();
      setJob(data);
      setActiveJobId(data.job_id);
      setJobs((prev) => {
        const others = prev.filter((item) => item.job_id !== data.job_id);
        return [data, ...others];
      });
      setSelectedCombinedJobs(new Set());
      setIsCreateWorkflowModalOpen(false);
    } catch (err) {
      console.error(err);
      setCombinedError(
        "Unable to start workflow. Confirm the FastAPI server is running and accepts requests.",
      );
    } finally {
      setIsSubmittingCombined(false);
    }
  };

  const handleSingleSubmit = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    const trimmed = singlePrompt.trim();
    if (!trimmed) {
      setSingleError("Enter a prompt before requesting a Sora2 video.");
      return;
    }

    setIsSubmittingSingle(true);
    setSingleError(null);
    try {
      const payload: Record<string, unknown> = {
        prompt: trimmed,
        resolution: singleResolution,
        duration: Number(singleDuration),
        refineWithGpt5: refineSingleWithGpt5,
      };

      if (singleReferenceFile) {
        payload.referenceImage = {
          filename: singleReferenceFile.name,
          dataUrl: await readFileAsDataUrl(singleReferenceFile),
          contentType: singleReferenceFile.type,
        };
      }

      const response = await fetch(`${API_BASE}/api/single-videos`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });
      if (!response.ok) {
        throw new Error(await response.text());
      }
      const data: SingleVideoJob = await response.json();
      setSingleJob(data);
      setActiveSingleJobId(data.job_id);
      setSingleJobs((prev) => {
        const others = prev.filter((item) => item.job_id !== data.job_id);
        return [data, ...others];
      });
      setSelectedSingleJobs(new Set());
      setIsCreateSingleVideoModalOpen(false);
    } catch (err) {
      console.error(err);
      setSingleError("Unable to start the single-video job. Verify the backend is reachable.");
    } finally {
      setIsSubmittingSingle(false);
    }
  };

  const handleSingleRemixSubmit = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    if (!singleJob?.job_id) {
      setSingleError("Start a single-video job before requesting a remix.");
      return;
    }
    const trimmed = singleRemixPrompt.trim();
    if (!trimmed) {
      setSingleError("Enter a prompt to remix this clip.");
      return;
    }

    setSingleError(null);
    setSingleRemixSubmitting(true);
    try {
      const response = await fetch(`${API_BASE}/api/single-videos/${singleJob.job_id}/remix`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ prompt: trimmed }),
      });
      if (!response.ok) {
        throw new Error(await response.text());
      }
      const data: SingleVideoJob = await response.json();
      setSingleJob(data);
      setSingleJobs((prev) => {
        const others = prev.filter((item) => item.job_id !== data.job_id);
        return [data, ...others];
      });
      setSingleRemixPrompt("");
    } catch (err) {
      console.error(err);
      setSingleError("Unable to submit single-video remix. Confirm the backend endpoint is reachable.");
    } finally {
      setSingleRemixSubmitting(false);
    }
  };

  const handleSelectSingleVersion = async (versionId: string) => {
    if (!singleJob?.job_id) {
      setSingleError("Start a single-video job before selecting versions.");
      return;
    }
    setSingleError(null);
    setSingleSelectionSubmitting((prev) => ({ ...prev, [versionId]: true }));
    try {
      const response = await fetch(`${API_BASE}/api/single-videos/${singleJob.job_id}/select`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ versionId }),
      });
      if (!response.ok) {
        throw new Error(await response.text());
      }
      const data: SingleVideoJob = await response.json();
      setSingleJob(data);
      setSingleJobs((prev) => {
        const others = prev.filter((item) => item.job_id !== data.job_id);
        return [data, ...others];
      });
    } catch (err) {
      console.error(err);
      setSingleError("Unable to update selected version. Please try again.");
    } finally {
      setSingleSelectionSubmitting((prev) => ({ ...prev, [versionId]: false }));
    }
  };

  const toggleCombinedSelection = (jobId: string) => {
    setSelectedCombinedJobs((prev) => {
      const next = new Set(prev);
      if (next.has(jobId)) {
        next.delete(jobId);
      } else {
        next.add(jobId);
      }
      return next;
    });
  };

  const toggleSingleSelection = (jobId: string) => {
    setSelectedSingleJobs((prev) => {
      const next = new Set(prev);
      if (next.has(jobId)) {
        next.delete(jobId);
      } else {
        next.add(jobId);
      }
      return next;
    });
  };

  const handleDeleteCombinedJobs = async () => {
    if (selectedCombinedJobs.size === 0) {
      return;
    }
    let deletionError: string | null = null;
    for (const jobId of Array.from(selectedCombinedJobs)) {
      try {
        const response = await fetch(`${API_BASE}/api/workflows/${jobId}`, {
          method: "DELETE",
        });
        if (!response.ok) {
          throw new Error(await response.text());
        }
      } catch (err) {
        console.error(err);
        deletionError = "Unable to delete one or more workflows. Ensure they are not running.";
      }
    }
    await loadCombinedJobs();
    setSelectedCombinedJobs(new Set());
    if (deletionError) {
      setCombinedError(deletionError);
    }
  };

  const handleDeleteSingleJobs = async () => {
    if (selectedSingleJobs.size === 0) {
      return;
    }
    let deletionError: string | null = null;
    for (const jobId of Array.from(selectedSingleJobs)) {
      try {
        const response = await fetch(`${API_BASE}/api/single-videos/${jobId}`, {
          method: "DELETE",
        });
        if (!response.ok) {
          throw new Error(await response.text());
        }
      } catch (err) {
        console.error(err);
        deletionError = "Unable to delete one or more single-video jobs. Ensure they are not running.";
      }
    }
    await loadSingleJobs();
    setSelectedSingleJobs(new Set());
    if (deletionError) {
      setSingleError(deletionError);
    }
  };

  if (!isAuthenticated) {
    return (
      <div className="flex min-h-screen items-center justify-center bg-slate-50 dark:bg-slate-950 px-4 py-10 text-slate-900 dark:text-slate-100">
        <div className="w-full max-w-md rounded-md border border-slate-200 dark:border-slate-800 bg-white dark:bg-slate-900 p-8 shadow-2xl">
          <div className="mb-6 flex flex-wrap items-center justify-between gap-3">
            <p className="text-[11px] font-semibold uppercase tracking-[0.3em] text-slate-500">{translate("Sora2 Studio")}</p>
            <div className="flex items-center gap-3"><ThemeToggle /><LanguageToggle /></div>
          </div>
          <div className="space-y-2 text-center">
            <h1 className="text-2xl font-semibold text-slate-900 dark:text-slate-100">{translate("Sign in to continue")}</h1>
            <p className="text-sm text-slate-500 dark:text-slate-400">{translate("Use the provided username and password to access the dashboard.")}</p>
          </div>
          <form className="mt-6 space-y-4" onSubmit={handleLogin}>
            <div>
              <label htmlFor="username" className="text-sm font-medium text-slate-600 dark:text-slate-300">
                {translate("Username")}
              </label>
              <input
                id="username"
                type="text"
                value={username}
                onChange={(event) => setUsername(event.target.value)}
                className="mt-1 w-full rounded border border-slate-300 dark:border-slate-700 bg-slate-100 dark:bg-slate-800 px-3 py-2 text-sm text-slate-900 dark:text-slate-100 focus:border-slate-500 focus:outline-none focus:ring-0"
                placeholder={translate("Enter username")}
                autoComplete="username"
              />
            </div>
            <div>
              <label htmlFor="password" className="text-sm font-medium text-slate-600 dark:text-slate-300">
                {translate("Password")}
              </label>
              <input
                id="password"
                type="password"
                value={password}
                onChange={(event) => setPassword(event.target.value)}
                className="mt-1 w-full rounded border border-slate-300 dark:border-slate-700 bg-slate-100 dark:bg-slate-800 px-3 py-2 text-sm text-slate-900 dark:text-slate-100 focus:border-slate-500 focus:outline-none focus:ring-0"
                placeholder={translate("Enter password")}
                autoComplete="current-password"
              />
            </div>
            {authError ? <p className="text-sm text-rose-500">{translate(authError)}</p> : null}
            <button
              type="submit"
              className="w-full rounded bg-slate-900 px-4 py-2 text-sm font-semibold text-white dark:bg-slate-100 dark:text-slate-900 transition hover:bg-slate-700"
            >
              {translate("Sign in")}
            </button>
          </form>
        </div>
      </div>
    );
  }

  return (
    <div className="relative min-h-screen overflow-hidden bg-slate-50 dark:bg-slate-950 text-slate-900 dark:text-slate-100">
      <div className="pointer-events-none absolute inset-0">
        <div className="absolute inset-0 bg-gradient-to-b from-white dark:from-slate-900 via-transparent to-slate-100 dark:to-slate-950" />
        <div className="absolute left-[-18%] top-[10%] h-[420px] w-[420px] rounded-full bg-gradient-to-br from-indigo-500/10 via-purple-500/5 to-transparent blur-3xl" />
        <div className="absolute right-[-12%] top-[40%] h-[380px] w-[380px] rounded-full bg-gradient-to-tr from-blue-500/10 via-cyan-500/5 to-transparent blur-[140px]" />
      </div>

      <header className="sticky top-0 z-20 flex items-center justify-between border-b border-slate-300 bg-slate-200/90 px-6 py-3 backdrop-blur-xl dark:border-slate-700 dark:bg-slate-800/90">
        <div className="flex items-center gap-4">
          <div className="flex items-center gap-3">
            <Image
              src="/sora2_logo.jpg"
              alt={translate("Sora Agent Workflow logo")}
              width={40}
              height={40}
              className="h-10 w-10 rounded border border-slate-300 object-cover shadow-sm shadow-black/50 dark:border-slate-700"
              priority
            />
            <div>
              <p className="text-[10px] font-semibold uppercase tracking-[0.35em] text-slate-500">{translate("Sora2 Studio")}</p>
            </div>
          </div>
          <div className="h-6 w-px bg-slate-400 dark:bg-slate-600" />
          <div className="flex items-center gap-1 rounded-lg bg-white p-1 dark:bg-slate-900">
            {TABS.map(({ id, label }) => {
              const isActive = activeTab === id;
              return (
                <button
                  key={id}
                  type="button"
                  onClick={() => {
                    setActiveTab(id);
                    scrollToWorkspaceTop();
                  }}
                  className={`rounded-md px-3 py-1.5 text-xs font-semibold transition ${
                    isActive
                      ? "bg-slate-100 text-slate-900 shadow-sm dark:bg-slate-700 dark:text-slate-100"
                      : "text-slate-500 hover:text-slate-900 dark:text-slate-400 dark:hover:text-slate-200"
                  }`}
                >
                  {translate(label)}
                </button>
              );
            })}
          </div>
        </div>

        <div className="flex items-center gap-3">
          <div className="flex items-center gap-3"><ThemeToggle /><LanguageToggle /></div>
          <span className={`${pillButtonClass} whitespace-nowrap`}>
            {translate("API ·")} <span className="font-mono text-[11px] text-slate-500">{displayApiBase || API_BASE}</span>
          </span>
          <button
            type="button"
            onClick={handleLogout}
            className="rounded-full border border-slate-300 px-3 py-1.5 text-xs font-semibold uppercase tracking-wide text-slate-500 transition hover:border-slate-400 hover:text-slate-900 dark:border-slate-700 dark:text-slate-400 dark:hover:border-slate-600 dark:hover:text-slate-100"
          >
            {translate("Sign out")}
          </button>
        </div>
      </header>

      <div className="relative mx-auto min-h-screen w-full px-4 pb-10 pt-6 max-w-7xl">
        <div className="mb-6 flex justify-between items-center">
          <h1 className="text-2xl font-bold text-slate-900 dark:text-slate-100">
            {activeTab === "combined" ? translate("Workflow Runs") : translate("Recent Single Jobs")}
          </h1>
          <button
            onClick={() => activeTab === "combined" ? setIsCreateWorkflowModalOpen(true) : setIsCreateSingleVideoModalOpen(true)}
            className="rounded-md bg-slate-900 px-4 py-2 text-sm font-semibold text-white dark:bg-slate-100 dark:text-slate-900 shadow-sm hover:bg-slate-700 transition"
          >
            {activeTab === "combined" ? translate("Start Workflow") : translate("Generate Single Clip")}
          </button>
        </div>

        <div className={`${panelCardClass} overflow-hidden`}>
          {activeTab === "combined" && (
            <>
              <div className="flex items-center justify-end mb-4">
                  <button
                    type="button"
                    onClick={handleDeleteCombinedJobs}
                    disabled={!selectedCombinedJobs.size}
                    className="inline-flex items-center gap-1 rounded border border-rose-200 dark:border-rose-900/50 bg-rose-50 dark:bg-rose-950/30 px-3 py-1.5 text-xs font-semibold text-rose-400 transition disabled:cursor-not-allowed disabled:opacity-50"
                  >
                    <TrashIcon className="h-4 w-4" />
                    {translate("Delete Selected")}
                  </button>
              </div>
              {jobs.length === 0 ? (
                <p className="text-sm text-slate-500 p-4">{translate("Start your first workflow to see it listed here.")}</p>
              ) : (
                <div className="overflow-x-auto">
                  <table className="min-w-full divide-y divide-slate-200 dark:divide-slate-800 text-sm">
                    <thead className="bg-slate-50 dark:bg-slate-800/50 text-[11px] uppercase tracking-wide text-slate-500">
                      <tr>
                        <th scope="col" className="px-6 py-3 text-left">
                          <span className="sr-only">{translate("Select workflows")}</span>
                        </th>
                        <th scope="col" className="px-6 py-3 text-left">{translate("Job ID")}</th>
                        <th scope="col" className="px-6 py-3 text-left">{translate("Status")}</th>
                        <th scope="col" className="px-6 py-3 text-left">{translate("Mode")}</th>
                        <th scope="col" className="px-6 py-3 text-left">{translate("Resolution")}</th>
                        <th scope="col" className="px-6 py-3 text-left">{translate("Progress")}</th>
                        <th scope="col" className="px-6 py-3 text-left">{translate("Started")}</th>
                      </tr>
                    </thead>
                    <tbody className="divide-y divide-slate-200 dark:divide-slate-800 bg-white dark:bg-slate-900">
                      {jobs.map((item) => {
                        const token = getStatusToken(item.status, language);
                        const isSelected = selectedCombinedJobs.has(item.job_id);
                        const progressPercent = Math.min(100, Math.max(0, Math.round((item.progress ?? 0) * 100)));
                        return (
                          <tr
                            key={item.job_id}
                            onClick={() => {
                              setActiveJobId(item.job_id);
                              setJob(item);
                              setIsJobDetailModalOpen(true);
                            }}
                            className="cursor-pointer transition hover:bg-slate-50 dark:hover:bg-slate-800/50"
                          >
                            <td className="px-6 py-4">
                              <input
                                type="checkbox"
                                checked={isSelected}
                                onChange={() => toggleCombinedSelection(item.job_id)}
                                onClick={(event) => event.stopPropagation()}
                                className="h-4 w-4 rounded border-slate-400 dark:border-slate-600 bg-slate-200 dark:bg-slate-700 text-indigo-500 focus:ring-indigo-500"
                              />
                            </td>
                            <td className="whitespace-nowrap px-6 py-4 font-mono text-xs text-slate-500 dark:text-slate-400">{item.job_id.slice(0, 12)}</td>
                            <td className="px-6 py-4">
                              <div className="flex flex-col gap-1">
                                <span className={`text-xs font-semibold uppercase tracking-wide ${token.className}`}>{token.label}</span>
                                <span className="text-[11px] text-slate-500">{stageLabel(item.stage, language)}</span>
                              </div>
                            </td>
                            <td className="px-6 py-4 text-xs text-slate-500 dark:text-slate-400">
                              {translate(item.generation_mode === "sequence" ? "Sequence" : "Parallel")}
                            </td>
                            <td className="px-6 py-4 text-xs text-slate-500 dark:text-slate-400">{item.resolution}</td>
                            <td className="px-6 py-4">
                              <div className="flex items-center gap-2">
                                <div className="h-2 w-24 rounded-full bg-slate-100 dark:bg-slate-800">
                                  <div
                                    className="h-full rounded-full bg-slate-400 transition-all"
                                    style={{ width: `${progressPercent}%` }}
                                  />
                                </div>
                                <span className="text-xs font-medium text-slate-500 dark:text-slate-400">{progressPercent}%</span>
                              </div>
                            </td>
                            <td className="whitespace-nowrap px-6 py-4 text-xs text-slate-500">{formatTimeDistance(item.started_at)}</td>
                          </tr>
                        );
                      })}
                    </tbody>
                  </table>
                </div>
              )}
            </>
          )}

          {activeTab === "single" && (
            <>
              <div className="flex items-center justify-end mb-4">
                  <button
                    type="button"
                    onClick={handleDeleteSingleJobs}
                    disabled={!selectedSingleJobs.size}
                    className="inline-flex items-center gap-1 rounded border border-rose-200 dark:border-rose-900/50 bg-rose-50 dark:bg-rose-950/30 px-3 py-1.5 text-xs font-semibold text-rose-400 transition disabled:cursor-not-allowed disabled:opacity-50"
                  >
                    <TrashIcon className="h-4 w-4" />
                    {translate("Delete Selected")}
                  </button>
              </div>
              {singleJobs.length === 0 ? (
                <p className="text-sm text-slate-500 p-4">{translate("Generate a clip to see it listed here.")}</p>
              ) : (
                <div className="overflow-x-auto">
                  <table className="min-w-full divide-y divide-slate-200 dark:divide-slate-800 text-sm">
                    <thead className="bg-slate-50 dark:bg-slate-800/50 text-[11px] uppercase tracking-wide text-slate-500">
                      <tr>
                        <th scope="col" className="px-6 py-3 text-left">
                          <span className="sr-only">{translate("Select workflows")}</span>
                        </th>
                        <th scope="col" className="px-6 py-3 text-left">{translate("Job ID")}</th>
                        <th scope="col" className="px-6 py-3 text-left">{translate("Status")}</th>
                        <th scope="col" className="px-6 py-3 text-left">{translate("Resolution")}</th>
                        <th scope="col" className="px-6 py-3 text-left">{translate("Duration")}</th>
                        <th scope="col" className="px-6 py-3 text-left">{translate("Progress")}</th>
                        <th scope="col" className="px-6 py-3 text-left">{translate("Started")}</th>
                      </tr>
                    </thead>
                    <tbody className="divide-y divide-slate-200 dark:divide-slate-800 bg-white dark:bg-slate-900">
                      {singleJobs.map((item) => {
                        const token = getStatusToken(item.status, language);
                        const isSelected = selectedSingleJobs.has(item.job_id);
                        const progressPercent = Math.min(100, Math.max(0, Math.round((item.progress ?? 0) * 100)));
                        return (
                          <tr
                            key={item.job_id}
                            onClick={() => {
                              setActiveSingleJobId(item.job_id);
                              setSingleJob(item);
                              setIsJobDetailModalOpen(true);
                            }}
                            className="cursor-pointer transition hover:bg-slate-50 dark:hover:bg-slate-800/50"
                          >
                            <td className="px-6 py-4">
                              <input
                                type="checkbox"
                                checked={isSelected}
                                onChange={() => toggleSingleSelection(item.job_id)}
                                onClick={(event) => event.stopPropagation()}
                                className="h-4 w-4 rounded border-slate-400 dark:border-slate-600 bg-slate-200 dark:bg-slate-700 text-indigo-500 focus:ring-indigo-500"
                              />
                            </td>
                            <td className="whitespace-nowrap px-6 py-4 font-mono text-xs text-slate-500 dark:text-slate-400">{item.job_id.slice(0, 12)}</td>
                            <td className="px-6 py-4">
                              <div className="flex flex-col gap-1">
                                <span className={`text-xs font-semibold uppercase tracking-wide ${token.className}`}>{token.label}</span>
                                <span className="text-[11px] text-slate-500">{stageLabel(item.status, language)}</span>
                              </div>
                            </td>
                            <td className="px-6 py-4 text-xs text-slate-500 dark:text-slate-400">{item.resolution}</td>
                            <td className="px-6 py-4 text-xs text-slate-500 dark:text-slate-400">
                              {item.duration}
                              {translate("s")}
                            </td>
                            <td className="px-6 py-4">
                              <div className="flex items-center gap-2">
                                <div className="h-2 w-24 rounded-full bg-slate-100 dark:bg-slate-800">
                                  <div
                                    className="h-full rounded-full bg-slate-400 transition-all"
                                    style={{ width: `${progressPercent}%` }}
                                  />
                                </div>
                                <span className="text-xs font-medium text-slate-500 dark:text-slate-400">{progressPercent}%</span>
                              </div>
                            </td>
                            <td className="whitespace-nowrap px-6 py-4 text-xs text-slate-500">{formatTimeDistance(item.started_at)}</td>
                          </tr>
                        );
                      })}
                    </tbody>
                  </table>
                </div>
              )}
            </>
          )}
        </div>

        {/* Modals */}
        <Modal isOpen={isCreateWorkflowModalOpen} onClose={() => setIsCreateWorkflowModalOpen(false)} title={translate("Start Workflow")}>
          {combinedError && (
            <div className="mb-4 flex items-start gap-3 rounded border border-rose-200 dark:border-rose-900/50 bg-rose-50 dark:bg-rose-950/30 px-4 py-3 text-sm text-rose-400 shadow-md shadow-rose-900/20">
              <ExclamationTriangleIcon className="h-5 w-5 flex-none text-rose-500" />
              <span>{translate(combinedError)}</span>
            </div>
          )}
          <p className="mb-4 text-sm text-slate-500 dark:text-slate-400">
            {translate("Paste your storyboard with scene headings.")}{" "}
            <span className="font-mono text-xs text-slate-500">**Scene 1 (Title)**</span>{" "}
            {translate("The Director agent will refine prompts following Sora2 best practices.")}
          </p>
          <form className="space-y-5" onSubmit={handleCombinedSubmit}>
            <textarea
              className="min-h-[260px] w-full resize-none rounded border border-slate-300 dark:border-slate-700 bg-slate-100 dark:bg-slate-800 px-4 py-3 text-sm text-slate-900 dark:text-slate-100 shadow-inner placeholder:text-slate-500 transition focus:border-slate-500 focus:outline-none focus:ring-2 focus:ring-slate-800"
              placeholder={translate("Enter script or storyboard prompt")}
              value={script}
              onChange={(event) => setScript(event.target.value)}
            />

            <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
              <div className="space-y-2">
                <label className="text-sm font-semibold text-slate-500 dark:text-slate-400">{translate("Resolution")}</label>
                <select
                  value={combinedResolution}
                  onChange={(event) => setCombinedResolution(event.target.value as (typeof resolutionOptions)[number])}
                  className="w-full rounded border border-slate-300 dark:border-slate-700 bg-slate-100 dark:bg-slate-800 px-3 py-2 text-sm text-slate-900 dark:text-slate-100 shadow-inner focus:border-slate-500 focus:outline-none focus:ring-2 focus:ring-slate-800"
                >
                  {resolutionOptions.map((option) => (
                    <option key={option} value={option}>
                      {option}
                    </option>
                  ))}
                </select>
              </div>
              <div className="space-y-2">
                <span className="text-sm font-semibold text-slate-500 dark:text-slate-400">{translate("Scene references")}</span>
                <button
                  type="button"
                  onClick={() => setUseSceneImages((value) => !value)}
                  className={`flex items-center gap-3 rounded border px-4 py-3 text-sm font-semibold transition ${
                    useSceneImages
                      ? "border-slate-400 dark:border-slate-600 bg-slate-200 dark:bg-slate-700 text-slate-900 dark:text-slate-100 shadow-sm"
                      : "border-slate-300 dark:border-slate-700 bg-slate-100 dark:bg-slate-800 text-slate-500 dark:text-slate-400 hover:border-slate-400 dark:border-slate-600 hover:text-slate-700 dark:text-slate-200"
                  }`}
                >
                  <span className={`inline-flex h-5 w-10 items-center rounded-full border transition ${
                    useSceneImages
                      ? "border-slate-500 bg-slate-600"
                      : "border-slate-400 dark:border-slate-600 bg-slate-200 dark:bg-slate-700"
                  }`}>
                    <span
                      className={`ml-1 inline-block h-3.5 w-3.5 rounded-full bg-slate-200 transition ${
                        useSceneImages ? "translate-x-4" : "translate-x-0"
                      }`}
                    />
                  </span>
                  {useSceneImages ? translate("Scene images enabled") : translate("No scene references")}
                </button>
              </div>
              <div className="space-y-2">
                <span className="text-sm font-semibold text-slate-500 dark:text-slate-400">{translate("Scene duration")}</span>
                <div className="flex gap-2">
                  {durationOptions.map((option) => {
                    const isActive = sceneDuration === option;
                    return (
                      <label
                        key={option}
                        className={`flex-1 cursor-pointer rounded-md border px-3 py-2 text-center text-sm font-semibold transition ${
                          isActive
                            ? "border-slate-400 dark:border-slate-600 bg-slate-200 dark:bg-slate-700 text-slate-900 dark:text-slate-100 shadow-sm"
                            : "border-slate-300 dark:border-slate-700 bg-slate-100 dark:bg-slate-800 text-slate-500 dark:text-slate-400 hover:border-slate-400 dark:border-slate-600 hover:text-slate-700 dark:text-slate-200"
                        }`}
                      >
                        <input
                          type="radio"
                          name="scene-duration"
                          value={option}
                          checked={isActive}
                          onChange={(event) => setSceneDuration(event.target.value as (typeof durationOptions)[number])}
                          className="sr-only"
                        />
                        {option}
                        {translate("s")}
                      </label>
                    );
                  })}
                </div>
              </div>
            </div>

            <div className="space-y-2">
              <label className="text-sm font-semibold text-slate-500 dark:text-slate-400">{translate("Generation mode")}</label>
              <div className="flex flex-wrap gap-3">
                {[
                  { id: "parallel" as const, label: translate("Parallel"), description: translate("Render scenes concurrently") },
                  { id: "sequence" as const, label: translate("Sequence"), description: translate("Chain scenes sequentially") },
                ].map((mode) => {
                  const isActive = generationMode === mode.id;
                  return (
                    <button
                      key={mode.id}
                      type="button"
                      onClick={() => setGenerationMode(mode.id)}
                      className={`flex flex-col rounded border px-4 py-2 text-left text-sm transition focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-slate-400 ${
                        isActive
                          ? "border-slate-400 dark:border-slate-600 bg-slate-200 dark:bg-slate-700 text-slate-900 dark:text-slate-100 shadow-sm"
                          : "border-slate-300 dark:border-slate-700 bg-slate-100 dark:bg-slate-800 text-slate-500 dark:text-slate-400 hover:border-slate-400 dark:border-slate-600 hover:text-slate-700 dark:text-slate-200"
                      }`}
                    >
                      <span className="font-semibold">{mode.label}</span>
                      <span className={`text-xs ${isActive ? "text-slate-600 dark:text-slate-300" : "text-slate-500"}`}>
                        {mode.description}
                      </span>
                    </button>
                  );
                })}
              </div>
              <p className="text-xs text-slate-500">
                {translate("Sequence mode accepts a single starting still, then reuses the last frame of each clip to guide the next scene for smoother continuity.")}
              </p>
            </div>

            <div className="rounded border border-slate-300 dark:border-slate-700 bg-slate-100/50 dark:bg-slate-100 dark:bg-slate-800/50 px-4 py-3">
              <div className="flex items-center justify-between gap-4">
                <div>
                  <p className="text-sm font-semibold text-slate-600 dark:text-slate-300">{translate("Refine script with GPT-5")}</p>
                  <p className="text-xs text-slate-500">
                    {translate("Use GPT-5 to polish the storyboard before planning scenes.")}
                  </p>
                </div>
                <button
                  type="button"
                  onClick={() => setRefineWorkflowWithGpt5((value) => !value)}
                  className={`inline-flex items-center gap-2 rounded-full border px-3 py-1 text-xs font-semibold transition ${
                    refineWorkflowWithGpt5
                      ? "border-slate-400 dark:border-slate-600 bg-slate-200 dark:bg-slate-700 text-slate-900 dark:text-slate-100"
                      : "border-slate-300 dark:border-slate-700 bg-slate-100 dark:bg-slate-800 text-slate-500 dark:text-slate-400 hover:border-slate-400 dark:border-slate-600 hover:text-slate-700 dark:text-slate-200"
                  }`}
                >
                  <span
                    className={`relative inline-flex h-5 w-10 items-center rounded-full border transition ${
                      refineWorkflowWithGpt5 ? "border-slate-500 bg-slate-600" : "border-slate-400 dark:border-slate-600 bg-slate-200 dark:bg-slate-700"
                    }`}
                  >
                    <span
                      className={`absolute left-1 h-3 rounded-full bg-slate-200 transition ${
                        refineWorkflowWithGpt5 ? "translate-x-4 w-4" : "w-3"
                      }`}
                    />
                  </span>
                  {translate(refineWorkflowWithGpt5 ? "Enabled" : "Disabled")}
                </button>
              </div>
            </div>

            {useSceneImages ? (
              <div className="space-y-3">
                <label className="text-sm font-semibold text-slate-500 dark:text-slate-400">{translate("Upload reference stills")}</label>
                <input
                  type="file"
                  accept="image/png,image/jpeg"
                  multiple={generationMode !== "sequence"}
                  onChange={handleSceneImagesChange}
                  className="max-w-xs text-xs text-slate-500"
                />
                {sceneImagePreviews.length ? (
                  <div className="grid grid-cols-3 gap-3 md:grid-cols-4">
                    {sceneImagePreviews.map((preview, index) => (
                      <div
                        key={`${preview}-${index}`}
                        className="flex h-28 items-center justify-center overflow-hidden rounded-md border border-slate-300 dark:border-slate-700 bg-slate-100 dark:bg-slate-800"
                      >
                        <Image
                          src={preview}
                          alt={`${translate("Scene reference")} ${index + 1}`}
                          width={160}
                          height={160}
                          unoptimized
                          className="h-full w-full object-cover"
                        />
                      </div>
                    ))}
                  </div>
                ) : (
                  <p className="text-xs text-slate-500">{translate("Accepted formats: PNG, JPG. Images remain local until submission.")}</p>
                )}
              </div>
            ) : null}

            <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
              <button
                type="submit"
                disabled={isSubmittingCombined}
                className="inline-flex items-center justify-center gap-2 rounded-md bg-slate-900 px-5 py-2.5 text-sm font-semibold text-white dark:bg-slate-100 dark:text-slate-900 shadow-sm shadow-slate-900/50 transition hover:bg-slate-700 focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-slate-400 disabled:cursor-not-allowed disabled:opacity-60"
              >
                {isSubmittingCombined ? (
                  <>
                    <ArrowPathIcon className="h-5 w-5 animate-spin" />
                    {translate("Starting Workflow...")}
                  </>
                ) : (
                  <>
                    <PaperAirplaneIcon className="h-5 w-5" />
                    {translate("Start Workflow")}
                  </>
                )}
              </button>
              <p className="text-xs text-slate-500">
                {translate("API target:")} <span className="font-mono text-[11px] text-slate-500">{API_BASE}/api/workflows</span>
              </p>
            </div>
          </form>
        </Modal>

        <Modal isOpen={isCreateSingleVideoModalOpen} onClose={() => setIsCreateSingleVideoModalOpen(false)} title={translate("Generate Single Clip")}>
          {singleError && (
            <div className="mb-4 flex items-start gap-3 rounded border border-rose-200 dark:border-rose-900/50 bg-rose-50 dark:bg-rose-950/30 px-4 py-3 text-sm text-rose-400 shadow-md shadow-rose-900/20">
              <ExclamationTriangleIcon className="h-5 w-5 flex-none text-rose-500" />
              <span>{translate(singleError)}</span>
            </div>
          )}
          <p className="mb-4 text-sm text-slate-500 dark:text-slate-400">
            {translate("Submit a single prompt directly to the Sora2 API. Optionally attach a still reference, select the render resolution, and choose a supported clip duration.")}
          </p>
          <form className="space-y-5" onSubmit={handleSingleSubmit}>
            <textarea
              className="min-h-[220px] w-full resize-none rounded border border-slate-300 dark:border-slate-700 bg-slate-100 dark:bg-slate-800 px-4 py-3 text-sm text-slate-900 dark:text-slate-100 shadow-inner placeholder:text-slate-500 transition focus:border-slate-500 focus:outline-none focus:ring-2 focus:ring-slate-800"
              placeholder={translate("Describe the scene, motion, cinematography, and mood for Sora2...")}
              value={singlePrompt}
              onChange={(event) => setSinglePrompt(event.target.value)}
            />

            <div className="grid gap-4 sm:grid-cols-2">
              <div className="space-y-2">
                <label className="text-sm font-semibold text-slate-500 dark:text-slate-400">{translate("Resolution")}</label>
                <select
                  value={singleResolution}
                  onChange={(event) => setSingleResolution(event.target.value as (typeof resolutionOptions)[number])}
                  className="w-full rounded border border-slate-300 dark:border-slate-700 bg-slate-100 dark:bg-slate-800 px-3 py-2 text-sm text-slate-900 dark:text-slate-100 shadow-inner focus:border-slate-500 focus:outline-none focus:ring-2 focus:ring-slate-800"
                >
                  {resolutionOptions.map((option) => (
                    <option key={option} value={option}>
                      {option}
                    </option>
                  ))}
                </select>
              </div>
              <div className="space-y-2">
                <span className="text-sm font-semibold text-slate-500 dark:text-slate-400">{translate("Duration")}</span
>
                <div className="flex gap-2">
                  {durationOptions.map((option) => {
                    const isActive = singleDuration === option;
                    return (
                      <label
                        key={option}
                        className={`flex-1 cursor-pointer rounded-md border px-3 py-2 text-center text-sm font-semibold transition ${
                          isActive
                            ? "border-slate-400 dark:border-slate-600 bg-slate-200 dark:bg-slate-700 text-slate-900 dark:text-slate-100 shadow-sm"
                            : "border-slate-300 dark:border-slate-700 bg-slate-100 dark:bg-slate-800 text-slate-500 dark:text-slate-400 hover:border-slate-400 dark:border-slate-600 hover:text-slate-700 dark:text-slate-200"
                        }`}
                      >
                        <input
                          type="radio"
                          name="single-duration"
                          value={option}
                          checked={isActive}
                          onChange={(event) => setSingleDuration(event.target.value as (typeof durationOptions)[number])}
                          className="sr-only"
                        />
                        {option}
                        {translate("s")}
                      </label>
                    );
                  })}
                </div>
              </div>
            </div>

            <div className="rounded border border-slate-300 dark:border-slate-700 bg-slate-100/50 dark:bg-slate-100 dark:bg-slate-800/50 px-4 py-3">
              <div className="flex items-center justify-between gap-4">
                <div>
                  <p className="text-sm font-semibold text-slate-600 dark:text-slate-300">{translate("Refine prompt with GPT-5")}</p>
                  <p className="text-xs text-slate-500">
                    {translate("Let GPT-5 tighten the single-scene prompt before submitting.")}
                  </p>
                </div>
                <button
                  type="button"
                  onClick={() => setRefineSingleWithGpt5((value) => !value)}
                  className={`inline-flex items-center gap-2 rounded-full border px-3 py-1 text-xs font-semibold transition ${
                    refineSingleWithGpt5
                      ? "border-slate-400 dark:border-slate-600 bg-slate-200 dark:bg-slate-700 text-slate-900 dark:text-slate-100"
                      : "border-slate-300 dark:border-slate-700 bg-slate-100 dark:bg-slate-800 text-slate-500 dark:text-slate-400 hover:border-slate-400 dark:border-slate-600 hover:text-slate-700 dark:text-slate-200"
                  }`}
                >
                  <span
                    className={`relative inline-flex h-5 w-10 items-center rounded-full border transition ${
                      refineSingleWithGpt5 ? "border-slate-500 bg-slate-600" : "border-slate-400 dark:border-slate-600 bg-slate-200 dark:bg-slate-700"
                    }`}
                  >
                    <span
                      className={`absolute left-1 h-3 rounded-full bg-slate-200 transition ${
                        refineSingleWithGpt5 ? "translate-x-4 w-4" : "w-3"
                      }`}
                    />
                  </span>
                  {translate(refineSingleWithGpt5 ? "Enabled" : "Disabled")}
                </button>
              </div>
            </div>

            <div className="space-y-3">
              <label className="text-sm font-semibold text-slate-500 dark:text-slate-400">{translate("Reference image (optional)")}</label>
              <input
                type="file"
                accept="image/png,image/jpeg"
                onChange={handleReferenceImageChange}
                className="max-w-xs text-xs text-slate-500"
              />
              {singleReferencePreview && (
                <div className="mt-2 flex h-40 w-32 items-center justify-center overflow-hidden rounded-md border border-slate-300 dark:border-slate-700 bg-slate-100 dark:bg-slate-800">
                  <Image
                    src={singleReferencePreview}
                    alt={translate("Reference preview")}
                    width={160}
                    height={200}
                    unoptimized
                    className="h-full w-full object-cover"
                  />
                </div>
              )}
            </div>

            <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
              <button
                type="submit"
                disabled={isSubmittingSingle}
                className="inline-flex items-center justify-center gap-2 rounded-md bg-slate-900 px-5 py-2.5 text-sm font-semibold text-white dark:bg-slate-100 dark:text-slate-900 shadow-sm shadow-slate-900/50 transition hover:bg-slate-700 focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-slate-400 disabled:cursor-not-allowed disabled:opacity-60"
              >
                {isSubmittingSingle ? (
                  <>
                    <ArrowPathIcon className="h-5 w-5 animate-spin" />
                    {translate("Sending Prompt...")}
                  </>
                ) : (
                  <>
                    <PaperAirplaneIcon className="h-5 w-5" />
                    {translate("Generate Clip")}
                  </>
                )}
              </button>
              <p className="text-xs text-slate-500">
                {translate("API target:")} <span className="font-mono text-[11px] text-slate-500">{API_BASE}/api/single-videos</span>
              </p>
            </div>
          </form>
        </Modal>

        <Modal isOpen={isJobDetailModalOpen} onClose={() => setIsJobDetailModalOpen(false)} title={translate("Job Details")} maxWidth="max-w-6xl">
          {activeTab === "combined" && job && (
            <div className="space-y-8">
              <div className="flex flex-wrap items-start justify-between gap-4">
                <div>
                  <h2 className="flex items-center gap-2 text-lg font-semibold text-slate-900 dark:text-slate-100">
                    <FilmIcon className="h-5 w-5 text-slate-500 dark:text-slate-400" />
                    {translate("Workflow Status")}
                  </h2>
                  <p className="text-xs uppercase tracking-wide text-slate-500">
                    {translate("Mode:")} {translate(job.generation_mode === "sequence" ? "Sequence" : "Parallel")}
                  </p>
                </div>
                <span className={`text-xs font-semibold uppercase tracking-wide ${currentStatusToken.className}`}>{currentStatusToken.label}</span>
              </div>

              <div className="mt-6 space-y-3">
                <div className="flex items-center justify-between text-xs text-slate-500 dark:text-slate-400">
                  <span>{translate("Progress")}</span>
                  <span>{combinedProgressPercent}%</span>
                </div>
                <div className="h-2.5 overflow-hidden rounded-full border border-slate-300 dark:border-slate-700 bg-slate-100 dark:bg-slate-800">
                  <div
                    className="h-full rounded-full bg-slate-100 transition-all duration-500 ease-out"
                    style={{ width: `${combinedProgressPercent}%` }}
                  />
                </div>
                {job.error && (
                  <div className="flex items-center gap-2 rounded-md border border-rose-200 dark:border-rose-900/50 bg-rose-50 dark:bg-rose-950/30 px-3 py-2 text-xs text-rose-400">
                    <ExclamationTriangleIcon className="h-4 w-4" />
                    {job.error}
                  </div>
                )}
              </div>

              <dl className="mt-6 grid gap-4 text-sm text-slate-500 dark:text-slate-400 sm:grid-cols-3">
                <div>
                  <dt className="text-xs uppercase tracking-wide text-slate-500">{translate("Started")}</dt>
                  <dd className="mt-1 text-slate-600 dark:text-slate-300">{formatTimeDistance(job.started_at)}</dd>
                </div>
                <div>
                  <dt className="text-xs uppercase tracking-wide text-slate-500">{translate("Completed")}</dt>
                  <dd className="mt-1 text-slate-600 dark:text-slate-300">{job.completed_at ? formatTimeDistance(job.completed_at) : "—"}</dd>
                </div>
                <div>
                  <dt className="text-xs uppercase tracking-wide text-slate-500">{translate("Resolution")}</dt>
                  <dd className="mt-1 text-slate-600 dark:text-slate-300">{job.resolution}</dd>
                </div>
              </dl>

              {finalVideoUrl && (
                <div className="mt-8 space-y-3">
                  <div className="flex items-center justify-between gap-3">
                    <h3 className="text-sm font-semibold text-slate-600 dark:text-slate-300">{translate("Final Video")}</h3>
                    <button
                      type="button"
                      onClick={handleRecompose}
                      disabled={!canRecompose || isRecomposing}
                      className="inline-flex items-center gap-2 rounded-md border border-slate-300 dark:border-slate-700 px-3 py-1.5 text-xs font-semibold text-slate-600 dark:text-slate-300 transition hover:border-slate-400 dark:border-slate-600 disabled:cursor-not-allowed disabled:opacity-60"
                    >
                      {isRecomposing ? <ArrowPathIcon className="h-4 w-4 animate-spin" /> : <ArrowPathIcon className="h-4 w-4" />}
                      {translate("Regenerate Final Video")}
                    </button>
                  </div>
                  <video
                    controls
                    className="max-h-[420px] w-full rounded border border-slate-200 dark:border-slate-800 bg-black/60"
                    src={finalVideoUrl}
                  >
                    {translate("Your browser does not support HTML5 video.")}
                  </video>
                  <a
                    href={finalVideoUrl}
                    target="_blank"
                    rel="noreferrer"
                    className="inline-flex items-center gap-2 text-sm font-medium text-slate-500 dark:text-slate-400 hover:text-slate-900 dark:text-slate-100"
                  >
                    <PlayCircleIcon className="h-4 w-4" />
                    {translate("Open MP4 in new tab")}
                  </a>
                </div>
              )}

              {job.scenes?.length ? (
                <div className="mt-8">
                  <h2 className="flex items-center gap-2 text-lg font-semibold text-slate-900 dark:text-slate-100">
                    <PlayCircleIcon className="h-5 w-5 text-slate-500 dark:text-slate-400" />
                    {translate("Scene Renders")}
                  </h2>
                  <div className="mt-5 grid grid-cols-1 gap-5 sm:grid-cols-2 lg:grid-cols-[repeat(auto-fit,minmax(360px,1fr))]">
                    {job.scenes.map((scene) => {
                      const versions = scene.versions ?? [];
                      const selectedVersion = versions.find((version) => version.version_id === scene.selected_version_id) ?? versions[0];
                      const isRegenerating = Boolean(sceneRegenerating[scene.order]);

                      return (
                        <div
                          key={scene.order}
                          className="rounded border border-slate-200 dark:border-slate-800 bg-white/80 dark:bg-slate-900/50 p-4 shadow-inner shadow-black/20"
                        >
                          <div className="flex items-start justify-between gap-2">
                            <div>
                              <h3 className="text-sm font-semibold text-slate-900 dark:text-slate-100">{scene.label}</h3>
                              <p className="text-xs text-slate-500 dark:text-slate-400">
                                {scene.duration_seconds} {translate("seconds")}
                              </p>
                            </div>
                            <span className="rounded-full border border-slate-300 dark:border-slate-700 bg-slate-100 dark:bg-slate-800 px-2 py-0.5 text-[11px] uppercase tracking-wide text-slate-500 dark:text-slate-400">
                              {translate("Scene")} {scene.order}
                            </span>
                          </div>

                          <div className="mt-2 flex flex-wrap items-center justify-between gap-2">
                            <p className="text-[11px] text-slate-500 dark:text-slate-400">
                              {translate("Video ID:")} {selectedVersion?.video_id ?? translate("Unavailable")}
                            </p>
                            <button
                              type="button"
                              onClick={() => handleRegenerateScene(scene.order)}
                              disabled={isRegenerating}
                              className="inline-flex items-center gap-1 rounded-md border border-slate-300 dark:border-slate-700 px-3 py-1 text-[11px] font-semibold text-slate-500 dark:text-slate-400 transition hover:border-slate-400 dark:border-slate-600 hover:text-slate-700 dark:text-slate-200 disabled:cursor-not-allowed disabled:opacity-60"
                            >
                              {isRegenerating ? (
                                <ArrowPathIcon className="h-3.5 w-3.5 animate-spin" />
                              ) : (
                                <ArrowPathIcon className="h-3.5 w-3.5" />
                              )}
                              {isRegenerating ? translate("Regenerating...") : translate("Regenerate Scene")}
                            </button>
                          </div>

                          {versions.length ? (
                            <div className="mt-4 space-y-3">
                              <h4 className="text-xs font-semibold uppercase tracking-wide text-slate-500">
                                {translate("Versions")}
                              </h4>
                              {versions.map((version) => {
                                const versionVideoUrl = buildAssetUrl(version.video_url);
                                const versionLastFrameUrl = showSequenceReferences ? buildAssetUrl(version.last_frame_url) : null;
                                const isActive = version.version_id === scene.selected_version_id;
                                const isBusy = Boolean(selectionSubmitting[version.version_id]);
                                return (
                                  <div
                                    key={version.version_id}
                                    className={`rounded border p-3 ${
                                      isActive
                                        ? "border-slate-400 dark:border-slate-600 bg-slate-100 dark:bg-slate-800"
                                        : "border-slate-200 dark:border-slate-800 bg-white dark:bg-slate-900/30"
                                    }`}
                                  >
                                    <div className="flex items-start justify-between gap-3">
                                      <div>
                                        <p className="text-sm font-semibold text-slate-700 dark:text-slate-200">{version.label}</p>
                                        <p className="text-xs text-slate-500">
                                          {version.source} • {formatTimeDistance(version.created_at)}
                                        </p>
                                      </div>
                                      <button
                                        type="button"
                                        onClick={() => handleSelectVersion(scene.order, version.version_id)}
                                        disabled={isActive || isBusy}
                                        className={`rounded-md border px-3 py-1 text-xs font-semibold transition ${
                                          isActive
                                            ? "border-emerald-200 dark:border-emerald-900/50 bg-emerald-50 dark:bg-emerald-950/30 text-emerald-400"
                                            : "border-slate-300 dark:border-slate-700 bg-slate-100 dark:bg-slate-800 text-slate-500 dark:text-slate-400 hover:border-slate-400 dark:border-slate-600 hover:text-slate-700 dark:text-slate-200 disabled:opacity-60"
                                        }`}
                                      >
                                        {isActive
                                          ? translate("Selected")
                                          : isBusy
                                            ? translate("Selecting...")
                                            : translate("Use this version")}
                                      </button>
                                    </div>
                                    {versionVideoUrl ? (
                                      <video
                                        key={`${version.version_id}-${versionVideoUrl}`}
                                        controls
                                        className="mt-2 max-h-40 w-full rounded-md border border-slate-200 dark:border-slate-800 bg-black/60"
                                        src={versionVideoUrl}
                                      >
                                        {translate("Version video unavailable.")}
                                      </video>
                                    ) : null}
                                    {versionLastFrameUrl ? (
                                      <div className="mt-2 rounded-md border border-slate-300 dark:border-slate-700 bg-slate-100/50 dark:bg-slate-100 dark:bg-slate-800/50 p-2">
                                        <p className="text-[11px] font-semibold uppercase tracking-wide text-slate-500">
                                          {translate("Last frame reference")}
                                        </p>
                                        <Image
                                          src={versionLastFrameUrl}
                                          alt={`${version.label} ${translate("Last frame reference")}`}
                                          width={280}
                                          height={200}
                                          unoptimized
                                          className="mt-2 h-28 w-full rounded object-cover"
                                        />
                                      </div>
                                    ) : null}
                                    {version.prompt ? (
                                      <button
                                        type="button"
                                        onClick={() =>
                                          setPromptPreview({
                                            sceneLabel: scene.label,
                                            versionLabel: version.label,
                                            prompt: version.prompt ?? "",
                                          })
                                        }
                                        className="mt-3 inline-flex items-center gap-2 rounded-md border border-slate-300 dark:border-slate-700 bg-slate-100 dark:bg-slate-800 px-3 py-1.5 text-xs font-semibold text-slate-600 dark:text-slate-300 transition hover:border-slate-400 dark:border-slate-600 hover:text-slate-700 dark:text-slate-200"
                                      >
                                        <DocumentTextIcon className="h-4 w-4" />
                                        {translate("View system prompt")}
                                      </button>
                                    ) : null}
                                    {version.video_id ? (
                                      <p className="text-[11px] text-slate-500">{translate("Azure video ID:")} {version.video_id}</p>
                                    ) : null}
                                  </div>
                                );
                              })}
                            </div>
                          ) : null}

                          <form className="mt-4 space-y-2" onSubmit={(event) => handleRemixSubmit(event, scene.order)}>
                            <label className="text-xs font-semibold uppercase tracking-wide text-slate-500">
                              {translate("Remix prompt")}
                            </label>
                            <textarea
                              className="min-h-[80px] w-full rounded-md border border-slate-300 dark:border-slate-700 bg-slate-100 dark:bg-slate-800 px-3 py-2 text-sm text-slate-900 dark:text-slate-100 focus:border-slate-500 focus:outline-none focus:ring-2 focus:ring-slate-800"
                              placeholder={translate("Describe how you want to remix this scene...")}
                              value={remixPrompts[scene.order] ?? ""}
                              onChange={(event) =>
                                setRemixPrompts((prev) => ({ ...prev, [scene.order]: event.target.value }))
                              }
                            />
                            <div className="flex items-center justify-between gap-2 text-xs text-slate-500">
                              <span>
                                {translate("Uses previous video ID:")} {selectedVersion?.video_id ?? translate("Unavailable")}
                              </span>
                              <button
                                type="submit"
                                disabled={!selectedVersion?.video_id || remixSubmitting[scene.order]}
                                className="inline-flex items-center gap-1 rounded-md border border-slate-300 dark:border-slate-700 px-3 py-1 text-xs font-semibold text-slate-500 dark:text-slate-400 transition hover:border-slate-400 dark:border-slate-600 hover:text-slate-700 dark:text-slate-200 disabled:cursor-not-allowed disabled:opacity-60"
                              >
                                {remixSubmitting[scene.order] ? (
                                  <ArrowPathIcon className="h-4 w-4 animate-spin" />
                                ) : (
                                  <ArrowPathIcon className="h-4 w-4" />
                                )}
                                {translate("Remix Scene")}
                              </button>
                            </div>
                          </form>
                        </div>
                      );
                    })}
                  </div>
                </div>
              ) : null}

              {job.messages?.length ? (
                <div className="mt-8">
                  <h2 className="flex items-center gap-2 text-lg font-semibold text-slate-900 dark:text-slate-100">
                    <ClockIcon className="h-5 w-5 text-slate-500 dark:text-slate-400" />
                    {translate("Workflow Timeline")}
                  </h2>
                  <ol className="mt-4 space-y-2 max-h-60 overflow-y-auto pr-2">
                    {job.messages.map((message, index) => {
                      const Icon = stageIcons[message.stage] ?? ArrowPathIcon;
                      return (
                        <li
                          key={`${message.timestamp}-${index}`}
                          className="relative flex gap-3 rounded border border-slate-200 dark:border-slate-800 bg-white/80 dark:bg-slate-900/50 p-2 shadow-inner shadow-black/20"
                        >
                          <div className="relative mt-0.5 flex h-7 w-7 items-center justify-center rounded-full border border-slate-300 dark:border-slate-700 bg-slate-100 dark:bg-slate-800">
                            <Icon className="h-4 w-4 text-slate-500 dark:text-slate-400" />
                          </div>
                          <div>
                            <p className="text-sm text-slate-600 dark:text-slate-300">{message.text}</p>
                            <p className="text-xs text-slate-500">
                              {formatTimestamp(message.timestamp)} · {stageLabel(message.stage, language)}
                            </p>
                          </div>
                        </li>
                      );
                    })}
                  </ol>
                </div>
              ) : null}
            </div>
          )}

          {activeTab === "single" && singleJob && (
            <div className="space-y-8">
              <div className="flex flex-wrap items-start justify-between gap-4">
                <div>
                  <h2 className="flex items-center gap-2 text-lg font-semibold text-slate-900 dark:text-slate-100">
                    <FilmIcon className="h-5 w-5 text-slate-500 dark:text-slate-400" />
                    {translate("Single Video Status")}
                  </h2>
                </div>
                <span className={`text-xs font-semibold uppercase tracking-wide ${singleStatusToken.className}`}>{singleStatusToken.label}</span>
              </div>

              <div className="mt-6 space-y-3">
                <div className="flex items-center justify-between text-xs text-slate-500 dark:text-slate-400">
                  <span>{translate("Progress")}</span>
                  <span>{singleProgressPercent}%</span>
                </div>
                <div className="h-2.5 overflow-hidden rounded-full border border-slate-300 dark:border-slate-700 bg-slate-100 dark:bg-slate-800">
                  <div
                    className="h-full rounded-full bg-slate-100 transition-all duration-500 ease-out"
                    style={{ width: `${singleProgressPercent}%` }}
                  />
                </div>
                {singleJob.error && (
                  <div className="flex items-center gap-2 rounded-md border border-rose-200 dark:border-rose-900/50 bg-rose-50 dark:bg-rose-950/30 px-3 py-2 text-xs text-rose-400">
                    <ExclamationTriangleIcon className="h-4 w-4" />
                    {singleJob.error}
                  </div>
                )}
              </div>

              <dl className="mt-6 grid gap-4 text-sm text-slate-500 dark:text-slate-400 sm:grid-cols-2 md:grid-cols-5">
                <div>
                  <dt className="text-xs uppercase tracking-wide text-slate-500">{translate("Started")}</dt>
                  <dd className="mt-1 text-slate-600 dark:text-slate-300">{formatTimeDistance(singleJob.started_at)}</dd>
                </div>
                <div>
                  <dt className="text-xs uppercase tracking-wide text-slate-500">{translate("Completed")}</dt>
                  <dd className="mt-1 text-slate-600 dark:text-slate-300">{singleJob.completed_at ? formatTimeDistance(singleJob.completed_at) : "—"}</dd>
                </div>
                <div>
                  <dt className="text-xs uppercase tracking-wide text-slate-500">{translate("Resolution")}</dt>
                  <dd className="mt-1 text-slate-600 dark:text-slate-300">{singleJob.resolution}</dd>
                </div>
                <div>
                  <dt className="text-xs uppercase tracking-wide text-slate-500">{translate("Duration")}</dt>
                  <dd className="mt-1 text-slate-600 dark:text-slate-300">
                    {singleJob.duration}
                    {translate("s")}
                  </dd>
                </div>
                <div>
                  <dt className="text-xs uppercase tracking-wide text-slate-500">{translate("Azure video ID:")}</dt>
                  <dd className="mt-1 text-slate-600 dark:text-slate-300">{singleJob.video_id ?? translate("Unavailable")}</dd>
                </div>
              </dl>

              {singleVideoUrl ? (
                <div className="mt-8 space-y-3">
                  <h3 className="text-sm font-semibold text-slate-600 dark:text-slate-300">{translate("Generated Video")}</h3>
                  <video
                    controls
                    className="max-h-[360px] w-full rounded border border-slate-200 dark:border-slate-800 bg-black/60"
                    src={singleVideoUrl}
                  >
                    {translate("Your browser does not support HTML5 video.")}
                  </video>
                  <a
                    href={singleVideoUrl}
                    target="_blank"
                    rel="noreferrer"
                    className="inline-flex items-center gap-2 text-sm font-medium text-slate-500 dark:text-slate-400 hover:text-slate-900 dark:text-slate-100"
                  >
                    <PlayCircleIcon className="h-4 w-4" />
                    {translate("Open MP4 in new tab")}
                  </a>
                </div>
              ) : (
                <div className="mt-8 flex h-40 items-center justify-center rounded border border-dashed border-slate-300 dark:border-slate-700 bg-slate-100/50 dark:bg-slate-100 dark:bg-slate-800/50 text-xs text-slate-500">
                  {translate("Video pending...")}
                </div>
              )}

              {singleJob.video_id ? (
                <form className="mt-6 space-y-2" onSubmit={handleSingleRemixSubmit}>
                  <div className="flex flex-wrap items-center justify-between gap-3">
                    <h3 className="text-sm font-semibold text-slate-600 dark:text-slate-300">{translate("Remix Single Video")}</h3>
                    <span className="text-[11px] text-slate-500">
                      {translate("Uses previous video ID:")} {singleJob.video_id}
                    </span>
                  </div>
                  <textarea
                    className="min-h-[80px] w-full rounded-md border border-slate-300 dark:border-slate-700 bg-slate-100 dark:bg-slate-800 px-3 py-2 text-sm text-slate-900 dark:text-slate-100 focus:border-slate-500 focus:outline-none focus:ring-2 focus:ring-slate-800"
                    placeholder={translate("Describe how you want to remix this clip...")}
                    value={singleRemixPrompt}
                    onChange={(event) => setSingleRemixPrompt(event.target.value)}
                  />
                  <div className="flex items-center justify-end">
                    <button
                      type="submit"
                      disabled={singleRemixSubmitting}
                      className="inline-flex items-center gap-2 rounded-md border border-slate-300 dark:border-slate-700 px-4 py-2 text-xs font-semibold text-slate-500 dark:text-slate-400 transition hover:border-slate-400 dark:border-slate-600 hover:text-slate-700 dark:text-slate-200 disabled:cursor-not-allowed disabled:opacity-60"
                    >
                      {singleRemixSubmitting ? (
                        <ArrowPathIcon className="h-4 w-4 animate-spin" />
                      ) : (
                        <ArrowPathIcon className="h-4 w-4" />
                      )}
                      {translate("Remix Clip")}
                    </button>
                  </div>
                </form>
              ) : null}

              {singleJob.versions?.length ? (
                <div className="mt-8 space-y-3">
                  <h3 className="text-sm font-semibold text-slate-600 dark:text-slate-300">{translate("Versions")}</h3>
                  <div className="space-y-3">
                    {singleJob.versions.map((version) => {
                      const versionVideoUrl = buildAssetUrl(version.video_url);
                      const isActive = version.version_id === singleJob.selected_version_id;
                      return (
                        <div
                          key={version.version_id}
                          className={`rounded border p-3 ${
                            isActive ? "border-slate-400 dark:border-slate-600 bg-slate-100 dark:bg-slate-800" : "border-slate-200 dark:border-slate-800 bg-white dark:bg-slate-900/30"
                          }`}
                        >
                          <div className="flex items-start justify-between gap-3">
                            <div>
                              <p className="text-sm font-semibold text-slate-700 dark:text-slate-200">{version.label}</p>
                              <p className="text-xs text-slate-500">
                                {version.source} • {formatTimeDistance(version.created_at)}
                              </p>
                            </div>
                            <button
                              type="button"
                              onClick={() => handleSelectSingleVersion(version.version_id)}
                              disabled={isActive || singleSelectionSubmitting[version.version_id]}
                              className={`rounded-md border px-3 py-1 text-xs font-semibold transition ${
                                isActive
                                  ? "border-emerald-200 dark:border-emerald-900/50 bg-emerald-50 dark:bg-emerald-950/30 text-emerald-400"
                                  : "border-slate-300 dark:border-slate-700 bg-slate-100 dark:bg-slate-800 text-slate-500 dark:text-slate-400 hover:border-slate-400 dark:border-slate-600 hover:text-slate-700 dark:text-slate-200 disabled:opacity-60"
                              }`}
                            >
                              {isActive
                                ? translate("Selected")
                                : singleSelectionSubmitting[version.version_id]
                                  ? translate("Selecting...")
                                  : translate("Use this version")}
                            </button>
                          </div>
                          {versionVideoUrl ? (
                            <video
                              key={`${version.version_id}-${versionVideoUrl}`}
                              controls
                              className="mt-2 max-h-48 w-full rounded-md border border-slate-200 dark:border-slate-800 bg-black/60"
                              src={versionVideoUrl}
                            >
                              {translate("Version video unavailable.")}
                            </video>
                          ) : null}
                          <p className="mt-2 text-[11px] text-slate-500">
                            {translate("Azure video ID:")} {version.video_id ?? translate("Unavailable")}
                          </p>
                          <p className="mt-1 text-xs text-slate-500 dark:text-slate-400">
                            {translate("Prompt:")} <span className="font-mono text-[11px] text-slate-500">{version.prompt}</span>
                          </p>
                        </div>
                      );
                    })}
                  </div>
                </div>
              ) : null}

              {singleJob.messages?.length ? (
                <div className="mt-8">
                  <h2 className="flex items-center gap-2 text-lg font-semibold text-slate-900 dark:text-slate-100">
                    <ClockIcon className="h-5 w-5 text-slate-500 dark:text-slate-400" />
                    {translate("Job Timeline")}
                  </h2>
                  <ol className="mt-4 space-y-2 max-h-60 overflow-y-auto pr-2">
                    {singleJob.messages.map((message, index) => {
                      const Icon = stageIcons[message.stage] ?? ArrowPathIcon;
                      return (
                        <li
                          key={`${message.timestamp}-${index}`}
                          className="relative flex gap-3 rounded border border-slate-200 dark:border-slate-800 bg-white/80 dark:bg-slate-900/50 p-2 shadow-inner shadow-black/20"
                        >
                          <div className="relative mt-0.5 flex h-7 w-7 items-center justify-center rounded-full border border-slate-300 dark:border-slate-700 bg-slate-100 dark:bg-slate-800">
                            <Icon className="h-4 w-4 text-slate-500 dark:text-slate-400" />
                          </div>
                          <div>
                            <p className="text-sm text-slate-600 dark:text-slate-300">{message.text}</p>
                            <p className="text-xs text-slate-500">
                              {formatTimestamp(message.timestamp)} · {stageLabel(message.stage, language)}
                            </p>
                          </div>
                        </li>
                      );
                    })}
                  </ol>
                </div>
              ) : null}
            </div>
          )}
        </Modal>

        <Modal
          isOpen={Boolean(promptPreview)}
          onClose={() => setPromptPreview(null)}
          title={translate("System prompt")}
          maxWidth="max-w-3xl"
        >
          {promptPreview ? (
            <div className="space-y-3">
              <p className="text-xs font-semibold uppercase tracking-wide text-slate-500">
                {promptPreview.sceneLabel} • {promptPreview.versionLabel}
              </p>
              <div className="max-h-[50vh] overflow-y-auto rounded-md border border-slate-200 dark:border-slate-800 bg-slate-50 dark:bg-slate-900/50 p-3">
                <pre className="whitespace-pre-wrap break-words font-mono text-[13px] leading-relaxed text-slate-800 dark:text-slate-100">
                  {promptPreview.prompt}
                </pre>
              </div>
            </div>
          ) : null}
        </Modal>

      </div>

      <footer className="border-t border-slate-200 bg-slate-100/90 py-4 text-center text-xs text-slate-500 backdrop-blur-xl dark:border-slate-800 dark:bg-slate-900/90 dark:text-slate-400">
        <p>Powered by Sora2 on Microsoft Azure, author Ming Gu from AI GBB team.</p>
      </footer>
    </div>
  );
}
