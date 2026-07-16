export function formatRuntimeMtime(mtimeNs: number | null): string {
  if (mtimeNs == null) return "missing";
  return new Date(mtimeNs / 1_000_000).toLocaleString();
}

export function formatDuration(seconds: number): string {
  if (!Number.isFinite(seconds) || seconds <= 0) return "0m";
  const total = Math.floor(seconds);
  const days = Math.floor(total / 86_400);
  const hours = Math.floor((total % 86_400) / 3_600);
  const minutes = Math.floor((total % 3_600) / 60);
  if (days > 0) return `${days}d ${hours}h`;
  if (hours > 0) return `${hours}h ${minutes}m`;
  return `${minutes}m`;
}

export function formatGapRange(start: string | null, end: string | null): string | null {
  if (!start || !end) return null;
  const fmt = (iso: string) =>
    new Date(iso).toLocaleString("en-US", {
      dateStyle: "medium",
      timeStyle: "short",
    });
  return `${fmt(start)} → ${fmt(end)}`;
}

export function formatMostActiveYear(bucket: string | null): string {
  if (!bucket) return "N/A";
  return new Date(bucket).toLocaleDateString("en-US", { year: "numeric" });
}

export function formatMostActiveMonth(bucket: string | null): string {
  if (!bucket) return "N/A";
  return new Date(bucket).toLocaleDateString("en-US", {
    month: "long",
    year: "numeric",
  });
}

export function formatMostActiveDay(bucket: string | null): string {
  if (!bucket) return "N/A";
  return new Date(bucket).toLocaleDateString("en-US", {
    month: "long",
    day: "numeric",
    year: "numeric",
  });
}

export function formatMostActiveHour(bucket: string | null): string {
  if (!bucket) return "N/A";
  return new Date(bucket).toLocaleString("en-US", {
    month: "long",
    day: "numeric",
    year: "numeric",
    hour: "2-digit",
    minute: "2-digit",
    hour12: true,
  });
}

const RELATIVE_TIME = new Intl.RelativeTimeFormat("en", { numeric: "auto" });
const RELATIVE_THRESHOLDS: Array<{
  unit: Intl.RelativeTimeFormatUnit;
  seconds: number;
}> = [
  { unit: "year", seconds: 60 * 60 * 24 * 365 },
  { unit: "month", seconds: 60 * 60 * 24 * 30 },
  { unit: "week", seconds: 60 * 60 * 24 * 7 },
  { unit: "day", seconds: 60 * 60 * 24 },
  { unit: "hour", seconds: 60 * 60 },
  { unit: "minute", seconds: 60 },
];

export function formatRelativeTime(ts: string | null): string {
  if (!ts) return "Unknown time";
  const target = new Date(ts).getTime();
  if (!Number.isFinite(target)) return "Unknown time";
  const diffSeconds = (target - Date.now()) / 1000;
  for (const { unit, seconds } of RELATIVE_THRESHOLDS) {
    if (Math.abs(diffSeconds) >= seconds) {
      return RELATIVE_TIME.format(Math.round(diffSeconds / seconds), unit);
    }
  }
  return RELATIVE_TIME.format(Math.round(diffSeconds), "second");
}

export function formatAbsoluteTime(ts: string | null): string {
  if (!ts) return "N/A";
  const parsed = new Date(ts);
  if (Number.isNaN(parsed.getTime())) return "N/A";
  return parsed.toLocaleString();
}

export function selectedCountLabel(count: number, total: number): string {
  return count === 0 ? `All ${total}` : `${count} selected`;
}
