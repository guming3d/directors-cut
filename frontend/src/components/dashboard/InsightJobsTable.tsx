import { formatTimeDistance, getStatusToken } from "@/app/dashboard/utils";
import { Language, VideoInsightJob } from "@/types/dashboard";

type InsightJobsTableProps = {
  jobs: VideoInsightJob[];
  language: Language;
  translate: (value: string) => string;
  selectedJobIds: Set<string>;
  onSelect: (jobId: string) => void;
  onRowClick: (job: VideoInsightJob) => void;
};

export function InsightJobsTable({
  jobs,
  language,
  translate,
  selectedJobIds,
  onSelect,
  onRowClick,
}: InsightJobsTableProps) {
  if (!jobs.length) {
    return (
      <p className="text-sm text-slate-500 p-4">
        {translate("Upload a video to see analysis jobs here.")}
      </p>
    );
  }

  return (
    <div className="overflow-x-auto">
      <table className="min-w-full divide-y divide-slate-200 dark:divide-slate-800 text-sm">
        <thead className="bg-zinc-100 dark:bg-zinc-900 border-b border-zinc-200 dark:border-zinc-800 text-[10px] font-bold uppercase tracking-wider text-slate-500 dark:text-slate-400">
          <tr>
            <th scope="col" className="px-6 py-3 text-left">
              <span className="sr-only">{translate("Select workflows")}</span>
            </th>
            <th scope="col" className="px-6 py-3 text-left">
              {translate("Job ID")}
            </th>
            <th scope="col" className="px-6 py-3 text-left">
              {translate("Status")}
            </th>
            <th scope="col" className="px-6 py-3 text-left">
              {translate("Uploaded video")}
            </th>
            <th scope="col" className="px-6 py-3 text-left">
              {translate("Progress")}
            </th>
            <th scope="col" className="px-6 py-3 text-left">
              {translate("Started")}
            </th>
          </tr>
        </thead>
        <tbody className="divide-y divide-slate-200 dark:divide-slate-800 bg-white dark:bg-slate-900">
          {jobs.map((item) => {
            const token = getStatusToken(item.status, language);
            const isSelected = selectedJobIds.has(item.job_id);
            const progressPercent = Math.min(100, Math.max(0, Math.round((item.progress ?? 0) * 100)));
            return (
              <tr
                key={item.job_id}
                data-testid={`insight-row-${item.job_id}`}
                onClick={() => onRowClick(item)}
                className="cursor-pointer transition hover:bg-slate-50 dark:hover:bg-zinc-800/50 border-b border-slate-100 dark:border-zinc-800/50"
              >
                <td className="px-6 py-4">
                  <input
                    type="checkbox"
                    checked={isSelected}
                    onChange={() => onSelect(item.job_id)}
                    onClick={(event) => event.stopPropagation()}
                    className="h-4 w-4 rounded border-slate-400 dark:border-slate-600 bg-slate-200 dark:bg-slate-700 text-indigo-500 focus:ring-indigo-500"
                  />
                </td>
                <td className="whitespace-nowrap px-6 py-4 font-mono text-xs text-slate-500 dark:text-slate-400">
                  {item.job_id.slice(0, 12)}
                </td>
                <td className="px-6 py-4">
                  <div className="flex flex-col gap-1">
                    <span className={`text-xs font-semibold uppercase tracking-wide ${token.className}`}>
                      {token.label}
                    </span>
                  </div>
                </td>
                <td className="px-6 py-4 text-xs text-slate-500 dark:text-slate-400">
                  {item.filename}
                </td>
                <td className="px-6 py-4">
                  <div className="flex items-center gap-2">
                    <div className="h-2 w-24 rounded-full bg-slate-100 dark:bg-slate-800">
                      <div
                        className="h-full rounded-full bg-slate-400 transition-all"
                        style={{ width: `${progressPercent}%` }}
                      />
                    </div>
                    <span className="text-xs font-medium text-slate-500 dark:text-slate-400">
                      {progressPercent}%
                    </span>
                  </div>
                </td>
                <td className="whitespace-nowrap px-6 py-4 text-xs text-slate-500">
                  {formatTimeDistance(item.started_at)}
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}
