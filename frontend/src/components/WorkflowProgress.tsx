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

const STATUS_TEXTS = {
  done: { en: 'Done', zh: '已完成' },
  active: { en: 'In progress', zh: '进行中' },
  next: { en: 'Up next', zh: '待处理' },
  failed: { en: 'Issue', zh: '异常' },
};

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
    <div className="w-full space-y-6 rounded-2xl border border-slate-200/80 bg-gradient-to-br from-white via-slate-50/80 to-slate-100/60 p-5 shadow-lg shadow-slate-900/5 dark:border-slate-700/60 dark:from-slate-900/40 dark:via-slate-900/30 dark:to-slate-950/40 dark:shadow-black/30">
      <div className="flex flex-wrap items-center justify-between gap-4">
        <div className="space-y-1">
          <p className="text-[11px] uppercase tracking-[0.14em] text-slate-500 dark:text-slate-400">
            {language === 'zh' ? '当前阶段' : 'Current stage'}
          </p>
          <div className="flex flex-wrap items-center gap-3">
            <span className="text-lg font-semibold text-slate-900 dark:text-slate-50">
              {activeStage.label[language]}
            </span>
            <span
              className={`inline-flex items-center gap-2 rounded-full border px-3 py-1 text-[11px] font-semibold uppercase tracking-wide transition
                ${isFailed
                  ? 'border-rose-300/60 bg-rose-50 text-rose-600 dark:border-rose-800/60 dark:bg-rose-900/30 dark:text-rose-200'
                  : 'border-slate-200/80 bg-white/80 text-slate-600 shadow-sm dark:border-slate-700/70 dark:bg-slate-900/60 dark:text-slate-200'}
              `}
            >
              {isFailed
                ? language === 'zh'
                  ? '失败'
                  : 'Failed'
                : `${currentStageIndex + 1}/${totalStages} ${language === 'zh' ? '阶段' : 'stages'}`}
            </span>
          </div>
        </div>
        <div className="flex min-w-[240px] flex-1 items-center gap-3 sm:flex-none">
          <div className="relative h-2 flex-1 overflow-hidden rounded-full bg-slate-200/80 shadow-inner dark:bg-slate-800">
            <div
              className="h-full rounded-full bg-gradient-to-r from-emerald-400 via-blue-500 to-blue-600 transition-all duration-500"
              style={{ width: `${progressPercent}%` }}
            />
            {isFailed ? <div className="absolute inset-0 rounded-full border border-rose-400/40" /> : null}
          </div>
          <span className="text-xs font-medium text-slate-600 dark:text-slate-300">
            {language === 'zh' ? `${Math.round(progressPercent)}% 完成` : `${Math.round(progressPercent)}% complete`}
          </span>
        </div>
      </div>

      <div className="relative mt-5 overflow-x-auto pb-2">
        <div className="relative min-w-[680px]">
          <div className="absolute left-0 right-0 top-6 h-1 rounded-full bg-slate-200/80 dark:bg-slate-800/70" />
          <div
            className="absolute left-0 top-6 h-1 rounded-full bg-gradient-to-r from-emerald-400 via-blue-500 to-blue-600 transition-all duration-500"
            style={{ width: `${progressPercent}%` }}
          />
          <div className="relative grid grid-cols-6 gap-4">
            {STAGES.map((stage, index) => {
              const Icon = stage.icon;
              const isActive = index === currentStageIndex;
              const isCompleted = index < currentStageIndex;
              const statusKey = isFailed && isActive ? 'failed' : isActive ? 'active' : isCompleted ? 'done' : 'next';

              let circleClass =
                'relative flex h-12 w-12 items-center justify-center rounded-full border-2 text-slate-500 shadow-sm transition-all duration-300';
              if (isCompleted) {
                circleClass +=
                  ' border-emerald-500 bg-gradient-to-br from-emerald-500 to-emerald-600 text-white shadow-emerald-500/30';
              } else if (isActive) {
                circleClass += isFailed
                  ? ' border-rose-500 bg-gradient-to-br from-rose-500 to-rose-600 text-white shadow-rose-500/30'
                  : ' border-blue-500 bg-gradient-to-br from-blue-500 to-blue-600 text-white shadow-blue-500/30 ring-4 ring-blue-200/70 dark:ring-blue-900/40';
              } else {
                circleClass += ' border-slate-200/80 bg-white/90 text-slate-400 dark:border-slate-800 dark:bg-slate-900';
              }

              return (
                <div key={stage.id} className="flex flex-col items-center gap-2 text-center">
                  <div className={circleClass}>
                    <Icon className="h-5 w-5" />
                  </div>
                  <span className={`text-xs font-semibold ${isActive ? 'text-slate-900 dark:text-slate-50' : 'text-slate-600 dark:text-slate-300'}`}>
                    {stage.label[language]}
                  </span>
                  <span
                    className={`text-[11px] font-semibold uppercase tracking-wide ${
                      statusKey === 'done'
                        ? 'text-emerald-600'
                        : statusKey === 'active'
                          ? 'text-blue-600'
                          : statusKey === 'failed'
                            ? 'text-rose-500'
                            : 'text-slate-400'
                    }`}
                  >
                    {STATUS_TEXTS[statusKey as keyof typeof STATUS_TEXTS][language]}
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
