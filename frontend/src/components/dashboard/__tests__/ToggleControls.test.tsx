import { render, screen, fireEvent } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { ThemeToggle } from "../ThemeToggle";
import { LanguageToggle } from "../LanguageToggle";

const translate = (value: string) => value;

describe("ThemeToggle", () => {
  it("calls onToggle when clicked", () => {
    const onToggle = vi.fn();
    render(<ThemeToggle theme="light" onToggle={onToggle} />);

    fireEvent.click(screen.getByRole("button", { name: /switch to dark mode/i }));

    expect(onToggle).toHaveBeenCalledTimes(1);
  });

  it("shows light mode label when theme is dark", () => {
    const onToggle = vi.fn();
    render(<ThemeToggle theme="dark" onToggle={onToggle} />);

    expect(screen.getByRole("button", { name: /switch to light mode/i })).toBeInTheDocument();
  });
});

describe("LanguageToggle", () => {
  it("highlights the active language and triggers change", () => {
    const onChange = vi.fn();
    render(<LanguageToggle language="en" translate={translate} onChange={onChange} />);

    const english = screen.getByRole("button", { name: "English" });
    const chinese = screen.getByRole("button", { name: "Chinese" });

    expect(english.className).toContain("text-slate-900");
    expect(chinese.className).not.toContain("text-slate-900");

    fireEvent.click(chinese);
    expect(onChange).toHaveBeenCalledWith("zh");
  });
});
