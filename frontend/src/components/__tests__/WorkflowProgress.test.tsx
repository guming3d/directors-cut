import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import WorkflowProgress from "../WorkflowProgress";

const scenes = [
  { order: 1, label: "Scene 1", versions: [{ version_id: "a", video_url: null }] },
  { order: 2, label: "Scene 2", versions: [] },
];

const baseJob = { job_id: "job", progress: 0, scenes, error: null };

describe("WorkflowProgress", () => {
  it("marks the stage a failed job stopped at, not the first stage", () => {
    render(
      <WorkflowProgress
        language="en"
        job={{
          ...baseJob,
          status: "failed",
          stage: "failed",
          error: "Kling task timed out",
          messages: [{ stage: "planning" }, { stage: "rendering" }, { stage: "failed" }],
        }}
      />,
    );

    expect(screen.getByRole("heading")).toHaveTextContent("Failed while rendering");
  });

  it("flags the failing stage as the current step", () => {
    render(
      <WorkflowProgress
        language="en"
        job={{ ...baseJob, status: "failed", stage: "failed", messages: [{ stage: "rendering" }, { stage: "failed" }] }}
      />,
    );

    expect(screen.getByRole("listitem", { current: "step" })).toHaveTextContent("Rendering");
  });

  it("reports rendered scene counts while rendering", () => {
    render(<WorkflowProgress language="en" job={{ ...baseJob, status: "running", stage: "rendering" }} />);

    expect(screen.getByText("1 of 2 scenes rendered")).toBeInTheDocument();
  });

  it("shows no current step once the job completes", () => {
    render(<WorkflowProgress language="en" job={{ ...baseJob, status: "completed", stage: "completed" }} />);

    expect(screen.queryByRole("listitem", { current: "step" })).toBeNull();
  });

  it("shows the job error", () => {
    render(
      <WorkflowProgress
        language="zh"
        job={{ ...baseJob, status: "failed", stage: "failed", error: "boom", messages: [{ stage: "composing" }] }}
      />,
    );

    expect(screen.getByText("boom")).toBeInTheDocument();
  });
});
