export type LinkPreviewData = {
  url: string;
  resolved_url?: string | null;
  title?: string | null;
  description?: string | null;
  image?: string | null;
  site_name?: string | null;
  favicon?: string | null;
  error?: string | null;
};

const MAX_ENTRIES = 500;
const cache = new Map<string, Promise<LinkPreviewData>>();

export function fetchLinkPreview(url: string): Promise<LinkPreviewData> {
  const cached = cache.get(url);
  if (cached) return cached;

  const request = fetch(`/api/link-preview?url=${encodeURIComponent(url)}`)
    .then(async (response) => {
      if (!response.ok) throw new Error(`HTTP ${response.status}`);
      return (await response.json()) as LinkPreviewData;
    })
    .catch((error: unknown) => ({
      url,
      error: error instanceof Error ? error.message : "Failed to load",
    }));

  if (cache.size >= MAX_ENTRIES) {
    const oldest = cache.keys().next().value;
    if (oldest) cache.delete(oldest);
  }
  cache.set(url, request);
  return request;
}
