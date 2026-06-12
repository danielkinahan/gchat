export const MESSAGE_URL_PATTERN = /\bhttps?:\/\/[^\s<>"']+/gi;
const PREVIEW_LINK_LIMIT = 2;

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
        if (seen.has(cleaned)) continue;
        seen.add(cleaned);
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
