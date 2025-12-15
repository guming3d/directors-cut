import { render, screen, within, fireEvent } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { WorkflowTable } from "../WorkflowTable";
import { JobStatus } from "@/types/dashboard";

const translate = (value: string) => value;

const makeJob = (overrides?: Partial<JobStatus>): JobStatus => ({
  job_id: "job_123456789012",
  status: "running",
  video_provider: "sora2",
  resolution: "720x1280",
  stage: "planning",
  progress: 0.42,
  started_at: "2024-01-01T00:00:00Z",
  completed_at: null,
  error: null,
  final_video_path: null,
  final_video_url: null,
  manifest: {},
  messages: [],
  scenes: [],
  generation_mode: "parallel",
  ...overrides,
});

describe("WorkflowTable", () => {
  it("renders workflow rows and triggers row click", () => {
    const job = makeJob();
    const onRowClick = vi.fn();
    const onSelect = vi.fn();

    render(
      <WorkflowTable
        jobs={[job]}
        language="en"
        translate={translate}
        selectedJobIds={new Set()}
        onSelect={onSelect}
        onRowClick={onRowClick}
      />,
    );

    fireEvent.click(screen.getByTestId(`workflow-row-${job.job_id}`));

    expect(onRowClick).toHaveBeenCalledWith(job);
    expect(screen.getByText("42%")).toBeInTheDocument();
    expect(screen.getByText("Planning scenes")).toBeInTheDocument();
  });

  it("stops row click when checkbox toggled", () => {
    const job = makeJob();
    const onRowClick = vi.fn();
    const onSelect = vi.fn();

    render(
      <WorkflowTable
        jobs={[job]}
        language="en"
        translate={translate}
        selectedJobIds={new Set()}
        onSelect={onSelect}
        onRowClick={onRowClick}
      />,
    );

    const row = screen.getByTestId(`workflow-row-${job.job_id}`);
    const checkbox = within(row).getByRole("checkbox");
    fireEvent.click(checkbox);

    expect(onSelect).toHaveBeenCalledWith(job.job_id);
    expect(onRowClick).not.toHaveBeenCalled();
  });
});
