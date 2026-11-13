import { render, screen } from "@testing-library/react";
import { ScoreBreakdown, Mandatory } from "./components/ui";
import { inr, pct, age } from "./lib/format";

const score = {
  total: 80, band: "VALID",
  components: [
    { key: "ema_alignment", name: "EMA 9/20 trend alignment", max: 10, points: 10, reasons: ["15m ok (+5)", "5m ok (+5)"], data_ok: true },
    { key: "pcr", name: "PCR", max: 5, points: 0, reasons: ["PCR change: data unavailable"], data_ok: false },
  ],
};

test("score breakdown shows total, band and per-component reasons", () => {
  render(<ScoreBreakdown score={score} />);
  expect(screen.getByText("80")).toBeTruthy();
  expect(screen.getByText("VALID")).toBeTruthy();
  expect(screen.getByText("10/10")).toBeTruthy();
  expect(screen.getByText(/data unavailable/)).toBeTruthy();
});

test("mandatory badges distinguish pass, fail and not evaluated", () => {
  render(<Mandatory m={{ data_quality: true, min_score: false, option_quality: "NOT_EVALUATED" }} />);
  expect(screen.getByText(/✓ data quality/)).toBeTruthy();
  expect(screen.getByText(/✗ min score/)).toBeTruthy();
  expect(screen.getByText(/– option quality/)).toBeTruthy();
});

test("formatters never invent numbers", () => {
  expect(inr(null)).toBe("—");
  expect(pct(undefined)).toBe("—");
  expect(age(null)).toBe("—");
  expect(inr(1234.5, 2)).toContain("1,234.50");
});
