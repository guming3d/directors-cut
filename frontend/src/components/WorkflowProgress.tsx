import React from 'react';
import {
  CheckCircleIcon,
  ClockIcon,
  PlayCircleIcon,
  FilmIcon,
  DocumentTextIcon,
  ArrowPathIcon,
  ExclamationTriangleIcon,
} from '@heroicons/react/24/outline';

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
  language: 'en' | 'zh';
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
  const normalizedStage = (job.stage || 'queued').toLowerCase();
  const stageIndex = STAGES.findIndex((stage) => stage.id === normalizedStage);
  const currentStageIndex = stageIndex === -1 ? 0 : stageIndex;
  const isFailed = job.status === 'failed';
  const totalStages = STAGES.length;
  const activeStage = STAGES[currentStageIndex];
  const progressPercent = Math.max(
    0,
    Math.min(100, ((currentStageIndex + (isFailed ? 0 : 0.4)) / (totalStages - 1)) * 100)
  );

  // Calculate scene progress
  const totalScenes = job.scenes.length;
  const completedScenes = job.scenes.filter(s => s.versions.length > 0).length;

  return (
    <div className="w-full space-y-6 rounded-xl border border-slate-200 bg-white/50 p-5 shadow-sm dark:border-slate-800 dark:bg-slate-900/50 backdrop-blur-sm">
      <div className="flex flex-wrap items-center justify-between gap-4">
        <div className="space-y-1">
          <p className="text-[10px] font-bold uppercase tracking-wider text-slate-500 dark:text-slate-400">
            {language === 'zh' ? '当前阶段' : 'Current stage'}
          </p>
          <div className="flex flex-wrap items-center gap-3">
            <span className="text-lg font-bold text-slate-900 dark:text-slate-100">
              {activeStage.label[language]}
            </span>
            <span
              className={`inline-flex items-center gap-2 rounded px-2 py-0.5 text-[10px] font-bold uppercase tracking-wide transition
                ${isFailed
                  ? 'bg-rose-100 text-rose-700 dark:bg-rose-900/30 dark:text-rose-400'
                  : 'bg-slate-100 text-slate-600 dark:bg-slate-800 dark:text-slate-300'}
              `}
            >
              {isFailed
                ? language === 'zh'
                  ? '失败'
                  : 'Failed'
                : `${currentStageIndex + 1}/${totalStages}`}
            </span>
          </div>
        </div>
        <div className="flex min-w-[240px] flex-1 items-center gap-3 sm:flex-none">
          <div className="relative h-1.5 flex-1 overflow-hidden rounded-full bg-slate-200 dark:bg-slate-800">
            <div
              className="h-full rounded-full bg-blue-600 transition-all duration-500"
              style={{ width: `${progressPercent}%` }}
            />
            {isFailed ? <div className="absolute inset-0 bg-rose-500/20" /> : null}
          </div>
          <span className="text-xs font-mono text-slate-500 dark:text-slate-400">
            {Math.round(progressPercent)}%
          </span>
        </div>
      </div>

      <div className="relative mt-5 overflow-x-auto pb-2">
        <div className="relative min-w-[680px]">
          <div className="absolute left-0 right-0 top-5 h-0.5 bg-slate-200 dark:bg-slate-800" />
          <div
            className="absolute left-0 top-5 h-0.5 bg-blue-600 transition-all duration-500"
            style={{ width: `${progressPercent}%` }}
          />
          <div className="relative grid grid-cols-6 gap-4">
            {STAGES.map((stage, index) => {
              const Icon = stage.icon;
              const isActive = index === currentStageIndex;
              const isCompleted = index < currentStageIndex;

              let circleClass =
                'relative flex h-10 w-10 items-center justify-center rounded-full border-2 text-zinc-600 transition-all duration-300 bg-slate-50 dark:bg-slate-900';
              if (isCompleted) {
                circleClass +=
                  ' border-blue-500 bg-blue-500 text-white';
              } else if (isActive) {
                circleClass += isFailed
                  ? ' border-rose-500 bg-rose-500 text-white'
                  : ' border-blue-500 bg-slate-200 text-blue-600 ring-4 ring-blue-500/10';
              } else {
                circleClass += ' border-slate-300 dark:border-slate-700 text-zinc-600 bg-slate-50 dark:bg-slate-900';
              }

              return (
                <div key={stage.id} className="flex flex-col items-center gap-2 text-center">
                  <div className={circleClass}>
                    <Icon className="h-5 w-5" />
                  </div>
                  <span className={`text-xs font-medium ${isActive ? 'text-slate-900 dark:text-slate-100' : 'text-slate-500 dark:text-slate-500'}`}>
                    {stage.label[language]}
                  </span>
                </div>
              );
            })}
          </div>
        </div>
      </div>

      {/* Detailed Status for Rendering Stage */}
      {job.stage === 'rendering' && (
        <div className="rounded-xl border border-slate-200/80 bg-white/80 p-4 shadow-inner shadow-slate-900/5 dark:border-slate-800 dark:bg-slate-900/50 dark:shadow-black/30">
          <div className="mb-3 flex flex-wrap items-center justify-between gap-2">
            <h4 className="text-sm font-semibold text-slate-700 dark:text-slate-200">
              {language === 'zh' ? '场景渲染进度' : 'Scene Rendering Progress'}
            </h4>
            <span className="text-xs font-medium text-slate-500 dark:text-slate-300">
              {completedScenes} / {totalScenes} {language === 'zh' ? '完成' : 'completed'}
            </span>
          </div>
          
          <div className="grid grid-cols-4 gap-2 sm:grid-cols-6 md:grid-cols-8 lg:grid-cols-12">
            {job.scenes.map((scene) => {
              const hasVersion = scene.versions.length > 0;
              return (
                <div 
                  key={scene.order}
                  className={`group relative flex aspect-square items-center justify-center rounded border text-xs font-medium shadow-sm transition-all
                    ${hasVersion 
                      ? 'border-emerald-200 bg-emerald-50 text-emerald-600 shadow-emerald-500/10 dark:border-emerald-900/50 dark:bg-emerald-900/20 dark:text-emerald-400' 
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
