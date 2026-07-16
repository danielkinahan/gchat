<script lang="ts">
    import { fetchJson } from "$lib/api";

    type SearchResult = {
        id: string;
        ts: string | null;
        content: string | null;
        person_name: string | null;
        person_color: string | null;
        channel_name: string | null;
        source_name: string | null;
        platform: string | null;
    };

    export let active = false;
    export let currentFilterParams: () => URLSearchParams;
    export let openInContext: (id: string) => void;

    let query = "";
    let results: SearchResult[] = [];
    let total = 0;
    let hasMore = false;
    let loading = false;
    let error = "";
    let searched = false;
    const PAGE = 20;

    async function doSearch(append = false) {
        const q = query.trim();
        if (!q) return;
        loading = true;
        error = "";
        try {
            const params = currentFilterParams();
            params.set("q", q);
            params.set("limit", String(PAGE));
            if (append) params.set("offset", String(results.length));
            const data = await fetchJson<{
                total: number;
                has_more: boolean;
                items: SearchResult[];
            }>(`/api/search?${params.toString()}`);
            results = append ? [...results, ...data.items] : data.items;
            total = data.total;
            hasMore = data.has_more;
            searched = true;
        } catch (e: any) {
            error = e?.message ?? "Search failed";
        } finally {
            loading = false;
        }
    }

    function onKeydown(e: KeyboardEvent) {
        if (e.key === "Enter") {
            results = [];
            searched = false;
            void doSearch();
        }
    }

    function highlightedParts(
        text: string | null,
        q: string,
    ): Array<{ text: string; highlighted: boolean }> {
        if (!text || !q) {
            return text ? [{ text, highlighted: false }] : [];
        }
        const escaped = q.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
        const matcher = new RegExp(escaped, "gi");
        const parts: Array<{ text: string; highlighted: boolean }> = [];
        let cursor = 0;
        for (const match of text.matchAll(matcher)) {
            const index = match.index ?? 0;
            if (index > cursor) {
                parts.push({
                    text: text.slice(cursor, index),
                    highlighted: false,
                });
            }
            parts.push({ text: match[0], highlighted: true });
            cursor = index + match[0].length;
        }
        if (cursor < text.length) {
            parts.push({ text: text.slice(cursor), highlighted: false });
        }
        return parts;
    }

    function formatTs(ts: string | null): string {
        if (!ts) return "";
        try {
            return new Date(ts).toLocaleString();
        } catch {
            return ts;
        }
    }

    const platformColor: Record<string, string> = {
        discord: "#5865f2",
        facebook: "#1877f2",
        signal: "#3a76f0",
    };
</script>

<section class="tab-content" class:hidden={!active}>
    <div class="search-tab-inner">
        <div class="search-bar">
            <input
                type="search"
                class="search-input"
                placeholder="Search messages…"
                bind:value={query}
                on:keydown={onKeydown}
                disabled={loading}
                autocomplete="off"
                spellcheck="false"
            />
            <button
                type="button"
                class="search-btn"
                on:click={() => { results = []; searched = false; void doSearch(); }}
                disabled={loading || !query.trim()}
            >
                {loading ? "Searching…" : "Search"}
            </button>
        </div>

        {#if error}
            <p class="muted">{error}</p>
        {:else if loading && !searched}
            <p class="muted">Searching…</p>
        {:else if searched && results.length === 0}
            <p class="muted">No messages found.</p>
        {:else if results.length > 0}
            <p class="search-summary muted">
                {total.toLocaleString()} result{total === 1 ? "" : "s"}
            </p>
            <div class="result-list">
                {#each results as r}
                    <div class="result-card">
                        <div class="result-meta">
                            {#if r.platform}
                                <span
                                    class="platform-chip-icon result-platform-icon"
                                    data-platform={r.platform}
                                ></span>
                            {/if}
                            {#if r.person_name}
                                <strong
                                    style="color: {r.person_color || '#94a3b8'}"
                                    >{r.person_name}</strong
                                >
                            {/if}
                            {#if r.channel_name}
                                <span class="result-channel">in {r.channel_name}</span>
                            {/if}
                            {#if r.ts}
                                <time class="muted">{formatTs(r.ts)}</time>
                            {/if}
                        </div>
                        <p class="result-content">
                            {#each highlightedParts(r.content, query.trim()) as part}
                                {#if part.highlighted}
                                    <mark>{part.text}</mark>
                                {:else}
                                    {part.text}
                                {/if}
                            {/each}
                        </p>
                        <button
                            type="button"
                            class="result-context-btn"
                            on:click={() => openInContext(r.id)}
                        >Show in context</button>
                    </div>
                {/each}
            </div>
            {#if hasMore}
                <button
                    type="button"
                    class="load-more-btn"
                    on:click={() => void doSearch(true)}
                    disabled={loading}
                >
                    {loading ? "Loading…" : "Load more"}
                </button>
            {/if}
        {/if}
    </div>
</section>

<style>
    .hidden {
        display: none;
    }

    .search-tab-inner {
        padding: 16px 0;
        max-width: 860px;
    }

    .search-bar {
        display: flex;
        gap: 8px;
        margin-bottom: 16px;
    }

    .search-input {
        flex: 1;
        padding: 9px 14px;
        background: #1e293b;
        border: 1px solid #334155;
        border-radius: 8px;
        color: #f1f5f9;
        font-size: 0.95rem;
        outline: none;
        transition: border-color 120ms ease;
    }

    .search-input:focus {
        border-color: #3b82f6;
    }

    .search-btn {
        padding: 9px 20px;
        background: #2563eb;
        border: none;
        border-radius: 8px;
        color: #fff;
        font-size: 0.9rem;
        font-weight: 600;
        cursor: pointer;
        transition: background 120ms ease;
        white-space: nowrap;
    }

    .search-btn:hover:not(:disabled) {
        background: #1d4ed8;
    }

    .search-btn:disabled {
        opacity: 0.5;
        cursor: default;
    }

    .search-summary {
        font-size: 0.82rem;
        margin-bottom: 12px;
    }

    .result-list {
        display: flex;
        flex-direction: column;
        gap: 10px;
    }

    .result-card {
        background: #1e293b;
        border: 1px solid #334155;
        border-radius: 10px;
        padding: 12px 14px;
        display: flex;
        flex-direction: column;
        gap: 6px;
    }

    .result-meta {
        display: flex;
        align-items: center;
        gap: 8px;
        flex-wrap: wrap;
        font-size: 0.82rem;
    }

    .result-platform-icon {
        width: 14px;
        height: 14px;
        flex-shrink: 0;
    }

    .result-channel {
        color: #64748b;
    }

    .result-content {
        margin: 0;
        font-size: 0.9rem;
        color: #cbd5e1;
        white-space: pre-wrap;
        word-break: break-word;
    }

    .result-content :global(mark) {
        background: #854d0e;
        color: #fef3c7;
        border-radius: 3px;
        padding: 0 2px;
    }

    .result-context-btn {
        align-self: flex-start;
        padding: 4px 10px;
        background: transparent;
        border: 1px solid #334155;
        border-radius: 6px;
        color: #64748b;
        font-size: 0.78rem;
        cursor: pointer;
        transition: color 120ms ease, border-color 120ms ease;
    }

    .result-context-btn:hover {
        color: #94a3b8;
        border-color: #475569;
    }

    .load-more-btn {
        margin-top: 12px;
        padding: 8px 20px;
        background: #1e293b;
        border: 1px solid #334155;
        border-radius: 8px;
        color: #94a3b8;
        font-size: 0.85rem;
        cursor: pointer;
        transition: background 120ms ease;
    }

    .load-more-btn:hover:not(:disabled) {
        background: #273548;
    }

    .load-more-btn:disabled {
        opacity: 0.5;
    }
</style>
