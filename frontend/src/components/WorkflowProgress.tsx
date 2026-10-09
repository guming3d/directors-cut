import React from 'react';
import { CheckIcon, ExclamationTriangleIcon, XMarkIcon } from '@heroicons/react/20/solid';

interface SceneVersion {
  version_id: string;
  video_url: string | null;
}

interface SceneStatus {
  order: number;
  label: string;
  versions: SceneVersion[];
}

interface JobMessage {
  stage: string;
}

interface JobStatus {
  job_id: string;
  status: string;
  stage: string | null;
  progress: number;
  scenes: SceneStatus[];
  error: string | null;
  messages?: JobMessage[];
}

interface WorkflowProgressProps {
  job: JobStatus;
  language: 'en' | 'zh';
}

type Language = WorkflowProgressProps['language'];

const STAGES = [
  { id: 'queued', label: { en: 'Queued', zh: '排队中' } },
  { id: 'initializing', label: { en: 'Initializing', zh: '初始化' } },
  { id: 'planning', label: { en: 'Planning', zh: '规划场景' } },
  { id: 'rendering', label: { en: 'Rendering', zh: '渲染视频' } },
  { id: 'composing', label: { en: 'Composing', zh: '合成视频' } },
  { id: 'completed', label: { en: 'Completed', zh: '完成' } },
] as const;

const stageIndexOf = (stage: string | null | undefined) =>
  STAGES.findIndex((item) => item.id === (stage || '').toLowerCase());

/**
 * A failed job's stage is "failed", which is not a pipeline step, so the step
 * it stopped at is the last pipeline stage recorded in its messages.
 */
function resolveStageIndex(job: JobStatus, isFailed: boolean): number {
  const direct = stageIndexOf(job.stage);
  if (direct !== -1 && !isFailed) return direct;
  const messages = job.messages ?? [];
  for (let i = messages.length - 1; i >= 0; i -= 1) {
    const index = stageIndexOf(messages[i].stage);
    if (index !== -1 && STAGES[index].id !== 'completed') return index;
  }
  return Math.max(direct, 0);
}

function describeStage(
  stageId: string,
  language: Language,
  scenes: { done: number; total: number },
): string {
  const zh = language === 'zh';
  switch (stageId) {
    case 'queued':
      return zh ? '等待开始处理' : 'Waiting to start';
    case 'initializing':
      return zh ? '正在准备任务' : 'Preparing the job';
    case 'planning':
      return zh ? '正在拆分剧本并编写场景提示词' : 'Splitting the script and writing scene prompts';
    case 'rendering':
      if (!scenes.total) return zh ? '正在渲染场景' : 'Rendering scenes';
      return zh
        ? `已完成 ${scenes.done} / ${scenes.total} 个场景`
        : `${scenes.done} of ${scenes.total} scenes rendered`;
    case 'composing':
      return zh ? '正在拼接最终视频' : 'Stitching clips into the final video';
    default:
      return zh ? '最终视频已生成' : 'Final video is ready';
  }
}

const STATUS_BADGES = {
  running: {
    label: { en: 'Running', zh: '进行中' },
    className: 'bg-blue-50 text-blue-700 ring-blue-600/20 dark:bg-blue-500/10 dark:text-blue-300 dark:ring-blue-400/30',
    dot: 'bg-blue-600 motion-safe:animate-pulse dark:bg-blue-400',
  },
  completed: {
    label: { en: 'Completed', zh: '已完成' },
    className: 'bg-emerald-50 text-emerald-700 ring-emerald-600/20 dark:bg-emerald-500/10 dark:text-emerald-300 dark:ring-emerald-400/30',
    dot: 'bg-emerald-600 dark:bg-emerald-400',
  },
  failed: {
    label: { en: 'Failed', zh: '失败' },
    className: 'bg-rose-50 text-rose-700 ring-rose-600/20 dark:bg-rose-500/10 dark:text-rose-300 dark:ring-rose-400/30',
    dot: 'bg-rose-600 dark:bg-rose-400',
  },
} as const;

export default function WorkflowProgress({ job, language }: WorkflowProgressProps) {
  const isFailed = job.status === 'failed';
  const isCompleted = !isFailed && (job.status === 'completed' || job.stage === 'completed');
  const currentIndex = isCompleted ? STAGES.length - 1 : resolveStageIndex(job, isFailed);
  const currentStage = STAGES[currentIndex];

  const totalScenes = job.scenes.length;
  const completedScenes = job.scenes.filter((scene) => scene.versions.length > 0).length;
  const badge = STATUS_BADGES[isFailed ? 'failed' : isCompleted ? 'completed' : 'running'];

  const title = isFailed
    ? language === 'zh'
      ? `在「${currentStage.label.zh}」阶段失败`
      : `Failed while ${currentStage.label.en.toLowerCase()}`
    : currentStage.label[language];
  const detail = isFailed
    ? language === 'zh'
      ? '查看下方错误信息，修复后重新生成失败的场景。'
      : 'See the error below, then regenerate the failed scenes.'
    : describeStage(currentStage.id, language, { done: completedScenes, total: totalScenes });

  return (
    <section className="w-full rounded-xl border border-slate-200 bg-white p-5 dark:border-slate-800 dark:bg-slate-900">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0">
          <h3 className="text-base font-semibold text-slate-900 dark:text-slate-100">{title}</h3>
          <p className="mt-0.5 text-sm text-slate-500 dark:text-slate-400">{detail}</p>
        </div>
        <span
          className={`inline-flex items-center gap-1.5 rounded-full px-2.5 py-1 text-xs font-medium ring-1 ring-inset ${badge.className}`}
        >
          <span aria-hidden="true" className={`h-1.5 w-1.5 rounded-full ${badge.dot}`} />
          {badge.label[language]}
        </span>
      </div>

      <ol className="mt-5 grid grid-cols-6 gap-1.5" aria-label={language === 'zh' ? '工作流阶段' : 'Workflow stages'}>
        {STAGES.map((stage, index) => {
          const done = isCompleted || index < currentIndex;
          const current = !isCompleted && index === currentIndex;
          const failedHere = isFailed && current;

          let track = 'bg-slate-200 dark:bg-slate-800';
          if (done) track = 'bg-blue-600 dark:bg-blue-500';
          else if (failedHere) track = 'bg-rose-500';

          let labelClass = 'text-slate-400 dark:text-slate-500';
          if (done) labelClass = 'text-slate-600 dark:text-slate-300';
          if (current) labelClass = failedHere ? 'font-medium text-rose-600 dark:text-rose-400' : 'font-medium text-slate-900 dark:text-slate-100';

          return (
            <li key={stage.id} className="min-w-0" aria-current={current ? 'step' : undefined}>
              <span className="sr-only sm:hidden">{stage.label[language]}</span>
              <div className={`relative h-1.5 overflow-hidden rounded-full ${track}`}>
                {current && !isFailed ? (
                  <div className="absolute inset-y-0 left-0 w-1/2 rounded-full bg-blue-600 motion-safe:animate-pulse dark:bg-blue-500" />
                ) : null}
              </div>
              <div className={`mt-2 hidden items-center gap-1 text-xs sm:flex ${labelClass}`}>
                {done ? <CheckIcon aria-hidden="true" className="h-3.5 w-3.5 flex-none text-blue-600 dark:text-blue-400" /> : null}
                {failedHere ? <XMarkIcon aria-hidden="true" className="h-3.5 w-3.5 flex-none" /> : null}
                <span className="truncate">{stage.label[language]}</span>
              </div>
            </li>
          );
        })}
      </ol>

      {currentStage.id === 'rendering' && totalScenes > 0 && !isFailed ? (
        <ul className="mt-5 flex flex-wrap gap-2 border-t border-slate-200 pt-4 dark:border-slate-800">
          {job.scenes.map((scene) => {
            const rendered = scene.versions.length > 0;
            return (
              <li
                key={scene.order}
                title={scene.label}
                className="inline-flex items-center gap-1.5 rounded-md border border-slate-200 px-2 py-1 text-xs text-slate-600 dark:border-slate-700 dark:text-slate-300"
              >
                <span
                  aria-hidden="true"
                  className={`h-1.5 w-1.5 rounded-full ${
                    rendered ? 'bg-emerald-500' : 'bg-blue-500 motion-safe:animate-pulse'
                  }`}
                />
                {language === 'zh' ? `场景 ${scene.order}` : `Scene ${scene.order}`}
                <span className="sr-only">{rendered ? (language === 'zh' ? '已完成' : 'rendered') : language === 'zh' ? '渲染中' : 'rendering'}</span>
              </li>
            );
          })}
        </ul>
      ) : null}

      {job.error ? (
        <div className="mt-5 flex items-start gap-2.5 rounded-lg bg-rose-50 px-3.5 py-3 text-sm text-rose-700 dark:bg-rose-500/10 dark:text-rose-300">
          <ExclamationTriangleIcon aria-hidden="true" className="mt-0.5 h-4 w-4 flex-none" />
          <p className="min-w-0 break-words">{job.error}</p>
        </div>
      ) : null}
    </section>
  );
}
