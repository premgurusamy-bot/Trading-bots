import { normaliseServer } from "./lib/platform";
import { phoneNotice } from "./App";

test("PC address normalisation", () => {
  expect(normaliseServer("192.168.1.20")).toBe("http://192.168.1.20:8000");
  expect(normaliseServer("192.168.1.20:9000")).toBe("http://192.168.1.20:9000");
  expect(normaliseServer(" http://100.64.1.2:8000/ ")).toBe("http://100.64.1.2:8000");
  expect(normaliseServer("https://desk.tail1234.ts.net")).toBe("https://desk.tail1234.ts.net");
  expect(normaliseServer("")).toBeNull();
});

test("phone notifications only for important events", () => {
  expect(phoneNotice({ kind: "signal", state: "PENDING", index: "NIFTY", direction: "BULLISH", setup: "A", score: { total: 80 }, plan: { trigger: 24100 } })?.[0])
    .toBe("NIFTY CE signal PENDING");
  expect(phoneNotice({ kind: "signal", state: "EXPIRED" })).toBeNull();
  expect(phoneNotice({ kind: "evaluation" })).toBeNull();
  expect(phoneNotice({ kind: "confirm_required" })?.[0]).toContain("LIVE");
});
