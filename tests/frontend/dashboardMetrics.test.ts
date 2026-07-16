import { test } from "node:test";

import { hourlyTotals, monthlyTotals } from "../../src/lib/dashboardMetrics";
import { formatDuration, formatGapRange } from "../../src/lib/formatters";
import {
  attachmentKind,
  attachmentLabel,
  toDisplayAttachmentUrl,
} from "../../src/lib/mediaUrls";

function assertEquals(actual: unknown, expected: unknown): void {
  const actualJson = JSON.stringify(actual);
  const expectedJson = JSON.stringify(expected);
  if (actualJson !== expectedJson) {
    throw new Error(`Expected ${expectedJson}, received ${actualJson}`);
  }
}

test("monthlyTotals combines platform counts", () => {
  assertEquals(
    monthlyTotals({
      granularity: "month",
      platforms: ["discord", "signal"],
      points: [
        {
          bucket: "2026-01-01T00:00:00",
          counts: { discord: 2, signal: 3 },
        },
      ],
    }),
    {
      points: [{ month: "2026-01-01T00:00:00", message_count: 5 }],
    },
  );
});

test("hourlyTotals combines weekdays and sorts hours", () => {
  assertEquals(
    hourlyTotals([
      { weekday: 1, hour: 12, message_count: 4 },
      { weekday: 2, hour: 8, message_count: 2 },
      { weekday: 3, hour: 12, message_count: 3 },
    ]),
    {
      points: [
        { hour: 8, message_count: 2 },
        { hour: 12, message_count: 7 },
      ],
    },
  );
});

test("duration and gap formatters handle dashboard edge cases", () => {
  assertEquals(formatDuration(0), "0m");
  assertEquals(formatDuration(90_061), "1d 1h");
  assertEquals(formatGapRange(null, "2026-01-02T00:00:00Z"), null);
  const range = formatGapRange(
    "2026-01-01T00:00:00Z",
    "2026-01-02T00:00:00Z",
  );
  if (!range?.includes("→")) {
    throw new Error(`Expected formatted range, received ${range}`);
  }
});

test("media helpers classify placeholders and reject local previews", () => {
  assertEquals(attachmentKind("/api/media-removed"), "image");
  assertEquals(attachmentKind("https://example.com/clip.mp4?x=1"), "video");
  assertEquals(attachmentKind("notes.txt"), "label");
  assertEquals(
    toDisplayAttachmentUrl(null, "http://127.0.0.1/private.jpg"),
    null,
  );
  assertEquals(
    toDisplayAttachmentUrl(null, "https://example.com/photo.jpg"),
    "https://example.com/photo.jpg",
  );
  assertEquals(
    attachmentLabel("https://example.com/files/my%20photo.jpg"),
    "my photo.jpg",
  );
});
