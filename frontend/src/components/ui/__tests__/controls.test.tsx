import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { SegmentedControl, SettingRow } from "../controls";

const options = [
  { value: "4", label: "4s" },
  { value: "8", label: "8s" },
  { value: "12", label: "12s" },
] as const;

describe("SettingRow", () => {
  it("exposes its title as the switch's accessible name", () => {
    render(<SettingRow title="AI first frames" checked={false} onChange={() => {}} />);

    expect(screen.getByRole("switch", { name: "AI first frames" })).toHaveAttribute("aria-checked", "false");
  });

  it("requests the opposite state when clicked", () => {
    const onChange = vi.fn();
    render(<SettingRow title="AI first frames" checked onChange={onChange} />);

    fireEvent.click(screen.getByRole("switch"));

    expect(onChange).toHaveBeenCalledWith(false);
  });
});

describe("SegmentedControl", () => {
  it("marks only the selected option as checked", () => {
    render(<SegmentedControl ariaLabel="Duration" options={options} value="8" onChange={() => {}} />);

    expect(screen.getByRole("radio", { checked: true })).toHaveTextContent("8s");
  });

  it("selects the clicked option", () => {
    const onChange = vi.fn();
    render(<SegmentedControl ariaLabel="Duration" options={options} value="8" onChange={onChange} />);

    fireEvent.click(screen.getByRole("radio", { name: "12s" }));

    expect(onChange).toHaveBeenCalledWith("12");
  });

  it("wraps around with the arrow keys", () => {
    const onChange = vi.fn();
    render(<SegmentedControl ariaLabel="Duration" options={options} value="12" onChange={onChange} />);

    fireEvent.keyDown(screen.getByRole("radio", { name: "12s" }), { key: "ArrowRight" });

    expect(onChange).toHaveBeenCalledWith("4");
  });
});
