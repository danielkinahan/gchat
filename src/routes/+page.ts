import type {
  Overview,
  PlatformOverTime,
} from "../lib/api";
import { fetchJson, filterQuery } from "../lib/api";
import {
  hourlyTotals,
  monthlyTotals,
  type HeatmapPoint,
} from "../lib/dashboardMetrics";

export const load = async ({ url, fetch }) => {
  const query = filterQuery(url.searchParams);
  const querySuffix = query ? `&${query.slice(1)}` : "";

  const [
    overview,
    activityHeatmap,
    topChats,
    topThemes,
    metadata,
    platformOverTime,
  ] = await Promise.all([
    fetchJson<Overview>(`/api/overview${query}`, fetch),
    fetchJson<{ points: HeatmapPoint[] }>(
      `/api/activity-heatmap${query}`,
      fetch,
    ),
    fetchJson<{
      items: Array<{
        id: number;
        name: string;
        theme_name: string;
        message_count: number;
      }>;
    }>(`/api/top-chats?limit=10${querySuffix}`, fetch),
    fetchJson<{
      items: Array<{ id: number; name: string; message_count: number }>;
    }>(`/api/top-themes?limit=10${querySuffix}`, fetch),
    fetchJson<{
      people: Array<
        { id: number; name: string; color: string; avatar: string }
      >;
      themes: Array<{ id: number; name: string; emoji: string }>;
      platforms: string[];
    }>(`/api/metadata`, fetch),
    fetchJson<PlatformOverTime>(
      `/api/platform-over-time?granularity=month${querySuffix}`,
      fetch,
    ),
  ]);

  return {
    overview,
    activityHeatmap,
    topChats,
    topThemes,
    messagesByMonth: monthlyTotals(platformOverTime),
    messagesByHour: hourlyTotals(activityHeatmap.points),
    metadata,
    platformOverTime,
    filters: {
      from: url.searchParams.get("from") ?? "",
      to: url.searchParams.get("to") ?? "",
      people: url.searchParams.get("people") ?? "",
      themes: url.searchParams.get("themes") ?? "",
      platforms: url.searchParams.get("platforms") ?? "",
    },
  };
};
