import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";

import type { DailyRecordedPoint } from "../../api/client";
import { PortfolioChart } from "./PortfolioChart";

afterEach(cleanup);

function point(date: string, value = "10000.123456789", complete = true): DailyRecordedPoint {
  return { date, value, currency: "USD", accounts_count: complete ? 2 : 1, accounts_total: 2, is_complete: complete };
}

describe("PortfolioChart", () => {
  it("filters recorded dates and lets the user inspect exact decimal values", () => {
    render(<PortfolioChart history={[point("2025-12-31"), point("2026-01-01", "11000.00"), point("2026-02-01", "12000.00")]} />);
    const slider = screen.getByLabelText("Inspect recorded snapshot");
    fireEvent.change(slider, { target: { value: "0" } });
    expect(slider.getAttribute("aria-valuetext")).toContain("10,000.123456789 USD");
    fireEvent.click(screen.getByRole("button", { name: "YTD" }));
    expect(slider.getAttribute("max")).toBe("1");
    fireEvent.change(slider, { target: { value: "0" } });
    expect(slider.getAttribute("aria-valuetext")).toContain("2026-01-01");
    fireEvent.click(screen.getByRole("button", { name: "1W" }));
    expect(slider.getAttribute("max")).toBe("0");
    expect(slider.getAttribute("aria-valuetext")).toContain("2026-02-01");
    expect(screen.getByText(/One recorded day/)).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "All" }));
    expect(slider.getAttribute("max")).toBe("2");
  });

  it("preserves time gaps and isolates incomplete account coverage", () => {
    const { container } = render(<PortfolioChart history={[
      point("2026-01-01"), point("2026-01-02"), point("2026-01-04"), point("2026-01-05", "6000.00", false), point("2026-01-06"),
    ]} />);
    expect(container.querySelectorAll("[data-chart-segment]")).toHaveLength(1);
    expect(screen.getByText(/Breaks in the line/)).toBeTruthy();
    expect(screen.getByText(/Hollow points/)).toBeTruthy();
    const slider = screen.getByLabelText("Inspect recorded snapshot");
    fireEvent.change(slider, { target: { value: "3" } });
    expect(slider.getAttribute("aria-valuetext")).toContain("1 of 2 accounts, incomplete");
  });

  it.each(["0.00", "-12.00"])("draws a visible single or flat value at %s", (value) => {
    const { container, rerender } = render(<PortfolioChart history={[point("2026-01-01", value)]} />);
    expect(container.querySelector("circle")?.getAttribute("cy")).toBe("135");
    rerender(<PortfolioChart history={[point("2026-01-01", value), point("2026-01-02", value)]} />);
    expect(container.querySelector("path")?.getAttribute("d")).not.toContain("NaN");
  });

  it("distinguishes loading, no history, failed history, and retained history", () => {
    const { rerender } = render(<PortfolioChart history={[]} loading />);
    expect(screen.getByRole("status").textContent).toContain("Loading portfolio history");
    rerender(<PortfolioChart history={[]} />);
    expect(screen.getByText(/No daily snapshots/)).toBeTruthy();
    rerender(<PortfolioChart history={[]} error />);
    expect(screen.getByRole("alert").textContent).toContain("history is unavailable");
    expect(screen.queryByText(/No daily snapshots/)).toBeNull();
    rerender(<PortfolioChart history={[point("2026-01-01")]} error />);
    expect(screen.getByRole("img", { name: /Portfolio value chart/ })).toBeTruthy();
    expect(screen.getByRole("alert")).toBeTruthy();
  });
});
