"use client";

import { ReactNode, useState } from "react";

const DEFAULT_RATIO = 9 / 16;

/** Width / height for a WIDTHxHEIGHT resolution string such as "720x1280". */
export function aspectRatioOf(resolution?: string | null): number {
  const match = /^(\d+)\s*x\s*(\d+)$/i.exec((resolution ?? "").trim());
  if (!match) return DEFAULT_RATIO;
  const width = Number(match[1]);
  const height = Number(match[2]);
  return width > 0 && height > 0 ? width / height : DEFAULT_RATIO;
}

/**
 * Tracks a media element's real aspect ratio, starting from the job's
 * requested resolution until the file reports its own dimensions.
 */
function useMediaRatio(initialRatio: number) {
  const [ratio, setRatio] = useState(initialRatio);
  const update = (width: number, height: number) => {
    if (width > 0 && height > 0) setRatio(width / height);
  };
  return [ratio, update] as const;
}

type Fit = "height" | "width";

interface MediaFrameProps {
  ratio: number;
  /** "height": fixed height, width follows the ratio. "width": fills the width. */
  fit: Fit;
  sizeClass: string;
  className?: string;
  children: ReactNode;
}

function MediaFrame({ ratio, fit, sizeClass, className = "", children }: MediaFrameProps) {
  return (
    <div
      style={{ aspectRatio: String(ratio) }}
      className={`relative max-w-full overflow-hidden rounded-lg bg-slate-100 dark:bg-slate-800 ${
        fit === "height" ? "w-auto" : "w-full"
      } ${sizeClass} ${className}`}
    >
      {children}
    </div>
  );
}

interface VideoFrameProps {
  src: string;
  initialRatio: number;
  fit: Fit;
  sizeClass: string;
  className?: string;
  fallbackText: string;
}

export function VideoFrame({ src, initialRatio, fit, sizeClass, className, fallbackText }: VideoFrameProps) {
  const [ratio, updateRatio] = useMediaRatio(initialRatio);
  return (
    <MediaFrame ratio={ratio} fit={fit} sizeClass={sizeClass} className={`bg-black dark:bg-black ${className ?? ""}`}>
      <video
        controls
        preload="metadata"
        src={src}
        onLoadedMetadata={(event) => updateRatio(event.currentTarget.videoWidth, event.currentTarget.videoHeight)}
        className="absolute inset-0 h-full w-full object-contain"
      >
        {fallbackText}
      </video>
    </MediaFrame>
  );
}

interface ImageFrameProps {
  src: string;
  alt: string;
  initialRatio: number;
  fit: Fit;
  sizeClass: string;
  className?: string;
}

export function ImageFrame({ src, alt, initialRatio, fit, sizeClass, className }: ImageFrameProps) {
  const [ratio, updateRatio] = useMediaRatio(initialRatio);
  return (
    <MediaFrame ratio={ratio} fit={fit} sizeClass={sizeClass} className={className}>
      {/* eslint-disable-next-line @next/next/no-img-element -- local static assets with unknown dimensions */}
      <img
        src={src}
        alt={alt}
        loading="lazy"
        onLoad={(event) => updateRatio(event.currentTarget.naturalWidth, event.currentTarget.naturalHeight)}
        className="absolute inset-0 h-full w-full object-contain"
      />
    </MediaFrame>
  );
}

export interface MaterialItem {
  key: string;
  label: string;
  src: string;
  alt: string;
}

interface MaterialGroupProps {
  title: string;
  items: MaterialItem[];
  initialRatio: number;
  sizeClass: string;
}

/** One labelled group of source images; each opens full size in a new tab. */
export function MaterialGroup({ title, items, initialRatio, sizeClass }: MaterialGroupProps) {
  return (
    <figure className="min-w-0">
      <figcaption className="mb-2 text-xs font-medium text-slate-600 dark:text-slate-300">{title}</figcaption>
      <div className="flex flex-wrap gap-2">
        {items.map((item) => (
          <a
            key={item.key}
            href={item.src}
            target="_blank"
            rel="noreferrer"
            title={item.label}
            className="group block rounded-lg focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-blue-600"
          >
            <ImageFrame
              src={item.src}
              alt={item.alt}
              initialRatio={initialRatio}
              fit="height"
              sizeClass={sizeClass}
              className="ring-1 ring-slate-200 transition group-hover:ring-slate-400 dark:ring-slate-700"
            />
          </a>
        ))}
      </div>
    </figure>
  );
}
