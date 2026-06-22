import { apiUrl } from "./api.ts";

export function toMediaUrl(value: string | null): string | null {
  return value && value.startsWith("/api/") ? apiUrl(value) : value;
}

export function toReactionImageUrl(
  sourceName: string,
  imageUrl: string | null,
): string | null {
  const resolved = toMediaUrl(imageUrl);
  if (resolved) return resolved;
  const preview = (imageUrl ?? "").trim();
  if (!preview || !sourceName.startsWith("Discord: ")) return null;
  const sourceFolder = sourceName.replace(/^Discord:\s*/, "");
  return apiUrl(
    `/api/media?${
      new URLSearchParams({
        platform: "discord",
        source: sourceFolder,
        path: preview,
      }).toString()
    }`,
  );
}

export function toDisplayAttachmentUrl(
  attachmentUrl: string | null,
  attachmentPreview: string | null,
): string | null {
  const resolved = toMediaUrl(attachmentUrl);
  if (resolved) return resolved;
  const preview = (attachmentPreview ?? "").trim();
  if (!preview) return null;
  if (preview.startsWith("http://") || preview.startsWith("https://")) {
    try {
      const parsed = new URL(preview);
      if (
        parsed.hostname === "localhost" ||
        parsed.hostname === "127.0.0.1"
      ) {
        return null;
      }
      return preview;
    } catch {
      return null;
    }
  }
  return null;
}

export function attachmentKind(
  preview: string | null,
): "image" | "video" | "audio" | "link" | "label" | null {
  if (!preview) return null;
  const normalized = preview.toLowerCase();
  const hasProtocol = normalized.startsWith("http://") ||
    normalized.startsWith("https://");
  if (/\.(png|jpe?g|gif|webp|bmp|svg)(\?|#|$)/.test(normalized)) {
    return "image";
  }
  if (/\.(mp4|mov|webm|m4v)(\?|#|$)/.test(normalized)) return "video";
  if (/\.(mp3|wav|ogg|m4a|aac|flac)(\?|#|$)/.test(normalized)) return "audio";
  if (hasProtocol) return "link";
  return "label";
}

export function attachmentLabel(preview: string): string {
  try {
    const url = new URL(preview);
    const segment = url.pathname.split("/").filter(Boolean).at(-1);
    return segment ? decodeURIComponent(segment) : preview;
  } catch {
    return preview;
  }
}
