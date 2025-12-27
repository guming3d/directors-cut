import { STATUS_TOKENS } from "./constants";
import { translateValue } from "./translations";
import { Language } from "@/types/dashboard";

export const stageLabel = (stage: string | null, language: Language) => {
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
      return translateValue(
        language,
        normalized.charAt(0).toUpperCase() + normalized.slice(1),
      );
  }
};

export const formatTimestamp = (value: string) => {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) {
    return value;
  }
  return `${date.toLocaleDateString()} ${date.toLocaleTimeString()}`;
};

export const formatTimeDistance = (value: string | null) => {
  if (!value) return "—";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) {
    return "—";
  }
  return date.toLocaleString();
};

export const formatSeconds = (value: number | null | undefined) => {
  if (value == null || Number.isNaN(value)) return "—";
  const total = Math.max(0, Number(value));
  const minutes = Math.floor(total / 60);
  const seconds = total % 60;
  if (minutes > 0) {
    return `${minutes}m ${seconds.toFixed(1)}s`;
  }
  return `${seconds.toFixed(1)}s`;
};

export const formatSegmentTimestampDisplay = (seconds?: number | null, fallback?: string | null) => {
  if (typeof seconds === "number" && Number.isFinite(seconds)) {
    const totalMilliseconds = Math.round(seconds * 1000);
    const hours = Math.floor(totalMilliseconds / 3_600_000);
    const minutes = Math.floor((totalMilliseconds % 3_600_000) / 60_000);
    const secondsWhole = Math.floor((totalMilliseconds % 60_000) / 1000);
    const milliseconds = totalMilliseconds % 1000;
    let secondsText = secondsWhole.toString().padStart(2, "0");
    if (milliseconds) {
      const fractional = milliseconds.toString().padStart(3, "0").replace(/0+$/, "");
      if (fractional) {
        secondsText = `${secondsText}.${fractional}`;
      }
    }
    const minuteText = minutes.toString().padStart(2, "0");
    if (hours > 0) {
      const hourText = hours.toString().padStart(2, "0");
      return `${hourText}:${minuteText}:${secondsText}`;
    }
    return `${minuteText}:${secondsText}`;
  }
  return fallback ?? "—";
};

export const buildAssetUrl = (base: string, path: string | null | undefined) => {
  if (!path) return null;
  if (/^https?:\/\//i.test(path)) {
    return path;
  }

  const normalizedBase = (base || "").replace(/\/+$/, "");
  let normalizedPath = path.replace(/\\/g, "/");

  const outputIndex = normalizedPath.indexOf("/output/");
  if (outputIndex >= 0) {
    const relative = normalizedPath.slice(outputIndex + "/output".length);
    normalizedPath = `/static${relative.startsWith("/") ? relative : `/${relative}`}`;
  }

  if (!normalizedPath.startsWith("/")) {
    normalizedPath = `/${normalizedPath}`;
  }

  return `${normalizedBase}${normalizedPath}`;
};

export const getStatusToken = (status: string | undefined, language: Language) => {
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
