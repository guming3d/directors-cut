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
  it("marks only the active language as pressed", () => {
    render(<LanguageToggle language="en" translate={translate} onChange={vi.fn()} />);

    expect(screen.getByRole("button", { pressed: true })).toHaveTextContent("English");
  });

  it("names each language in its own script", () => {
    render(<LanguageToggle language="en" translate={translate} onChange={vi.fn()} />);

    expect(screen.getByRole("button", { name: "中文" })).toBeInTheDocument();
  });

  it("switches language when another option is clicked", () => {
    const onChange = vi.fn();
    render(<LanguageToggle language="en" translate={translate} onChange={onChange} />);

    fireEvent.click(screen.getByRole("button", { name: "中文" }));

    expect(onChange).toHaveBeenCalledWith("zh");
  });

  it("labels the group for assistive technology", () => {
    render(<LanguageToggle language="en" translate={translate} onChange={vi.fn()} />);

    expect(screen.getByRole("group", { name: "Language" })).toBeInTheDocument();
  });
});
