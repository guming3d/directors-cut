import { describe, expect, it } from "vitest";
import { VIDEO_PROVIDER_OPTIONS, getDurationOptions, isFalProvider } from "../constants";

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

describe("Foundry Fal models", () => {
  it("lists all four Fal deployments as providers", () => {
    expect(VIDEO_PROVIDER_OPTIONS).toEqual(
      expect.arrayContaining([
        "fal-kling-v3-pro",
        "fal-ltx-2.5-pro",
        "fal-minimax-h3-max",
        "fal-minimax-h3-max-turbo",
      ]),
    );
  });

  it("starts Kling V3 Pro at 4 seconds", () => {
    expect(getDurationOptions("fal-kling-v3-pro")[0]).toBe("4");
  });

  it("starts LTX 2.5 Pro at its 6-second minimum", () => {
    expect(getDurationOptions("fal-ltx-2.5-pro")).toEqual(["6", "8", "10"]);
  });

  it("does not treat inherited object keys as Fal providers", () => {
    expect(isFalProvider("constructor")).toBe(false);
  });
});
