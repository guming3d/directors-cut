import React from 'react';
import {
  CheckCircleIcon,
  ClockIcon,
  PlayCircleIcon,
  FilmIcon,
  DocumentTextIcon,
  ArrowPathIcon,
  ExclamationTriangleIcon,
} from "@heroicons/react/24/outline";

interface SceneVersion {
  version_id: string;
  video_url: string | null;
}

interface SceneStatus {
  order: number;
  label: string;
  versions: SceneVersion[];
}

interface JobStatus {
  job_id: string;
  status: string;
  stage: string | null;
  progress: number;
  scenes: SceneStatus[];
  error: string | null;
}

interface WorkflowProgressProps {
  job: JobStatus;
  language: "en" | "zh";
}

const STAGES = [
  { id: 'queued', icon: ClockIcon, label: { en: 'Queued', zh: '排队中' } },
  { id: 'initializing', icon: ArrowPathIcon, label: { en: 'Initializing', zh: '初始化' } },
  { id: 'planning', icon: DocumentTextIcon, label: { en: 'Planning', zh: '规划场景' } },
  { id: 'rendering', icon: FilmIcon, label: { en: 'Rendering', zh: '渲染视频' } },
  { id: 'composing', icon: PlayCircleIcon, label: { en: 'Composing', zh: '合成视频' } },
  { id: 'completed', icon: CheckCircleIcon, label: { en: 'Completed', zh: '完成' } },
];

export default function WorkflowProgress({ job, language }: WorkflowProgressProps) {
  const currentStageIndex = STAGES.findIndex(s => s.id === (job.stage || 'queued').toLowerCase());
  const isFailed = job.status === 'failed';

  // Calculate scene progress
  const totalScenes = job.scenes.length;
  const completedScenes = job.scenes.filter(s => s.versions.length > 0).length;

  return (
    <div className="w-full space-y-6">
      {/* Main Progress Bar / Stepper */}
      <div className="relative">
        <div className="absolute left-0 top-1/2 h-0.5 w-full -translate-y-1/2 bg-slate-200 dark:bg-slate-800" />
        <div 
          className="absolute left-0 top-1/2 h-0.5 -translate-y-1/2 bg-emerald-500 transition-all duration-500"
          style={{ width: `${Math.max(0, Math.min(100, (currentStageIndex / (STAGES.length - 1)) * 100))}%` }}
        />
        
        <div className="relative flex justify-between">
          {STAGES.map((stage, index) => {
            const Icon = stage.icon;
            const isActive = index === currentStageIndex;
            const isCompleted = index < currentStageIndex;

            let statusColor = "bg-white dark:bg-slate-900 border-slate-300 dark:border-slate-700 text-slate-400";
            if (isCompleted) {
              statusColor = "bg-emerald-500 border-emerald-500 text-white";
            } else if (isActive) {
              statusColor = isFailed 
                ? "bg-rose-500 border-rose-500 text-white"
                : "bg-blue-500 border-blue-500 text-white ring-4 ring-blue-100 dark:ring-blue-900/30";
            }

            return (
              <div key={stage.id} className="flex flex-col items-center gap-2">
                <div className={`flex h-8 w-8 items-center justify-center rounded-full border-2 transition-all ${statusColor} z-10`}>
                  <Icon className="h-4 w-4" />
                </div>
                <span className={`text-xs font-medium ${isActive ? 'text-slate-900 dark:text-slate-100' : 'text-slate-500'}`}>
                  {stage.label[language]}
                </span>
              </div>
            );
          })}
        </div>
      </div>

      {/* Detailed Status for Rendering Stage */}
      {job.stage === 'rendering' && (
        <div className="rounded-lg border border-slate-200 dark:border-slate-800 bg-slate-50 dark:bg-slate-900/50 p-4">
          <div className="mb-3 flex items-center justify-between">
            <h4 className="text-sm font-semibold text-slate-700 dark:text-slate-300">
              {language === 'zh' ? '场景渲染进度' : 'Scene Rendering Progress'}
            </h4>
            <span className="text-xs text-slate-500">
              {completedScenes} / {totalScenes} {language === 'zh' ? '完成' : 'completed'}
            </span>
          </div>
          
          <div className="grid grid-cols-4 gap-2 sm:grid-cols-6 md:grid-cols-8 lg:grid-cols-12">
            {job.scenes.map((scene) => {
              const hasVersion = scene.versions.length > 0;
              return (
                <div 
                  key={scene.order}
                  className={`group relative flex aspect-square items-center justify-center rounded border text-xs font-medium transition-all
                    ${hasVersion 
                      ? 'border-emerald-200 bg-emerald-50 text-emerald-600 dark:border-emerald-900/50 dark:bg-emerald-900/20 dark:text-emerald-400' 
                      : 'border-slate-200 bg-white text-slate-400 dark:border-slate-700 dark:bg-slate-800'
                    }`}
                  title={scene.label}
                >
                  {scene.order}
                  {hasVersion && (
                    <div className="absolute -right-1 -top-1 h-2 w-2 rounded-full bg-emerald-500" />
                  )}
                </div>
              );
            })}
          </div>
        </div>
      )}

      {/* Error Message */}
      {job.error && (
        <div className="flex items-center gap-3 rounded-md border border-rose-200 bg-rose-50 p-4 text-sm text-rose-600 dark:border-rose-900/50 dark:bg-rose-900/20 dark:text-rose-400">
          <ExclamationTriangleIcon className="h-5 w-5 flex-shrink-0" />
          <p>{job.error}</p>
        </div>
      )}
    </div>
  );
}
