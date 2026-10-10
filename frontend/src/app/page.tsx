"use client";

import { ChangeEvent, FormEvent, useCallback, useEffect, useMemo, useRef, useState } from "react";
import Modal from "@/components/Modal";
import WorkflowProgress from "@/components/WorkflowProgress";
import { ImageFrame, MaterialGroup, VideoFrame, aspectRatioOf } from "@/components/SceneMedia";
import {
  Field,
  SegmentedControl,
  SettingRow,
  SettingsList,
  fileInputClass,
  inputClass,
  primaryButtonClass,
  secondaryButtonClass,
} from "@/components/ui/controls";
import { LanguageToggle } from "@/components/dashboard/LanguageToggle";
import { ThemeToggle } from "@/components/dashboard/ThemeToggle";
import { WorkflowTable } from "@/components/dashboard/WorkflowTable";
import { SingleJobsTable } from "@/components/dashboard/SingleJobsTable";
import { InsightJobsTable } from "@/components/dashboard/InsightJobsTable";
import { ReproduceJobsTable } from "@/components/dashboard/ReproduceJobsTable";
import {
  ArrowDownTrayIcon,
  ArrowPathIcon,
  CheckCircleIcon,
  ClockIcon,
  DocumentTextIcon,
  ExclamationTriangleIcon,
  FilmIcon,
  PaperAirplaneIcon,
  PlayCircleIcon,
  TrashIcon,
  ArrowRightStartOnRectangleIcon,
} from "@heroicons/react/24/outline";
import Image from "next/image";
import {
  AUTH_HEADER_STORAGE_KEY,
  AUTH_STORAGE_KEY,
  DEFAULT_WORKFLOW_PROMPT_TEMPLATE,
  IMAGE_GENERATOR_OPTIONS,
  REFERENCE_FRAME_OPTIONS,
  SEGMENTATION_OPTIONS,
  SORA_DURATION_OPTIONS,
  TABS,
  VIDEO_PROVIDER_OPTIONS,
  VISUAL_STYLE_OPTIONS,
  getDurationOptions,
  getVideoProviderLabel,
  getImageGeneratorLabel,
  getReferenceModeLabel,
  getSegmentationLabel,
  resolutionOptions,
  stageIcons,
  terminalStatuses,
} from "./dashboard/constants";
import {
  buildAssetUrl,
  formatSeconds,
  formatSegmentTimestampDisplay,
  formatTimeDistance,
  formatTimestamp,
  getStatusToken,
  stageLabel,
} from "./dashboard/utils";
import { translateValue } from "./dashboard/translations";
import {
  DurationOption,
  ImageGeneratorOption,
  JobStatus,
  Language,
  PromptPreview,
  ReferenceFrameMode,
  RefinementReview,
  ReproduceSegmentationOption,
  SingleVideoJob,
  TabId,
  VideoInsightJob,
  VideoReproduceJob,
  VideoReproduceSegment,
  VisualStyle,
} from "@/types/dashboard";

const RAW_API_BASE = (process.env.NEXT_PUBLIC_API_BASE_URL ?? "").replace(/\/$/, "");
const DEFAULT_API_BASE = (RAW_API_BASE || "http://localhost:8000").replace(/\/$/, "");

const normalizeProvider = (value?: string | null) => {
  const normalized = (value ?? "").trim().toLowerCase();
  return (VIDEO_PROVIDER_OPTIONS as readonly string[]).includes(
    normalized as (typeof VIDEO_PROVIDER_OPTIONS)[number],
  )
    ? normalized
    : null;
};

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

  const [language, setLanguage] = useState<Language>("zh");
  const [isAuthenticated, setIsAuthenticated] = useState(false);
  const [authHeader, setAuthHeader] = useState<string | null>(null);
  const [isAuthReady, setIsAuthReady] = useState(false);
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [authError, setAuthError] = useState<string | null>(null);
  const translate = useCallback((value: string) => translateValue(language, value), [language]);

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

  useEffect(() => {
    return () => {
      if (copyTimeoutRef.current) {
        clearTimeout(copyTimeoutRef.current);
      }
    };
  }, []);

  const [apiBase, setApiBase] = useState<string>(DEFAULT_API_BASE);

  useEffect(() => {
    if (RAW_API_BASE) {
      return;
    }
    if (typeof window === "undefined") {
      return;
    }
    const origin = window.location.origin.replace(/\/$/, "");
    let shouldStickToDefault = false;
    try {
      // Avoid overwriting the explicit backend URL when the UI runs on the local Next.js dev server.
      const url = new URL(origin);
      const devPorts = new Set(["3000", "3001", "3002"]);
      const isLocalhost = url.hostname === "localhost" || url.hostname === "127.0.0.1";
      shouldStickToDefault = isLocalhost && devPorts.has(url.port || "");
    } catch {
      // no-op: fall back to default apiBase
    }
    if (!shouldStickToDefault && origin && origin !== apiBase) {
      setApiBase(origin);
    }
  }, [apiBase]);

  const clearAuthSession = useCallback(() => {
    setIsAuthenticated(false);
    setAuthHeader(null);
    if (typeof window !== "undefined") {
      window.localStorage.removeItem(AUTH_STORAGE_KEY);
      window.localStorage.removeItem(AUTH_HEADER_STORAGE_KEY);
    }
  }, []);

  const verifyAuthSession = useCallback(
    async (header: string | null): Promise<{ ok: boolean; error?: string }> => {
      try {
        const headers = new Headers();
        if (header) {
          headers.set("Authorization", header);
        }
        const response = await fetch(`${apiBase}/api/auth/session`, { headers });
        if (!response.ok) {
          return { ok: false, error: "Invalid username or password." };
        }
        return { ok: true };
      } catch (err) {
        console.error(err);
        return { ok: false, error: "Unable to contact backend authentication endpoint." };
      }
    },
    [apiBase],
  );

  const apiFetch = useCallback(
    async (input: RequestInfo | URL, init?: RequestInit): Promise<Response> => {
      const headers = new Headers(init?.headers ?? {});
      if (authHeader && !headers.has("Authorization")) {
        headers.set("Authorization", authHeader);
      }
      const response = await fetch(input, { ...init, headers });
      if (response.status === 401) {
        clearAuthSession();
        setAuthError("Authentication required. Sign in again.");
      }
      return response;
    },
    [authHeader, clearAuthSession],
  );

  // Combined workflow state
  const [script, setScript] = useState(DEFAULT_WORKFLOW_PROMPT_TEMPLATE);
  const [useSceneImages, setUseSceneImages] = useState(false);
  const [generateAiFirstFrames, setGenerateAiFirstFrames] = useState(false);
  const [generationMode, setGenerationMode] = useState<"parallel" | "sequence">("parallel");
  const [combinedResolution, setCombinedResolution] = useState<(typeof resolutionOptions)[number]>(resolutionOptions[0]);
  const [sceneDuration, setSceneDuration] = useState<DurationOption>(SORA_DURATION_OPTIONS[1]);
  const [visualStyle, setVisualStyle] = useState<VisualStyle>("3d_animation");
  const [sceneImageFiles, setSceneImageFiles] = useState<File[]>([]);
  const [sceneImagePreviews, setSceneImagePreviews] = useState<string[]>([]);
  const [jobs, setJobs] = useState<JobStatus[]>([]);
  const [activeJobId, setActiveJobId] = useState<string | null>(null);
  const [job, setJob] = useState<JobStatus | null>(null);
  const [activeVideoProvider, setActiveVideoProvider] = useState<string | null>(null);
  const [availableVideoProviders, setAvailableVideoProviders] = useState<string[]>([...VIDEO_PROVIDER_OPTIONS]);
  const [workflowVideoProvider, setWorkflowVideoProvider] = useState<string | null>(null);
  const [isSubmittingCombined, setIsSubmittingCombined] = useState(false);
  const [combinedError, setCombinedError] = useState<string | null>(null);
  const [selectedCombinedJobs, setSelectedCombinedJobs] = useState<Set<string>>(new Set());
  const [remixPrompts, setRemixPrompts] = useState<Record<number, string>>({});
  const [remixSubmitting, setRemixSubmitting] = useState<Record<number, boolean>>({});
  const [selectionSubmitting, setSelectionSubmitting] = useState<Record<string, boolean>>({});
  const [isRecomposing, setIsRecomposing] = useState(false);
  const [refineWorkflowWithGpt5, setRefineWorkflowWithGpt5] = useState(false);
  const [sceneRegenerating, setSceneRegenerating] = useState<Record<number, boolean>>({});
  const [sceneRegenerationTargets, setSceneRegenerationTargets] = useState<
    Record<number, { versionCount: number; latestVersionId: string | null }>
  >({});
  const [scenePromptEdits, setScenePromptEdits] = useState<Record<number, string>>({});
  const [activeSceneOrder, setActiveSceneOrder] = useState<number | null>(null);

  // Single video state
  const [singlePrompt, setSinglePrompt] = useState("");
  const [singleResolution, setSingleResolution] = useState<(typeof resolutionOptions)[number]>(resolutionOptions[0]);
  const [singleDuration, setSingleDuration] = useState<DurationOption>(SORA_DURATION_OPTIONS[1]);
  const [singleReferenceFile, setSingleReferenceFile] = useState<File | null>(null);
  const [singleReferencePreview, setSingleReferencePreview] = useState<string | null>(null);
  const [singleJobs, setSingleJobs] = useState<SingleVideoJob[]>([]);
  const [activeSingleJobId, setActiveSingleJobId] = useState<string | null>(null);
  const [singleJob, setSingleJob] = useState<SingleVideoJob | null>(null);
  const [singleVideoProvider, setSingleVideoProvider] = useState<string | null>(null);
  const [isSubmittingSingle, setIsSubmittingSingle] = useState(false);
  const [singleError, setSingleError] = useState<string | null>(null);
  const [selectedSingleJobs, setSelectedSingleJobs] = useState<Set<string>>(new Set());
  const [refineSingleWithGpt5, setRefineSingleWithGpt5] = useState(false);
  const [singleRemixPrompt, setSingleRemixPrompt] = useState("");
  const [singleRemixSubmitting, setSingleRemixSubmitting] = useState(false);
  const [singleSelectionSubmitting, setSingleSelectionSubmitting] = useState<Record<string, boolean>>({});
  const [promptPreview, setPromptPreview] = useState<PromptPreview | null>(null);
  const [refinementReview, setRefinementReview] = useState<RefinementReview | null>(null);
  const [refinementDraft, setRefinementDraft] = useState("");
  const [workflowTemplateDraft, setWorkflowTemplateDraft] = useState("");
  const [workflowTemplateOriginal, setWorkflowTemplateOriginal] = useState("");
  const [workflowTemplateProvider, setWorkflowTemplateProvider] = useState<string | null>(null);
  const [workflowTemplateLoading, setWorkflowTemplateLoading] = useState(false);
  const [workflowTemplateSaving, setWorkflowTemplateSaving] = useState(false);
  const [workflowTemplateError, setWorkflowTemplateError] = useState<string | null>(null);
  const [assetDownloadError, setAssetDownloadError] = useState<string | null>(null);
  const [isDownloadingAssets, setIsDownloadingAssets] = useState(false);
  const [copyNotice, setCopyNotice] = useState<string | null>(null);
  const copyTimeoutRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const reproduceJobIdRef = useRef<string | null>(null);

  const resetWorkflowTemplateState = useCallback(() => {
    setWorkflowTemplateDraft("");
    setWorkflowTemplateOriginal("");
    setWorkflowTemplateProvider(null);
    setWorkflowTemplateError(null);
    setWorkflowTemplateLoading(false);
    setWorkflowTemplateSaving(false);
  }, []);

  const loadWorkflowTemplate = useCallback(
    async (provider: string) => {
      setWorkflowTemplateLoading(true);
      setWorkflowTemplateError(null);
      try {
        const response = await apiFetch(
          `${apiBase}/api/prompts/workflow?videoProvider=${encodeURIComponent(provider)}`,
        );
        if (!response.ok) {
          throw new Error(await response.text());
        }
        const data = await response.json();
        const templateText = (data.template ?? "").toString();
        setWorkflowTemplateOriginal(templateText);
        setWorkflowTemplateDraft(templateText);
        setWorkflowTemplateProvider(provider);
      } catch (err) {
        console.error(err);
        setWorkflowTemplateError(
          "Unable to load prompt template. Confirm the backend exposes /api/prompts/workflow.",
        );
      } finally {
        setWorkflowTemplateLoading(false);
      }
    },
    [apiBase, apiFetch],
  );

  // Video insight state
  const [insightJobs, setInsightJobs] = useState<VideoInsightJob[]>([]);
  const [insightJob, setInsightJob] = useState<VideoInsightJob | null>(null);
  const [activeInsightJobId, setActiveInsightJobId] = useState<string | null>(null);
  const [selectedInsightJobs, setSelectedInsightJobs] = useState<Set<string>>(new Set());
  const [insightFile, setInsightFile] = useState<File | null>(null);
  const [insightNotes, setInsightNotes] = useState("");
  const [insightError, setInsightError] = useState<string | null>(null);
  const [insightFormError, setInsightFormError] = useState<string | null>(null);
  const [isSubmittingInsight, setIsSubmittingInsight] = useState(false);

  // Video reproduce state
  const [reproduceJobs, setReproduceJobs] = useState<VideoReproduceJob[]>([]);
  const [reproduceJob, setReproduceJob] = useState<VideoReproduceJob | null>(null);
  const [activeReproduceJobId, setActiveReproduceJobId] = useState<string | null>(null);
  const [selectedReproduceJobs, setSelectedReproduceJobs] = useState<Set<string>>(new Set());
  const [reproduceFile, setReproduceFile] = useState<File | null>(null);
  const [reproduceNotes, setReproduceNotes] = useState("");
  const [reproduceThreads, setReproduceThreads] = useState("3");
  const [reproduceImageGenerator, setReproduceImageGenerator] = useState<ImageGeneratorOption>(
    IMAGE_GENERATOR_OPTIONS[0].value,
  );
  const [reproduceSegmentation, setReproduceSegmentation] =
    useState<ReproduceSegmentationOption>(SEGMENTATION_OPTIONS[0].value);
  const [reproduceReferenceMode, setReproduceReferenceMode] =
    useState<ReferenceFrameMode>(REFERENCE_FRAME_OPTIONS[0].value);
  const [reproduceError, setReproduceError] = useState<string | null>(null);
  const [reproduceFormError, setReproduceFormError] = useState<string | null>(null);
  const [isSubmittingReproduce, setIsSubmittingReproduce] = useState(false);
  const [regeneratingSegment, setRegeneratingSegment] = useState<number | null>(null);
  const [segmentPrompts, setSegmentPrompts] = useState<Record<number, string>>({});

  // Modal states
  const [isCreateWorkflowModalOpen, setIsCreateWorkflowModalOpen] = useState(false);
  const [isCreateSingleVideoModalOpen, setIsCreateSingleVideoModalOpen] = useState(false);
  const [isCreateInsightModalOpen, setIsCreateInsightModalOpen] = useState(false);
  const [isCreateReproduceModalOpen, setIsCreateReproduceModalOpen] = useState(false);
  const [isJobDetailModalOpen, setIsJobDetailModalOpen] = useState(false);

  const loadCombinedJobs = useCallback(async () => {
    try {
      const response = await apiFetch(`${apiBase}/api/workflows`);
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
  }, [apiBase, apiFetch]);

  const loadSingleJobs = useCallback(async () => {
    try {
      const response = await apiFetch(`${apiBase}/api/single-videos`);
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
  }, [apiBase, apiFetch]);

  const loadInsightJobs = useCallback(async () => {
    try {
      const response = await apiFetch(`${apiBase}/api/video-insights`);
      if (!response.ok) {
        throw new Error(await response.text());
      }
      const data: VideoInsightJob[] = await response.json();
      setInsightJobs(data);
      if (data.length === 0) {
        setInsightJob(null);
        setActiveInsightJobId(null);
      } else {
        setInsightJob((current) =>
          current && data.some((item) => item.job_id === current.job_id) ? current : data[0],
        );
        setActiveInsightJobId((current) =>
          current && data.some((item) => item.job_id === current) ? current : data[0].job_id,
        );
      }
      setSelectedInsightJobs((previous) => {
        if (previous.size === 0) return previous;
        const next = new Set<string>();
        data.forEach((item) => {
          if (previous.has(item.job_id)) {
            next.add(item.job_id);
          }
        });
        return next;
      });
      setInsightError(null);
    } catch (err) {
      console.error(err);
      setInsightError("Unable to load video insights. Verify the API endpoint.");
    }
  }, [apiBase, apiFetch]);

  const loadReproduceJobs = useCallback(async () => {
    try {
      const response = await apiFetch(`${apiBase}/api/reproduce`);
      if (!response.ok) {
        throw new Error(await response.text());
      }
      const data: VideoReproduceJob[] = await response.json();
      setReproduceJobs(data);
      if (data.length === 0) {
        setReproduceJob(null);
        setActiveReproduceJobId(null);
      } else {
        setReproduceJob((current) =>
          current && data.some((item) => item.job_id === current.job_id) ? current : data[0],
        );
        setActiveReproduceJobId((current) =>
          current && data.some((item) => item.job_id === current) ? current : data[0].job_id,
        );
      }
      setSelectedReproduceJobs((previous) => {
        if (previous.size === 0) return previous;
        const next = new Set<string>();
        data.forEach((item) => {
          if (previous.has(item.job_id)) {
            next.add(item.job_id);
          }
        });
        return next;
      });
      setReproduceError(null);
    } catch (err) {
      console.error(err);
      setReproduceError("Unable to load reproduce jobs. Verify the API endpoint.");
    }
  }, [apiBase, apiFetch]);

  useEffect(() => {
    if (!isAuthenticated) {
      setJobs([]);
      setJob(null);
      setActiveJobId(null);
      setSingleJobs([]);
      setSingleJob(null);
      setActiveSingleJobId(null);
      setInsightJobs([]);
      setInsightJob(null);
      setActiveInsightJobId(null);
      setSelectedInsightJobs(new Set());
      setReproduceJobs([]);
      setReproduceJob(null);
      setActiveReproduceJobId(null);
      setSelectedReproduceJobs(new Set());
      setSegmentPrompts({});
      setActiveVideoProvider(null);
      setAvailableVideoProviders([...VIDEO_PROVIDER_OPTIONS]);
      setWorkflowVideoProvider(null);
      setSingleVideoProvider(null);
      return;
    }
    void loadCombinedJobs();
    void loadSingleJobs();
    void loadInsightJobs();
    void loadReproduceJobs();
  }, [
    isAuthenticated,
    loadCombinedJobs,
    loadSingleJobs,
    loadInsightJobs,
    loadReproduceJobs,
  ]);

  useEffect(() => {
    if (!isAuthenticated) {
      setActiveVideoProvider(null);
      setAvailableVideoProviders([...VIDEO_PROVIDER_OPTIONS]);
      return;
    }
    let disposed = false;
    const fetchProvider = async () => {
      try {
        const response = await apiFetch(`${apiBase}/api/providers/active`);
        if (!response.ok) {
          throw new Error(await response.text());
        }
        const data: { active?: string; available?: string[] } = await response.json();
        if (!disposed) {
          const normalizedAvailable = Array.isArray(data.available)
            ? data.available
                .map((value) => normalizeProvider(value))
                .filter((value): value is string => Boolean(value))
            : [];
          const providerChoices =
            normalizedAvailable.length > 0
              ? normalizedAvailable
              : [...VIDEO_PROVIDER_OPTIONS];
          setAvailableVideoProviders(providerChoices);
          const normalizedActive = normalizeProvider(data.active);
          setActiveVideoProvider(normalizedActive ?? providerChoices[0] ?? null);
        }
      } catch (err) {
        console.error(err);
        if (!disposed) {
          setActiveVideoProvider(null);
          setAvailableVideoProviders([...VIDEO_PROVIDER_OPTIONS]);
        }
      }
    };
    void fetchProvider();
    return () => {
      disposed = true;
    };
  }, [isAuthenticated, apiBase, apiFetch]);

  useEffect(() => {
    setWorkflowVideoProvider((current) => {
      if (current && availableVideoProviders.includes(current)) {
        return current;
      }
      if (activeVideoProvider && availableVideoProviders.includes(activeVideoProvider)) {
        return activeVideoProvider;
      }
      return availableVideoProviders[0] ?? VIDEO_PROVIDER_OPTIONS[0];
    });
  }, [availableVideoProviders, activeVideoProvider]);

  useEffect(() => {
    setSingleVideoProvider((current) => {
      if (current && availableVideoProviders.includes(current)) {
        return current;
      }
      if (activeVideoProvider && availableVideoProviders.includes(activeVideoProvider)) {
        return activeVideoProvider;
      }
      return availableVideoProviders[0] ?? VIDEO_PROVIDER_OPTIONS[0];
    });
  }, [availableVideoProviders, activeVideoProvider]);

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
        const response = await apiFetch(`${apiBase}/api/workflows/${activeJobId}`);
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
  }, [activeJobId, isAuthenticated, apiBase, apiFetch]);

  useEffect(() => {
    setScenePromptEdits({});
    setSceneRegenerating({});
    setSceneRegenerationTargets({});
  }, [job?.job_id]);

  useEffect(() => {
    if (!job?.scenes?.length) {
      setActiveSceneOrder(null);
      return;
    }
    setActiveSceneOrder((current) => {
      if (current && job.scenes.some((scene) => scene.order === current)) {
        return current;
      }
      return job.scenes[0].order;
    });
  }, [job?.scenes]);

  useEffect(() => {
    if (!job?.scenes?.length) {
      return;
    }
    if (!Object.keys(sceneRegenerating).length) {
      return;
    }
    const nextTargets = { ...sceneRegenerationTargets };
    const nextFlags = { ...sceneRegenerating };
    let changed = false;

    job.scenes.forEach((scene) => {
      if (!nextFlags[scene.order]) {
        return;
      }
      const versions = scene.versions ?? [];
      const versionCount = versions.length;
      const latestVersionId = versionCount ? versions[versionCount - 1].version_id : null;
      const target = nextTargets[scene.order];
      const hasNewVersion =
        versionCount > (target?.versionCount ?? 0) ||
        (target?.latestVersionId && latestVersionId && target.latestVersionId !== latestVersionId);

      if (hasNewVersion || job.status === "failed") {
        delete nextFlags[scene.order];
        delete nextTargets[scene.order];
        changed = true;
      }
    });

    if (changed) {
      setSceneRegenerationTargets(nextTargets);
      setSceneRegenerating(nextFlags);
    }
  }, [job?.scenes, job?.status, sceneRegenerating, sceneRegenerationTargets]);

  useEffect(() => {
    if (!reproduceJob) {
      reproduceJobIdRef.current = null;
      setSegmentPrompts({});
      return;
    }
    const derivePrompt = (segment: VideoReproduceSegment) => {
      const activeVersion = segment.versions.find((version) => version.version_id === segment.selected_version_id);
      const fallback = segment.versions.at(-1);
      return activeVersion?.prompt ?? fallback?.prompt ?? segment.description;
    };
    if (reproduceJobIdRef.current !== reproduceJob.job_id) {
      reproduceJobIdRef.current = reproduceJob.job_id;
      const defaults: Record<number, string> = {};
      reproduceJob.segments.forEach((segment) => {
        defaults[segment.order] = derivePrompt(segment);
      });
      setSegmentPrompts(defaults);
      return;
    }
    setSegmentPrompts((previous) => {
      const next = { ...previous };
      reproduceJob.segments.forEach((segment) => {
        if (next[segment.order] === undefined) {
          next[segment.order] = derivePrompt(segment);
        }
      });
      return next;
    });
  }, [reproduceJob]);

  useEffect(() => {
    if (!activeSingleJobId || !isAuthenticated) return;
    let disposed = false;
    let interval: ReturnType<typeof setInterval> | undefined;

    const fetchStatus = async () => {
      try {
        const response = await apiFetch(`${apiBase}/api/single-videos/${activeSingleJobId}`);
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
  }, [activeSingleJobId, isAuthenticated, apiBase, apiFetch]);

  useEffect(() => {
    if (!activeInsightJobId || !isAuthenticated) return;
    let disposed = false;
    let interval: ReturnType<typeof setInterval> | undefined;

    const fetchStatus = async () => {
      try {
        const response = await apiFetch(`${apiBase}/api/video-insights/${activeInsightJobId}`);
        if (!response.ok) {
          throw new Error(await response.text());
        }
        const data: VideoInsightJob = await response.json();
        if (disposed) {
          return;
        }
        setInsightJob(data);
        setInsightJobs((prev) => {
          const others = prev.filter((item) => item.job_id !== data.job_id);
          return [data, ...others];
        });
        if (terminalStatuses.has(data.status) && interval) {
          clearInterval(interval);
          interval = undefined;
        }
      } catch (err) {
        console.error(err);
        if (!disposed) {
          setInsightError("Unable to load video insights. Verify the API endpoint.");
        }
      }
    };

    void fetchStatus();
    interval = setInterval(fetchStatus, 5000);

    return () => {
      disposed = true;
      if (interval) {
        clearInterval(interval);
      }
    };
  }, [activeInsightJobId, isAuthenticated, apiBase, apiFetch]);

  useEffect(() => {
    if (!activeReproduceJobId || !isAuthenticated) return;
    let disposed = false;
    let interval: ReturnType<typeof setInterval> | undefined;

    const fetchStatus = async () => {
      try {
        const response = await apiFetch(`${apiBase}/api/reproduce/${activeReproduceJobId}`);
        if (!response.ok) {
          throw new Error(await response.text());
        }
        const data: VideoReproduceJob = await response.json();
        if (disposed) return;
        setReproduceJob(data);
        setReproduceJobs((prev) => {
          const index = prev.findIndex((item) => item.job_id === data.job_id);
          if (index !== -1) {
            const next = [...prev];
            next[index] = data;
            return next;
          }
          return [data, ...prev];
        });
        setReproduceError(null);
        if (terminalStatuses.has(data.status) && interval) {
          clearInterval(interval);
          interval = undefined;
        }
      } catch (err) {
        console.error(err);
        if (!disposed) {
          setReproduceError("Unable to load reproduce job details. Verify the API endpoint.");
        }
      }
    };

    void fetchStatus();
    interval = setInterval(fetchStatus, 5000);

    return () => {
      disposed = true;
      if (interval) {
        clearInterval(interval);
      }
    };
  }, [activeReproduceJobId, isAuthenticated, apiBase, apiFetch]);


  const singleProgressPercent = useMemo(() => {
    if (!singleJob) return 0;
    const value = Number.isFinite(singleJob.progress) ? singleJob.progress : 0;
    return Math.max(0, Math.min(100, Math.round(value * 100)));
  }, [singleJob]);

  const insightProgressPercent = useMemo(() => {
    if (!insightJob) return 0;
    const value = Number.isFinite(insightJob.progress) ? insightJob.progress : 0;
    return Math.max(0, Math.min(100, Math.round(value * 100)));
  }, [insightJob]);

  const reproduceProgressPercent = useMemo(() => {
    if (!reproduceJob) return 0;
    const value = Number.isFinite(reproduceJob.progress) ? reproduceJob.progress : 0;
    return Math.max(0, Math.min(100, Math.round(value * 100)));
  }, [reproduceJob]);

  const finalVideoUrl = buildAssetUrl(apiBase, job?.final_video_url);
  const hasScenes = Boolean(job?.scenes?.length);
  const isWorkflowActive = job?.status === "running" || job?.status === "queued";
  const canRecompose = Boolean(hasScenes && !isWorkflowActive);
  const currentStatusToken = getStatusToken(job?.status, language);
  const showSequenceReferences = job?.generation_mode === "sequence";
  const showAiFirstFrames = Boolean(job?.generate_ai_first_frame);
  const activeScene =
    job?.scenes?.find((scene) => scene.order === activeSceneOrder) ?? job?.scenes?.[0];
  const activeSceneVersions = activeScene?.versions ?? [];
  const activeSceneSelectedVersion =
    activeSceneVersions.find((version) => version.version_id === activeScene?.selected_version_id) ??
    activeSceneVersions[0];
  const activeSceneVideoUrl = buildAssetUrl(apiBase, activeSceneSelectedVersion?.video_url);
  const activeSceneFirstFrameUrl =
    showAiFirstFrames && activeScene ? buildAssetUrl(apiBase, activeScene.first_frame_url) : null;
  const activeSceneLastFrameUrl =
    showSequenceReferences && activeSceneSelectedVersion
      ? buildAssetUrl(
          apiBase,
          activeSceneSelectedVersion.last_frame_url ?? activeScene?.last_frame_url,
        )
      : null;

  const jobAspectRatio = aspectRatioOf(job?.resolution);
  const isPortraitJob = jobAspectRatio < 1;
  const characterRefUrls = (job?.character_ref_urls ?? [])
    .map((url) => buildAssetUrl(apiBase, url))
    .filter((url): url is string => Boolean(url));

  const singleVideoUrl = buildAssetUrl(apiBase, singleJob?.video_url);
  const singleStatusToken = getStatusToken(singleJob?.status, language);
  const insightVideoUrl = buildAssetUrl(apiBase, insightJob?.video_url);
  const insightResultUrl = buildAssetUrl(apiBase, insightJob?.result_url);
  const insightStatusToken = getStatusToken(insightJob?.status, language);
  const reproduceSourceVideoUrl = buildAssetUrl(apiBase, reproduceJob?.video_url);
  const reproduceFinalVideoUrl = buildAssetUrl(apiBase, reproduceJob?.final_video_url);
  const reproduceStatusToken = getStatusToken(reproduceJob?.status, language);
  const workflowProviderSelection =
    workflowVideoProvider ?? availableVideoProviders[0] ?? VIDEO_PROVIDER_OPTIONS[0];
  const singleProviderSelection =
    singleVideoProvider ?? availableVideoProviders[0] ?? VIDEO_PROVIDER_OPTIONS[0];
  const workflowDurationOptions = getDurationOptions(workflowProviderSelection);
  const singleDurationOptions = getDurationOptions(singleProviderSelection);


  const panelCardClass =
    "rounded-xl border border-zinc-200 dark:border-zinc-800 bg-white/80 dark:bg-zinc-900/50 p-6 shadow-2xl backdrop-blur-xl";
  const scrollToWorkspaceTop = () => {
    if (typeof window !== "undefined") {
      window.scrollTo({ top: 0, behavior: "smooth" });
    }
  };

  const openPrimaryModal = () => {
    if (activeTab === "combined") {
      setIsCreateWorkflowModalOpen(true);
    } else if (activeTab === "single") {
      setIsCreateSingleVideoModalOpen(true);
    } else if (activeTab === "reproduce") {
      setReproduceFormError(null);
      setIsCreateReproduceModalOpen(true);
    } else {
      setInsightFormError(null);
      setIsCreateInsightModalOpen(true);
    }
  };

  const handleExportInsight = useCallback(() => {
    if (!insightJob) return;
    if (typeof window === "undefined") return;
    try {
      const payload = {
        jobId: insightJob.job_id,
        filename: insightJob.filename,
        status: insightJob.status,
        startedAt: insightJob.started_at,
        completedAt: insightJob.completed_at,
        videoUrl: insightVideoUrl,
        resultUrl: insightResultUrl,
        notes: insightJob.notes,
        keyFrames: insightJob.key_frames,
        segments: insightJob.segments,
      };
      const blob = new Blob([JSON.stringify(payload, null, 2)], { type: "application/json" });
      const url = URL.createObjectURL(blob);
      const anchor = document.createElement("a");
      anchor.href = url;
      anchor.download = `${insightJob.job_id}_insights.json`;
      document.body.appendChild(anchor);
      anchor.click();
      anchor.remove();
      URL.revokeObjectURL(url);
    } catch (err) {
      console.error(err);
    }
  }, [insightJob, insightVideoUrl, insightResultUrl]);

  const handleCopyPrompt = async (text: string) => {
    if (copyTimeoutRef.current) {
      clearTimeout(copyTimeoutRef.current);
    }
    if (!text) {
      setCopyNotice("Prompt unavailable.");
      return;
    }
    if (typeof navigator === "undefined" || !navigator.clipboard) {
      setCopyNotice("Clipboard unavailable");
      copyTimeoutRef.current = setTimeout(() => setCopyNotice(null), 1800);
      return;
    }
    try {
      await navigator.clipboard.writeText(text);
      setCopyNotice("Copied to clipboard");
    } catch (err) {
      console.error(err);
      setCopyNotice("Unable to copy prompt");
    }
    copyTimeoutRef.current = setTimeout(() => setCopyNotice(null), 1800);
  };


  useEffect(() => {
    const allowed = getDurationOptions(workflowProviderSelection);
    if (!allowed.includes(sceneDuration)) {
      setSceneDuration(allowed[0]);
    }
  }, [workflowProviderSelection, sceneDuration]);

  useEffect(() => {
    const allowed = getDurationOptions(singleProviderSelection);
    if (!allowed.includes(singleDuration)) {
      setSingleDuration(allowed[0]);
    }
  }, [singleProviderSelection, singleDuration]);

  useEffect(() => {
    setAssetDownloadError(null);
    setIsDownloadingAssets(false);
  }, [job?.job_id]);

  const activeReviewMode = refinementReview?.mode;
  const activeReviewProvider = refinementReview?.videoProvider;

  useEffect(() => {
    if (activeReviewMode !== "workflow") {
      resetWorkflowTemplateState();
      return;
    }
    const provider = activeReviewProvider ?? workflowProviderSelection;
    if (!provider) {
      return;
    }
    if (
      workflowTemplateProvider === provider &&
      (workflowTemplateOriginal || workflowTemplateLoading)
    ) {
      return;
    }
    loadWorkflowTemplate(provider);
  }, [
    activeReviewMode,
    activeReviewProvider,
    workflowProviderSelection,
    workflowTemplateProvider,
    workflowTemplateOriginal,
    workflowTemplateLoading,
    loadWorkflowTemplate,
    resetWorkflowTemplateState,
  ]);



  useEffect(() => {
    let canceled = false;
    const bootstrapAuth = async () => {
      if (typeof window === "undefined") {
        if (!canceled) {
          setIsAuthReady(true);
        }
        return;
      }
      const storedHeader = window.localStorage.getItem(AUTH_HEADER_STORAGE_KEY);
      const result = await verifyAuthSession(storedHeader);
      if (canceled) {
        return;
      }
      if (result.ok) {
        setIsAuthenticated(true);
        setAuthHeader(storedHeader);
        setAuthError(null);
        window.localStorage.setItem(AUTH_STORAGE_KEY, "true");
      } else {
        clearAuthSession();
        setAuthError(result.error ?? null);
      }
      setIsAuthReady(true);
    };
    void bootstrapAuth();
    return () => {
      canceled = true;
    };
  }, [clearAuthSession, verifyAuthSession]);

  const handleLogin = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    const user = username.trim();
    const secret = password;
    if (!user || !secret) {
      setAuthError("Enter username and password.");
      return;
    }
    const encoded = typeof window !== "undefined" ? window.btoa(`${user}:${secret}`) : "";
    const header = `Basic ${encoded}`;
    const result = await verifyAuthSession(header);
    if (result.ok) {
      setIsAuthenticated(true);
      setAuthHeader(header);
      setAuthError(null);
      if (typeof window !== "undefined") {
        window.localStorage.setItem(AUTH_STORAGE_KEY, "true");
        window.localStorage.setItem(AUTH_HEADER_STORAGE_KEY, header);
      }
    } else {
      setAuthError(result.error ?? "Invalid username or password.");
    }
  };

  const handleLogout = () => {
    clearAuthSession();
    setAuthError(null);
    setUsername("");
    setPassword("");
    setActiveVideoProvider(null);
    setAvailableVideoProviders([...VIDEO_PROVIDER_OPTIONS]);
    setWorkflowVideoProvider(null);
    setSingleVideoProvider(null);
    setInsightJobs([]);
    setInsightJob(null);
    setActiveInsightJobId(null);
    setSelectedInsightJobs(new Set());
  };

  const toggleUseSceneImages = () => {
    setUseSceneImages((value) => {
      const next = !value;
      if (next) {
        setGenerateAiFirstFrames(false);
      } else {
        setSceneImageFiles([]);
        setSceneImagePreviews([]);
      }
      return next;
    });
  };

  const toggleGenerateAiFirstFrames = () => {
    setGenerateAiFirstFrames((value) => {
      const next = !value;
      if (next) {
        setUseSceneImages(false);
        setSceneImageFiles([]);
        setSceneImagePreviews([]);
      }
      return next;
    });
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

  const requestWorkflowRefinement = async (content: string, providerOverride?: string) => {
    const provider = providerOverride ?? workflowProviderSelection;
    const response = await apiFetch(`${apiBase}/api/refine/workflow`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      // The refiner sizes scenes to these settings, so send what will be rendered.
      body: JSON.stringify({
        script: content,
        videoProvider: provider,
        sceneDuration: Number(sceneDuration),
        resolution: combinedResolution,
        visualStyle,
        usesStartImage: generateAiFirstFrames || useSceneImages,
      }),
    });
    if (!response.ok) {
      throw new Error(await response.text());
    }
    const data = await response.json();
    return (data.refinedScript ?? data.refined_script ?? content).trim() || content;
  };

  const requestSingleRefinement = async (content: string) => {
    const response = await apiFetch(`${apiBase}/api/refine/single`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        prompt: content,
        videoProvider: singleProviderSelection,
        duration: Number(singleDuration),
        resolution: singleResolution,
        usesStartImage: Boolean(singleReferenceFile),
      }),
    });
    if (!response.ok) {
      throw new Error(await response.text());
    }
    const data = await response.json();
    return (data.refinedPrompt ?? data.refined_prompt ?? content).trim() || content;
  };

  const handleWorkflowTemplateReload = () => {
    const provider =
      workflowTemplateProvider ?? refinementReview?.videoProvider ?? workflowProviderSelection;
    if (!provider) {
      return;
    }
    loadWorkflowTemplate(provider);
  };

  const handleWorkflowTemplateSave = async () => {
    if (!refinementReview || refinementReview.mode !== "workflow") {
      return;
    }
    const provider =
      workflowTemplateProvider ?? refinementReview.videoProvider ?? workflowProviderSelection;
    if (!provider) {
      return;
    }
    if (!workflowTemplateDraft.trim()) {
      setWorkflowTemplateError(
        "Unable to update prompt template. Confirm the backend accepts template edits.",
      );
      return;
    }
    setWorkflowTemplateSaving(true);
    setWorkflowTemplateError(null);
    const previousRefinedText = refinementReview.refinedText;
    let templateUpdated = false;
    try {
      const response = await apiFetch(`${apiBase}/api/prompts/workflow`, {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ template: workflowTemplateDraft, videoProvider: provider }),
      });
      if (!response.ok) {
        throw new Error(await response.text());
      }
      const data = await response.json();
      const templateText = (data.template ?? "").toString();
      setWorkflowTemplateOriginal(templateText);
      setWorkflowTemplateDraft(templateText);
      templateUpdated = true;

      const refined = await requestWorkflowRefinement(refinementReview.originalText, provider);
      setRefinementReview((prev) =>
        prev && prev.mode === "workflow" ? { ...prev, refinedText: refined } : prev,
      );
      setRefinementDraft((currentDraft) => {
        if (!currentDraft || currentDraft === previousRefinedText) {
          return refined;
        }
        return currentDraft;
      });
    } catch (err) {
      console.error(err);
      setWorkflowTemplateError(
        templateUpdated
          ? "Template updated but GPT-6.1-sol rerun failed. Try refining again."
          : "Unable to update prompt template. Confirm the backend accepts template edits.",
      );
      return;
    } finally {
      setWorkflowTemplateSaving(false);
    }
  };

  const submitWorkflowRequest = async (scriptText: string, shouldRefine: boolean) => {
    const trimmedScript = scriptText.trim();
    if (!trimmedScript) {
      setCombinedError("Please provide a storyboard script before starting the workflow.");
      return false;
    }

    setIsSubmittingCombined(true);
    setCombinedError(null);
    try {
      const payload: Record<string, unknown> = { script: trimmedScript };
      payload.videoProvider = workflowProviderSelection;
      payload.useSceneImages = useSceneImages && !generateAiFirstFrames;
      payload.generateAiFirstFrame = generateAiFirstFrames;
      payload.resolution = combinedResolution;
      payload.generationMode = generationMode;
      payload.refineWithGpt5 = shouldRefine;
      payload.sceneDuration = Number(sceneDuration);
      payload.visualStyle = visualStyle;
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

      const response = await apiFetch(`${apiBase}/api/workflows`, {
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
      return true;
    } catch (err) {
      console.error(err);
      setCombinedError(
        "Unable to start workflow. Confirm the FastAPI server is running and accepts requests.",
      );
      return false;
    } finally {
      setIsSubmittingCombined(false);
    }
  };

  const submitSingleRequest = async (promptText: string, shouldRefine: boolean) => {
    const trimmedPrompt = promptText.trim();
    if (!trimmedPrompt) {
      setSingleError("Enter a prompt before requesting a video clip.");
      return false;
    }

    setIsSubmittingSingle(true);
    setSingleError(null);
    try {
      const payload: Record<string, unknown> = {
        prompt: trimmedPrompt,
        resolution: singleResolution,
        duration: Number(singleDuration),
        refineWithGpt5: shouldRefine,
      };
      payload.videoProvider = singleProviderSelection;

      if (singleReferenceFile) {
        payload.referenceImage = {
          filename: singleReferenceFile.name,
          dataUrl: await readFileAsDataUrl(singleReferenceFile),
          contentType: singleReferenceFile.type,
        };
      }

      const response = await apiFetch(`${apiBase}/api/single-videos`, {
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
      return true;
    } catch (err) {
      console.error(err);
      setSingleError("Unable to start the single-video job. Verify the backend is reachable.");
      return false;
    } finally {
      setIsSubmittingSingle(false);
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
      const response = await apiFetch(`${apiBase}/api/workflows/${job.job_id}/scenes/${sceneOrder}/remix`, {
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

  const handleRegenerateScene = async (sceneOrder: number, promptOverride?: string) => {
    if (!job?.job_id) {
      setCombinedError("Start a workflow before regenerating scenes.");
      return;
    }
    const trimmedOverride = promptOverride?.trim() ?? "";
    setCombinedError(null);
    const targetScene = job.scenes?.find((scene) => scene.order === sceneOrder);
    const targetVersions = targetScene?.versions ?? [];
    const baselineVersionCount = targetVersions.length;
    const baselineLatestVersionId =
      baselineVersionCount > 0 ? targetVersions[baselineVersionCount - 1].version_id : null;
    setSceneRegenerationTargets((prev) => ({
      ...prev,
      [sceneOrder]: { versionCount: baselineVersionCount, latestVersionId: baselineLatestVersionId },
    }));
    setSceneRegenerating((prev) => ({ ...prev, [sceneOrder]: true }));
    try {
      const hasOverride = Boolean(trimmedOverride);
      const response = await apiFetch(`${apiBase}/api/workflows/${job.job_id}/scenes/${sceneOrder}/regenerate`, {
        method: "POST",
        headers: hasOverride ? { "Content-Type": "application/json" } : undefined,
        body: hasOverride ? JSON.stringify({ prompt: trimmedOverride }) : undefined,
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
      if (hasOverride) {
        setScenePromptEdits((prev) => ({ ...prev, [sceneOrder]: trimmedOverride }));
      }
    } catch (err) {
      console.error(err);
      setCombinedError("Failed to regenerate this scene. Confirm the backend is reachable.");
      setSceneRegenerating((prev) => ({ ...prev, [sceneOrder]: false }));
      setSceneRegenerationTargets((prev) => {
        const next = { ...prev };
        delete next[sceneOrder];
        return next;
      });
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
      const response = await apiFetch(`${apiBase}/api/workflows/${job.job_id}/scenes/${sceneOrder}/select`, {
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
      const response = await apiFetch(`${apiBase}/api/workflows/${job.job_id}/recompose`, {
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

    if (refineWorkflowWithGpt5) {
      setIsSubmittingCombined(true);
      setCombinedError(null);
      try {
        const refined = await requestWorkflowRefinement(trimmed);
        setRefinementReview({
          mode: "workflow",
          originalText: trimmed,
          refinedText: refined,
          videoProvider: workflowProviderSelection ?? undefined,
        });
        setRefinementDraft(refined || trimmed);
      } catch (err) {
        console.error(err);
        setCombinedError(
          "Unable to refine the storyboard. Continue without refinement or try again.",
        );
      } finally {
        setIsSubmittingCombined(false);
      }
      return;
    }

    await submitWorkflowRequest(trimmed, false);
  };

  const handleDownloadWorkflowAssets = async () => {
    if (!job?.job_id) {
      return;
    }
    setAssetDownloadError(null);
    setIsDownloadingAssets(true);
    try {
      const response = await apiFetch(`${apiBase}/api/workflows/${job.job_id}/assets.zip`);
      if (!response.ok) {
        throw new Error(await response.text());
      }
      const blob = await response.blob();
      const url = window.URL.createObjectURL(blob);
      const anchor = document.createElement("a");
      anchor.href = url;
      anchor.download = `${job.job_id}_assets.zip`;
      document.body.appendChild(anchor);
      anchor.click();
      anchor.remove();
      window.URL.revokeObjectURL(url);
    } catch (err) {
      console.error(err);
      setAssetDownloadError(
        "Unable to download workflow assets. Confirm the backend can bundle files.",
      );
    } finally {
      setIsDownloadingAssets(false);
    }
  };

  const handleSingleSubmit = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    const trimmed = singlePrompt.trim();
    if (!trimmed) {
      setSingleError("Enter a prompt before requesting a video clip.");
      return;
    }

    if (refineSingleWithGpt5) {
      setIsSubmittingSingle(true);
      setSingleError(null);
      try {
        const refined = await requestSingleRefinement(trimmed);
        setRefinementReview({
          mode: "single",
          originalText: trimmed,
          refinedText: refined,
          videoProvider: singleProviderSelection ?? undefined,
        });
        setRefinementDraft(refined || trimmed);
      } catch (err) {
        console.error(err);
        setSingleError("Unable to refine the prompt. Continue without refinement or try again.");
      } finally {
        setIsSubmittingSingle(false);
      }
      return;
    }

    await submitSingleRequest(trimmed, false);
  };

  const handleInsightFileChange = (event: ChangeEvent<HTMLInputElement>) => {
    const file = event.target.files?.[0] ?? null;
    setInsightFile(file);
  };

  const handleInsightSubmit = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    if (!insightFile) {
      setInsightFormError("Upload video for analysis");
      return;
    }
    setInsightFormError(null);
    setIsSubmittingInsight(true);
    try {
      const formData = new FormData();
      formData.append("file", insightFile);
      const trimmedNotes = insightNotes.trim();
      if (trimmedNotes) {
        formData.append("notes", trimmedNotes);
      }
      const response = await apiFetch(`${apiBase}/api/video-insights`, {
        method: "POST",
        body: formData,
      });
      if (!response.ok) {
        throw new Error(await response.text());
      }
      const data: VideoInsightJob = await response.json();
      setInsightJobs((prev) => {
        const others = prev.filter((item) => item.job_id !== data.job_id);
        return [data, ...others];
      });
      setInsightJob(data);
      setActiveInsightJobId(data.job_id);
      setInsightFile(null);
      setInsightNotes("");
      setIsCreateInsightModalOpen(false);
      setInsightFormError(null);
      await loadInsightJobs();
    } catch (err) {
      console.error(err);
      setInsightFormError("Unable to analyze video. Confirm the backend endpoint is reachable.");
    } finally {
      setIsSubmittingInsight(false);
    }
  };

  const handleReproduceFileChange = (event: ChangeEvent<HTMLInputElement>) => {
    const file = event.target.files?.[0] ?? null;
    setReproduceFile(file);
  };

  const handleReproduceSubmit = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    if (!reproduceFile) {
      setReproduceFormError("Upload video for reproduction");
      return;
    }
    const threads = Number.parseInt(reproduceThreads.trim() || "0", 10);
    if (!Number.isFinite(threads) || threads < 1 || threads > 10) {
      setReproduceFormError("Thread count must be between 1 and 10.");
      return;
    }
    setReproduceFormError(null);
    setIsSubmittingReproduce(true);
    try {
      const formData = new FormData();
      formData.append("file", reproduceFile);
      if (reproduceNotes.trim()) {
        formData.append("notes", reproduceNotes.trim());
      }
      formData.append("threadCount", String(threads));
      formData.append("imageGenerator", reproduceImageGenerator);
      formData.append("segmentationBackend", reproduceSegmentation);
      formData.append("frameReferenceMode", reproduceReferenceMode);
      const response = await apiFetch(`${apiBase}/api/reproduce`, {
        method: "POST",
        body: formData,
      });
      if (!response.ok) {
        throw new Error(await response.text());
      }
      const data: VideoReproduceJob = await response.json();
      setReproduceJobs((prev) => [data, ...prev.filter((item) => item.job_id !== data.job_id)]);
      setReproduceJob(data);
      setActiveReproduceJobId(data.job_id);
      setReproduceFile(null);
      setReproduceNotes("");
      setSegmentPrompts({});
      setReproduceError(null);
      await loadReproduceJobs();
    } catch (err) {
      console.error(err);
      setReproduceError("Unable to start reproduce workflow. Confirm the backend is reachable.");
    } finally {
      setIsSubmittingReproduce(false);
    }
  };

  const toggleReproduceSelection = (jobId: string) => {
    setSelectedReproduceJobs((previous) => {
      const next = new Set(previous);
      if (next.has(jobId)) {
        next.delete(jobId);
      } else {
        next.add(jobId);
      }
      return next;
    });
  };

  const handleDeleteReproduceJobs = useCallback(async () => {
    if (!selectedReproduceJobs.size) return;
    let encounteredError: string | null = null;
    for (const jobId of selectedReproduceJobs) {
      try {
        const response = await apiFetch(`${apiBase}/api/reproduce/${jobId}`, {
          method: "DELETE",
        });
        if (!response.ok) {
          throw new Error(await response.text());
        }
      } catch (err) {
        console.error(err);
        encounteredError = "Unable to delete one or more reproduce jobs.";
        break;
      }
    }
    setSelectedReproduceJobs(new Set());
    if (encounteredError) {
      setReproduceError(encounteredError);
    }
    await loadReproduceJobs();
  }, [selectedReproduceJobs, apiBase, loadReproduceJobs, apiFetch]);

  const handleSegmentPromptChange = (order: number, value: string) => {
    setSegmentPrompts((previous) => ({ ...previous, [order]: value }));
  };

  const handleSegmentRegenerate = useCallback(
    async (order: number) => {
      if (!reproduceJob) return;
      const promptText = (segmentPrompts[order] ?? "").trim();
      if (!promptText) {
        setReproduceFormError("Copy or update the prompt before proceeding.");
        return;
      }
      setReproduceFormError(null);
      setRegeneratingSegment(order);
      try {
        const response = await apiFetch(
          `${apiBase}/api/reproduce/${reproduceJob.job_id}/segments/${order}/regenerate`,
          {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ prompt: promptText }),
          },
        );
        if (!response.ok) {
          throw new Error(await response.text());
        }
        const data: VideoReproduceJob = await response.json();
        setReproduceJob(data);
        setReproduceJobs((prev) => {
          const index = prev.findIndex((item) => item.job_id === data.job_id);
          if (index !== -1) {
            const next = [...prev];
            next[index] = data;
            return next;
          }
          return [data, ...prev];
        });
        setSegmentPrompts((previous) => ({ ...previous, [order]: promptText }));
        setReproduceError(null);
      } catch (err) {
        console.error(err);
        setReproduceError("Unable to regenerate this segment. Verify the API endpoint.");
      } finally {
        setRegeneratingSegment(null);
        await loadReproduceJobs();
      }
    },
    [apiBase, reproduceJob, segmentPrompts, loadReproduceJobs, apiFetch],
  );

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
      const response = await apiFetch(`${apiBase}/api/single-videos/${singleJob.job_id}/remix`, {
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

  const handleRefinementProceed = async () => {
    if (!refinementReview) return;
    const finalText =
      refinementDraft.trim() || refinementReview.refinedText || refinementReview.originalText;
    if (refinementReview.mode === "workflow") {
      const success = await submitWorkflowRequest(finalText, false);
      if (success) {
        setRefinementReview(null);
        setCopyNotice(null);
        resetWorkflowTemplateState();
      }
    } else {
      const success = await submitSingleRequest(finalText, false);
      if (success) {
        setRefinementReview(null);
        setCopyNotice(null);
        resetWorkflowTemplateState();
      }
    }
  };

  const handleRefinementDismiss = () => {
    setRefinementReview(null);
    setRefinementDraft("");
    setCopyNotice(null);
    resetWorkflowTemplateState();
  };

  const handleSelectSingleVersion = async (versionId: string) => {
    if (!singleJob?.job_id) {
      setSingleError("Start a single-video job before selecting versions.");
      return;
    }
    setSingleError(null);
    setSingleSelectionSubmitting((prev) => ({ ...prev, [versionId]: true }));
    try {
      const response = await apiFetch(`${apiBase}/api/single-videos/${singleJob.job_id}/select`, {
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

  const toggleInsightSelection = (jobId: string) => {
    setSelectedInsightJobs((prev) => {
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
        const response = await apiFetch(`${apiBase}/api/workflows/${jobId}`, {
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
        const response = await apiFetch(`${apiBase}/api/single-videos/${jobId}`, {
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

  const handleDeleteInsightJobs = async () => {
    if (!selectedInsightJobs.size) return;
    const jobIds = Array.from(selectedInsightJobs);
    let encounteredError: string | null = null;
    await Promise.all(
      jobIds.map(async (jobId) => {
        try {
          const response = await apiFetch(`${apiBase}/api/video-insights/${jobId}`, {
            method: "DELETE",
          });
          if (!response.ok) {
            throw new Error(await response.text());
          }
        } catch (err) {
          console.error(err);
          encounteredError = "Unable to delete one or more insight jobs.";
        }
      }),
    );
    if (encounteredError) {
      setInsightError(encounteredError);
    } else {
      setInsightError(null);
    }
    setSelectedInsightJobs(new Set());
    await loadInsightJobs();
  };

  if (!isAuthReady) {
    return (
      <div className="flex min-h-screen items-center justify-center bg-slate-50 dark:bg-slate-950 px-4 py-10 text-slate-900 dark:text-slate-100">
        <p className="text-sm text-slate-500 dark:text-slate-300">{translate("Checking session...")}</p>
      </div>
    );
  }

  if (!isAuthenticated) {
    return (
      <div className="flex min-h-screen items-center justify-center bg-slate-50 dark:bg-slate-950 px-4 py-10 text-slate-900 dark:text-slate-100">
        <div className="w-full max-w-md rounded-md border border-slate-200 dark:border-slate-800 bg-white dark:bg-slate-900 p-8 shadow-2xl">
          <div className="mb-6 flex flex-wrap items-center justify-between gap-3">
            <div className="flex items-center gap-3">
              <Image
                src="/logo-mark.png"
                alt=""
                aria-hidden="true"
                width={36}
                height={36}
                priority
                className="h-9 w-9 dark:brightness-0 dark:invert"
              />
              <div>
                <p className="text-[11px] font-semibold uppercase tracking-[0.3em] text-slate-500">{translate("Streams Studio")}</p>
                <p className="text-[9px] text-slate-400">{translate("video generation workflow by AI")}</p>
              </div>
            </div>
            <div className="flex items-center gap-3">
              <ThemeToggle theme={theme} onToggle={() => setTheme(theme === "dark" ? "light" : "dark")} />
              <LanguageToggle language={language} translate={translate} onChange={setLanguage} />
            </div>
          </div>
          <div className="space-y-2 text-center">
            <h1 className="text-2xl font-semibold text-slate-900 dark:text-slate-100">{translate("Sign in to continue")}</h1>
            <p className="text-sm text-slate-500 dark:text-slate-400">{translate("Use backend API credentials to access the dashboard.")}</p>
          </div>
          <form className="mt-6 space-y-4" onSubmit={handleLogin}>
            <div>
              <label htmlFor="username" className="text-sm font-medium text-zinc-600 dark:text-slate-300">
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
              <label htmlFor="password" className="text-sm font-medium text-zinc-600 dark:text-slate-300">
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
    <div className="relative min-h-screen overflow-hidden bg-zinc-50 dark:bg-zinc-950 text-slate-900 dark:text-slate-100">
      <div className="pointer-events-none absolute inset-0">
        <div className="absolute inset-0 bg-gradient-to-b from-white dark:from-zinc-900 via-transparent to-zinc-100 dark:to-zinc-950" />
        <div className="absolute left-[-18%] top-[10%] h-[420px] w-[420px] rounded-full bg-gradient-to-br from-indigo-500/10 via-purple-500/5 to-transparent blur-3xl" />
        <div className="absolute right-[-12%] top-[40%] h-[380px] w-[380px] rounded-full bg-gradient-to-tr from-blue-500/10 via-cyan-500/5 to-transparent blur-[140px]" />
      </div>

      <header className="sticky top-0 z-20 flex items-center justify-between border-b border-zinc-200 bg-white/80 px-6 py-3 backdrop-blur-xl dark:border-zinc-800 dark:bg-zinc-900/80">
        <div className="flex items-center gap-4">
          <div className="flex items-center gap-2.5">
            <Image
              src="/logo-mark.png"
              alt=""
              aria-hidden="true"
              width={28}
              height={28}
              priority
              className="h-7 w-7 dark:brightness-0 dark:invert"
            />
            <p className="text-[10px] font-semibold uppercase tracking-[0.35em] text-slate-500">{translate("Streams Studio")}</p>
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

        <div className="flex items-center gap-2">
          <ThemeToggle theme={theme} onToggle={() => setTheme(theme === "dark" ? "light" : "dark")} />
          <LanguageToggle language={language} translate={translate} onChange={setLanguage} />
          <div aria-hidden="true" className="mx-1 h-5 w-px bg-slate-200 dark:bg-slate-700" />
          <button
            type="button"
            onClick={handleLogout}
            className="inline-flex h-8 items-center gap-1.5 rounded-md px-2.5 text-sm font-medium text-slate-600 transition hover:bg-slate-100 hover:text-slate-900 focus-visible:outline-2 focus-visible:outline-blue-600 dark:text-slate-300 dark:hover:bg-slate-800 dark:hover:text-slate-100"
          >
            <ArrowRightStartOnRectangleIcon aria-hidden="true" className="h-4 w-4" />
            {translate("Sign out")}
          </button>
        </div>
      </header>

      <div className="relative mx-auto min-h-screen w-full px-4 pb-10 pt-6 max-w-7xl">
        <div className="mb-6 flex justify-between items-center">
          <h1 className="text-2xl font-bold text-slate-900 dark:text-slate-100">
            {activeTab === "combined"
              ? translate("Workflow Runs")
              : activeTab === "single"
                ? translate("Recent Single Jobs")
                : activeTab === "reproduce"
                  ? translate("Reproduce jobs")
                  : translate("Video insight jobs")}
          </h1>
          <button
            onClick={openPrimaryModal}
            className="rounded-md bg-slate-900 px-4 py-2 text-sm font-semibold text-white dark:bg-slate-100 dark:text-slate-900 shadow-sm hover:bg-slate-700 transition"
          >
            {activeTab === "combined"
              ? translate("Start Workflow")
              : activeTab === "single"
                ? translate("Generate Single Clip")
                : activeTab === "reproduce"
                  ? translate("Start reproduce workflow")
                  : translate("Analyze Video")}
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
              <WorkflowTable
                jobs={jobs}
                language={language}
                translate={translate}
                selectedJobIds={selectedCombinedJobs}
                onSelect={toggleCombinedSelection}
                onRowClick={(item) => {
                  setActiveJobId(item.job_id);
                  setJob(item);
                  setIsJobDetailModalOpen(true);
                }}
              />
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
              <SingleJobsTable
                jobs={singleJobs}
                language={language}
                translate={translate}
                selectedJobIds={selectedSingleJobs}
                onSelect={toggleSingleSelection}
                onRowClick={(item) => {
                  setActiveSingleJobId(item.job_id);
                  setSingleJob(item);
                  setIsJobDetailModalOpen(true);
                }}
              />
            </>
          )}

          {activeTab === "insight" && (
            <>
              <div className="flex flex-wrap items-center justify-between gap-3 mb-4">
                {insightError ? (
                  <p className="text-xs text-rose-500 dark:text-rose-300">{translate(insightError)}</p>
                ) : (
                  <p className="text-xs text-slate-500">
                    {translate("Describe using Azure Content Understanding")}
                  </p>
                )}
                <button
                  type="button"
                  onClick={handleDeleteInsightJobs}
                  disabled={!selectedInsightJobs.size}
                  className="inline-flex items-center gap-1 rounded border border-rose-200 dark:border-rose-900/50 bg-rose-50 dark:bg-rose-950/30 px-3 py-1.5 text-xs font-semibold text-rose-400 transition disabled:cursor-not-allowed disabled:opacity-50"
                >
                  <TrashIcon className="h-4 w-4" />
                  {translate("Delete Selected")}
                </button>
              </div>
              <InsightJobsTable
                jobs={insightJobs}
                language={language}
                translate={translate}
                selectedJobIds={selectedInsightJobs}
                onSelect={toggleInsightSelection}
                onRowClick={(item) => {
                  setActiveInsightJobId(item.job_id);
                  setInsightJob(item);
                  setIsJobDetailModalOpen(true);
                }}
              />
            </>
          )}

          {activeTab === "reproduce" && (
            <>
              <div className="flex flex-wrap items-center justify-between gap-3 mb-4">
                {reproduceError ? (
                  <p className="text-xs text-rose-500 dark:text-rose-300">{translate(reproduceError)}</p>
                ) : (
                  <p className="text-xs text-slate-500">
                    {translate("Investigate and reproduce uploaded videos.")}
                  </p>
                )}
                <button
                  type="button"
                  onClick={handleDeleteReproduceJobs}
                  disabled={!selectedReproduceJobs.size}
                  className="inline-flex items-center gap-1 rounded border border-rose-200 dark:border-rose-900/50 bg-rose-50 dark:bg-rose-950/30 px-3 py-1.5 text-xs font-semibold text-rose-400 transition disabled:cursor-not-allowed disabled:opacity-50"
                >
                  <TrashIcon className="h-4 w-4" />
                  {translate("Delete Selected")}
                </button>
              </div>
              <ReproduceJobsTable
                jobs={reproduceJobs}
                language={language}
                translate={translate}
                selectedJobIds={selectedReproduceJobs}
                onSelect={toggleReproduceSelection}
                onRowClick={(item) => {
                  setActiveReproduceJobId(item.job_id);
                  setReproduceJob(item);
                  setIsJobDetailModalOpen(true);
                }}
              />

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
          <form className="space-y-6" onSubmit={handleCombinedSubmit}>
            <Field
              label={translate("Script")}
              htmlFor="workflow-script"
              hint={
                <>
                  {translate("Start each scene with a heading like")}{" "}
                  <code className="rounded bg-slate-100 px-1 py-0.5 font-mono text-[11px] text-slate-700 dark:bg-slate-800 dark:text-slate-300">
                    **Scene 1 (Title)**
                  </code>
                  {translate(". Without headings, GPT-6.1-sol splits the script into scenes for you.")}
                </>
              }
            >
              <textarea
                id="workflow-script"
                className={`${inputClass} min-h-[220px] resize-y py-2.5 leading-6`}
                placeholder={translate("Enter script or storyboard prompt")}
                value={script}
                onChange={(event) => setScript(event.target.value)}
              />
            </Field>

            <div className="grid gap-4 sm:grid-cols-2">
              <Field label={translate("Video backend")} htmlFor="workflow-provider">
                <select
                  id="workflow-provider"
                  value={workflowProviderSelection}
                  onChange={(event) => setWorkflowVideoProvider(event.target.value)}
                  className={`${inputClass} h-9`}
                >
                  {availableVideoProviders.map((option) => (
                    <option key={option} value={option}>
                      {getVideoProviderLabel(option)}
                    </option>
                  ))}
                </select>
              </Field>
              <Field label={translate("Resolution")} htmlFor="workflow-resolution">
                <select
                  id="workflow-resolution"
                  value={combinedResolution}
                  onChange={(event) => setCombinedResolution(event.target.value as (typeof resolutionOptions)[number])}
                  className={`${inputClass} h-9`}
                >
                  {resolutionOptions.map((option) => (
                    <option key={option} value={option}>
                      {option}
                    </option>
                  ))}
                </select>
              </Field>
              <Field label={translate("Visual style")}>
                <SegmentedControl
                  ariaLabel={translate("Visual style")}
                  options={VISUAL_STYLE_OPTIONS.map((option) => ({ value: option.value, label: translate(option.label) }))}
                  value={visualStyle}
                  onChange={setVisualStyle}
                />
              </Field>
              <Field label={translate("Scene duration")}>
                <SegmentedControl
                  ariaLabel={translate("Scene duration")}
                  options={workflowDurationOptions.map((option) => ({ value: option, label: `${option}${translate("s")}` }))}
                  value={sceneDuration}
                  onChange={setSceneDuration}
                />
              </Field>
              <Field
                label={translate("Generation mode")}
                hint={translate(
                  generationMode === "sequence"
                    ? "Sequence mode accepts a single starting still, then reuses the last frame of each clip to guide the next scene for smoother continuity."
                    : "All scenes render at the same time. This is the fastest option.",
                )}
              >
                <SegmentedControl
                  ariaLabel={translate("Generation mode")}
                  options={[
                    { value: "parallel" as const, label: translate("Parallel") },
                    { value: "sequence" as const, label: translate("Sequence") },
                  ]}
                  value={generationMode}
                  onChange={setGenerationMode}
                />
              </Field>
            </div>

            <SettingsList>
              <SettingRow
                title={translate("AI first frames")}
                description={translate("Generate an opening image for each scene with gpt-image-2.5.")}
                checked={generateAiFirstFrames}
                onChange={toggleGenerateAiFirstFrames}
              />
              <SettingRow
                title={translate("Use my own start images")}
                description={translate("Upload one image per scene as its opening frame. Turns off AI first frames.")}
                checked={useSceneImages}
                onChange={toggleUseSceneImages}
              />
              <SettingRow
                title={translate("Refine script with GPT-6.1-sol")}
                description={translate("Use GPT-6.1-sol to polish the storyboard before planning scenes.")}
                checked={refineWorkflowWithGpt5}
                onChange={setRefineWorkflowWithGpt5}
              />
            </SettingsList>

            {useSceneImages ? (
              <div className="space-y-3">
                <label htmlFor="workflow-scene-images" className="block text-[13px] font-medium text-slate-700 dark:text-slate-300">
                  {translate("Upload reference stills")}
                </label>
                <input
                  id="workflow-scene-images"
                  type="file"
                  accept="image/png,image/jpeg"
                  multiple={generationMode !== "sequence"}
                  onChange={handleSceneImagesChange}
                  className={fileInputClass}
                />
                {sceneImagePreviews.length ? (
                  <div className="grid grid-cols-3 gap-3 md:grid-cols-4">
                    {sceneImagePreviews.map((preview, index) => (
                      <div
                        key={`${preview}-${index}`}
                        className="flex h-28 items-center justify-center overflow-hidden rounded-lg border border-slate-200 bg-slate-50 dark:border-slate-800 dark:bg-slate-800"
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
                  <p className="text-xs text-slate-500 dark:text-slate-400">{translate("Accepted formats: PNG, JPG. Images remain local until submission.")}</p>
                )}
              </div>
            ) : null}

            <div className="flex flex-col-reverse gap-3 border-t border-slate-200 pt-5 sm:flex-row sm:items-center sm:justify-between dark:border-slate-800">
              <p className="text-xs text-slate-500 dark:text-slate-400">
                {translate("API target:")} <span className="font-mono text-[11px]">{apiBase}/api/workflows</span>
              </p>
              <button type="submit" disabled={isSubmittingCombined} className={primaryButtonClass}>
                {isSubmittingCombined ? (
                  <>
                    <ArrowPathIcon className="h-4 w-4 animate-spin" />
                    {translate("Starting Workflow...")}
                  </>
                ) : (
                  <>
                    <PaperAirplaneIcon className="h-4 w-4" />
                    {translate("Start Workflow")}
                  </>
                )}
              </button>
            </div>
          </form>
        </Modal>

        <Modal
          isOpen={isCreateReproduceModalOpen}
          onClose={() => {
            setIsCreateReproduceModalOpen(false);
            setReproduceFormError(null);
          }}
          title={translate("Start reproduce workflow")}
        >
          {reproduceFormError && (
            <div className="mb-4 flex items-start gap-3 rounded border border-rose-200 dark:border-rose-900/50 bg-rose-50 dark:bg-rose-950/30 px-4 py-3 text-sm text-rose-400 shadow-md shadow-rose-900/20">
              <ExclamationTriangleIcon className="h-5 w-5 flex-none text-rose-500" />
              <span>{translate(reproduceFormError)}</span>
            </div>
          )}
          <p className="mb-4 text-sm text-slate-500 dark:text-slate-400">
            {translate("Upload video for reproduction")}
          </p>
          <form className="space-y-5" onSubmit={handleReproduceSubmit}>
            <div className="space-y-2">
              <label className="text-sm font-semibold text-slate-500 dark:text-slate-400">
                {translate("Upload video for reproduction")}
              </label>
              <input
                type="file"
                accept="video/*"
                onChange={handleReproduceFileChange}
                className="max-w-xs text-xs text-slate-500"
              />
              <p className="text-xs text-slate-500 dark:text-slate-400">
                {reproduceFile ? reproduceFile.name : translate("Uploaded video")}
              </p>
            </div>
            <div className="space-y-2">
              <label className="text-sm font-semibold text-slate-500 dark:text-slate-400">
                {translate("Content notes")}
              </label>
              <textarea
                className="min-h-[120px] w-full rounded border border-slate-300 dark:border-slate-700 bg-slate-100 dark:bg-slate-800 px-4 py-3 text-sm text-slate-900 dark:text-slate-100 shadow-inner placeholder:text-slate-500 transition focus:border-slate-500 focus:outline-none focus:ring-2 focus:ring-slate-800"
                placeholder={translate("Describe any context for this clip")}
                value={reproduceNotes}
                onChange={(event) => setReproduceNotes(event.target.value)}
              />
            </div>
            <div className="space-y-2">
              <label className="text-sm font-semibold text-slate-500 dark:text-slate-400">
                {translate("Parallel Kling renders")}
              </label>
              <input
                type="number"
                min={1}
                max={10}
                value={reproduceThreads}
                onChange={(event) => setReproduceThreads(event.target.value)}
                className="w-32 rounded border border-slate-300 dark:border-slate-700 bg-slate-100 dark:bg-slate-800 px-3 py-2 text-sm text-slate-900 dark:text-slate-100 shadow-inner focus:border-slate-500 focus:outline-none focus:ring-2 focus:ring-slate-800"
              />
            </div>
            <div className="space-y-2">
              <label className="text-sm font-semibold text-slate-500 dark:text-slate-400">
                {translate("Stylized frame generator")}
              </label>
              <p className="text-xs text-slate-500 dark:text-slate-400">
                {translate("Select how key frames get stylized before Kling renders.")}
              </p>
              <select
                value={reproduceImageGenerator}
                onChange={(event) => setReproduceImageGenerator(event.target.value as ImageGeneratorOption)}
                className="w-full rounded border border-slate-300 dark:border-slate-700 bg-slate-100 dark:bg-slate-800 px-3 py-2 text-sm text-slate-900 dark:text-slate-100 shadow-inner focus:border-slate-500 focus:outline-none focus:ring-2 focus:ring-slate-800"
              >
                {IMAGE_GENERATOR_OPTIONS.map((option) => (
                  <option key={option.value} value={option.value}>
                    {translate(option.label)}
                  </option>
                ))}
              </select>
            </div>
            <div className="space-y-2">
              <label className="text-sm font-semibold text-slate-500 dark:text-slate-400">
                {translate("Reference frame usage")}
              </label>
              <p className="text-xs text-slate-500 dark:text-slate-400">
                {translate(
                  "Choose whether Kling receives both start and end frames or only the first frame reference.",
                )}
              </p>
              <select
                value={reproduceReferenceMode}
                onChange={(event) => setReproduceReferenceMode(event.target.value as ReferenceFrameMode)}
                className="w-full rounded border border-slate-300 dark:border-slate-700 bg-slate-100 dark:bg-slate-800 px-3 py-2 text-sm text-slate-900 dark:text-slate-100 shadow-inner focus:border-slate-500 focus:outline-none focus:ring-2 focus:ring-slate-800"
              >
                {REFERENCE_FRAME_OPTIONS.map((option) => (
                  <option key={option.value} value={option.value}>
                    {translate(option.label)}
                  </option>
                ))}
              </select>
            </div>
            <div className="space-y-2">
              <label className="text-sm font-semibold text-slate-500 dark:text-slate-400">
                {translate("Segmentation backend")}
              </label>
              <p className="text-xs text-slate-500 dark:text-slate-400">
                {translate("Select how video segments are detected.")}
              </p>
              <select
                value={reproduceSegmentation}
                onChange={(event) => setReproduceSegmentation(event.target.value as ReproduceSegmentationOption)}
                className="w-full rounded border border-slate-300 dark:border-slate-700 bg-slate-100 dark:bg-slate-800 px-3 py-2 text-sm text-slate-900 dark:text-slate-100 shadow-inner focus:border-slate-500 focus:outline-none focus:ring-2 focus:ring-slate-800"
              >
                {SEGMENTATION_OPTIONS.map((option) => (
                  <option key={option.value} value={option.value}>
                    {translate(option.label)}
                  </option>
                ))}
              </select>
            </div>
            <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
              <button
                type="submit"
                disabled={isSubmittingReproduce}
                className="inline-flex items-center justify-center gap-2 rounded-md bg-slate-900 px-5 py-2.5 text-sm font-semibold text-white dark:bg-slate-100 dark:text-slate-900 shadow-sm shadow-slate-900/50 transition hover:bg-slate-700 focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-slate-400 disabled:cursor-not-allowed disabled:opacity-60"
              >
                {isSubmittingReproduce ? (
                  <>
                    <ArrowPathIcon className="h-5 w-5 animate-spin" />
                    {translate("Starting Workflow...")}
                  </>
                ) : (
                  <>
                    <PaperAirplaneIcon className="h-5 w-5" />
                    {translate("Start reproduce workflow")}
                  </>
                )}
              </button>
              <p className="text-xs text-slate-500">
                {translate("API target:")} <span className="font-mono text-[11px] text-slate-500">{apiBase}/api/reproduce</span>
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
          <form className="space-y-6" onSubmit={handleSingleSubmit}>
            <Field label={translate("Prompt")} htmlFor="single-prompt">
              <textarea
                id="single-prompt"
                className={`${inputClass} min-h-[180px] resize-y py-2.5 leading-6`}
                placeholder={translate("Describe the scene, motion, cinematography, and mood for the selected video backend...")}
                value={singlePrompt}
                onChange={(event) => setSinglePrompt(event.target.value)}
              />
            </Field>

            <div className="grid gap-4 sm:grid-cols-2">
              <Field label={translate("Video backend")} htmlFor="single-provider">
                <select
                  id="single-provider"
                  value={singleProviderSelection}
                  onChange={(event) => setSingleVideoProvider(event.target.value)}
                  className={`${inputClass} h-9`}
                >
                  {availableVideoProviders.map((option) => (
                    <option key={option} value={option}>
                      {getVideoProviderLabel(option)}
                    </option>
                  ))}
                </select>
              </Field>
              <Field label={translate("Resolution")} htmlFor="single-resolution">
                <select
                  id="single-resolution"
                  value={singleResolution}
                  onChange={(event) => setSingleResolution(event.target.value as (typeof resolutionOptions)[number])}
                  className={`${inputClass} h-9`}
                >
                  {resolutionOptions.map((option) => (
                    <option key={option} value={option}>
                      {option}
                    </option>
                  ))}
                </select>
              </Field>
              <Field label={translate("Duration")}>
                <SegmentedControl
                  ariaLabel={translate("Duration")}
                  options={singleDurationOptions.map((option) => ({ value: option, label: `${option}${translate("s")}` }))}
                  value={singleDuration}
                  onChange={setSingleDuration}
                />
              </Field>
            </div>

            <SettingsList>
              <SettingRow
                title={translate("Refine prompt with GPT-6.1-sol")}
                description={translate("Let GPT-6.1-sol tighten the single-scene prompt before submitting.")}
                checked={refineSingleWithGpt5}
                onChange={setRefineSingleWithGpt5}
              />
            </SettingsList>

            <div className="space-y-3">
              <label htmlFor="single-reference" className="block text-[13px] font-medium text-slate-700 dark:text-slate-300">
                {translate("Reference image (optional)")}
              </label>
              <input
                id="single-reference"
                type="file"
                accept="image/png,image/jpeg"
                onChange={handleReferenceImageChange}
                className={fileInputClass}
              />
              {singleReferencePreview && (
                <div className="flex h-40 w-32 items-center justify-center overflow-hidden rounded-lg border border-slate-200 bg-slate-50 dark:border-slate-800 dark:bg-slate-800">
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

            <div className="flex flex-col-reverse gap-3 border-t border-slate-200 pt-5 sm:flex-row sm:items-center sm:justify-between dark:border-slate-800">
              <p className="text-xs text-slate-500 dark:text-slate-400">
                {translate("API target:")} <span className="font-mono text-[11px]">{apiBase}/api/single-videos</span>
              </p>
              <button type="submit" disabled={isSubmittingSingle} className={primaryButtonClass}>
                {isSubmittingSingle ? (
                  <>
                    <ArrowPathIcon className="h-4 w-4 animate-spin" />
                    {translate("Sending Prompt...")}
                  </>
                ) : (
                  <>
                    <PaperAirplaneIcon className="h-4 w-4" />
                    {translate("Generate Clip")}
                  </>
                )}
              </button>
            </div>
          </form>
        </Modal>

        <Modal
          isOpen={isCreateInsightModalOpen}
          onClose={() => {
            setIsCreateInsightModalOpen(false);
            setInsightFormError(null);
          }}
          title={translate("Analyze Video")}
        >
          {insightFormError && (
            <div className="mb-4 flex items-start gap-3 rounded border border-rose-200 dark:border-rose-900/50 bg-rose-50 dark:bg-rose-950/30 px-4 py-3 text-sm text-rose-400 shadow-md shadow-rose-900/20">
              <ExclamationTriangleIcon className="h-5 w-5 flex-none text-rose-500" />
              <span>{translate(insightFormError)}</span>
            </div>
          )}
          <p className="mb-4 text-sm text-slate-500 dark:text-slate-400">
            {translate("Azure Content Understanding extracts key frames and segments.")}
          </p>
          <form className="space-y-5" onSubmit={handleInsightSubmit}>
            <div className="space-y-2">
              <label className="text-sm font-semibold text-slate-500 dark:text-slate-400">
                {translate("Upload video for analysis")}
              </label>
              <input
                type="file"
                accept="video/mp4,video/webm,video/quicktime,video/x-matroska"
                onChange={handleInsightFileChange}
                className="max-w-xs text-xs text-slate-500"
              />
              <p className="text-xs text-slate-500 dark:text-slate-400">
                {insightFile ? insightFile.name : translate("Uploaded video")}
              </p>
            </div>
            <div className="space-y-2">
              <label className="text-sm font-semibold text-slate-500 dark:text-slate-400">
                {translate("Notes (optional)")}
              </label>
              <textarea
                className="min-h-[120px] w-full rounded border border-slate-300 dark:border-slate-700 bg-slate-100 dark:bg-slate-800 px-4 py-3 text-sm text-slate-900 dark:text-slate-100 shadow-inner placeholder:text-slate-500 transition focus:border-slate-500 focus:outline-none focus:ring-2 focus:ring-slate-800"
                placeholder={translate("Describe any context for this clip")}
                value={insightNotes}
                onChange={(event) => setInsightNotes(event.target.value)}
              />
            </div>
            <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
              <button
                type="submit"
                disabled={isSubmittingInsight}
                className="inline-flex items-center justify-center gap-2 rounded-md bg-slate-900 px-5 py-2.5 text-sm font-semibold text-white dark:bg-slate-100 dark:text-slate-900 shadow-sm shadow-slate-900/50 transition hover:bg-slate-700 focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-slate-400 disabled:cursor-not-allowed disabled:opacity-60"
              >
                {isSubmittingInsight ? (
                  <>
                    <ArrowPathIcon className="h-5 w-5 animate-spin" />
                    {translate("Analyze Video")}
                  </>
                ) : (
                  <>
                    <PaperAirplaneIcon className="h-5 w-5" />
                    {translate("Analyze Video")}
                  </>
                )}
              </button>
              <p className="text-xs text-slate-500">
                {translate("API target:")} {" "}
                <span className="font-mono text-[11px] text-slate-500">{apiBase}/api/video-insights</span>
              </p>
            </div>
          </form>
        </Modal>

        <Modal isOpen={isJobDetailModalOpen} onClose={() => setIsJobDetailModalOpen(false)} title={translate("Job Details")} maxWidth="max-w-[95vw]">
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
                  <p className="text-xs uppercase tracking-wide text-slate-500">
                    {translate("Video backend")}: {(job.video_provider ?? translate("Unknown")).toUpperCase()}
                  </p>
                </div>
                <div className="flex flex-col gap-2">
                  <div className="flex flex-wrap items-center gap-2">
                    <span className={`text-xs font-semibold uppercase tracking-wide ${currentStatusToken.className}`}>{currentStatusToken.label}</span>
                    {job.script?.trim() ? (
                      <button
                        type="button"
                        onClick={() =>
                          setPromptPreview({
                            sceneLabel: translate("Workflow prompt"),
                            versionLabel: translate("Storyboard Script"),
                            prompt: job.script ?? "",
                          })
                        }
                        className="inline-flex items-center gap-2 rounded-md border border-slate-300 dark:border-slate-700 px-3 py-1 text-[11px] font-semibold text-zinc-600 dark:text-slate-300 transition hover:border-slate-400 dark:border-slate-600 hover:text-slate-800 dark:hover:text-slate-100"
                      >
                        <DocumentTextIcon className="h-4 w-4" />
                        {translate("View input prompt")}
                      </button>
                    ) : null}
                    <button
                      type="button"
                      onClick={handleDownloadWorkflowAssets}
                      disabled={!job?.job_id || isDownloadingAssets}
                      className="inline-flex items-center gap-2 rounded-md border border-slate-300 dark:border-slate-700 px-3 py-1 text-[11px] font-semibold text-zinc-600 dark:text-slate-300 transition hover:border-slate-400 dark:border-slate-600 hover:text-slate-800 dark:hover:text-slate-100 disabled:cursor-not-allowed disabled:opacity-60"
                    >
                      {isDownloadingAssets ? (
                        <ArrowPathIcon className="h-4 w-4 animate-spin" />
                      ) : (
                        <ArrowDownTrayIcon className="h-4 w-4" />
                      )}
                      {translate(isDownloadingAssets ? "Preparing ZIP..." : "Download scene assets")}
                    </button>
                  </div>
                  {assetDownloadError ? (
                    <p className="text-[11px] text-rose-500">{translate(assetDownloadError)}</p>
                  ) : null}
                </div>
              </div>

              <div className="mt-6">
                <WorkflowProgress job={job} language={language} />
              </div>

              <dl className="mt-6 grid gap-4 text-sm text-slate-500 dark:text-slate-400 sm:grid-cols-3">
                <div>
                  <dt className="text-xs uppercase tracking-wide text-slate-500">{translate("Started")}</dt>
                  <dd className="mt-1 text-zinc-600 dark:text-slate-300">{formatTimeDistance(job.started_at)}</dd>
                </div>
                <div>
                  <dt className="text-xs uppercase tracking-wide text-slate-500">{translate("Completed")}</dt>
                  <dd className="mt-1 text-zinc-600 dark:text-slate-300">{job.completed_at ? formatTimeDistance(job.completed_at) : "—"}</dd>
                </div>
                <div>
                  <dt className="text-xs uppercase tracking-wide text-slate-500">{translate("Resolution")}</dt>
                  <dd className="mt-1 text-zinc-600 dark:text-slate-300">{job.resolution}</dd>
                </div>
              </dl>

              {hasScenes && (
                <div className="mt-8 space-y-3">
                  <div className="flex items-center justify-between gap-3">
                    <h3 className="text-sm font-semibold text-zinc-600 dark:text-slate-300">{translate("Final Video")}</h3>
                    <button
                      type="button"
                      onClick={handleRecompose}
                      disabled={!canRecompose || isRecomposing}
                      className="inline-flex items-center gap-2 rounded-md border border-slate-300 dark:border-slate-700 px-3 py-1.5 text-xs font-semibold text-zinc-600 dark:text-slate-300 transition hover:border-slate-400 dark:border-slate-600 disabled:cursor-not-allowed disabled:opacity-60"
                    >
                      {isRecomposing ? <ArrowPathIcon className="h-4 w-4 animate-spin" /> : <ArrowPathIcon className="h-4 w-4" />}
                      {translate(finalVideoUrl ? "Regenerate Final Video" : "Compose Available Scenes")}
                    </button>
                  </div>
                  {finalVideoUrl ? (
                    <>
                      <div className="flex justify-center rounded-xl bg-slate-50 p-4 dark:bg-slate-950/40">
                        <VideoFrame
                          key={finalVideoUrl}
                          src={finalVideoUrl}
                          initialRatio={jobAspectRatio}
                          fit={isPortraitJob ? "height" : "width"}
                          sizeClass={isPortraitJob ? "h-[min(70vh,720px)]" : "max-w-5xl"}
                          fallbackText={translate("Your browser does not support HTML5 video.")}
                        />
                      </div>
                      <a
                        href={finalVideoUrl}
                        target="_blank"
                        rel="noreferrer"
                        className="inline-flex items-center gap-2 text-sm font-medium text-slate-500 dark:text-slate-400 hover:text-slate-900 dark:text-slate-100"
                      >
                        <PlayCircleIcon className="h-4 w-4" />
                        {translate("Open MP4 in new tab")}
                      </a>
                    </>
                  ) : null}
                </div>
              )}

              {job.scenes?.length ? (
                <div className="mt-8">
                  <div className="flex flex-wrap items-center justify-between gap-3">
                    <div>
                      <h2 className="flex items-center gap-2 text-lg font-semibold text-slate-900 dark:text-slate-100">
                        <PlayCircleIcon className="h-5 w-5 text-slate-500 dark:text-slate-400" />
                        {translate("Scene Renders")}
                      </h2>
                      <p className="text-xs text-slate-500 dark:text-slate-400">
                        {translate("Scenes this workflow")}: {job.scenes.length}
                      </p>
                    </div>
                    <div className="flex flex-wrap items-center gap-2 text-xs text-slate-600 dark:text-slate-300">
                      {[
                        job.resolution,
                        translate(job.generation_mode === "sequence" ? "Sequence" : "Parallel"),
                        translate(showAiFirstFrames ? "AI start stills enabled" : "No AI first frames"),
                      ].map((tag) => (
                        <span
                          key={tag}
                          className="rounded-full border border-slate-200 bg-white px-2.5 py-0.5 dark:border-slate-700 dark:bg-slate-900"
                        >
                          {tag}
                        </span>
                      ))}
                    </div>
                  </div>
                  <div className="mt-5 grid gap-6 lg:grid-cols-[300px_minmax(0,1fr)]">
                    <nav
                      aria-label={translate("Scenes")}
                      className="max-h-[80vh] overflow-y-auto rounded-xl border border-slate-200 bg-white p-2 dark:border-slate-800 dark:bg-slate-900"
                    >
                      <p className="px-2 pb-2 pt-1 text-xs font-medium text-slate-500 dark:text-slate-400">
                        {translate("Scenes")} ({job.scenes.length})
                      </p>
                      <ul className="space-y-1">
                        {job.scenes.map((scene) => {
                          const versions = scene.versions ?? [];
                          const selectedVersion =
                            versions.find((version) => version.version_id === scene.selected_version_id) ??
                            versions[0];
                          const previewUrl = buildAssetUrl(
                            apiBase,
                            showSequenceReferences
                              ? scene.last_frame_url ?? selectedVersion?.last_frame_url
                              : scene.first_frame_url ??
                                selectedVersion?.first_frame_url ??
                                selectedVersion?.last_frame_url,
                          );
                          const isActive = activeScene?.order === scene.order;
                          const isFailed = selectedVersion?.source === "failed";
                          const isRegenerating = Boolean(sceneRegenerating[scene.order]);
                          const thumbSize = isPortraitJob ? "h-20" : "h-14";
                          return (
                            <li key={scene.order}>
                              <button
                                type="button"
                                onClick={() => setActiveSceneOrder(scene.order)}
                                aria-current={isActive ? "true" : undefined}
                                className={`flex w-full items-center gap-3 rounded-lg p-2 text-left transition focus-visible:outline-2 focus-visible:outline-blue-600 ${
                                  isActive
                                    ? "bg-blue-50 ring-1 ring-blue-600/25 dark:bg-blue-500/10 dark:ring-blue-400/30"
                                    : "hover:bg-slate-50 dark:hover:bg-slate-800/60"
                                }`}
                              >
                                <div className="flex-none">
                                  {previewUrl ? (
                                    <ImageFrame
                                      src={previewUrl}
                                      alt={`${scene.label} ${translate("First frame")}`}
                                      initialRatio={jobAspectRatio}
                                      fit="height"
                                      sizeClass={thumbSize}
                                    />
                                  ) : (
                                    <div
                                      style={{ aspectRatio: String(jobAspectRatio) }}
                                      className={`${thumbSize} rounded-lg border border-dashed border-slate-300 dark:border-slate-700`}
                                    />
                                  )}
                                </div>
                                <div className="min-w-0 flex-1">
                                  <p className="truncate text-sm font-medium text-slate-900 dark:text-slate-100">
                                    {scene.label}
                                  </p>
                                  <p className="mt-1 flex items-center gap-2 text-xs text-slate-500 dark:text-slate-400">
                                    <span>{scene.duration_seconds}s</span>
                                    <span>v{versions.length}</span>
                                    {isFailed ? (
                                      <span className="font-medium text-rose-600 dark:text-rose-400">{translate("Failed")}</span>
                                    ) : null}
                                    {isRegenerating ? (
                                      <ArrowPathIcon aria-hidden="true" className="h-3.5 w-3.5 animate-spin text-blue-600" />
                                    ) : null}
                                  </p>
                                </div>
                              </button>
                            </li>
                          );
                        })}
                      </ul>
                    </nav>

                    <div className="max-h-[80vh] min-w-0 space-y-6 overflow-y-auto rounded-xl border border-slate-200 bg-white p-5 dark:border-slate-800 dark:bg-slate-900">
                      {activeScene ? (
                        <>
                          <div className="flex flex-wrap items-start justify-between gap-3">
                            <div className="min-w-0">
                              <h3 className="text-lg font-semibold text-slate-900 dark:text-slate-100">
                                {activeScene.label}
                              </h3>
                              <p className="mt-1 flex flex-wrap gap-x-4 gap-y-1 text-xs text-slate-500 dark:text-slate-400">
                                <span>
                                  {translate("Scene duration")}: {activeScene.duration_seconds} {translate("seconds")}
                                </span>
                                <span>
                                  {translate("Versions")}: {activeSceneVersions.length}
                                </span>
                                {activeSceneSelectedVersion ? (
                                  <span>
                                    {translate("Selected")}: {activeSceneSelectedVersion.label},{" "}
                                    {formatTimeDistance(activeSceneSelectedVersion.created_at)}
                                  </span>
                                ) : null}
                                {activeSceneSelectedVersion?.video_id ? (
                                  <span className="max-w-full truncate">
                                    {translate("Video ID:")} {activeSceneSelectedVersion.video_id}
                                  </span>
                                ) : null}
                              </p>
                            </div>
                            {activeSceneSelectedVersion?.prompt ? (
                              <button
                                type="button"
                                onClick={() =>
                                  setPromptPreview({
                                    sceneLabel: activeScene.label,
                                    versionLabel: activeSceneSelectedVersion.label,
                                    prompt: activeSceneSelectedVersion.prompt ?? "",
                                  })
                                }
                                className={secondaryButtonClass}
                              >
                                <DocumentTextIcon className="h-4 w-4" />
                                {translate("View system prompt")}
                              </button>
                            ) : null}
                          </div>

                          {activeSceneSelectedVersion?.source === "failed" ? (
                            <div className="flex items-start gap-2.5 rounded-lg bg-rose-50 px-3.5 py-3 dark:bg-rose-500/10">
                              <ExclamationTriangleIcon className="mt-0.5 h-4 w-4 flex-none text-rose-600 dark:text-rose-400" />
                              <div>
                                <p className="text-sm font-medium text-rose-700 dark:text-rose-300">
                                  {translate("Scene generation blocked")}
                                </p>
                                <p className="text-xs text-rose-600 dark:text-rose-300">
                                  {translate("Generation blocked. Update the prompt and regenerate this scene.")}
                                </p>
                              </div>
                            </div>
                          ) : null}

                          {sceneRegenerating[activeScene.order] ? (
                            <div className="flex items-center gap-2.5 rounded-lg bg-blue-50 px-3.5 py-3 text-sm text-blue-700 dark:bg-blue-500/10 dark:text-blue-300">
                              <ArrowPathIcon className="h-4 w-4 flex-none animate-spin" />
                              <span className="font-medium">{translate("Scene regeneration in progress")}</span>
                              <span className="text-blue-600/80 dark:text-blue-300/80">{translate("Awaiting version refresh...")}</span>
                            </div>
                          ) : null}

                          <div
                            className={`grid gap-6 ${
                              isPortraitJob
                                ? "lg:grid-cols-[auto_minmax(0,1fr)]"
                                : "xl:grid-cols-[minmax(0,3fr)_minmax(320px,2fr)]"
                            }`}
                          >
                            <div className="min-w-0">
                              {activeSceneVideoUrl ? (
                                <VideoFrame
                                  key={`${activeSceneSelectedVersion?.version_id ?? "scene"}-${activeSceneVideoUrl}`}
                                  src={activeSceneVideoUrl}
                                  initialRatio={jobAspectRatio}
                                  fit={isPortraitJob ? "height" : "width"}
                                  sizeClass={isPortraitJob ? "h-[min(64vh,600px)] min-h-[320px]" : ""}
                                  fallbackText={translate("Version video unavailable.")}
                                />
                              ) : (
                                <div
                                  style={{ aspectRatio: String(jobAspectRatio) }}
                                  className={`flex items-center justify-center rounded-lg border border-dashed border-slate-300 text-sm text-slate-500 dark:border-slate-700 dark:text-slate-400 ${
                                    isPortraitJob ? "h-[min(64vh,600px)] min-h-[320px]" : "w-full"
                                  }`}
                                >
                                  {translate("Video pending...")}
                                </div>
                              )}
                            </div>

                            <div className="min-w-0 space-y-6">
                              <Field label={translate("Edit prompt before regenerating")} htmlFor="scene-prompt-edit">
                                <textarea
                                  id="scene-prompt-edit"
                                  className={`${inputClass} min-h-[200px] resize-y py-2.5 leading-6`}
                                  value={
                                    scenePromptEdits[activeScene.order] ??
                                    activeSceneSelectedVersion?.prompt ??
                                    ""
                                  }
                                  onChange={(event) =>
                                    setScenePromptEdits((prev) => ({
                                      ...prev,
                                      [activeScene.order]: event.target.value,
                                    }))
                                  }
                                  placeholder={translate("Edit prompt before regenerating")}
                                />
                              </Field>
                              <div className="-mt-3 flex flex-wrap items-center justify-end gap-2">
                                <button
                                  type="button"
                                  onClick={() =>
                                    handleCopyPrompt(
                                      scenePromptEdits[activeScene.order] ??
                                        activeSceneSelectedVersion?.prompt ??
                                        "",
                                    )
                                  }
                                  className={secondaryButtonClass}
                                >
                                  <DocumentTextIcon className="h-4 w-4" />
                                  {translate("Copy prompt")}
                                </button>
                                <button
                                  type="button"
                                  onClick={() =>
                                    handleRegenerateScene(
                                      activeScene.order,
                                      scenePromptEdits[activeScene.order] ??
                                        activeSceneSelectedVersion?.prompt ??
                                        "",
                                    )
                                  }
                                  disabled={Boolean(sceneRegenerating[activeScene.order])}
                                  className={`${primaryButtonClass} h-8 px-3 text-xs`}
                                >
                                  <ArrowPathIcon
                                    className={`h-4 w-4 ${sceneRegenerating[activeScene.order] ? "animate-spin" : ""}`}
                                  />
                                  {sceneRegenerating[activeScene.order]
                                    ? translate("Regenerating...")
                                    : translate("Regenerate Scene")}
                                </button>
                              </div>

                              {job.video_provider === "sora2" ? (
                                <form
                                  className="space-y-3 border-t border-slate-200 pt-5 dark:border-slate-800"
                                  onSubmit={(event) => handleRemixSubmit(event, activeScene.order)}
                                >
                                  <Field
                                    label={translate("Remix prompt")}
                                    htmlFor="scene-remix-prompt"
                                    hint={
                                      <>
                                        {translate("Uses previous video ID:")}{" "}
                                        {activeSceneSelectedVersion?.video_id ?? translate("Unavailable")}
                                      </>
                                    }
                                  >
                                    <textarea
                                      id="scene-remix-prompt"
                                      className={`${inputClass} min-h-[96px] resize-y py-2.5 leading-6`}
                                      placeholder={translate("Describe how you want to remix this scene...")}
                                      value={remixPrompts[activeScene.order] ?? ""}
                                      onChange={(event) =>
                                        setRemixPrompts((prev) => ({
                                          ...prev,
                                          [activeScene.order]: event.target.value,
                                        }))
                                      }
                                    />
                                  </Field>
                                  <div className="flex justify-end">
                                    <button
                                      type="submit"
                                      disabled={
                                        !activeSceneSelectedVersion?.video_id || remixSubmitting[activeScene.order]
                                      }
                                      className={secondaryButtonClass}
                                    >
                                      <ArrowPathIcon
                                        className={`h-4 w-4 ${remixSubmitting[activeScene.order] ? "animate-spin" : ""}`}
                                      />
                                      {translate("Remix Scene")}
                                    </button>
                                  </div>
                                </form>
                              ) : null}
                            </div>
                          </div>

                          {characterRefUrls.length || activeSceneFirstFrameUrl || activeSceneLastFrameUrl ? (
                            <section className="border-t border-slate-200 pt-5 dark:border-slate-800">
                              <h4 className="text-sm font-semibold text-slate-900 dark:text-slate-100">
                                {translate("Scene materials")}
                              </h4>
                              <p className="mt-0.5 text-xs text-slate-500 dark:text-slate-400">
                                {translate("The images this clip was generated from. Click one to open it full size.")}
                              </p>
                              <div className="mt-4 flex flex-wrap items-start gap-x-8 gap-y-5">
                                {characterRefUrls.length ? (
                                  <MaterialGroup
                                    title={translate("Character references")}
                                    initialRatio={jobAspectRatio}
                                    sizeClass={isPortraitJob ? "h-40" : "h-28"}
                                    items={characterRefUrls.map((url, index) => ({
                                      key: url,
                                      src: url,
                                      label: `${translate("Character references")} ${index + 1}`,
                                      alt: `${translate("Character references")} ${index + 1}`,
                                    }))}
                                  />
                                ) : null}
                                {activeSceneFirstFrameUrl ? (
                                  <MaterialGroup
                                    title={translate("First frame")}
                                    initialRatio={jobAspectRatio}
                                    sizeClass={isPortraitJob ? "h-40" : "h-28"}
                                    items={[
                                      {
                                        key: activeSceneFirstFrameUrl,
                                        src: activeSceneFirstFrameUrl,
                                        label: translate("First frame"),
                                        alt: `${activeScene.label} ${translate("First frame")}`,
                                      },
                                    ]}
                                  />
                                ) : null}
                                {activeSceneLastFrameUrl ? (
                                  <MaterialGroup
                                    title={translate("Last frame reference")}
                                    initialRatio={jobAspectRatio}
                                    sizeClass={isPortraitJob ? "h-40" : "h-28"}
                                    items={[
                                      {
                                        key: activeSceneLastFrameUrl,
                                        src: activeSceneLastFrameUrl,
                                        label: translate("Last frame reference"),
                                        alt: `${activeScene.label} ${translate("Last frame reference")}`,
                                      },
                                    ]}
                                  />
                                ) : null}
                              </div>
                            </section>
                          ) : null}

                          <section className="border-t border-slate-200 pt-5 dark:border-slate-800">
                            <h4 className="text-sm font-semibold text-slate-900 dark:text-slate-100">
                              {translate("Versions")}{" "}
                              <span className="font-normal text-slate-500 dark:text-slate-400">({activeSceneVersions.length})</span>
                            </h4>
                            <ul className="mt-4 flex gap-3 overflow-x-auto pb-2">
                              {activeSceneVersions.map((version) => {
                                const versionVideoUrl = buildAssetUrl(apiBase, version.video_url);
                                const isActiveVersion = version.version_id === activeScene?.selected_version_id;
                                const isBusy = Boolean(selectionSubmitting[version.version_id]);
                                const versionMediaSize = isPortraitJob ? "h-56" : "h-32";
                                return (
                                  <li
                                    key={version.version_id}
                                    className={`flex w-min min-w-[220px] flex-none flex-col rounded-xl border bg-white p-3 dark:bg-slate-900 ${
                                      isActiveVersion
                                        ? "border-blue-600 ring-2 ring-blue-600/15 dark:border-blue-500"
                                        : "border-slate-200 dark:border-slate-800"
                                    }`}
                                  >
                                    <div className="flex justify-center rounded-lg bg-slate-50 dark:bg-slate-950/40">
                                      {versionVideoUrl ? (
                                        <VideoFrame
                                          key={`${version.version_id}-${versionVideoUrl}`}
                                          src={versionVideoUrl}
                                          initialRatio={jobAspectRatio}
                                          fit="height"
                                          sizeClass={versionMediaSize}
                                          fallbackText={translate("Version video unavailable.")}
                                        />
                                      ) : (
                                        <div
                                          className={`flex ${versionMediaSize} items-center justify-center px-4 text-xs text-slate-500 dark:text-slate-400`}
                                        >
                                          {translate("Video unavailable.")}
                                        </div>
                                      )}
                                    </div>
                                    <div className="mt-3 flex items-center justify-between gap-3">
                                      <p className="min-w-0 truncate text-sm font-medium text-slate-900 dark:text-slate-100">
                                        {version.label}
                                      </p>
                                      {isActiveVersion ? (
                                        <span className="inline-flex flex-none items-center gap-1 rounded-full bg-blue-50 px-2 py-0.5 text-xs font-medium text-blue-700 dark:bg-blue-500/10 dark:text-blue-300">
                                          <CheckCircleIcon aria-hidden="true" className="h-3.5 w-3.5" />
                                          {translate("Selected")}
                                        </span>
                                      ) : (
                                        <button
                                          type="button"
                                          onClick={() => handleSelectVersion(activeScene.order, version.version_id)}
                                          disabled={isBusy}
                                          className={`${secondaryButtonClass} h-7 flex-none px-2.5`}
                                        >
                                          {isBusy ? translate("Selecting...") : translate("Use this version")}
                                        </button>
                                      )}
                                    </div>
                                    <p className="mt-1 text-xs text-slate-500 dark:text-slate-400">
                                      {formatTimeDistance(version.created_at)}
                                    </p>
                                    {version.prompt ? (
                                      <button
                                        type="button"
                                        onClick={() =>
                                          setPromptPreview({
                                            sceneLabel: activeScene.label,
                                            versionLabel: version.label,
                                            prompt: version.prompt ?? "",
                                          })
                                        }
                                        className="mt-2 self-start text-xs font-medium text-blue-700 hover:underline dark:text-blue-300"
                                      >
                                        {translate("View system prompt")}
                                      </button>
                                    ) : null}
                                  </li>
                                );
                              })}
                            </ul>
                          </section>
                        </>
                      ) : (
                        <p className="text-sm text-slate-500 dark:text-slate-400">
                          {translate("Start a workflow before regenerating scenes.")}
                        </p>
                      )}
                    </div>
                  </div>
                </div>
              ) : null}

              {job.messages?.length ? (
                <details className="group mt-8">
                  <summary className="flex cursor-pointer items-center gap-2 text-lg font-semibold text-slate-900 dark:text-slate-100 list-none">
                    <ClockIcon className="h-5 w-5 text-slate-500 dark:text-slate-400" />
                    {translate("Workflow Timeline")}
                    <div className="ml-auto text-xs font-normal text-slate-500">
                      <span className="group-open:hidden">Show</span>
                      <span className="hidden group-open:inline">Hide</span>
                    </div>
                  </summary>
                  <ol className="mt-4 space-y-2 max-h-60 overflow-y-auto pr-2">
                    {job.messages.map((message, index) => {
                      const Icon = stageIcons[message.stage.toLowerCase()] ?? ArrowPathIcon;
                      return (
                        <li
                          key={`${message.timestamp}-${index}`}
                          className="relative flex gap-3 rounded border border-slate-200 dark:border-slate-800 bg-white/80 dark:bg-slate-900/50 p-2 shadow-inner shadow-black/20"
                        >
                          <div className="relative mt-0.5 flex h-7 w-7 items-center justify-center rounded-full border border-slate-300 dark:border-slate-700 bg-slate-100 dark:bg-slate-800">
                            <Icon className="h-4 w-4 text-zinc-600 dark:text-slate-400" />
                          </div>
                          <div>
                            <p className="text-sm text-zinc-600 dark:text-slate-300">{message.text}</p>
                            <p className="text-xs text-slate-500">
                              {formatTimestamp(message.timestamp)} · {stageLabel(message.stage, language)}
                            </p>
                          </div>
                        </li>
                      );
                    })}
                  </ol>
                </details>
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
                  <p className="text-xs uppercase tracking-wide text-slate-500">
                    {translate("Video backend")}: {(singleJob.video_provider ?? translate("Unknown")).toUpperCase()}
                  </p>
                </div>
                <div className="flex flex-wrap items-center gap-2">
                  <span className={`text-xs font-semibold uppercase tracking-wide ${singleStatusToken.className}`}>{singleStatusToken.label}</span>
                  {singleJob.prompt?.trim() ? (
                    <button
                      type="button"
                      onClick={() =>
                        setPromptPreview({
                          sceneLabel: translate("Single video prompt"),
                          versionLabel: translate("Submitted prompt"),
                          prompt: singleJob.prompt,
                        })
                      }
                      className="inline-flex items-center gap-2 rounded-md border border-slate-300 dark:border-slate-700 px-3 py-1 text-[11px] font-semibold text-zinc-600 dark:text-slate-300 transition hover:border-slate-400 dark:border-slate-600 hover:text-slate-800 dark:hover:text-slate-100"
                    >
                      <DocumentTextIcon className="h-4 w-4" />
                      {translate("View input prompt")}
                    </button>
                  ) : null}
                </div>
              </div>

              <div className="mt-6 space-y-3">
                <div className="flex items-center justify-between text-xs text-slate-500 dark:text-slate-400">
                  <span>{translate("Progress")}</span>
                  <span>{singleProgressPercent}%</span>
                </div>
                <div className="h-2.5 overflow-hidden rounded-full border border-slate-300 dark:border-slate-700 bg-slate-100 dark:bg-slate-800">
                  <div
                    className="h-full rounded-full bg-slate-500 dark:bg-slate-200 transition-[width] duration-500 ease-out"
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
                  <dd className="mt-1 text-zinc-600 dark:text-slate-300">{formatTimeDistance(singleJob.started_at)}</dd>
                </div>
                <div>
                  <dt className="text-xs uppercase tracking-wide text-slate-500">{translate("Completed")}</dt>
                  <dd className="mt-1 text-zinc-600 dark:text-slate-300">{singleJob.completed_at ? formatTimeDistance(singleJob.completed_at) : "—"}</dd>
                </div>
                <div>
                  <dt className="text-xs uppercase tracking-wide text-slate-500">{translate("Resolution")}</dt>
                  <dd className="mt-1 text-zinc-600 dark:text-slate-300">{singleJob.resolution}</dd>
                </div>
                <div>
                  <dt className="text-xs uppercase tracking-wide text-slate-500">{translate("Duration")}</dt>
                  <dd className="mt-1 text-zinc-600 dark:text-slate-300">
                    {singleJob.duration}
                    {translate("s")}
                  </dd>
                </div>
                <div>
                  <dt className="text-xs uppercase tracking-wide text-slate-500">{translate("Azure video ID:")}</dt>
                  <dd className="mt-1 text-zinc-600 dark:text-slate-300">{singleJob.video_id ?? translate("Unavailable")}</dd>
                </div>
              </dl>

              {singleVideoUrl ? (
                <div className="mt-8 space-y-3">
                  <h3 className="text-sm font-semibold text-zinc-600 dark:text-slate-300">{translate("Generated Video")}</h3>
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
                    <h3 className="text-sm font-semibold text-zinc-600 dark:text-slate-300">{translate("Remix Single Video")}</h3>
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
                  <h3 className="text-sm font-semibold text-zinc-600 dark:text-slate-300">{translate("Versions")}</h3>
                  <div className="space-y-3">
                    {singleJob.versions.map((version) => {
                      const versionVideoUrl = buildAssetUrl(apiBase, version.video_url);
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
                <details className="group mt-8">
                  <summary className="flex cursor-pointer items-center gap-2 text-lg font-semibold text-slate-900 dark:text-slate-100 list-none">
                    <ClockIcon className="h-5 w-5 text-slate-500 dark:text-slate-400" />
                    {translate("Job Timeline")}
                    <div className="ml-auto text-xs font-normal text-slate-500">
                      <span className="group-open:hidden">Show</span>
                      <span className="hidden group-open:inline">Hide</span>
                    </div>
                  </summary>
                  <ol className="mt-4 space-y-2 max-h-60 overflow-y-auto pr-2">
                    {singleJob.messages.map((message, index) => {
                      const Icon = stageIcons[message.stage.toLowerCase()] ?? ArrowPathIcon;
                      return (
                        <li
                          key={`${message.timestamp}-${index}`}
                          className="relative flex gap-3 rounded border border-slate-200 dark:border-slate-800 bg-white/80 dark:bg-slate-900/50 p-2 shadow-inner shadow-black/20"
                        >
                          <div className="relative mt-0.5 flex h-7 w-7 items-center justify-center rounded-full border border-slate-300 dark:border-slate-700 bg-slate-100 dark:bg-slate-800">
                            <Icon className="h-4 w-4 text-zinc-600 dark:text-slate-400" />
                          </div>
                          <div>
                            <p className="text-sm text-zinc-600 dark:text-slate-300">{message.text}</p>
                            <p className="text-xs text-slate-500">
                              {formatTimestamp(message.timestamp)} · {stageLabel(message.stage, language)}
                            </p>
                          </div>
                        </li>
                      );
                    })}
                  </ol>
                </details>
              ) : null}
            </div>
          )}

          {activeTab === "insight" && insightJob && (
            <div className="space-y-8">
              <div className="flex flex-wrap items-start justify-between gap-4">
                <div>
                  <h2 className="flex items-center gap-2 text-lg font-semibold text-slate-900 dark:text-slate-100">
                    <FilmIcon className="h-5 w-5 text-slate-500 dark:text-slate-400" />
                    {translate("Video insight")}
                  </h2>
                  <p className="text-xs uppercase tracking-wide text-slate-500">
                    {translate("Uploaded video")}: {insightJob.filename}
                  </p>
                  {insightJob.notes ? (
                    <p className="text-xs text-slate-500 dark:text-slate-400">{insightJob.notes}</p>
                  ) : null}
                </div>
                <span className={`text-xs font-semibold uppercase tracking-wide ${insightStatusToken.className}`}>
                  {insightStatusToken.label}
                </span>
              </div>

              <div className="mt-6 space-y-3">
                <div className="flex items-center justify-between text-xs text-slate-500 dark:text-slate-400">
                  <span>{translate("Progress")}</span>
                  <span>{insightProgressPercent}%</span>
                </div>
                <div className="h-2.5 overflow-hidden rounded-full border border-slate-300 dark:border-slate-700 bg-slate-100 dark:bg-slate-800">
                  <div
                    className="h-full rounded-full bg-slate-500 dark:bg-slate-200 transition-[width] duration-500 ease-out"
                    style={{ width: `${insightProgressPercent}%` }}
                  />
                </div>
                {insightJob.error && (
                  <div className="flex items-center gap-2 rounded-md border border-rose-200 dark:border-rose-900/50 bg-rose-50 dark:bg-rose-950/30 px-3 py-2 text-xs text-rose-400">
                    <ExclamationTriangleIcon className="h-4 w-4" />
                    {insightJob.error}
                  </div>
                )}
              </div>

              <dl className="mt-6 grid gap-4 text-sm text-slate-500 dark:text-slate-400 sm:grid-cols-3">
                <div>
                  <dt className="text-xs uppercase tracking-wide text-slate-500">{translate("Started")}</dt>
                  <dd className="mt-1 text-zinc-600 dark:text-slate-300">{formatTimeDistance(insightJob.started_at)}</dd>
                </div>
                <div>
                  <dt className="text-xs uppercase tracking-wide text-slate-500">{translate("Completed")}</dt>
                  <dd className="mt-1 text-zinc-600 dark:text-slate-300">
                    {insightJob.completed_at ? formatTimeDistance(insightJob.completed_at) : "—"}
                  </dd>
                </div>
                <div>
                  <dt className="text-xs uppercase tracking-wide text-slate-500">{translate("Content type")}</dt>
                  <dd className="mt-1 text-zinc-600 dark:text-slate-300">{insightJob.content_type ?? translate("Unavailable")}</dd>
                </div>
              </dl>

              <div className="mt-8 space-y-3">
                <h3 className="text-sm font-semibold text-zinc-600 dark:text-slate-300">
                  {translate("Result files")}
                </h3>
                {insightVideoUrl ? (
                  <>
                    <video
                      controls
                      className="max-h-[320px] w-full rounded border border-slate-200 dark:border-slate-800 bg-black/60"
                      src={insightVideoUrl}
                    >
                      {translate("Your browser does not support HTML5 video.")}
                    </video>
                    <a
                      href={insightVideoUrl}
                      target="_blank"
                      rel="noreferrer"
                      className="inline-flex items-center gap-2 text-sm font-medium text-slate-500 dark:text-slate-400 hover:text-slate-900 dark:text-slate-100"
                    >
                      <PlayCircleIcon className="h-4 w-4" />
                      {translate("Open analyzed video")}
                    </a>
                  </>
                ) : (
                  <div className="flex h-32 items-center justify-center rounded border border-dashed border-slate-300 dark:border-slate-700 bg-slate-100/50 dark:bg-slate-800/30 text-xs text-slate-500">
                    {translate("Video pending...")}
                  </div>
                )}
                {insightResultUrl ? (
                  <a
                    href={insightResultUrl}
                    target="_blank"
                    rel="noreferrer"
                    className="inline-flex items-center gap-2 text-xs font-semibold text-slate-500 dark:text-slate-300 hover:text-slate-900 dark:hover:text-slate-100"
                  >
                    <DocumentTextIcon className="h-4 w-4" />
                    {translate("Download JSON result")}
                  </a>
                ) : (
                  <p className="text-xs text-slate-500">{translate("JSON result unavailable.")}</p>
                )}
              </div>

              <div className="mt-8 flex flex-wrap items-center justify-between gap-3 rounded-md border border-slate-200 bg-white/60 px-4 py-3 text-sm dark:border-slate-800 dark:bg-slate-900/40">
                <div>
                  <p className="font-semibold text-slate-700 dark:text-slate-200">{translate("Export insights JSON")}</p>
                  <p className="text-xs text-slate-500 dark:text-slate-400">
                    {translate("Download processed insight data as JSON.")}
                  </p>
                </div>
                <button
                  type="button"
                  onClick={handleExportInsight}
                  className="inline-flex items-center gap-2 rounded-md border border-slate-300 bg-slate-100/70 px-3 py-1.5 text-xs font-semibold text-zinc-600 transition hover:border-slate-400 hover:text-slate-900 dark:border-slate-700 dark:bg-slate-800/60 dark:text-slate-300 dark:hover:text-white"
                >
                  <ArrowDownTrayIcon className="h-4 w-4" />
                  {translate("Export insights JSON")}
                </button>
              </div>

              <div className="mt-8 space-y-3">
                <h3 className="text-sm font-semibold text-zinc-600 dark:text-slate-300">
                  {translate("Key frames")}
                </h3>
                {insightJob.key_frames?.length ? (
                  <div className="overflow-x-auto">
                    <table className="min-w-full divide-y divide-slate-200 dark:divide-slate-800 text-sm">
                      <thead className="bg-slate-50 dark:bg-slate-800/50 text-[11px] uppercase tracking-wide text-slate-500">
                        <tr>
                          <th className="px-4 py-2 text-left">{translate("Timestamp")}</th>
                          <th className="px-4 py-2 text-left">{translate("Summary")}</th>
                          <th className="px-4 py-2 text-left">{translate("Confidence")}</th>
                          <th className="px-4 py-2 text-left">{translate("Tags")}</th>
                        </tr>
                      </thead>
                      <tbody className="divide-y divide-slate-200 dark:divide-slate-800 bg-white dark:bg-slate-900/40">
                        {insightJob.key_frames.map((frame) => (
                          <tr key={frame.frame_id ?? `${frame.timestamp}-${frame.description}`}>
                            <td className="px-4 py-2 text-xs text-slate-500 dark:text-slate-400">{frame.timestamp ?? "—"}</td>
                            <td className="px-4 py-2 text-xs text-slate-500 dark:text-slate-400">
                              {frame.description ?? "—"}
                            </td>
                            <td className="px-4 py-2 text-xs text-slate-500 dark:text-slate-400">
                              {frame.confidence != null ? `${Math.round(frame.confidence * 100)}%` : "—"}
                            </td>
                            <td className="px-4 py-2 text-xs text-slate-500 dark:text-slate-400">
                              {frame.tags?.length ? frame.tags.join(", ") : "—"}
                            </td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                ) : (
                  <p className="text-xs text-slate-500">{translate("No insights yet.")}</p>
                )}
              </div>

              <div className="mt-8 space-y-3">
                <h3 className="text-sm font-semibold text-zinc-600 dark:text-slate-300">
                  {translate("Video segments")}
                </h3>
                {insightJob.segments?.length ? (
                  <div className="overflow-x-auto">
                    <table className="min-w-full divide-y divide-slate-200 dark:divide-slate-800 text-sm">
                      <thead className="bg-slate-50 dark:bg-slate-800/50 text-[11px] uppercase tracking-wide text-slate-500">
                        <tr>
                          <th className="px-4 py-2 text-left">{translate("Start")}</th>
                          <th className="px-4 py-2 text-left">{translate("Start frame")}</th>
                          <th className="px-4 py-2 text-left">{translate("End")}</th>
                          <th className="px-4 py-2 text-left">{translate("End frame")}</th>
                          <th className="px-4 py-2 text-left">{translate("Summary")}</th>
                          <th className="px-4 py-2 text-left">{translate("Tags")}</th>
                        </tr>
                      </thead>
                      <tbody className="divide-y divide-slate-200 dark:divide-slate-800 bg-white dark:bg-slate-900/40">
                        {insightJob.segments.map((segment) => {
                          const startFrameUrl = buildAssetUrl(apiBase, segment.start_frame_url);
                          const endFrameUrl = buildAssetUrl(apiBase, segment.end_frame_url);
                          return (
                            <tr key={segment.segment_id ?? `${segment.start}-${segment.end}`}>
                              <td className="px-4 py-2 text-xs text-slate-500 dark:text-slate-400">
                                {formatSegmentTimestampDisplay(segment.start_seconds, segment.start)}
                              </td>
                              <td className="px-4 py-2">
                                {startFrameUrl ? (
                                  <a
                                    href={startFrameUrl}
                                    target="_blank"
                                    rel="noreferrer"
                                    className="inline-flex rounded border border-slate-200 dark:border-slate-700 shadow-sm"
                                    title={translate("View full image")}
                                  >
                                    <Image
                                      src={startFrameUrl}
                                      alt={`${translate("Start frame")} ${segment.segment_id ?? ""}`}
                                      width={120}
                                      height={68}
                                      className="h-16 w-24 object-cover"
                                      unoptimized
                                    />
                                  </a>
                                ) : (
                                  <span className="text-xs text-slate-400">—</span>
                                )}
                              </td>
                              <td className="px-4 py-2 text-xs text-slate-500 dark:text-slate-400">
                                {formatSegmentTimestampDisplay(segment.end_seconds, segment.end)}
                              </td>
                              <td className="px-4 py-2">
                                {endFrameUrl ? (
                                  <a
                                    href={endFrameUrl}
                                    target="_blank"
                                    rel="noreferrer"
                                    className="inline-flex rounded border border-slate-200 dark:border-slate-700 shadow-sm"
                                    title={translate("View full image")}
                                  >
                                    <Image
                                      src={endFrameUrl}
                                      alt={`${translate("End frame")} ${segment.segment_id ?? ""}`}
                                      width={120}
                                      height={68}
                                      className="h-16 w-24 object-cover"
                                      unoptimized
                                    />
                                  </a>
                                ) : (
                                  <span className="text-xs text-slate-400">—</span>
                                )}
                              </td>
                              <td className="px-4 py-2 text-xs text-slate-500 dark:text-slate-400">
                                {segment.summary ?? segment.label ?? "—"}
                              </td>
                              <td className="px-4 py-2 text-xs text-slate-500 dark:text-slate-400">
                                {segment.tags?.length ? segment.tags.join(", ") : "—"}
                              </td>
                            </tr>
                          );
                        })}
                      </tbody>
                    </table>
                  </div>
                ) : (
                  <p className="text-xs text-slate-500">{translate("No insights yet.")}</p>
                )}
              </div>

              {insightJob.messages?.length ? (
                <details className="group mt-8">
                  <summary className="flex cursor-pointer items-center gap-2 text-lg font-semibold text-slate-900 dark:text-slate-100 list-none">
                    <ClockIcon className="h-5 w-5 text-slate-500 dark:text-slate-400" />
                    {translate("Job Timeline")}
                    <div className="ml-auto text-xs font-normal text-slate-500">
                      <span className="group-open:hidden">Show</span>
                      <span className="hidden group-open:inline">Hide</span>
                    </div>
                  </summary>
                  <ol className="mt-4 space-y-2 max-h-60 overflow-y-auto pr-2">
                    {insightJob.messages.map((message, index) => {
                      const Icon = stageIcons[message.stage.toLowerCase()] ?? ArrowPathIcon;
                      return (
                        <li
                          key={`${message.timestamp}-${index}`}
                          className="relative flex gap-3 rounded border border-slate-200 dark:border-slate-800 bg-white/80 dark:bg-slate-900/50 p-2 shadow-inner shadow-black/20"
                        >
                          <div className="relative mt-0.5 flex h-7 w-7 items-center justify-center rounded-full border border-slate-300 dark:border-slate-700 bg-slate-100 dark:bg-slate-800">
                            <Icon className="h-4 w-4 text-zinc-600 dark:text-slate-400" />
                          </div>
                          <div>
                            <p className="text-sm text-zinc-600 dark:text-slate-300">{message.text}</p>
                            <p className="text-xs text-slate-500">
                              {formatTimestamp(message.timestamp)} · {stageLabel(message.stage, language)}
                            </p>
                          </div>
                        </li>
                      );
                    })}
                  </ol>
                </details>
              ) : null}
            </div>
          )}

          {activeTab === "reproduce" && reproduceJob && (
            <div className="space-y-8">
              <div className="flex flex-wrap items-start justify-between gap-3">
                <div>
                  <h3 className="text-lg font-semibold text-slate-900 dark:text-slate-100">{translate("Video reproduce")}</h3>
                  <p className="text-xs uppercase tracking-wide text-slate-500">
                    {translate("Uploaded video")}: {reproduceJob.filename}
                  </p>
                  {reproduceJob.notes ? (
                    <p className="text-xs text-slate-500 dark:text-slate-400">{reproduceJob.notes}</p>
                  ) : null}
                </div>
                <span className={`text-xs font-semibold uppercase tracking-wide ${reproduceStatusToken.className}`}>
                  {reproduceStatusToken.label}
                </span>
              </div>

              <div className="space-y-3">
                <div className="flex items-center justify-between text-xs text-slate-500 dark:text-slate-400">
                  <span>{translate("Progress")}</span>
                  <span>{reproduceProgressPercent}%</span>
                </div>
                <div className="h-2.5 overflow-hidden rounded-full border border-slate-300 dark:border-slate-700 bg-slate-100 dark:bg-slate-800">
                  <div
                    className="h-full rounded-full bg-slate-500 dark:bg-slate-200 transition-[width] duration-500 ease-out"
                    style={{ width: `${reproduceProgressPercent}%` }}
                  />
                </div>
                {reproduceJob.error ? (
                  <div className="flex items-center gap-2 rounded-md border border-rose-200 dark:border-rose-900/50 bg-rose-50 dark:bg-rose-950/30 px-3 py-2 text-xs text-rose-400">
                    <ExclamationTriangleIcon className="h-4 w-4" />
                    {reproduceJob.error}
                  </div>
                ) : null}
              </div>

              <dl className="grid gap-4 text-sm text-zinc-600 dark:text-slate-300 sm:grid-cols-4">
                <div>
                  <dt className="text-xs uppercase tracking-wide text-slate-500">{translate("Started")}</dt>
                  <dd className="mt-1">{formatTimeDistance(reproduceJob.started_at)}</dd>
                </div>
                <div>
                  <dt className="text-xs uppercase tracking-wide text-slate-500">{translate("Completed")}</dt>
                  <dd className="mt-1">{reproduceJob.completed_at ? formatTimeDistance(reproduceJob.completed_at) : "—"}</dd>
                </div>
                <div>
                  <dt className="text-xs uppercase tracking-wide text-slate-500">{translate("Resolution")}</dt>
                  <dd className="mt-1">{reproduceJob.resolution}</dd>
                </div>
                <div>
                  <dt className="text-xs uppercase tracking-wide text-slate-500">{translate("Image generator")}</dt>
                  <dd className="mt-1">
                    {translate(getImageGeneratorLabel(reproduceJob.image_generation_backend))}
                  </dd>
                </div>
                <div>
                  <dt className="text-xs uppercase tracking-wide text-slate-500">{translate("Segmentation backend")}</dt>
                  <dd className="mt-1">
                    {translate(getSegmentationLabel(reproduceJob.segmentation_backend))}
                  </dd>
                </div>
                <div>
                  <dt className="text-xs uppercase tracking-wide text-slate-500">{translate("Reference frames")}</dt>
                  <dd className="mt-1">{translate(getReferenceModeLabel(reproduceJob.reference_mode))}</dd>
                </div>
                <div>
                  <dt className="text-xs uppercase tracking-wide text-slate-500">{translate("Parallel Kling renders")}</dt>
                  <dd className="mt-1">{reproduceJob.thread_count}</dd>
                </div>
              </dl>

              <div className="grid gap-4 lg:grid-cols-2">
                <div>
                  <h4 className="text-sm font-semibold text-zinc-600 dark:text-slate-300">{translate("Uploaded video")}</h4>
                  {reproduceSourceVideoUrl ? (
                    <video
                      controls
                      className="mt-2 max-h-64 w-full rounded border border-slate-200 dark:border-slate-800 bg-black/60"
                      src={reproduceSourceVideoUrl}
                    >
                      {translate("Your browser does not support HTML5 video.")}
                    </video>
                  ) : (
                    <p className="mt-2 text-xs text-slate-500">{translate("Video pending...")}</p>
                  )}
                </div>
                <div>
                  <h4 className="text-sm font-semibold text-zinc-600 dark:text-slate-300">{translate("Merged Kling video")}</h4>
                  {reproduceFinalVideoUrl ? (
                    <video
                      controls
                      className="mt-2 max-h-64 w-full rounded border border-slate-200 dark:border-slate-800 bg-black/60"
                      src={reproduceFinalVideoUrl}
                    >
                      {translate("Your browser does not support HTML5 video.")}
                    </video>
                  ) : (
                    <p className="mt-2 text-xs text-slate-500">{translate("Video pending...")}</p>
                  )}
                </div>
              </div>

              <div className="space-y-4">
                <h4 className="text-sm font-semibold text-zinc-600 dark:text-slate-300">{translate("Video segments")}</h4>
                {reproduceJob.segments?.length ? (
                  reproduceJob.segments.map((segment) => {
                    const activeVersion =
                      segment.versions.find((version) => version.version_id === segment.selected_version_id) ??
                      segment.versions.at(-1);
                    const originalSegmentUrl = buildAssetUrl(apiBase, segment.original_video_url);
                    const klingSegmentUrl = buildAssetUrl(apiBase, activeVersion?.video_url);
                    const startFrameUrl = buildAssetUrl(apiBase, segment.start_frame_url);
                    const endFrameUrl = buildAssetUrl(apiBase, segment.end_frame_url);
                    const stylizedStartFrameUrl = buildAssetUrl(apiBase, segment.stylized_start_frame_url);
                    const stylizedEndFrameUrl = buildAssetUrl(apiBase, segment.stylized_end_frame_url);
                    const stylizationDisabled = reproduceJob.image_generation_backend === "original";
                    const useTailReference = reproduceJob.reference_mode !== "start_only";
                    const framePreviews = stylizationDisabled
                      ? [
                          { key: "start", label: translate("Start frame"), url: startFrameUrl },
                          ...(useTailReference
                            ? [{ key: "end", label: translate("End frame"), url: endFrameUrl }]
                            : []),
                        ]
                      : [
                          { key: "start", label: translate("Start frame"), url: startFrameUrl },
                          { key: "stylized-start", label: translate("Stylized start frame"), url: stylizedStartFrameUrl },
                          ...(useTailReference
                            ? [
                                { key: "end", label: translate("End frame"), url: endFrameUrl },
                                {
                                  key: "stylized-end",
                                  label: translate("Stylized end frame"),
                                  url: stylizedEndFrameUrl,
                                },
                              ]
                            : []),
                        ];
                    const promptValue = segmentPrompts[segment.order] ?? "";
                    const isBusy = regeneratingSegment === segment.order;
                    return (
                      <div
                        key={segment.order}
                        className="rounded border border-slate-200 dark:border-slate-800 bg-white/80 dark:bg-slate-900/40 p-4 shadow-inner shadow-black/10"
                      >
                        <div className="flex flex-wrap items-start justify-between gap-3">
                          <div>
                            <h5 className="text-sm font-semibold text-slate-900 dark:text-slate-100">{segment.label}</h5>
                            <p className="text-xs text-slate-500 dark:text-slate-400">
                              {formatSeconds(segment.start_seconds)} - {formatSeconds(segment.end_seconds)} · {formatSeconds(segment.duration_seconds)}
                            </p>
                          </div>
                          <span className="rounded-full border border-slate-300 dark:border-slate-700 bg-slate-100 dark:bg-slate-800 px-2 py-0.5 text-[11px] uppercase tracking-wide text-slate-500 dark:text-slate-400">
                            {translate("Segment")} {segment.order}
                          </span>
                        </div>
                        <p className="mt-3 text-sm text-zinc-600 dark:text-slate-300">{segment.description}</p>
                        {segment.tags?.length ? (
                          <p className="text-xs text-slate-500 dark:text-slate-400">{segment.tags.join(", ")}</p>
                        ) : null}
                        <div className="mt-3 grid gap-3 md:grid-cols-2">
                          <div>
                            <p className="text-xs font-semibold uppercase tracking-wide text-slate-500">{translate("Original segment")}</p>
                            {originalSegmentUrl ? (
                              <video
                                controls
                                className="mt-1 max-h-48 w-full rounded border border-slate-200 dark:border-slate-800 bg-black/60"
                                src={originalSegmentUrl}
                              >
                                {translate("Version video unavailable.")}
                              </video>
                            ) : (
                              <p className="mt-1 text-xs text-slate-500">{translate("Video pending...")}</p>
                            )}
                          </div>
                          <div>
                            <p className="text-xs font-semibold uppercase tracking-wide text-slate-500">{translate("Reproduced clip")}</p>
                            {klingSegmentUrl ? (
                              <video
                                controls
                                className="mt-1 max-h-48 w-full rounded border border-slate-200 dark:border-slate-800 bg-black/60"
                                src={klingSegmentUrl}
                              >
                                {translate("Version video unavailable.")}
                              </video>
                            ) : (
                              <p className="mt-1 text-xs text-slate-500">{translate("Video pending...")}</p>
                            )}
                          </div>
                        </div>
                        <div className="mt-3 grid gap-3 md:grid-cols-2 xl:grid-cols-4">
                          {framePreviews.map((frame) => (
                            <div key={`${segment.order}-${frame.key}`}>
                              <p className="text-xs font-semibold uppercase tracking-wide text-slate-500">{frame.label}</p>
                              {frame.url ? (
                                <Image
                                  src={frame.url}
                                  alt={`${segment.label} ${frame.label}`}
                                  width={320}
                                  height={180}
                                  unoptimized
                                  className="mt-1 h-28 w-full rounded object-cover"
                                />
                              ) : (
                                <p className="mt-1 text-xs text-slate-500">{translate("Image pending...")}</p>
                              )}
                            </div>
                          ))}
                        </div>
                        <div className="mt-4 space-y-2">
                          <textarea
                            className="w-full rounded-md border border-slate-300 dark:border-slate-700 bg-slate-100 dark:bg-slate-800 px-3 py-2 text-xs text-slate-900 dark:text-slate-100 focus:border-slate-500 focus:outline-none focus:ring-2 focus:ring-slate-800"
                            value={promptValue}
                            onChange={(event) => handleSegmentPromptChange(segment.order, event.target.value)}
                          />
                          <div className="flex flex-wrap items-center justify-between gap-2 text-[11px] text-slate-500 dark:text-slate-400">
                            <span>
                              {translate("Status")}: {translate(segment.status)}
                            </span>
                            <button
                              type="button"
                              onClick={() => handleSegmentRegenerate(segment.order)}
                              disabled={isBusy}
                              className="inline-flex items-center gap-1 rounded-md border border-slate-300 dark:border-slate-700 px-3 py-1 text-[11px] font-semibold text-slate-500 dark:text-slate-400 transition hover:border-slate-400 dark:border-slate-600 hover:text-slate-700 dark:text-slate-200 disabled:cursor-not-allowed disabled:opacity-60"
                            >
                              {isBusy ? <ArrowPathIcon className="h-3.5 w-3.5 animate-spin" /> : <ArrowPathIcon className="h-3.5 w-3.5" />}
                              {translate("Regenerate segment")}
                            </button>
                          </div>
                        </div>
                      </div>
                    );
                  })
                ) : (
                  <p className="text-xs text-slate-500">{translate("No insights yet.")}</p>
                )}
              </div>

              {reproduceJob.messages?.length ? (
                <details className="group space-y-2">
                  <summary className="flex cursor-pointer items-center gap-2 text-sm font-semibold text-zinc-600 dark:text-slate-300 list-none">
                    {translate("Workflow Timeline")}
                    <div className="ml-auto text-xs font-normal text-slate-500">
                      <span className="group-open:hidden">Show</span>
                      <span className="hidden group-open:inline">Hide</span>
                    </div>
                  </summary>
                  <ol className="space-y-2 max-h-60 overflow-y-auto pr-2">
                    {reproduceJob.messages.map((message, index) => {
                      const Icon = stageIcons[message.stage.toLowerCase()] ?? ArrowPathIcon;
                      return (
                        <li
                          key={`${message.timestamp}-${index}`}
                          className="relative flex gap-3 rounded border border-slate-200 dark:border-slate-800 bg-white/80 dark:bg-slate-900/40 p-2 shadow-inner shadow-black/10"
                        >
                          <div className="relative mt-0.5 flex h-6 w-6 items-center justify-center rounded-full border border-slate-300 dark:border-slate-700 bg-slate-100 dark:bg-slate-800">
                            <Icon className="h-3.5 w-3.5 text-zinc-600 dark:text-slate-400" />
                          </div>
                          <div>
                            <p className="text-xs text-zinc-600 dark:text-slate-300">{message.text}</p>
                            <p className="text-[11px] text-slate-500">
                              {formatTimestamp(message.timestamp)} · {stageLabel(message.stage, language)}
                            </p>
                          </div>
                        </li>
                      );
                    })}
                  </ol>
                </details>
              ) : null}
            </div>
          )}
        </Modal>

        <Modal
          isOpen={Boolean(refinementReview)}
          onClose={handleRefinementDismiss}
          title={translate("Review refined prompt")}
          maxWidth="max-w-4xl"
        >
          {refinementReview ? (
            <div className="space-y-4">
              <p className="text-sm text-slate-500 dark:text-slate-400">
                {translate(
                  refinementReview.mode === "workflow"
                    ? "GPT-6.1-sol refined your storyboard. Edit it below or proceed."
                    : "GPT-6.1-sol refined your prompt. Edit it below or proceed.",
                )}
              </p>
              <div className="space-y-2">
                <label className="text-xs font-semibold uppercase tracking-wide text-slate-500">
                  {translate("Refined prompt")}
                </label>
                <textarea
                  className="min-h-[200px] w-full rounded-md border border-slate-300 dark:border-slate-700 bg-slate-100 dark:bg-slate-800 px-3 py-2 text-sm text-slate-900 dark:text-slate-100 focus:border-slate-500 focus:outline-none focus:ring-2 focus:ring-slate-800"
                  value={refinementDraft}
                  onChange={(event) => setRefinementDraft(event.target.value)}
                />
              </div>
              {refinementReview.mode === "workflow" ? (
                <div className="space-y-3 rounded-md border border-slate-200 dark:border-slate-800 bg-slate-50 dark:bg-slate-900/40 p-3">
                  <div className="flex flex-wrap items-start justify-between gap-3">
                    <div>
                      <p className="text-sm font-semibold text-zinc-600 dark:text-slate-200">{translate("GPT-6.1-sol prompt template")}</p>
                      <p className="text-xs text-slate-500 dark:text-slate-400">
                        {translate("Edit or replace the GPT-6.1-sol prompt file before refining scenes.")}
                      </p>
                    </div>
                    <button
                      type="button"
                      onClick={handleWorkflowTemplateReload}
                      disabled={workflowTemplateLoading || workflowTemplateSaving}
                      className="inline-flex items-center gap-2 rounded-md border border-slate-300 dark:border-slate-700 px-3 py-1 text-[11px] font-semibold text-zinc-600 dark:text-slate-300 transition hover:border-slate-400 dark:border-slate-600 hover:text-slate-800 dark:hover:text-slate-100 disabled:cursor-not-allowed disabled:opacity-60"
                    >
                      {workflowTemplateLoading ? (
                        <ArrowPathIcon className="h-4 w-4 animate-spin" />
                      ) : (
                        <ArrowPathIcon className="h-4 w-4" />
                      )}
                      {translate("Reload prompt file")}
                    </button>
                  </div>
                  <textarea
                    className="min-h-[160px] w-full rounded-md border border-slate-300 dark:border-slate-700 bg-white dark:bg-slate-800 px-3 py-2 text-sm text-slate-900 dark:text-slate-100 focus:border-slate-500 focus:outline-none focus:ring-2 focus:ring-slate-800 disabled:opacity-60"
                    value={workflowTemplateDraft}
                    onChange={(event) => setWorkflowTemplateDraft(event.target.value)}
                    disabled={workflowTemplateLoading || workflowTemplateSaving}
                    placeholder={translate("Edit or replace the GPT-6.1-sol prompt file before refining scenes.")}
                  />
                  <div className="flex flex-wrap items-center justify-between gap-2">
                    {workflowTemplateError ? (
                      <p className="text-xs text-rose-500">{translate(workflowTemplateError)}</p>
                    ) : workflowTemplateLoading ? (
                      <p className="text-xs text-slate-500">{translate("Loading prompt template...")}</p>
                    ) : (
                      <span className="text-xs text-transparent">.</span>
                    )}
                    <div className="flex gap-2">
                      <button
                        type="button"
                        onClick={() => setWorkflowTemplateDraft(workflowTemplateOriginal)}
                        disabled={
                          workflowTemplateDraft === workflowTemplateOriginal ||
                          workflowTemplateLoading ||
                          workflowTemplateSaving
                        }
                        className="inline-flex items-center gap-2 rounded-md border border-slate-300 dark:border-slate-700 px-3 py-1 text-[11px] font-semibold text-zinc-600 dark:text-slate-300 transition hover:border-slate-400 dark:border-slate-600 hover:text-slate-800 dark:hover:text-slate-100 disabled:cursor-not-allowed disabled:opacity-60"
                      >
                        {translate("Reset changes")}
                      </button>
                      <button
                        type="button"
                        onClick={handleWorkflowTemplateSave}
                        disabled={
                          workflowTemplateDraft === workflowTemplateOriginal ||
                          workflowTemplateLoading ||
                          workflowTemplateSaving
                        }
                        className="inline-flex items-center gap-2 rounded-md border border-slate-300 dark:border-slate-700 px-3 py-1 text-[11px] font-semibold text-zinc-600 dark:text-slate-300 transition hover:border-slate-400 dark:border-slate-600 hover:text-slate-800 dark:hover:text-slate-100 disabled:cursor-not-allowed disabled:opacity-60"
                      >
                        {workflowTemplateSaving ? (
                          <ArrowPathIcon className="h-4 w-4 animate-spin" />
                        ) : (
                          <CheckCircleIcon className="h-4 w-4" />
                        )}
                        {translate("Save template & re-run")}
                      </button>
                    </div>
                  </div>
                </div>
              ) : null}
              <div className="rounded-md border border-slate-200 dark:border-slate-800 bg-slate-50 dark:bg-slate-900/50 p-3">
                <p className="text-[11px] font-semibold uppercase tracking-wide text-slate-500">
                  {translate("Original input")}
                </p>
                <div className="mt-2 max-h-32 overflow-y-auto rounded bg-slate-100 dark:bg-slate-800 p-2">
                  <pre className="whitespace-pre-wrap break-words font-mono text-[12px] leading-relaxed text-slate-800 dark:text-slate-100">
                    {refinementReview.originalText}
                  </pre>
                </div>
              </div>
              <div className="flex flex-wrap items-center justify-between gap-3">
                {copyNotice ? (
                  <span className="text-[11px] font-semibold text-emerald-600 dark:text-emerald-400">
                    {translate(copyNotice)}
                  </span>
                ) : (
                  <span className="text-[11px] text-slate-500">
                    {translate("Copy or update the prompt before proceeding.")}
                  </span>
                )}
                <div className="flex flex-wrap items-center gap-2">
                  <button
                    type="button"
                    onClick={() =>
                      handleCopyPrompt(
                        refinementDraft.trim()
                          ? refinementDraft
                          : refinementReview.refinedText || refinementReview.originalText,
                      )
                    }
                    className="inline-flex items-center gap-2 rounded-md border border-slate-300 dark:border-slate-700 px-3 py-1.5 text-xs font-semibold text-zinc-600 dark:text-slate-300 transition hover:border-slate-400 dark:border-slate-600 hover:text-slate-800 dark:hover:text-slate-100"
                  >
                    <DocumentTextIcon className="h-4 w-4" />
                    {translate("Copy prompt")}
                  </button>
                  <button
                    type="button"
                    onClick={handleRefinementDismiss}
                    className="inline-flex items-center gap-2 rounded-md border border-slate-300 dark:border-slate-700 px-3 py-1.5 text-xs font-semibold text-zinc-600 dark:text-slate-300 transition hover:border-slate-400 dark:border-slate-600 hover:text-slate-800 dark:hover:text-slate-100"
                  >
                    {translate("Back to edit")}
                  </button>
                  <button
                    type="button"
                    onClick={handleRefinementProceed}
                    disabled={refinementReview.mode === "workflow" ? isSubmittingCombined : isSubmittingSingle}
                    className="inline-flex items-center gap-2 rounded-md bg-slate-900 px-4 py-2 text-xs font-semibold text-white dark:bg-slate-100 dark:text-slate-900 shadow-sm shadow-slate-900/50 transition hover:bg-slate-700 focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-slate-400 disabled:cursor-not-allowed disabled:opacity-60"
                  >
                    {refinementReview.mode === "workflow" ? (
                      isSubmittingCombined ? <ArrowPathIcon className="h-4 w-4 animate-spin" /> : <PaperAirplaneIcon className="h-4 w-4" />
                    ) : isSubmittingSingle ? (
                      <ArrowPathIcon className="h-4 w-4 animate-spin" />
                    ) : (
                      <PaperAirplaneIcon className="h-4 w-4" />
                    )}
                    {translate(refinementReview.mode === "workflow" ? "Proceed to workflow" : "Proceed to single video")}
                  </button>
                </div>
              </div>
            </div>
          ) : null}
        </Modal>

        <Modal
          isOpen={Boolean(promptPreview)}
          onClose={() => {
            setPromptPreview(null);
            setCopyNotice(null);
          }}
          title={translate("Prompt details")}
          maxWidth="max-w-3xl"
        >
          {promptPreview ? (
            <div className="space-y-3">
              <div className="flex flex-wrap items-center justify-between gap-3">
                <p className="text-xs font-semibold uppercase tracking-wide text-slate-500">
                  {promptPreview.sceneLabel} • {promptPreview.versionLabel}
                </p>
                <div className="flex items-center gap-2">
                  {copyNotice ? (
                    <span className="text-[11px] font-semibold text-emerald-600 dark:text-emerald-400">
                      {translate(copyNotice)}
                    </span>
                  ) : null}
                  <button
                    type="button"
                    onClick={() => handleCopyPrompt(promptPreview.prompt)}
                    className="inline-flex items-center gap-2 rounded-md border border-slate-300 dark:border-slate-700 px-3 py-1.5 text-[11px] font-semibold text-zinc-600 dark:text-slate-300 transition hover:border-slate-400 dark:border-slate-600 hover:text-slate-800 dark:hover:text-slate-100"
                  >
                    <DocumentTextIcon className="h-4 w-4" />
                    {translate("Copy prompt")}
                  </button>
                </div>
              </div>
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
