export const MESSAGE_URL_PATTERN = /\bhttps?:\/\/[^\s<>"']+/gi;
const PREVIEW_LINK_LIMIT = 2;

/**
 * Returns a canonical key for a URL so that equivalent URLs (e.g. a YouTube
 * short link and its full counterpart) are treated as duplicates.
 * Falls back to the URL itself when no known normalization applies.
 */
export function canonicalUrlKey(url: string): string {
    try {
        const u = new URL(url);
        const host = u.hostname.replace(/^www\./, "");

        // youtu.be/VIDEO_ID  ↔  youtube.com/watch?v=VIDEO_ID
        if (host === "youtu.be") {
            const id = u.pathname.slice(1).split("/")[0];
            if (id) return `youtube:${id}`;
        }
        if (host === "youtube.com" || host === "m.youtube.com") {
            const id = u.searchParams.get("v");
            if (id) return `youtube:${id}`;
        }
    } catch {
        // not a valid URL — fall through
    }
    return url;
}

export function extractMessageLinks(content: string | null): string[] {
    const text = (content ?? "").trim();
    if (!text) return [];
    const matches = text.match(MESSAGE_URL_PATTERN);
    if (!matches) return [];
    const seen = new Set<string>();
    const result: string[] = [];
    for (const raw of matches) {
        const cleaned = raw.replace(/[)\].,!?;:]+$/u, "");
        if (cleaned.length < 8) continue;
        const key = canonicalUrlKey(cleaned);
        if (seen.has(key)) continue;
        seen.add(key);
        result.push(cleaned);
        if (result.length >= PREVIEW_LINK_LIMIT) break;
    }
    return result;
}

/**
 * Returns content with the given URLs stripped out (for display when the URLs
 * are already rendered as link preview cards beneath the text).
 */
export function stripPreviewedLinks(
    content: string | null,
    links: string[],
): string {
    if (!links.length) return content ?? "";
    let result = content ?? "";
    for (const link of links) {
        result = result.split(link).join("");
    }
    return result.replace(/\s{2,}/g, " ").trim();
}

export function isUrlOnlyMessage(content: string | null): boolean {
    const text = (content ?? "").trim();
    if (!text) return false;
    const matches = text.match(MESSAGE_URL_PATTERN);
    if (!matches || matches.length === 0) return false;
    let stripped = text;
    for (const match of matches) {
        stripped = stripped.replace(match, "");
    }
    return stripped.replace(/[\s\u200b]+/g, "").length === 0;
}
