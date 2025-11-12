"use client";

import { FormEvent, useEffect, useMemo, useState } from "react";
import {
  ArrowPathIcon,
  CheckCircleIcon,
  ClockIcon,
  DocumentTextIcon,
  ExclamationTriangleIcon,
  FilmIcon,
  PaperAirplaneIcon,
  PlayCircleIcon,
} from "@heroicons/react/24/outline";
import Image from "next/image";

const API_BASE = (process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://localhost:8000").replace(
  /\/$/,
  "",
);

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
  completed: CheckCircleIcon,
  failed: ExclamationTriangleIcon,
};

const terminalStatuses = new Set(["completed", "failed"]);

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
  const [script, setScript] = useState("");
  const [jobs, setJobs] = useState<JobStatus[]>([]);
  const [activeJobId, setActiveJobId] = useState<string | null>(null);
  const [job, setJob] = useState<JobStatus | null>(null);
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    const loadJobs = async () => {
      try {
        const response = await fetch(`${API_BASE}/api/workflows`);
        if (!response.ok) {
          throw new Error(await response.text());
        }
        const data: JobStatus[] = await response.json();
        setJobs(data);
        if (data.length) {
          const latest = data[0];
          setJob((current) => current ?? latest);
          setActiveJobId((current) => current ?? latest.job_id);
        }
        setError(null);
      } catch (err) {
        console.error(err);
        setError(
          "Unable to load existing workflows. Start one below or verify the API URL.",
        );
      }
    };
    void loadJobs();
  }, []);

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
        setError(null);

        if (terminalStatuses.has(data.status) && interval) {
          clearInterval(interval);
          interval = undefined;
        }
      } catch (err) {
        console.error(err);
        if (!disposed) {
          setError(
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

  const handleSubmit = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    const trimmed = script.trim();
    if (!trimmed) {
      setError("Please provide a storyboard script before starting the workflow.");
      return;
    }

    setIsSubmitting(true);
    setError(null);
    try {
      const response = await fetch(`${API_BASE}/api/workflows`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ script: trimmed }),
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
      setError(null);
    } catch (err) {
      console.error(err);
      setError(
        "Unable to start workflow. Confirm the FastAPI server is running and accepts requests.",
      );
    } finally {
      setIsSubmitting(false);
    }
  };

  const progressPercent = useMemo(() => {
    if (!job) return 0;
    const value = Number.isFinite(job.progress) ? job.progress : 0;
    return Math.max(0, Math.min(100, Math.round(value * 100)));
  }, [job]);

  const finalVideoUrl = buildAssetUrl(job?.final_video_url);
  const currentStatusToken = getStatusToken(job?.status);

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
                  Launch story-driven renders with a refined interface 
                  of visuals.
                </p>
              </div>
            </div>
            {job ? (
              <div className="rounded-2xl border border-slate-200 bg-white/70 p-5 text-right shadow-sm">
                <div className="text-xs font-semibold uppercase tracking-wide text-slate-500">
                  Current Workflow
                </div>
                <div className="mt-2 font-mono text-sm text-slate-800">{job.job_id}</div>
                <div className="mt-1 text-xs text-slate-500">
                  Started {formatTimeDistance(job.started_at)}
                </div>
              </div>
            ) : (
              <div className="rounded-2xl border border-dashed border-slate-200 bg-white/40 p-5 text-sm text-slate-500">
                Start a storyboard workflow to track its progress here.
              </div>
            )}
          </div>
        </header>

        {error && (
          <div className="flex items-start gap-3 rounded-2xl border border-rose-200 bg-rose-50/90 px-4 py-3 text-sm text-rose-700 shadow-sm">
            <ExclamationTriangleIcon className="h-5 w-5 flex-none text-rose-400" />
            <span>{error}</span>
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
                Paste your storyboard using{" "}
                <span className="font-mono text-xs text-indigo-500">
                  **Scene 1 (Title)**
                </span>{" "}
                headings. The Director agent will craft prompts for each scene.
              </p>
              <form className="mt-5 space-y-4" onSubmit={handleSubmit}>
                <textarea
                  className="min-h-[280px] w-full resize-none rounded-2xl border border-slate-200 bg-white px-4 py-3 text-sm text-slate-700 shadow-[0_20px_45px_-35px_rgba(28,33,53,0.5)] transition focus:border-indigo-300 focus:outline-none focus:ring-4 focus:ring-indigo-200/80"
                  placeholder="**Scene 1 (Sunrise Over the City)**&#10;The camera glides through..."
                  value={script}
                  onChange={(event) => setScript(event.target.value)}
                />
                <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
                  <button
                    type="submit"
                    disabled={isSubmitting}
                    className="inline-flex items-center justify-center gap-2 rounded-lg border border-slate-900/10 bg-slate-900 px-5 py-2.5 text-sm font-semibold text-white shadow-sm transition hover:bg-slate-700 focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-slate-900 disabled:cursor-not-allowed disabled:border-slate-200 disabled:bg-slate-200 disabled:text-slate-500"
                  >
                    {isSubmitting ? (
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
                    API target:{" "}
                    <span className="font-mono text-[11px] text-slate-700">
                      {API_BASE}/api/workflows
                    </span>
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
                    <span>{progressPercent}%</span>
                  </div>
                  <div className="h-2.5 overflow-hidden rounded-full border border-slate-200 bg-slate-100">
                    <div
                      className="h-full rounded-full bg-slate-900 transition-all duration-500 ease-out"
                      style={{ width: `${progressPercent}%` }}
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
                    <dt className="text-xs uppercase tracking-wide text-slate-500">
                      Started
                    </dt>
                    <dd className="mt-1 text-slate-700">{formatTimeDistance(job.started_at)}</dd>
                  </div>
                  <div>
                    <dt className="text-xs uppercase tracking-wide text-slate-500">
                      Completed
                    </dt>
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
                            <h3 className="text-sm font-semibold text-slate-900">
                              {scene.label}
                            </h3>
                            <p className="text-xs text-slate-500">
                              {scene.duration_seconds} seconds
                            </p>
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

            {jobs.length > 0 && (
              <div className="rounded-3xl border border-slate-200 bg-white/95 p-6 shadow-sm shadow-slate-200/70 backdrop-blur">
                <h2 className="flex items-center gap-2 text-lg font-semibold text-slate-900">
                  <ArrowPathIcon className="h-5 w-5 text-indigo-500" />
                  Recent Runs
                </h2>
                <ul className="mt-4 space-y-3 text-sm">
                  {jobs.map((item) => {
                    const token = getStatusToken(item.status);
                    const isActive = item.job_id === activeJobId;
                    return (
                      <li key={item.job_id}>
                        <button
                          type="button"
                          onClick={() => setActiveJobId(item.job_id)}
                          className={`w-full rounded-2xl border px-4 py-3 text-left transition ${
                            isActive
                              ? "border-indigo-200 bg-indigo-50 shadow-sm"
                              : "border-slate-200 bg-white shadow-sm hover:border-indigo-200 hover:bg-indigo-50/70"
                          }`}
                        >
                          <div className="flex items-center justify-between">
                            <span className="font-mono text-xs text-slate-700">
                              {item.job_id.slice(0, 12)}
                            </span>
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
                      </li>
                    );
                  })}
                </ul>
              </div>
            )}
          </aside>
        </div>
      </div>
    </div>
  );
}
