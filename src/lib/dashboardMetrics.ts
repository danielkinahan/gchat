import type { PlatformOverTime } from "./api.ts";

export type HeatmapPoint = {
  weekday: number;
  hour: number;
  message_count: number;
};

export function monthlyTotals(platformData: PlatformOverTime): {
  points: Array<{ month: string; message_count: number }>;
} {
  return {
    points: platformData.points.map((point) => ({
      month: point.bucket,
      message_count: Object.values(point.counts).reduce(
        (total, count) => total + count,
        0,
      ),
    })),
  };
}

export function hourlyTotals(heatmapPoints: HeatmapPoint[]): {
  points: Array<{ hour: number; message_count: number }>;
} {
  const totals = new Map<number, number>();
  for (const point of heatmapPoints) {
    totals.set(
      point.hour,
      (totals.get(point.hour) ?? 0) + point.message_count,
    );
  }
  return {
    points: [...totals.entries()]
      .sort(([left], [right]) => left - right)
      .map(([hour, message_count]) => ({ hour, message_count })),
  };
}
