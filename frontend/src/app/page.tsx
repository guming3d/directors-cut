"use client";

import { ChangeEvent, FormEvent, useCallback, useEffect, useMemo, useState } from "react";
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
  messages: JobMessage[];
};

const STATUS_TOKENS: Record<string, { label: string; className: string }> = {
  queued: {
    label: "Queued",
    className: "border border-slate-300 bg-slate-100 text-slate-600",
  },
  running: {
    label: "In Progress",
    className: "border border-slate-300 bg-white text-slate-600",
  },
  completed: {
    label: "Completed",
    className: "border border-emerald-200 bg-emerald-50 text-emerald-700",
  },
  failed: {
    label: "Failed",
    className: "border border-rose-200 bg-rose-50 text-rose-700",
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

const TABS: { id: TabId; label: string }[] = [
  { id: "combined", label: "Storyboard Workflow" },
  { id: "single", label: "Single Sora2 Video" },
];

const durationOptions = ["4", "8", "12"] as const;
const resolutionOptions = ["720x1280", "1280x720", "1920x1080"] as const;

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

const stageLabel = (stage: string | null) => {
  if (!stage) return "Waiting to start";
  switch (stage) {
    case "planning":
      return "Planning scenes";
    case "rendering":
      return "Rendering video clips";
    case "composing":
      return "Composing final video";
    case "initializing":
      return "Preparing workflow";
    case "running":
      return "Generating video";
    case "completed":
      return "Workflow complete";
    case "failed":
      return "Workflow failed";
    default:
      return stage.charAt(0).toUpperCase() + stage.slice(1);
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

const getStatusToken = (status: string | undefined) => {
  if (!status) {
    return {
      label: "Idle",
      className: "border border-slate-200 bg-white text-slate-600",
    };
  }
  return (
    STATUS_TOKENS[status] ?? {
      label: status.charAt(0).toUpperCase() + status.slice(1),
      className: "border border-slate-200 bg-white text-slate-600",
    }
  );
};

export default function Home() {
  const [activeTab, setActiveTab] = useState<TabId>("combined");

  // Combined workflow state
  const [script, setScript] = useState("");
  const [useSceneImages, setUseSceneImages] = useState(true);
  const [generationMode, setGenerationMode] = useState<"parallel" | "sequence">("parallel");
  const [combinedResolution, setCombinedResolution] = useState<(typeof resolutionOptions)[number]>(resolutionOptions[0]);
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
    void loadCombinedJobs();
    void loadSingleJobs();
  }, [loadCombinedJobs, loadSingleJobs]);

  useEffect(() => {
    if (!activeJobId) return;
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
          const others = prev.filter((item) => item.job_id !== data.job_id);
          return [data, ...others];
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
  }, [activeJobId]);

  useEffect(() => {
    if (!activeSingleJobId) return;
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
          const others = prev.filter((item) => item.job_id !== data.job_id);
          return [data, ...others];
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
  }, [activeSingleJobId]);

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
  const currentStatusToken = getStatusToken(job?.status);

  const singleVideoUrl = buildAssetUrl(singleJob?.video_url);
  const singleStatusToken = getStatusToken(singleJob?.status);

  const combinedStats = useMemo(() => {
    const totalJobs = jobs.length;
    const running = jobs.filter((item) => item.status === "running" || item.status === "queued").length;
    const completed = jobs.filter((item) => item.status === "completed").length;
    return { totalJobs, running, completed };
  }, [jobs]);

  const singleStats = useMemo(() => {
    const totalJobs = singleJobs.length;
    const running = singleJobs.filter((item) => item.status === "running" || item.status === "queued").length;
    const completed = singleJobs.filter((item) => item.status === "completed").length;
    return { totalJobs, running, completed };
  }, [singleJobs]);

  const activeScenes = job?.scenes?.length ?? 0;
  const panelCardClass =
    "rounded-3xl border border-slate-200 bg-white p-6 shadow-[0_35px_120px_-85px_rgba(15,23,42,0.12)] backdrop-blur";
  const pillButtonClass =
    "rounded-full border border-slate-200 bg-white px-3 py-1.5 text-xs font-semibold uppercase tracking-wide text-slate-600";
  const displayApiBase = API_BASE.replace(/^https?:\/\//, "");
  const scrollToWorkspaceTop = () => {
    if (typeof window !== "undefined") {
      window.scrollTo({ top: 0, behavior: "smooth" });
    }
  };
  const focusStoryboardWorkspace = () => {
    setActiveTab("combined");
    scrollToWorkspaceTop();
  };
  const focusSingleClipWorkspace = () => {
    setActiveTab("single");
    scrollToWorkspaceTop();
  };
  const refreshAllJobs = () => {
    void loadCombinedJobs();
    void loadSingleJobs();
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
    } catch (err) {
      console.error(err);
      setSingleError("Unable to start the single-video job. Verify the backend is reachable.");
    } finally {
      setIsSubmittingSingle(false);
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

  return (
    <div className="relative min-h-screen overflow-hidden bg-slate-50 text-slate-900">
      <div className="pointer-events-none absolute inset-0">
        <div className="absolute inset-0 bg-gradient-to-b from-white via-transparent to-slate-100" />
        <div className="absolute left-[-18%] top-[10%] h-[420px] w-[420px] rounded-full bg-gradient-to-br from-slate-200/60 via-white/40 to-transparent blur-3xl" />
        <div className="absolute right-[-12%] top-[40%] h-[380px] w-[380px] rounded-full bg-gradient-to-tr from-slate-300/40 via-white/30 to-transparent blur-[140px]" />
      </div>

      <div className="relative mx-auto flex min-h-screen w-full max-w-[1500px] flex-col gap-10 px-6 pb-20 pt-12 xl:flex-row xl:gap-12">
        <aside className="hidden w-80 flex-col gap-6 xl:flex">
          <div className={`${panelCardClass} space-y-6`}>
            <div className="flex items-center gap-3">
              <Image
                src="/sora2_logo.jpg"
                alt="Sora Agent Workflow logo"
                width={56}
                height={56}
                className="h-14 w-14 rounded-2xl border border-slate-200 object-cover shadow-sm shadow-slate-300/50"
                priority
              />
              <div>
                <p className="text-[10px] font-semibold uppercase tracking-[0.35em] text-slate-500">Sora2 Studio</p>
                <h2 className="mt-1 text-xl font-semibold text-slate-900">Production Workspace</h2>
              </div>
            </div>
            <p className="text-sm text-slate-500">
              Monitor orchestrator agents, track scene deliveries, and keep your queue aligned with Sora best practices.
            </p>
            <div className="rounded-2xl border border-slate-200 bg-white p-4">
              <p className="text-xs uppercase text-slate-500">Workflow health</p>
              <dl className="mt-4 space-y-3 text-sm text-slate-600">
                <div className="flex items-center justify-between">
                  <dt>Active workflows</dt>
                  <dd className="text-lg font-semibold text-slate-900">{combinedStats.running}</dd>
                </div>
                <div className="flex items-center justify-between">
                  <dt>Completed runs</dt>
                  <dd className="text-lg font-semibold text-slate-900">{combinedStats.completed}</dd>
                </div>
                <div className="flex items-center justify-between">
                  <dt>Scenes this run</dt>
                  <dd className="text-lg font-semibold text-slate-900">{activeScenes}</dd>
                </div>
                <div className="flex items-center justify-between">
                  <dt>Single queue</dt>
                  <dd className="text-lg font-semibold text-slate-900">{singleStats.running}</dd>
                </div>
              </dl>
            </div>
          </div>

          <div className={`${panelCardClass} space-y-4`}>
            <p className="text-xs uppercase text-slate-500">Quick actions</p>
            <div className="flex flex-col gap-2 text-sm">
              <button
                type="button"
                onClick={focusStoryboardWorkspace}
                className="group flex items-center justify-between rounded-2xl border border-slate-200 bg-white px-4 py-3 font-medium text-slate-600 transition hover:border-slate-300 hover:bg-slate-100 hover:text-slate-900"
              >
                <span className="flex items-center gap-2">
                  <DocumentTextIcon className="h-4 w-4 text-slate-600 transition group-hover:text-slate-500" />
                  Storyboard workspace
                </span>
                <PaperAirplaneIcon className="h-5 w-5 text-slate-500 transition group-hover:translate-x-1 group-hover:text-slate-500" />
              </button>
              <button
                type="button"
                onClick={focusSingleClipWorkspace}
                className="group flex items-center justify-between rounded-2xl border border-slate-200 bg-white px-4 py-3 font-medium text-slate-600 transition hover:border-slate-300 hover:bg-slate-100 hover:text-slate-900"
              >
                <span className="flex items-center gap-2">
                  <PlayCircleIcon className="h-4 w-4 text-slate-600 transition group-hover:text-slate-500" />
                  Single clip console
                </span>
                <ArrowPathIcon className="h-5 w-5 text-slate-500 transition group-hover:animate-spin group-hover:text-slate-500" />
              </button>
              <button
                type="button"
                onClick={refreshAllJobs}
                className="group flex items-center justify-between rounded-2xl border border-slate-200 bg-white px-4 py-3 font-medium text-slate-600 transition hover:border-slate-300 hover:bg-slate-100 hover:text-slate-900"
              >
                <span className="flex items-center gap-2">
                  <ArrowPathIcon className="h-4 w-4 text-emerald-300 transition group-hover:animate-spin group-hover:text-emerald-200" />
                  Refresh telemetry
                </span>
                <CheckCircleIcon className="h-5 w-5 text-slate-500 transition group-hover:text-emerald-200" />
              </button>
            </div>
          </div>
        </aside>

        <main className="flex-1 space-y-10">
          <header className={`${panelCardClass} p-8`}>
            <div className="flex flex-wrap items-start justify-between gap-6">
              <div className="space-y-3">
                <span className="text-[11px] font-semibold uppercase tracking-[0.3em] text-slate-500">
                  Script-to-Video Control Room
                </span>
                <h1 className="text-3xl font-semibold text-slate-900">Sora Agent Production Dashboard</h1>
                <p className="max-w-2xl text-sm text-slate-500">
                  Orchestrate multi-scene storyboards, monitor scene renders, and review delivery timelines. The workspace syncs live with your FastAPI backend.
                </p>
              </div>
              <span className={`${pillButtonClass} whitespace-nowrap`}>API · <span className="font-mono text-[11px] text-slate-500">{displayApiBase || API_BASE}</span></span>
            </div>

            <div className="mt-8 grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
              <div className="rounded-2xl border border-slate-200 bg-white p-4">
                <p className="text-xs uppercase text-slate-500">Live jobs</p>
                <div className="mt-2 flex items-baseline gap-2">
                  <span className="text-3xl font-semibold text-slate-900">{combinedStats.running + singleStats.running}</span>
                  <span className="text-xs text-slate-500">running or queued</span>
                </div>
              </div>
              <div className="rounded-2xl border border-slate-200 bg-white p-4">
                <p className="text-xs uppercase text-slate-500">Completed jobs</p>
                <div className="mt-2 text-3xl font-semibold text-slate-900">{combinedStats.completed + singleStats.completed}</div>
                <p className="text-xs text-slate-500">Since session start</p>
              </div>
              <div className="rounded-2xl border border-slate-200 bg-white p-4">
                <p className="text-xs uppercase text-slate-500">Scenes this workflow</p>
                <div className="mt-2 text-3xl font-semibold text-slate-900">{activeScenes}</div>
                <p className="text-xs text-slate-500">Current storyboard</p>
              </div>
              <div className="rounded-2xl border border-slate-200 bg-white p-4">
                <p className="text-xs uppercase text-slate-500">Tracked workstreams</p>
                <div className="mt-2 text-3xl font-semibold text-slate-900">{combinedStats.totalJobs + singleStats.totalJobs}</div>
                <p className="text-xs text-slate-500">Across storyboard & single clips</p>
              </div>
            </div>
          </header>

          <div className="space-y-8">
            <div className="flex flex-wrap gap-3 rounded-2xl border border-slate-200 bg-slate-50 p-2 shadow-inner shadow-slate-200/60 backdrop-blur">
              {TABS.map(({ id, label }) => {
                const isActive = activeTab === id;
                return (
                  <button
                    key={id}
                    type="button"
                    onClick={() => setActiveTab(id)}
                    className={`flex-1 rounded-xl px-4 py-2 text-sm font-semibold transition focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-slate-400 ${
                      isActive
                        ? "bg-slate-900 text-white shadow-sm"
                        : "bg-transparent text-slate-500 hover:bg-slate-100 hover:text-slate-900"
                    }`}
                  >
                    {label}
                  </button>
                );
              })}
            </div>

            {activeTab === "combined" ? (
              <>
                {combinedError && (
                  <div className="flex items-start gap-3 rounded-2xl border border-rose-200 bg-rose-50 px-4 py-3 text-sm text-rose-700 shadow-md shadow-rose-200/40">
                    <ExclamationTriangleIcon className="h-5 w-5 flex-none text-rose-700" />
                    <span>{combinedError}</span>
                  </div>
                )}

                <div className="grid gap-8 xl:grid-cols-[1.15fr,0.85fr]">
                  <section className="space-y-8">
                    <div className={`${panelCardClass}`}>
                      <div className="flex items-center justify-between">
                        <div className="flex items-center gap-2 text-lg font-semibold text-slate-900">
                          <DocumentTextIcon className="h-5 w-5 text-slate-600" />
                          Storyboard Script
                        </div>
                      </div>
                      <p className="mt-2 text-sm text-slate-500">
                        Paste your storyboard with <span className="font-mono text-xs text-slate-600">**Scene 1 (Title)**</span> headings. The Director agent will refine prompts following Sora2 best practices.
                      </p>
                      <form className="mt-5 space-y-5" onSubmit={handleCombinedSubmit}>
                        <textarea
                          className="min-h-[260px] w-full resize-none rounded-2xl border border-slate-200 bg-white px-4 py-3 text-sm text-slate-900 shadow-[0_30px_100px_-80px_rgba(15,23,42,0.12)] placeholder:text-slate-500 transition focus:border-slate-400 focus:outline-none focus:ring-4 focus:ring-slate-200"
                          placeholder="**Scene 1 (Sunrise Over the City)**
The camera glides through..."
                          value={script}
                          onChange={(event) => setScript(event.target.value)}
                        />

                        <div className="grid gap-4 sm:grid-cols-2">
                          <div className="space-y-2">
                            <label className="text-sm font-semibold text-slate-600">Resolution</label>
                            <select
                              value={combinedResolution}
                              onChange={(event) => setCombinedResolution(event.target.value as (typeof resolutionOptions)[number])}
                              className="w-full rounded-2xl border border-slate-200 bg-white px-3 py-2 text-sm text-slate-900 shadow-inner shadow-slate-200/50 focus:border-slate-400 focus:outline-none focus:ring-2 focus:ring-slate-200"
                            >
                              {resolutionOptions.map((option) => (
                                <option key={option} value={option}>
                                  {option}
                                </option>
                              ))}
                            </select>
                          </div>
                          <div className="space-y-2">
                            <span className="text-sm font-semibold text-slate-600">Scene references</span>
                            <button
                              type="button"
                              onClick={() => setUseSceneImages((value) => !value)}
                              className={`flex items-center gap-3 rounded-2xl border px-4 py-3 text-sm font-semibold transition ${
                                useSceneImages
                                  ? "border-slate-300 bg-slate-100 text-slate-900 shadow-sm"
                                  : "border-slate-200 bg-white text-slate-600 hover:border-slate-300 hover:text-slate-900"
                              }`}
                            >
                              <span className={`inline-flex h-5 w-10 items-center rounded-full border transition ${
                                useSceneImages
                                  ? "border-slate-300 bg-slate-200"
                                  : "border-slate-200 bg-white"
                              }`}>
                                <span
                                  className={`ml-1 inline-block h-3.5 w-3.5 rounded-full bg-white transition ${
                                    useSceneImages ? "translate-x-4" : "translate-x-0"
                                  }`}
                                />
                              </span>
                              {useSceneImages ? "Scene images enabled" : "No scene references"}
                            </button>
                          </div>
                        </div>

                        <div className="space-y-2">
                          <label className="text-sm font-semibold text-slate-600">Generation mode</label>
                          <div className="flex flex-wrap gap-3">
                            {[
                              { id: "parallel" as const, label: "Parallel", description: "Render scenes concurrently" },
                              { id: "sequence" as const, label: "Sequence", description: "Chain scenes sequentially" },
                            ].map((mode) => {
                              const isActive = generationMode === mode.id;
                              return (
                                <button
                                  key={mode.id}
                                  type="button"
                                  onClick={() => setGenerationMode(mode.id)}
                                  className={`flex flex-col rounded-2xl border px-4 py-2 text-left text-sm transition focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-slate-400 ${
                                    isActive
                                      ? "border-slate-900 bg-slate-900 text-white shadow-sm"
                                      : "border-slate-200 bg-white text-slate-600 hover:border-slate-300 hover:text-slate-900"
                                  }`}
                                >
                                  <span className="font-semibold">{mode.label}</span>
                                  <span className={`text-xs ${isActive ? "text-slate-100" : "text-slate-500"}`}>
                                    {mode.description}
                                  </span>
                                </button>
                              );
                            })}
                          </div>
                          <p className="text-xs text-slate-500">
                            Sequence mode accepts a single starting still, then reuses the last frame of each clip to guide the next scene for smoother continuity.
                          </p>
                        </div>

                        {useSceneImages ? (
                          <div className="space-y-3">
                            <label className="text-sm font-semibold text-slate-600">Upload reference stills</label>
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
                                    className="flex h-28 items-center justify-center overflow-hidden rounded-xl border border-slate-200 bg-white"
                                  >
                                    <Image
                                      src={preview}
                                      alt={`Scene reference ${index + 1}`}
                                      width={160}
                                      height={160}
                                      unoptimized
                                      className="h-full w-full object-cover"
                                    />
                                  </div>
                                ))}
                              </div>
                            ) : (
                              <p className="text-xs text-slate-500">Accepted formats: PNG, JPG. Images remain local until submission.</p>
                            )}
                          </div>
                        ) : null}

                        <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
                          <button
                            type="submit"
                            disabled={isSubmittingCombined}
                            className="inline-flex items-center justify-center gap-2 rounded-xl bg-slate-900 px-5 py-2.5 text-sm font-semibold text-white shadow-sm shadow-slate-300/50 transition hover:bg-slate-700 focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-slate-400 disabled:cursor-not-allowed disabled:opacity-60"
                          >
                            {isSubmittingCombined ? (
                              <>
                                <ArrowPathIcon className="h-5 w-5 animate-spin" />
                                Starting Workflow...
                              </>
                            ) : (
                              <>
                                <PaperAirplaneIcon className="h-5 w-5" />
                                Start Workflow
                              </>
                            )}
                          </button>
                          <p className="text-xs text-slate-500">
                            API target: <span className="font-mono text-[11px] text-slate-500">{API_BASE}/api/workflows</span>
                          </p>
                        </div>
                      </form>
                    </div>

                    {job && (
                      <div className={`${panelCardClass}`}>
                        <div className="flex flex-wrap items-start justify-between gap-4">
                          <div>
                            <h2 className="flex items-center gap-2 text-lg font-semibold text-slate-900">
                              <FilmIcon className="h-5 w-5 text-slate-600" />
                              Workflow Status
                            </h2>
                            <p className="mt-1 text-sm text-slate-500">{stageLabel(job.stage)}</p>
                            <p className="text-xs uppercase tracking-wide text-slate-400">
                              Mode: {job.generation_mode === "sequence" ? "Sequence" : "Parallel"}
                            </p>
                          </div>
                          <span className={`${pillButtonClass} ${currentStatusToken.className}`}>{currentStatusToken.label}</span>
                        </div>

                        <div className="mt-6 space-y-3">
                          <div className="flex items-center justify-between text-xs text-slate-500">
                            <span>Progress</span>
                            <span>{combinedProgressPercent}%</span>
                          </div>
                          <div className="h-2.5 overflow-hidden rounded-full border border-slate-200 bg-white">
                            <div
                              className="h-full rounded-full bg-slate-900 transition-all duration-500 ease-out"
                              style={{ width: `${combinedProgressPercent}%` }}
                            />
                          </div>
                          {job.error && (
                            <div className="flex items-center gap-2 rounded-xl border border-rose-200 bg-rose-50 px-3 py-2 text-xs text-rose-700">
                              <ExclamationTriangleIcon className="h-4 w-4" />
                              {job.error}
                            </div>
                          )}
                        </div>

                        <dl className="mt-6 grid gap-4 text-sm text-slate-600 sm:grid-cols-3">
                          <div>
                            <dt className="text-xs uppercase tracking-wide text-slate-500">Started</dt>
                            <dd className="mt-1 text-slate-600">{formatTimeDistance(job.started_at)}</dd>
                          </div>
                          <div>
                            <dt className="text-xs uppercase tracking-wide text-slate-500">Completed</dt>
                            <dd className="mt-1 text-slate-600">{job.completed_at ? formatTimeDistance(job.completed_at) : "—"}</dd>
                          </div>
                          <div>
                            <dt className="text-xs uppercase tracking-wide text-slate-500">Resolution</dt>
                            <dd className="mt-1 text-slate-600">{job.resolution}</dd>
                          </div>
                        </dl>

                        {finalVideoUrl && (
                          <div className="mt-8 space-y-3">
                            <div className="flex items-center justify-between gap-3">
                              <h3 className="text-sm font-semibold text-slate-600">Final Video</h3>
                              <button
                                type="button"
                                onClick={handleRecompose}
                                disabled={!canRecompose || isRecomposing}
                                className="inline-flex items-center gap-2 rounded-xl border border-slate-300 px-3 py-1.5 text-xs font-semibold text-slate-700 transition hover:border-slate-400 disabled:cursor-not-allowed disabled:opacity-60"
                              >
                                {isRecomposing ? <ArrowPathIcon className="h-4 w-4 animate-spin" /> : <ArrowPathIcon className="h-4 w-4" />}
                                Regenerate Final Video
                              </button>
                            </div>
                            <video
                              controls
                              className="max-h-[420px] w-full rounded-2xl border border-slate-200 bg-black/60"
                              src={finalVideoUrl}
                            >
                              Your browser does not support HTML5 video.
                            </video>
                            <a
                              href={finalVideoUrl}
                              target="_blank"
                              rel="noreferrer"
                              className="inline-flex items-center gap-2 text-sm font-medium text-slate-600 hover:text-slate-900"
                            >
                              <PlayCircleIcon className="h-4 w-4" />
                              Open MP4 in new tab
                            </a>
                          </div>
                        )}
                      </div>
                    )}

                    {job?.scenes?.length ? (
                      <div className={`${panelCardClass}`}>
                        <h2 className="flex items-center gap-2 text-lg font-semibold text-slate-900">
                          <PlayCircleIcon className="h-5 w-5 text-slate-600" />
                          Scene Renders
                        </h2>
                        <div className="mt-5 grid gap-5 md:grid-cols-2">
                          {job.scenes.map((scene) => {
                            const versions = scene.versions ?? [];
                            const selectedVersion = versions.find((version) => version.version_id === scene.selected_version_id) ?? versions[0];
                            const sceneVideoUrl = buildAssetUrl(selectedVersion?.video_url ?? scene.video_url);
                            return (
                              <div
                                key={scene.order}
                                className="rounded-2xl border border-slate-200 bg-white p-4 shadow-inner shadow-slate-200/60"
                              >
                                <div className="flex items-start justify-between gap-2">
                                  <div>
                                    <h3 className="text-sm font-semibold text-slate-900">{scene.label}</h3>
                                    <p className="text-xs text-slate-500">{scene.duration_seconds} seconds</p>
                                  </div>
                                  <span className="rounded-full border border-slate-200 bg-slate-100 px-2 py-0.5 text-[11px] uppercase tracking-wide text-slate-600">
                                    Scene {scene.order}
                                  </span>
                                </div>
                                {sceneVideoUrl ? (
                                  <video
                                    key={`${scene.order}-${sceneVideoUrl}`}
                                    controls
                                    className="mt-3 max-h-56 w-full rounded-xl border border-slate-200 bg-black/60"
                                    src={sceneVideoUrl}
                                  >
                                    Video unavailable.
                                  </video>
                                ) : (
                                  <div className="mt-3 flex h-40 items-center justify-center rounded-xl border border-dashed border-slate-200 bg-slate-100 text-xs text-slate-500">
                                    Clip pending...
                                  </div>
                                )}
                                <p className="mt-2 text-[11px] text-slate-500">
                                  Video ID: {selectedVersion?.video_id ?? "Unavailable"}
                                </p>

                                {versions.length ? (
                                  <div className="mt-4 space-y-3">
                                    <h4 className="text-xs font-semibold uppercase tracking-wide text-slate-500">Versions</h4>
                                    {versions.map((version) => {
                                      const versionVideoUrl = buildAssetUrl(version.video_url);
                                      const isActive = version.version_id === scene.selected_version_id;
                                      const isBusy = Boolean(selectionSubmitting[version.version_id]);
                                      return (
                                        <div
                                          key={version.version_id}
                                          className={`rounded-2xl border p-3 ${
                                            isActive
                                              ? "border-slate-900 bg-slate-50"
                                              : "border-slate-200 bg-white"
                                          }`}
                                        >
                                          <div className="flex items-start justify-between gap-3">
                                            <div>
                                              <p className="text-sm font-semibold text-slate-900">{version.label}</p>
                                              <p className="text-xs text-slate-500">
                                                {version.source} • {formatTimeDistance(version.created_at)}
                                              </p>
                                            </div>
                                            <button
                                              type="button"
                                              onClick={() => handleSelectVersion(scene.order, version.version_id)}
                                              disabled={isActive || isBusy}
                                              className={`rounded-xl border px-3 py-1 text-xs font-semibold transition ${
                                                isActive
                                                  ? "border-emerald-200 bg-emerald-50 text-emerald-700"
                                                  : "border-slate-200 bg-white text-slate-600 hover:border-slate-300 hover:text-slate-900 disabled:opacity-60"
                                              }`}
                                            >
                                              {isActive ? "Selected" : isBusy ? "Selecting..." : "Use this version"}
                                            </button>
                                          </div>
                                          {versionVideoUrl ? (
                                            <video
                                              key={`${version.version_id}-${versionVideoUrl}`}
                                              controls
                                              className="mt-2 max-h-40 w-full rounded-xl border border-slate-200 bg-black/60"
                                              src={versionVideoUrl}
                                            >
                                              Version video unavailable.
                                            </video>
                                          ) : null}
                                          {version.prompt ? (
                                            <p className="mt-2 text-xs text-slate-600">
                                              Prompt: <span className="font-mono text-[11px] text-slate-500">{version.prompt}</span>
                                            </p>
                                          ) : null}
                                          {version.video_id ? (
                                            <p className="text-[11px] text-slate-400">Azure video ID: {version.video_id}</p>
                                          ) : null}
                                        </div>
                                      );
                                    })}
                                  </div>
                                ) : null}

                                <form className="mt-4 space-y-2" onSubmit={(event) => handleRemixSubmit(event, scene.order)}>
                                  <label className="text-xs font-semibold uppercase tracking-wide text-slate-500">
                                    Remix prompt
                                  </label>
                                  <textarea
                                    className="min-h-[80px] w-full rounded-xl border border-slate-200 bg-white px-3 py-2 text-sm text-slate-900 focus:border-slate-400 focus:outline-none focus:ring-2 focus:ring-slate-200"
                                    placeholder="Describe how you want to remix this scene..."
                                    value={remixPrompts[scene.order] ?? ""}
                                    onChange={(event) =>
                                      setRemixPrompts((prev) => ({ ...prev, [scene.order]: event.target.value }))
                                    }
                                  />
                                  <div className="flex items-center justify-between gap-2 text-xs text-slate-500">
                                    <span>Uses previous video ID: {selectedVersion?.video_id ?? "Unavailable"}</span>
                                    <button
                                      type="submit"
                                      disabled={!selectedVersion?.video_id || remixSubmitting[scene.order]}
                                      className="inline-flex items-center gap-1 rounded-xl border border-slate-300 px-3 py-1 text-xs font-semibold text-slate-700 transition hover:border-slate-400 disabled:cursor-not-allowed disabled:opacity-60"
                                    >
                                      {remixSubmitting[scene.order] ? (
                                        <ArrowPathIcon className="h-4 w-4 animate-spin" />
                                      ) : (
                                        <ArrowPathIcon className="h-4 w-4" />
                                      )}
                                      Remix Scene
                                    </button>
                                  </div>
                                </form>
                              </div>
                            );
                          })}
                        </div>
                      </div>
                    ) : null}
                  </section>

                  <aside className="space-y-8">
                    {job?.messages?.length ? (
                      <div className={`${panelCardClass}`}>
                        <h2 className="flex items-center gap-2 text-lg font-semibold text-slate-900">
                          <ClockIcon className="h-5 w-5 text-slate-600" />
                          Workflow Timeline
                        </h2>
                        <ol className="mt-6 space-y-4">
                          {job.messages.map((message, index) => {
                            const Icon = stageIcons[message.stage] ?? ArrowPathIcon;
                            return (
                              <li
                                key={`${message.timestamp}-${index}`}
                                className="relative flex gap-3 rounded-2xl border border-slate-200 bg-white p-3 shadow-inner shadow-slate-200/60"
                              >
                                <div className="relative mt-0.5 flex h-7 w-7 items-center justify-center rounded-full border border-slate-200 bg-slate-100">
                                  <Icon className="h-4 w-4 text-slate-600" />
                                </div>
                                <div>
                                  <p className="text-sm text-slate-600">{message.text}</p>
                                  <p className="text-xs text-slate-500">
                                    {formatTimestamp(message.timestamp)} · {stageLabel(message.stage)}
                                  </p>
                                </div>
                              </li>
                            );
                          })}
                        </ol>
                      </div>
                    ) : null}

                    <div className={`${panelCardClass}`}>
                      <div className="flex items-center justify-between">
                        <h2 className="flex items-center gap-2 text-lg font-semibold text-slate-900">
                          <ArrowPathIcon className="h-5 w-5 text-slate-600" />
                          Workflow Runs
                        </h2>
                        <button
                          type="button"
                          onClick={handleDeleteCombinedJobs}
                          disabled={!selectedCombinedJobs.size}
                          className="inline-flex items-center gap-1 rounded-lg border border-rose-500/50 bg-rose-50 px-3 py-1.5 text-xs font-semibold text-rose-700 transition disabled:cursor-not-allowed disabled:opacity-50"
                        >
                          <TrashIcon className="h-4 w-4" />
                          Delete Selected
                        </button>
                      </div>
                      {jobs.length === 0 ? (
                        <p className="mt-4 text-sm text-slate-500">Start your first workflow to see it listed here.</p>
                      ) : (
                        <ul className="mt-4 space-y-3 text-sm">
                          {jobs.map((item) => {
                            const token = getStatusToken(item.status);
                            const isActive = item.job_id === activeJobId;
                            const isSelected = selectedCombinedJobs.has(item.job_id);
                            return (
                              <li
                                key={item.job_id}
                                className={`rounded-2xl border p-4 transition ${
                                  isActive
                                    ? "border-slate-400 bg-slate-100 shadow-sm"
                                    : "border-slate-200 bg-white hover:border-slate-300 hover:bg-slate-100"
                                }`}
                              >
                                <div className="flex items-start gap-3">
                                  <input
                                    type="checkbox"
                                    checked={isSelected}
                                    onChange={() => toggleCombinedSelection(item.job_id)}
                                    onClick={(event) => event.stopPropagation()}
                                    className="mt-1 h-4 w-4 rounded border-slate-200 bg-white text-slate-900 focus:ring-slate-400"
                                  />
                                  <button
                                    type="button"
                                    onClick={() => {
                                      setActiveJobId(item.job_id);
                                      setJob(item);
                                    }}
                                    className="flex-1 text-left"
                                  >
                                    <div className="flex items-center justify-between">
                                      <span className="font-mono text-xs text-slate-500">{item.job_id.slice(0, 12)}</span>
                                      <span
                                        className={`inline-flex items-center rounded-full px-2 py-0.5 text-[10px] font-semibold uppercase tracking-wide ${token.className}`}
                                      >
                                        {token.label}
                                      </span>
                                    </div>
                                    <div className="mt-1 text-[11px] text-slate-500">
                                      {item.resolution} · {stageLabel(item.stage)} · {formatTimeDistance(item.started_at)}
                                    </div>
                                  </button>
                                </div>
                              </li>
                            );
                          })}
                        </ul>
                      )}
                    </div>
                  </aside>
                </div>
              </>
            ) : (
              <>
                {singleError && (
                  <div className="flex items-start gap-3 rounded-2xl border border-rose-200 bg-rose-50 px-4 py-3 text-sm text-rose-700 shadow-md shadow-rose-200/40">
                    <ExclamationTriangleIcon className="h-5 w-5 flex-none text-rose-700" />
                    <span>{singleError}</span>
                  </div>
                )}

                <div className="grid gap-8 xl:grid-cols-[1.05fr,0.95fr]">
                  <section className="space-y-8">
                    <div className={`${panelCardClass}`}>
                      <div className="flex items-center gap-2 text-lg font-semibold text-slate-900">
                        <FilmIcon className="h-5 w-5 text-slate-600" />
                        Generate Single Clip
                      </div>
                      <p className="mt-2 text-sm text-slate-500">
                        Submit a single prompt directly to the Sora2 API. Optionally attach a still reference, select the render resolution, and choose a supported clip duration.
                      </p>
                      <form className="mt-5 space-y-5" onSubmit={handleSingleSubmit}>
                        <textarea
                          className="min-h-[220px] w-full resize-none rounded-2xl border border-slate-200 bg-white px-4 py-3 text-sm text-slate-900 shadow-[0_30px_100px_-80px_rgba(15,23,42,0.12)] placeholder:text-slate-500 transition focus:border-slate-400 focus:outline-none focus:ring-4 focus:ring-slate-200"
                          placeholder="Describe the scene, motion, cinematography, and mood for Sora2..."
                          value={singlePrompt}
                          onChange={(event) => setSinglePrompt(event.target.value)}
                        />

                        <div className="grid gap-4 sm:grid-cols-2">
                          <div className="space-y-2">
                            <label className="text-sm font-semibold text-slate-600">Resolution</label>
                            <select
                              value={singleResolution}
                              onChange={(event) => setSingleResolution(event.target.value as (typeof resolutionOptions)[number])}
                              className="w-full rounded-2xl border border-slate-200 bg-white px-3 py-2 text-sm text-slate-900 shadow-inner shadow-slate-200/50 focus:border-slate-400 focus:outline-none focus:ring-2 focus:ring-slate-200"
                            >
                              {resolutionOptions.map((option) => (
                                <option key={option} value={option}>
                                  {option}
                                </option>
                              ))}
                            </select>
                          </div>
                          <div className="space-y-2">
                            <span className="text-sm font-semibold text-slate-600">Duration</span>
                            <div className="flex gap-2">
                              {durationOptions.map((option) => {
                                const isActive = singleDuration === option;
                                return (
                                  <label
                                    key={option}
                                    className={`flex-1 cursor-pointer rounded-xl border px-3 py-2 text-center text-sm font-semibold transition ${
                                      isActive
                                        ? "border-slate-300 bg-slate-900 text-white shadow-sm"
                                        : "border-slate-200 bg-white text-slate-600 hover:border-slate-300 hover:text-slate-900"
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
                                    {option}s
                                  </label>
                                );
                              })}
                            </div>
                          </div>
                        </div>

                        <div className="space-y-3">
                          <label className="text-sm font-semibold text-slate-600">Reference image (optional)</label>
                          <input
                            type="file"
                            accept="image/png,image/jpeg"
                            onChange={handleReferenceImageChange}
                            className="max-w-xs text-xs text-slate-500"
                          />
                          {singleReferencePreview && (
                            <div className="mt-2 flex h-40 w-32 items-center justify-center overflow-hidden rounded-xl border border-slate-200 bg-white">
                              <Image
                                src={singleReferencePreview}
                                alt="Reference preview"
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
                            className="inline-flex items-center justify-center gap-2 rounded-xl bg-slate-900 px-5 py-2.5 text-sm font-semibold text-white shadow-sm shadow-slate-300/50 transition hover:bg-slate-700 focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-slate-400 disabled:cursor-not-allowed disabled:opacity-60"
                          >
                            {isSubmittingSingle ? (
                              <>
                                <ArrowPathIcon className="h-5 w-5 animate-spin" />
                                Sending Prompt...
                              </>
                            ) : (
                              <>
                                <PaperAirplaneIcon className="h-5 w-5" />
                                Generate Clip
                              </>
                            )}
                          </button>
                          <p className="text-xs text-slate-500">
                            API target: <span className="font-mono text-[11px] text-slate-500">{API_BASE}/api/single-videos</span>
                          </p>
                        </div>
                      </form>
                    </div>

                    {singleJob && (
                      <div className={`${panelCardClass}`}>
                        <div className="flex flex-wrap items-start justify-between gap-4">
                          <div>
                            <h2 className="flex items-center gap-2 text-lg font-semibold text-slate-900">
                              <FilmIcon className="h-5 w-5 text-slate-600" />
                              Single Video Status
                            </h2>
                            <p className="mt-1 text-sm text-slate-500">{stageLabel(singleJob.status)}</p>
                          </div>
                          <span className={`${pillButtonClass} ${singleStatusToken.className}`}>{singleStatusToken.label}</span>
                        </div>

                        <div className="mt-6 space-y-3">
                          <div className="flex items-center justify-between text-xs text-slate-500">
                            <span>Progress</span>
                            <span>{singleProgressPercent}%</span>
                          </div>
                          <div className="h-2.5 overflow-hidden rounded-full border border-slate-200 bg-white">
                            <div
                              className="h-full rounded-full bg-slate-900 transition-all duration-500 ease-out"
                              style={{ width: `${singleProgressPercent}%` }}
                            />
                          </div>
                          {singleJob.error && (
                            <div className="flex items-center gap-2 rounded-xl border border-rose-200 bg-rose-50 px-3 py-2 text-xs text-rose-700">
                              <ExclamationTriangleIcon className="h-4 w-4" />
                              {singleJob.error}
                            </div>
                          )}
                        </div>

                        <dl className="mt-6 grid gap-4 text-sm text-slate-600 sm:grid-cols-2 md:grid-cols-4">
                          <div>
                            <dt className="text-xs uppercase tracking-wide text-slate-500">Started</dt>
                            <dd className="mt-1 text-slate-600">{formatTimeDistance(singleJob.started_at)}</dd>
                          </div>
                          <div>
                            <dt className="text-xs uppercase tracking-wide text-slate-500">Completed</dt>
                            <dd className="mt-1 text-slate-600">{singleJob.completed_at ? formatTimeDistance(singleJob.completed_at) : "—"}</dd>
                          </div>
                          <div>
                            <dt className="text-xs uppercase tracking-wide text-slate-500">Resolution</dt>
                            <dd className="mt-1 text-slate-600">{singleJob.resolution}</dd>
                          </div>
                          <div>
                            <dt className="text-xs uppercase tracking-wide text-slate-500">Duration</dt>
                            <dd className="mt-1 text-slate-600">{singleJob.duration}s</dd>
                          </div>
                        </dl>

                        {singleVideoUrl ? (
                          <div className="mt-8 space-y-3">
                            <h3 className="text-sm font-semibold text-slate-600">Generated Video</h3>
                            <video
                              controls
                              className="max-h-[360px] w-full rounded-2xl border border-slate-200 bg-black/60"
                              src={singleVideoUrl}
                            >
                              Your browser does not support HTML5 video.
                            </video>
                            <a
                              href={singleVideoUrl}
                              target="_blank"
                              rel="noreferrer"
                              className="inline-flex items-center gap-2 text-sm font-medium text-slate-600 hover:text-slate-900"
                            >
                              <PlayCircleIcon className="h-4 w-4" />
                              Open MP4 in new tab
                            </a>
                          </div>
                        ) : (
                          <div className="mt-8 flex h-40 items-center justify-center rounded-2xl border border-dashed border-slate-200 bg-slate-100 text-xs text-slate-500">
                            Video pending...
                          </div>
                        )}
                      </div>
                    )}
                  </section>

                  <aside className="space-y-8">
                    {singleJob?.messages?.length ? (
                      <div className={`${panelCardClass}`}>
                        <h2 className="flex items-center gap-2 text-lg font-semibold text-slate-900">
                          <ClockIcon className="h-5 w-5 text-slate-600" />
                          Job Timeline
                        </h2>
                        <ol className="mt-6 space-y-4">
                          {singleJob.messages.map((message, index) => {
                            const Icon = stageIcons[message.stage] ?? ArrowPathIcon;
                            return (
                              <li
                                key={`${message.timestamp}-${index}`}
                                className="relative flex gap-3 rounded-2xl border border-slate-200 bg-white p-3 shadow-inner shadow-slate-200/60"
                              >
                                <div className="relative mt-0.5 flex h-7 w-7 items-center justify-center rounded-full border border-slate-200 bg-slate-100">
                                  <Icon className="h-4 w-4 text-slate-600" />
                                </div>
                                <div>
                                  <p className="text-sm text-slate-600">{message.text}</p>
                                  <p className="text-xs text-slate-500">
                                    {formatTimestamp(message.timestamp)} · {stageLabel(message.stage)}
                                  </p>
                                </div>
                              </li>
                            );
                          })}
                        </ol>
                      </div>
                    ) : null}

                    <div className={`${panelCardClass}`}>
                      <div className="flex items-center justify-between">
                        <h2 className="flex items-center gap-2 text-lg font-semibold text-slate-900">
                          <ArrowPathIcon className="h-5 w-5 text-slate-600" />
                          Recent Single Jobs
                        </h2>
                        <button
                          type="button"
                          onClick={handleDeleteSingleJobs}
                          disabled={!selectedSingleJobs.size}
                          className="inline-flex items-center gap-1 rounded-lg border border-rose-500/50 bg-rose-50 px-3 py-1.5 text-xs font-semibold text-rose-700 transition disabled:cursor-not-allowed disabled:opacity-50"
                        >
                          <TrashIcon className="h-4 w-4" />
                          Delete Selected
                        </button>
                      </div>
                      {singleJobs.length === 0 ? (
                        <p className="mt-4 text-sm text-slate-500">Generate a clip to see it listed here.</p>
                      ) : (
                        <ul className="mt-4 space-y-3 text-sm">
                          {singleJobs.map((item) => {
                            const token = getStatusToken(item.status);
                            const isActive = item.job_id === activeSingleJobId;
                            const isSelected = selectedSingleJobs.has(item.job_id);
                            return (
                              <li
                                key={item.job_id}
                                className={`rounded-2xl border p-4 transition ${
                                  isActive
                                    ? "border-slate-400 bg-slate-100 shadow-sm"
                                    : "border-slate-200 bg-white hover:border-slate-300 hover:bg-slate-100"
                                }`}
                              >
                                <div className="flex items-start gap-3">
                                  <input
                                    type="checkbox"
                                    checked={isSelected}
                                    onChange={() => toggleSingleSelection(item.job_id)}
                                    onClick={(event) => event.stopPropagation()}
                                    className="mt-1 h-4 w-4 rounded border-slate-200 bg-white text-slate-900 focus:ring-slate-400"
                                  />
                                  <button
                                    type="button"
                                    onClick={() => {
                                      setActiveSingleJobId(item.job_id);
                                      setSingleJob(item);
                                    }}
                                    className="flex-1 text-left"
                                  >
                                    <div className="flex items-center justify-between">
                                      <span className="font-mono text-xs text-slate-500">{item.job_id.slice(0, 12)}</span>
                                      <span
                                        className={`inline-flex items-center rounded-full px-2 py-0.5 text-[10px] font-semibold uppercase tracking-wide ${token.className}`}
                                      >
                                        {token.label}
                                      </span>
                                    </div>
                                    <div className="mt-1 text-[11px] text-slate-500">
                                      {item.resolution} · {item.duration}s · {formatTimeDistance(item.started_at)}
                                    </div>
                                  </button>
                                </div>
                              </li>
                            );
                          })}
                        </ul>
                      )}
                    </div>
                  </aside>
                </div>
              </>
            )}
          </div>
        </main>
      </div>
    </div>
  );
}
