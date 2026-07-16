<script lang="ts">
    import {
        fetchLinkPreview,
        type LinkPreviewData,
    } from "$lib/linkPreview";

    export let url: string;

    let preview: LinkPreviewData | null = null;
    let loading = true;
    let lastUrl = "";

    async function load(target: string): Promise<void> {
        if (!target || target === lastUrl) return;
        lastUrl = target;
        loading = true;
        preview = null;
        try {
            const result = await fetchLinkPreview(target);
            if (lastUrl === target) preview = result;
        } finally {
            if (lastUrl === target) loading = false;
        }
    }

    $: void load(url);

    function hostFor(value: string): string {
        try {
            return new URL(value).host;
        } catch (_err) {
            return value;
        }
    }

    function hasContent(p: LinkPreviewData | null): boolean {
        if (!p) return false;
        if (p.error) return false;
        return Boolean(
            (p.title && p.title.trim().length > 0) ||
                (p.description && p.description.trim().length > 0) ||
                (p.image && p.image.trim().length > 0),
        );
    }
</script>

{#if loading}
    <a
        class="link-preview is-loading"
        href={url}
        target="_blank"
        rel="noreferrer"
    >
        <span class="link-preview-skeleton"></span>
        <span class="link-preview-host">{hostFor(url)}</span>
    </a>
{:else if hasContent(preview)}
    <a
        class="link-preview"
        href={preview?.resolved_url || url}
        target="_blank"
        rel="noreferrer"
    >
        {#if preview?.image}
            <span class="link-preview-thumb">
                <img src={preview.image} alt="" loading="lazy" />
            </span>
        {/if}
        <span class="link-preview-text">
            <span class="link-preview-site">
                {#if preview?.favicon}
                    <img
                        class="link-preview-favicon"
                        src={preview.favicon}
                        alt=""
                        loading="lazy"
                    />
                {/if}
                <span>{preview?.site_name || hostFor(url)}</span>
            </span>
            {#if preview?.title}
                <strong>{preview.title}</strong>
            {/if}
            {#if preview?.description}
                <span class="link-preview-desc">{preview.description}</span>
            {/if}
        </span>
    </a>
{:else}
    <a class="link-preview is-bare" href={url} target="_blank" rel="noreferrer"
        >{url}</a
    >
{/if}

<style>
    .link-preview {
        display: flex;
        gap: 12px;
        align-items: stretch;
        text-decoration: none;
        color: inherit;
        border: 1px solid #1f2937;
        background: #0b1220;
        border-radius: 10px;
        padding: 10px;
        max-width: 540px;
        transition:
            border-color 0.15s ease,
            background 0.15s ease;
    }

    .link-preview:hover {
        border-color: #475569;
        background: rgba(99, 102, 241, 0.08);
    }

    .link-preview.is-bare {
        display: inline-block;
        background: transparent;
        border: none;
        padding: 0;
        color: #60a5fa;
        text-decoration: underline;
        word-break: break-all;
    }

    .link-preview.is-loading {
        align-items: center;
        gap: 8px;
        color: #94a3b8;
        font-size: 0.85rem;
    }

    .link-preview-skeleton {
        width: 14px;
        height: 14px;
        border-radius: 50%;
        border: 2px solid #334155;
        border-top-color: #60a5fa;
        animation: link-preview-spin 0.8s linear infinite;
    }

    .link-preview-host {
        font-size: 0.85rem;
    }

    .link-preview-thumb {
        flex: 0 0 120px;
        max-width: 160px;
        display: block;
        overflow: hidden;
        border-radius: 6px;
        background: #111827;
    }

    .link-preview-thumb img {
        width: 100%;
        height: 100%;
        object-fit: cover;
        display: block;
        aspect-ratio: 16 / 9;
    }

    .link-preview-text {
        display: flex;
        flex-direction: column;
        gap: 4px;
        min-width: 0;
        flex: 1 1 auto;
    }

    .link-preview-site {
        display: inline-flex;
        align-items: center;
        gap: 6px;
        font-size: 0.75rem;
        color: #94a3b8;
        text-transform: uppercase;
        letter-spacing: 0.04em;
    }

    .link-preview-favicon {
        width: 14px;
        height: 14px;
        border-radius: 3px;
        object-fit: contain;
    }

    .link-preview-text strong {
        font-size: 0.95rem;
        line-height: 1.3;
        color: #f8fafc;
        display: -webkit-box;
        -webkit-line-clamp: 2;
        line-clamp: 2;
        -webkit-box-orient: vertical;
        overflow: hidden;
    }

    .link-preview-desc {
        font-size: 0.85rem;
        color: #cbd5f5;
        line-height: 1.35;
        display: -webkit-box;
        -webkit-line-clamp: 3;
        line-clamp: 3;
        -webkit-box-orient: vertical;
        overflow: hidden;
    }

    @keyframes link-preview-spin {
        to {
            transform: rotate(360deg);
        }
    }
</style>
