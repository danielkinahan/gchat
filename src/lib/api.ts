export type Overview = {
  total_messages: number;
  date_range: {
    start: string | null;
    end: string | null;
  };
  message_stats: {
    with_text: number;
    with_links: number;
    with_images: number;
    with_gifs: number;
    with_videos: number;
    with_stickers: number;
    with_audio_files: number;
    with_documents: number;
    with_other_files: number;
    edited_messages: number;
    average_per_day: number;
    longest_period_without_messages_seconds: number;
    longest_active_conversation_seconds: number;
    most_active_year: { bucket: string | null; count: number };
    most_active_month: { bucket: string | null; count: number };
    most_active_day: { bucket: string | null; count: number };
    most_active_hour: { bucket: string | null; count: number };
    conversation_count: number;
    avg_messages_per_conversation: number;
    longest_conversation_message_count: number;
  };
  people: Array<{
    id: number;
    display_name: string;
    color: string;
    message_count: number;
  }>;
};

export type TimePoint = {
  bucket: string;
  message_count: number;
};

export type TopPeople = {
  items: Array<{
    id: number;
    display_name: string;
    color: string;
    message_count: number;
  }>;
};

export type PersonDiversity = {
  source: "materialized" | "live";
  items: Array<{
    id: number;
    display_name: string;
    color: string;
    avatar: string;
    message_count: number;
    unique_words: number;
    total_words: number;
    ttr: number;
    word_entropy: number;
    channel_count: number;
    theme_count: number;
    platform_count: number;
    channel_hhi: number;
  }>;
};

export type PlatformOverTime = {
  granularity: string;
  platforms: string[];
  points: Array<{
    bucket: string;
    counts: Record<string, number>;
  }>;
};

export function apiUrl(path: string): string {
  if (!path) return path;
  if (path.startsWith("http://") || path.startsWith("https://")) {
    return path;
  }
  if (path.startsWith("/")) return path;
  return `/${path}`;
}

export async function fetchJson<T>(
  path: string,
  fetcher: typeof fetch = fetch,
): Promise<T> {
  const url = apiUrl(path);
  const response = await fetcher(url);
  if (!response.ok) {
    throw new Error(`Request failed: ${response.status}`);
  }
  return response.json() as Promise<T>;
}

export function filterQuery(params: URLSearchParams): string {
  const query = new URLSearchParams();
  for (const key of ["from", "to", "people", "themes", "platforms"]) {
    const value = params.get(key);
    if (value) query.set(key, value);
  }
  const encoded = query.toString();
  return encoded ? `?${encoded}` : "";
}
