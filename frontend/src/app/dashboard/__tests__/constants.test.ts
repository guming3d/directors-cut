import { describe, expect, it } from "vitest";
import { VIDEO_PROVIDER_OPTIONS, getDurationOptions } from "../constants";

describe("getDurationOptions", () => {
  it("offers the cheap 4-second option for Seedance", () => {
    expect(getDurationOptions("seedance")).toEqual(["4", "5", "8", "10", "12"]);
  });

  it("keeps Kling and Jimeng on 5 or 10 seconds", () => {
    expect(getDurationOptions("jimeng")).toEqual(["5", "10"]);
  });

  it("falls back to Sora durations for unknown providers", () => {
    expect(getDurationOptions(null)).toEqual(["4", "8", "12"]);
  });
});

describe("VIDEO_PROVIDER_OPTIONS", () => {
  it("includes Seedance", () => {
    expect(VIDEO_PROVIDER_OPTIONS).toContain("seedance");
  });
});
