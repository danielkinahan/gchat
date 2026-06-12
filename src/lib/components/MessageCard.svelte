<script lang="ts">
    import LinkPreview from "$lib/LinkPreview.svelte";
    import {
        extractMessageLinks,
        isUrlOnlyMessage,
        stripPreviewedLinks,
    } from "$lib/messageLinks";

    export let message: any;
    export let highlight: boolean = false;
    export let bare: boolean = false;
    export let showMeta: boolean = true;
    // Kept for API stability with callers that still pass it; no longer used.
    // eslint-disable-next-line @typescript-eslint/no-unused-vars
    export let large: boolean = false;
    void large;
    export let resolveReactionImage:
        | ((source: string, imageUrl: string) => string | null)
        | null = null;

    function attachmentKind(
        url: string | null,
    ): "image" | "video" | "audio" | "link" | null {
        if (!url) return null;
        const u = url.toLowerCase();
        if (u.match(/\.(png|jpe?g|gif|webp)(?:$|\?)/)) return "image";
        if (u.match(/\.(mp4|webm|ogg|mov)(?:$|\?)/)) return "video";
        if (u.match(/\.(mp3|wav|m4a|flac|ogg)(?:$|\?)/)) return "audio";
        return "link";
    }

    function isSingleEmojiLike(name: string): boolean {
        const s = (name || "").trim();
        if (!s) return false;
        if (s.length > 3) return false;
        try {
            return !/[\p{L}\p{N}]/u.test(s);
        } catch {
            return !/[A-Za-z0-9]/.test(s);
        }
    }

    function normalizeReactions(
        details: any,
        summary: string | null,
    ): Array<{ name: string; count: number; image_url: string | null }> {
        const out: Array<{
            name: string;
            count: number;
            image_url: string | null;
        }> = [];
        const seen = new Set<string>();
        if (Array.isArray(details)) {
            for (const raw of details) {
                const name = String(raw?.name ?? "").trim();
                if (!name) continue;
                const count = Number(raw?.count ?? 0) || 0;
                let image_url: string | null = null;
                if (raw?.image_url) {
                    const resolved = resolveReactionImage
                        ? resolveReactionImage(
                              message?.source_name || "",
                              String(raw.image_url),
                          )
                        : String(raw.image_url);
                    image_url = resolved || null;
                }
                const key = `${name}::${image_url || ""}`;
                if (seen.has(key)) continue;
                seen.add(key);
                out.push({ name, count, image_url });
            }
            return out;
        }
        if (summary) {
            for (const part of String(summary).split(/\s+/)) {
                const m = part.match(/^(.*)×(\d+)$/);
                if (!m) continue;
                out.push({
                    name: m[1],
                    count: Number(m[2]) || 0,
                    image_url: null,
                });
            }
        }
        return out;
    }

    $: contentLinks = extractMessageLinks(message?.content || null);
    $: displayContent = stripPreviewedLinks(message?.content || null, contentLinks);
    $: reactions = normalizeReactions(
        message?.reaction_details,
        message?.reaction_summary || null,
    );
    $: containerClass = bare
        ? `message-inner${highlight ? " highlight" : ""}`
        : `message-card${highlight ? " highlight" : ""}`;
</script>

<div
    id={`chatlog__message-container-${message?.id || ""}`}
    class={containerClass}
>
    {#if showMeta}
        <div class="meta">
            <strong style={`color:${message?.person_color || "#fff"}`}
                >{message?.person_name || "Unknown"}</strong
            >
            <time
                >{message?.ts
                    ? new Date(message.ts).toLocaleString()
                    : "N/A"}</time
            >
        </div>
    {/if}

    {#if displayContent && !isUrlOnlyMessage(message?.content || null)}
        <p class="content">{displayContent}</p>
    {/if}

    {#each contentLinks as link (link)}
        <LinkPreview url={link} />
    {/each}

    {#if message?.attachment_url}
        {@const attachKind = attachmentKind(message.attachment_url)}
        {#if attachKind === "image"}
            <img
                class="reaction-attachment-image"
                src={message.attachment_url}
                alt="attachment"
                loading="lazy"
            />
        {:else if attachKind === "video"}
            <!-- svelte-ignore a11y_media_has_caption -->
            <video
                class="reaction-attachment-video"
                src={message.attachment_url}
                controls
                preload="metadata"
            ></video>
        {:else if attachKind === "audio"}
            <audio
                class="reaction-attachment-audio"
                src={message.attachment_url}
                controls
                preload="none"
            ></audio>
        {:else if !contentLinks.includes(message.attachment_url)}
            <LinkPreview url={message.attachment_url} />
        {/if}
    {/if}

    {#if reactions.length}
        <div class="reaction-pills">
            {#each reactions as reaction}
                <span class="reaction-pill">
                    {#if reaction.image_url}
                        <img
                            src={reaction.image_url}
                            alt={reaction.name}
                            loading="lazy"
                        />
                    {/if}
                    {#if !(reaction.image_url && isSingleEmojiLike(reaction.name))}
                        <span>{reaction.name}</span>
                    {/if}
                    <strong>×{reaction.count}</strong>
                </span>
            {/each}
        </div>
    {/if}
</div>

<style>
    .message-card {
        padding: 12px;
        border-radius: 10px;
        background: #0b1220;
        border: 1px solid #1f2937;
        margin-bottom: 12px;
    }

    .message-inner {
        padding: 4px 0;
    }

    .highlight {
        box-shadow: 0 0 0 3px rgba(14, 165, 233, 0.08);
        border-color: #0ea5e9;
    }

    .meta {
        color: #94a3b8;
        font-size: 0.9rem;
        display: flex;
        gap: 8px;
        align-items: center;
    }

    .content {
        margin-top: 8px;
        white-space: pre-wrap;
        word-break: break-word;
    }

    .reaction-attachment-image {
        margin-top: 8px;
        max-width: min(100%, 520px);
        max-height: 320px;
        border-radius: 10px;
        border: 1px solid #1f2937;
        background: #020617;
    }

    .reaction-attachment-video {
        margin-top: 8px;
        width: min(100%, 520px);
    }

    .reaction-attachment-audio {
        margin-top: 8px;
        width: min(100%, 380px);
    }

    .reaction-pills {
        display: flex;
        flex-wrap: wrap;
        gap: 5px;
        margin: 6px 0;
    }

    .reaction-pill {
        display: inline-flex;
        align-items: center;
        gap: 4px;
        padding: 4px 8px;
        border-radius: 999px;
        background: rgba(15, 23, 42, 0.95);
        border: 1px solid #1f2937;
        font-size: 0.76rem;
        color: #e2e8f0;
    }

    .reaction-pill img {
        width: 16px;
        height: 16px;
        object-fit: contain;
        flex: 0 0 auto;
    }
</style>
