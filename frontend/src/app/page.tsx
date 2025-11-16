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

type SceneStatus = {
  order: number;
  label: string;
  duration_seconds: number;
  prompt_path: string;
  video_path: string;
  video_url: string | null;
  log_path: string | null;
  client_log_path: string | null;
};

type JobStatus = {
  job_id: string;
  status: string;
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
    className: "border border-amber-200 bg-amber-50 text-amber-700",
  },
  running: {
    label: "In Progress",
    className: "border border-sky-200 bg-sky-50 text-sky-700",
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
      className: "border border-slate-200 bg-slate-100 text-slate-600",
    };
  }
  return (
    STATUS_TOKENS[status] ?? {
      label: status.charAt(0).toUpperCase() + status.slice(1),
      className: "border border-slate-200 bg-slate-100 text-slate-600",
    }
  );
};

export default function Home() {
  const [activeTab, setActiveTab] = useState<TabId>("combined");

  // Combined workflow state
  const [script, setScript] = useState("");
  const [sceneImageFiles, setSceneImageFiles] = useState<File[]>([]);
  const [sceneImagePreviews, setSceneImagePreviews] = useState<string[]>([]);
  const [jobs, setJobs] = useState<JobStatus[]>([]);
  const [activeJobId, setActiveJobId] = useState<string | null>(null);
  const [job, setJob] = useState<JobStatus | null>(null);
  const [isSubmittingCombined, setIsSubmittingCombined] = useState(false);
  const [combinedError, setCombinedError] = useState<string | null>(null);
  const [selectedCombinedJobs, setSelectedCombinedJobs] = useState<Set<string>>(new Set());

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
    let interval: ReturnType<typeof setInterval> | undefined;

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
        if (terminalStatuses.has(data.status) && interval) {
          clearInterval(interval);
          interval = undefined;
        }
      } catch (err) {
        console.error(err);
        if (!disposed) {
          setCombinedError(
            "Failed to refresh workflow status. Check that the FastAPI server is reachable.",
          );
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
  const currentStatusToken = getStatusToken(job?.status);

  const singleVideoUrl = buildAssetUrl(singleJob?.video_url);
  const singleStatusToken = getStatusToken(singleJob?.status);

  const handleSceneImagesChange = async (event: ChangeEvent<HTMLInputElement>) => {
    const files = event.target.files ? Array.from(event.target.files) : [];
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
      if (sceneImageFiles.length) {
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

  const headerJobId = activeTab === "combined" ? job?.job_id : singleJob?.job_id;
  const headerStartedAt = activeTab === "combined" ? job?.started_at : singleJob?.started_at;

  return (
    <div className="relative min-h-screen overflow-hidden bg-[#f6f5f2] text-slate-800">
      <div className="pointer-events-none absolute inset-0">
        <div className="absolute -left-24 -top-16 h-72 w-72 rounded-full bg-gradient-to-br from-indigo-200/70 via-rose-100/40 to-transparent blur-3xl" />
        <div className="absolute right-[-12%] top-24 h-80 w-80 rounded-full bg-gradient-to-br from-sky-100/60 via-white/40 to-transparent blur-3xl" />
        <div className="absolute bottom-[-18%] left-[18%] h-96 w-96 rounded-[260px] bg-gradient-to-br from-violet-100/70 via-white/60 to-transparent blur-3xl" />
      </div>
      <div className="mx-auto flex max-w-6xl flex-col gap-8 px-6 pb-20 pt-16">
        <header className="relative overflow-hidden rounded-3xl border border-slate-200/80 bg-white/90 px-8 py-10 shadow-[0_45px_120px_-80px_rgba(20,32,62,0.6)] backdrop-blur">
          <div className="pointer-events-none absolute inset-y-0 right-0 hidden w-1/2 translate-x-10 bg-[radial-gradient(circle_at_top,_rgba(99,102,241,0.12),transparent_60%)] md:block" />
          <div className="relative flex flex-col gap-8 lg:flex-row lg:items-center lg:justify-between">
            <div className="flex items-center gap-5">
              <Image
                src="/sora2_logo.jpg"
                alt="Sora Agent Workflow logo"
                width={64}
                height={64}
                className="h-16 w-16 rounded-2xl border border-slate-200/80 object-cover shadow-sm"
                priority
              />
              <div>
                <span className="text-xs font-semibold uppercase tracking-[0.35em] text-slate-500">
                  Sora Agent Workflow by Azure Sora2
                </span>
                <h1 className="mt-2 text-3xl font-semibold text-slate-900 lg:text-4xl">
                  Script to Video AI Composer
                </h1>
                <p className="mt-3 max-w-xl text-sm leading-relaxed text-slate-600">
                  Launch story-driven renders or quick single-shot clips with a refined interface for tracking progress and assets.
                </p>
              </div>
            </div>
            {headerJobId ? (
              <div className="rounded-2xl border border-slate-200 bg-white/70 p-5 text-right shadow-sm">
                <div className="text-xs font-semibold uppercase tracking-wide text-slate-500">
                  {activeTab === "combined" ? "Current Workflow" : "Current Single Video"}
                </div>
                <div className="mt-2 font-mono text-sm text-slate-800">{headerJobId}</div>
                <div className="mt-1 text-xs text-slate-500">
                  Started {formatTimeDistance(headerStartedAt ?? null)}
                </div>
              </div>
            ) : (
              <div className="rounded-2xl border border-dashed border-slate-200 bg-white/40 p-5 text-sm text-slate-500">
                Start a job to track its progress here.
              </div>
            )}
          </div>
        </header>

        <div className="flex flex-wrap gap-3 rounded-2xl border border-slate-200 bg-white/70 p-2 backdrop-blur">
          {TABS.map(({ id, label }) => {
            const isActive = activeTab === id;
            return (
              <button
                key={id}
                type="button"
                onClick={() => setActiveTab(id)}
                className={`flex-1 rounded-xl px-4 py-2 text-sm font-semibold transition focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-slate-900 ${
                  isActive
                    ? "bg-slate-900 text-white shadow"
                    : "bg-transparent text-slate-600 hover:bg-slate-100"
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
              <div className="flex items-start gap-3 rounded-2xl border border-rose-200 bg-rose-50/90 px-4 py-3 text-sm text-rose-700 shadow-sm">
                <ExclamationTriangleIcon className="h-5 w-5 flex-none text-rose-400" />
                <span>{combinedError}</span>
              </div>
            )}

            <div className="grid gap-8 xl:grid-cols-[1.15fr,0.85fr]">
              <section className="space-y-8">
                <div className="rounded-3xl border border-slate-200 bg-white/95 p-6 shadow-[0_30px_90px_-70px_rgba(29,37,59,0.55)] backdrop-blur">
                  <div className="flex items-center justify-between">
                    <div className="flex items-center gap-2 text-lg font-semibold text-slate-900">
                      <DocumentTextIcon className="h-5 w-5 text-indigo-500" />
                      Storyboard Script
                    </div>
                  </div>
                  <p className="mt-2 text-sm text-slate-600">
                    Paste your storyboard using <span className="font-mono text-xs text-indigo-500">**Scene 1 (Title)**</span> headings. The Director agent will refine each scene prompt following Sora2 best practices.
                  </p>
                  <form className="mt-5 space-y-5" onSubmit={handleCombinedSubmit}>
                    <textarea
                      className="min-h-[260px] w-full resize-none rounded-2xl border border-slate-200 bg-white px-4 py-3 text-sm text-slate-700 shadow-[0_20px_45px_-35px_rgba(28,33,53,0.5)] transition focus:border-indigo-300 focus:outline-none focus:ring-4 focus:ring-indigo-200/80"
                      placeholder="**Scene 1 (Sunrise Over the City)**\nThe camera glides through..."
                      value={script}
                      onChange={(event) => setScript(event.target.value)}
                    />

                    <div className="space-y-3">
                      <label className="text-sm font-semibold text-slate-800">Scene start images (optional)</label>
                      <div className="flex flex-col gap-2 sm:flex-row sm:items-center">
                        <input
                          type="file"
                          accept="image/png,image/jpeg"
                          multiple
                          onChange={handleSceneImagesChange}
                          className="max-w-xs text-xs text-slate-600"
                        />
                        <span className="text-xs text-slate-500">
                          Provide images to override defaults from <code className="font-mono text-[11px]">scene_start_screen/</code>.
                        </span>
                      </div>
                      {sceneImagePreviews.length > 0 && (
                        <div className="grid grid-cols-2 gap-3 sm:grid-cols-3">
                          {sceneImagePreviews.map((src, index) => (
                            <div
                              key={`${src}-${index}`}
                              className="flex h-24 items-center justify-center overflow-hidden rounded-xl border border-slate-200 bg-slate-50"
                            >
                              <Image
                                src={src}
                                alt={`Scene reference ${index + 1}`}
                                width={160}
                                height={160}
                                unoptimized
                                className="h-full w-full object-cover"
                              />
                            </div>
                          ))}
                        </div>
                      )}
                    </div>

                    <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
                      <button
                        type="submit"
                        disabled={isSubmittingCombined}
                        className="inline-flex items-center justify-center gap-2 rounded-lg border border-slate-900/10 bg-slate-900 px-5 py-2.5 text-sm font-semibold text-white shadow-sm transition hover:bg-slate-700 focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-slate-900 disabled:cursor-not-allowed disabled:border-slate-200 disabled:bg-slate-200 disabled:text-slate-500"
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
                        API target: <span className="font-mono text-[11px] text-slate-700">{API_BASE}/api/workflows</span>
                      </p>
                    </div>
                  </form>
                </div>

                {job && (
                  <div className="rounded-3xl border border-slate-200 bg-white/95 p-6 shadow-sm shadow-slate-200/70 backdrop-blur">
                    <div className="flex flex-wrap items-start justify-between gap-4">
                      <div>
                        <h2 className="flex items-center gap-2 text-lg font-semibold text-slate-900">
                          <FilmIcon className="h-5 w-5 text-indigo-500" />
                          Workflow Status
                        </h2>
                        <p className="mt-1 text-sm text-slate-600">{stageLabel(job.stage)}</p>
                      </div>
                      <span
                        className={`inline-flex items-center gap-2 rounded-full px-3 py-1 text-xs font-semibold uppercase tracking-wide ${currentStatusToken.className}`}
                      >
                        {currentStatusToken.label}
                      </span>
                    </div>

                    <div className="mt-6 space-y-3">
                      <div className="flex items-center justify-between text-xs text-slate-500">
                        <span>Progress</span>
                        <span>{combinedProgressPercent}%</span>
                      </div>
                      <div className="h-2.5 overflow-hidden rounded-full border border-slate-200 bg-slate-100">
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

                    <dl className="mt-6 grid gap-4 text-sm text-slate-600 sm:grid-cols-2">
                      <div>
                        <dt className="text-xs uppercase tracking-wide text-slate-500">Started</dt>
                        <dd className="mt-1 text-slate-700">{formatTimeDistance(job.started_at)}</dd>
                      </div>
                      <div>
                        <dt className="text-xs uppercase tracking-wide text-slate-500">Completed</dt>
                        <dd className="mt-1 text-slate-700">
                          {job.completed_at ? formatTimeDistance(job.completed_at) : "—"}
                        </dd>
                      </div>
                    </dl>

                    {finalVideoUrl && (
                      <div className="mt-8 space-y-3">
                        <h3 className="text-sm font-semibold text-slate-800">Final Video</h3>
                        <video
                          controls
                          className="max-h-[420px] w-full rounded-2xl border border-slate-200 bg-black"
                          src={finalVideoUrl}
                        >
                          Your browser does not support HTML5 video.
                        </video>
                        <a
                          href={finalVideoUrl}
                          target="_blank"
                          rel="noreferrer"
                          className="inline-flex items-center gap-2 text-sm font-medium text-indigo-600 hover:text-indigo-500"
                        >
                          <PlayCircleIcon className="h-4 w-4" />
                          Open MP4 in new tab
                        </a>
                      </div>
                    )}
                  </div>
                )}

                {job?.scenes?.length ? (
                  <div className="rounded-3xl border border-slate-200 bg-white/95 p-6 shadow-sm shadow-slate-200/70 backdrop-blur">
                    <h2 className="flex items-center gap-2 text-lg font-semibold text-slate-900">
                      <PlayCircleIcon className="h-5 w-5 text-indigo-500" />
                      Scene Renders
                    </h2>
                    <div className="mt-5 grid gap-5 md:grid-cols-2">
                      {job.scenes.map((scene) => {
                        const sceneVideoUrl = buildAssetUrl(scene.video_url);
                        return (
                          <div
                            key={scene.order}
                            className="rounded-2xl border border-slate-200 bg-white p-4 shadow-sm shadow-slate-200/60"
                          >
                            <div className="flex items-start justify-between gap-2">
                              <div>
                                <h3 className="text-sm font-semibold text-slate-900">{scene.label}</h3>
                                <p className="text-xs text-slate-500">{scene.duration_seconds} seconds</p>
                              </div>
                              <span className="rounded-full border border-slate-200 px-2 py-0.5 text-[11px] uppercase tracking-wide text-slate-500">
                                Scene {scene.order}
                              </span>
                            </div>
                            {sceneVideoUrl ? (
                              <video
                                key={`${scene.order}-${sceneVideoUrl}`}
                                controls
                                className="mt-3 max-h-56 w-full rounded-xl border border-slate-200 bg-black"
                                src={sceneVideoUrl}
                              >
                                Video unavailable.
                              </video>
                            ) : (
                              <div className="mt-3 flex h-40 items-center justify-center rounded-xl border border-dashed border-slate-200 bg-slate-50 text-xs text-slate-400">
                                Clip pending...
                              </div>
                            )}
                          </div>
                        );
                      })}
                    </div>
                  </div>
                ) : null}
              </section>

              <aside className="space-y-8">
                {job?.messages?.length ? (
                  <div className="rounded-3xl border border-slate-200 bg-white/95 p-6 shadow-sm shadow-slate-200/70 backdrop-blur">
                    <h2 className="flex items-center gap-2 text-lg font-semibold text-slate-900">
                      <ClockIcon className="h-5 w-5 text-indigo-500" />
                      Workflow Timeline
                    </h2>
                    <ol className="mt-6 space-y-4">
                      {job.messages.map((message, index) => {
                        const Icon = stageIcons[message.stage] ?? ArrowPathIcon;
                        return (
                          <li
                            key={`${message.timestamp}-${index}`}
                            className="relative flex gap-3 rounded-2xl border border-slate-100 bg-white/90 p-3 shadow-sm shadow-slate-200/40"
                          >
                            <div className="relative mt-0.5 flex h-7 w-7 items-center justify-center rounded-full border border-slate-200 bg-white">
                              <Icon className="h-4 w-4 text-indigo-500" />
                            </div>
                            <div>
                              <p className="text-sm text-slate-800">{message.text}</p>
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

                <div className="rounded-3xl border border-slate-200 bg-white/95 p-6 shadow-sm shadow-slate-200/70 backdrop-blur">
                  <div className="flex items-center justify-between">
                    <h2 className="flex items-center gap-2 text-lg font-semibold text-slate-900">
                      <ArrowPathIcon className="h-5 w-5 text-indigo-500" />
                      Workflow Runs
                    </h2>
                    <button
                      type="button"
                      onClick={handleDeleteCombinedJobs}
                      disabled={!selectedCombinedJobs.size}
                      className="inline-flex items-center gap-1 rounded-lg border border-rose-200 bg-rose-50 px-3 py-1.5 text-xs font-semibold text-rose-600 transition disabled:cursor-not-allowed disabled:opacity-50"
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
                                ? "border-indigo-200 bg-indigo-50 shadow-sm"
                                : "border-slate-200 bg-white shadow-sm hover:border-indigo-200 hover:bg-indigo-50/70"
                            }`}
                          >
                            <div className="flex items-start gap-3">
                              <input
                                type="checkbox"
                                checked={isSelected}
                                onChange={() => toggleCombinedSelection(item.job_id)}
                                onClick={(event) => event.stopPropagation()}
                                className="mt-1 h-4 w-4 rounded border-slate-300 text-indigo-600 focus:ring-indigo-500"
                              />
                              <button
                                type="button"
                                onClick={() => setActiveJobId(item.job_id)}
                                className="flex-1 text-left"
                              >
                                <div className="flex items-center justify-between">
                                  <span className="font-mono text-xs text-slate-700">{item.job_id.slice(0, 12)}</span>
                                  <span
                                    className={`inline-flex items-center rounded-full px-2 py-0.5 text-[10px] font-semibold uppercase tracking-wide ${token.className}`}
                                  >
                                    {token.label}
                                  </span>
                                </div>
                                <div className="mt-1 text-[11px] text-slate-500">
                                  {stageLabel(item.stage)} · {formatTimeDistance(item.started_at)}
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
              <div className="flex items-start gap-3 rounded-2xl border border-rose-200 bg-rose-50/90 px-4 py-3 text-sm text-rose-700 shadow-sm">
                <ExclamationTriangleIcon className="h-5 w-5 flex-none text-rose-400" />
                <span>{singleError}</span>
              </div>
            )}

            <div className="grid gap-8 xl:grid-cols-[1.05fr,0.95fr]">
              <section className="space-y-8">
                <div className="rounded-3xl border border-slate-200 bg-white/95 p-6 shadow-[0_30px_90px_-70px_rgba(29,37,59,0.55)] backdrop-blur">
                  <div className="flex items-center gap-2 text-lg font-semibold text-slate-900">
                    <FilmIcon className="h-5 w-5 text-indigo-500" />
                    Generate Single Clip
                  </div>
                  <p className="mt-2 text-sm text-slate-600">
                    Submit one prompt directly to the Sora2 video API. Provide an optional reference image, choose the resolution, and pick a supported duration.
                  </p>
                  <form className="mt-5 space-y-5" onSubmit={handleSingleSubmit}>
                    <textarea
                      className="min-h-[220px] w-full resize-none rounded-2xl border border-slate-200 bg-white px-4 py-3 text-sm text-slate-700 shadow-[0_20px_45px_-35px_rgba(28,33,53,0.5)] transition focus:border-indigo-300 focus:outline-none focus:ring-4 focus:ring-indigo-200/80"
                      placeholder="Describe the scene, motion, cinematography, and mood for Sora2..."
                      value={singlePrompt}
                      onChange={(event) => setSinglePrompt(event.target.value)}
                    />

                    <div className="grid gap-4 sm:grid-cols-2">
                      <div className="space-y-2">
                        <label className="text-sm font-semibold text-slate-800">Resolution</label>
                        <select
                          value={singleResolution}
                          onChange={(event) => setSingleResolution(event.target.value as (typeof resolutionOptions)[number])}
                          className="w-full rounded-xl border border-slate-200 px-3 py-2 text-sm text-slate-700 shadow-sm focus:border-indigo-300 focus:outline-none focus:ring-4 focus:ring-indigo-200/80"
                        >
                          {resolutionOptions.map((option) => (
                            <option key={option} value={option}>
                              {option}
                            </option>
                          ))}
                        </select>
                      </div>
                      <div className="space-y-2">
                        <span className="text-sm font-semibold text-slate-800">Duration</span>
                        <div className="flex gap-2">
                          {durationOptions.map((option) => {
                            const isActive = singleDuration === option;
                            return (
                              <label
                                key={option}
                                className={`flex-1 cursor-pointer rounded-xl border px-3 py-2 text-center text-sm font-semibold transition ${
                                  isActive
                                    ? "border-slate-900 bg-slate-900 text-white shadow"
                                    : "border-slate-200 bg-white text-slate-600 hover:border-indigo-200"
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
                      <label className="text-sm font-semibold text-slate-800">Reference image (optional)</label>
                      <input
                        type="file"
                        accept="image/png,image/jpeg"
                        onChange={handleReferenceImageChange}
                        className="max-w-xs text-xs text-slate-600"
                      />
                      {singleReferencePreview && (
                        <div className="mt-2 flex h-40 w-32 items-center justify-center overflow-hidden rounded-xl border border-slate-200 bg-slate-50">
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
                        className="inline-flex items-center justify-center gap-2 rounded-lg border border-slate-900/10 bg-slate-900 px-5 py-2.5 text-sm font-semibold text-white shadow-sm transition hover:bg-slate-700 focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-slate-900 disabled:cursor-not-allowed disabled:border-slate-200 disabled:bg-slate-200 disabled:text-slate-500"
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
                        API target: <span className="font-mono text-[11px] text-slate-700">{API_BASE}/api/single-videos</span>
                      </p>
                    </div>
                  </form>
                </div>

                {singleJob && (
                  <div className="rounded-3xl border border-slate-200 bg-white/95 p-6 shadow-sm shadow-slate-200/70 backdrop-blur">
                    <div className="flex flex-wrap items-start justify-between gap-4">
                      <div>
                        <h2 className="flex items-center gap-2 text-lg font-semibold text-slate-900">
                          <FilmIcon className="h-5 w-5 text-indigo-500" />
                          Single Video Status
                        </h2>
                        <p className="mt-1 text-sm text-slate-600">{stageLabel(singleJob.status)}</p>
                      </div>
                      <span
                        className={`inline-flex items-center gap-2 rounded-full px-3 py-1 text-xs font-semibold uppercase tracking-wide ${singleStatusToken.className}`}
                      >
                        {singleStatusToken.label}
                      </span>
                    </div>

                    <div className="mt-6 space-y-3">
                      <div className="flex items-center justify-between text-xs text-slate-500">
                        <span>Progress</span>
                        <span>{singleProgressPercent}%</span>
                      </div>
                      <div className="h-2.5 overflow-hidden rounded-full border border-slate-200 bg-slate-100">
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

                    <dl className="mt-6 grid gap-4 text-sm text-slate-600 sm:grid-cols-2">
                      <div>
                        <dt className="text-xs uppercase tracking-wide text-slate-500">Started</dt>
                        <dd className="mt-1 text-slate-700">{formatTimeDistance(singleJob.started_at)}</dd>
                      </div>
                      <div>
                        <dt className="text-xs uppercase tracking-wide text-slate-500">Completed</dt>
                        <dd className="mt-1 text-slate-700">
                          {singleJob.completed_at ? formatTimeDistance(singleJob.completed_at) : "—"}
                        </dd>
                      </div>
                    </dl>

                    {singleVideoUrl ? (
                      <div className="mt-8 space-y-3">
                        <h3 className="text-sm font-semibold text-slate-800">Generated Video</h3>
                        <video
                          controls
                          className="max-h-[360px] w-full rounded-2xl border border-slate-200 bg-black"
                          src={singleVideoUrl}
                        >
                          Your browser does not support HTML5 video.
                        </video>
                        <a
                          href={singleVideoUrl}
                          target="_blank"
                          rel="noreferrer"
                          className="inline-flex items-center gap-2 text-sm font-medium text-indigo-600 hover:text-indigo-500"
                        >
                          <PlayCircleIcon className="h-4 w-4" />
                          Open MP4 in new tab
                        </a>
                      </div>
                    ) : (
                      <div className="mt-8 flex h-40 items-center justify-center rounded-2xl border border-dashed border-slate-200 bg-slate-50 text-xs text-slate-400">
                        Video pending...
                      </div>
                    )}
                  </div>
                )}
              </section>

              <aside className="space-y-8">
                {singleJob?.messages?.length ? (
                  <div className="rounded-3xl border border-slate-200 bg-white/95 p-6 shadow-sm shadow-slate-200/70 backdrop-blur">
                    <h2 className="flex items-center gap-2 text-lg font-semibold text-slate-900">
                      <ClockIcon className="h-5 w-5 text-indigo-500" />
                      Job Timeline
                    </h2>
                    <ol className="mt-6 space-y-4">
                      {singleJob.messages.map((message, index) => {
                        const Icon = stageIcons[message.stage] ?? ArrowPathIcon;
                        return (
                          <li
                            key={`${message.timestamp}-${index}`}
                            className="relative flex gap-3 rounded-2xl border border-slate-100 bg-white/90 p-3 shadow-sm shadow-slate-200/40"
                          >
                            <div className="relative mt-0.5 flex h-7 w-7 items-center justify-center rounded-full border border-slate-200 bg-white">
                              <Icon className="h-4 w-4 text-indigo-500" />
                            </div>
                            <div>
                              <p className="text-sm text-slate-800">{message.text}</p>
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

                <div className="rounded-3xl border border-slate-200 bg-white/95 p-6 shadow-sm shadow-slate-200/70 backdrop-blur">
                  <div className="flex items-center justify-between">
                    <h2 className="flex items-center gap-2 text-lg font-semibold text-slate-900">
                      <ArrowPathIcon className="h-5 w-5 text-indigo-500" />
                      Recent Single Jobs
                    </h2>
                    <button
                      type="button"
                      onClick={handleDeleteSingleJobs}
                      disabled={!selectedSingleJobs.size}
                      className="inline-flex items-center gap-1 rounded-lg border border-rose-200 bg-rose-50 px-3 py-1.5 text-xs font-semibold text-rose-600 transition disabled:cursor-not-allowed disabled:opacity-50"
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
                                ? "border-indigo-200 bg-indigo-50 shadow-sm"
                                : "border-slate-200 bg-white shadow-sm hover:border-indigo-200 hover:bg-indigo-50/70"
                            }`}
                          >
                            <div className="flex items-start gap-3">
                              <input
                                type="checkbox"
                                checked={isSelected}
                                onChange={() => toggleSingleSelection(item.job_id)}
                                onClick={(event) => event.stopPropagation()}
                                className="mt-1 h-4 w-4 rounded border-slate-300 text-indigo-600 focus:ring-indigo-500"
                              />
                              <button
                                type="button"
                                onClick={() => setActiveSingleJobId(item.job_id)}
                                className="flex-1 text-left"
                              >
                                <div className="flex items-center justify-between">
                                  <span className="font-mono text-xs text-slate-700">{item.job_id.slice(0, 12)}</span>
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
    </div>
  );
}
