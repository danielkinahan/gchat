import type { Overview, TimePoint, TopPeople } from "$lib/api";
import { fetchJson, filterQuery } from "$lib/api";

export const load = async ({ url, fetch }) => {
  const query = filterQuery(url.searchParams);
  const querySuffix = query ? `&${query.slice(1)}` : "";

  const [
    overview,
    messagesOverTime,
    topPeople,
    calendar,
    activityHeatmap,
    topChats,
    topThemes,
    messagesByMonth,
    messagesByHour,
    metadata,
    nameHistory,
  ] = await Promise.all([
    fetchJson<Overview>(`/api/overview${query}`, fetch),
    fetchJson<{ granularity: string; points: TimePoint[] }>(
      `/api/messages-over-time${query}`,
      fetch,
    ),
    fetchJson<TopPeople>(`/api/top-people?limit=10${querySuffix}`, fetch),
    fetchJson<{ points: Array<{ day: string; message_count: number }> }>(
      `/api/calendar${query}`,
      fetch,
    ),
    fetchJson<{
      points: Array<{ weekday: number; hour: number; message_count: number }>;
    }>(`/api/activity-heatmap${query}`, fetch),
    fetchJson<{
      items: Array<{
        id: number;
        name: string;
        theme_name: string;
        message_count: number;
      }>;
    }>(`/api/top-chats?limit=15${querySuffix}`, fetch),
    fetchJson<{
      items: Array<{ id: number; name: string; message_count: number }>;
    }>(`/api/top-themes?limit=15${querySuffix}`, fetch),
    fetchJson<{ points: Array<{ month: string; message_count: number }> }>(
      `/api/messages-by-month${query}`,
      fetch,
    ),
    fetchJson<{ points: Array<{ hour: number; message_count: number }> }>(
      `/api/messages-by-hour${query}`,
      fetch,
    ),
    fetchJson<{
      people: Array<{ id: number; name: string }>;
      themes: Array<{ id: number; name: string }>;
      platforms: string[];
    }>(`/api/metadata`, fetch),
    fetchJson<{
      chats: Array<{
        id: number;
        platform: string;
        source_name: string;
        current_name: string;
        platform_channel_id: string;
        previous_names: Array<{
          previous_name: string | null;
          new_name: string;
          ts: string | null;
        }>;
        participants: Array<{
          id: number;
          display_name: string;
          history: Array<{
            previous_name: string | null;
            new_name: string;
            ts: string | null;
          }>;
        }>;
      }>;
    }>(`/api/name-history${query}`, fetch),
  ]);

  return {
    overview,
    messagesOverTime,
    topPeople,
    calendar,
    activityHeatmap,
    topChats,
    topThemes,
    messagesByMonth,
    messagesByHour,
    metadata,
    nameHistory,
    filters: {
      from: url.searchParams.get("from") ?? "",
      to: url.searchParams.get("to") ?? "",
      people: url.searchParams.get("people") ?? "",
      themes: url.searchParams.get("themes") ?? "",
      platforms: url.searchParams.get("platforms") ?? "",
    },
  };
};
