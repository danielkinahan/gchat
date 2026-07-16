<script lang="ts">
    import { apiUrl, fetchJson } from "$lib/api";

    type EmojiItem = {
        name: string;
        count: number;
        image_url: string | null;
    };
    type ReactionCoverage = {
        supported: boolean;
        total_reactions: number;
        identified_reactions: number;
        resolved_reactions: number;
        identity_coverage: number;
        people: Array<{
            id: number;
            display_name: string;
            color: string;
            count: number;
        }>;
    };

    export let active = false;
    export let filterSignature: string;
    export let currentFilterParams: () => URLSearchParams;
    export let topLimit: number = 10;

    let emojiItems: EmojiItem[] = [];
    let reactionCoverage: ReactionCoverage | null = null;
    let loading = false;
    let error = "";
    let loadedFilterKey = "";

    $: if (active && !loading && loadedFilterKey !== filterSignature + topLimit) {
        void loadData();
    }

    async function loadData() {
        loading = true;
        error = "";
        try {
            const params = currentFilterParams();
            params.set("limit", String(Math.max(topLimit, 30)));
            const coverageParams = currentFilterParams();
            coverageParams.set("limit", String(topLimit));
            const [data, coverage] = await Promise.all([
                fetchJson<{ items: EmojiItem[] }>(
                    `/api/emoji-usage?${params.toString()}`,
                ),
                fetchJson<ReactionCoverage>(
                    `/api/reaction-identity-coverage?${coverageParams.toString()}`,
                ),
            ]);
            emojiItems = data.items ?? [];
            reactionCoverage = coverage;
            loadedFilterKey = filterSignature + topLimit;
        } catch (e: any) {
            error = e?.message ?? "Failed to load emoji data";
        } finally {
            loading = false;
        }
    }

    function resolveImageUrl(url: string | null): string | null {
        if (!url) return null;
        if (url.startsWith("/api/")) return apiUrl(url);
        try {
            const u = new URL(url);
            if (u.protocol === "http:" || u.protocol === "https:") return url;
        } catch {}
        return url;
    }

</script>

<section class="tab-content" class:hidden={!active}>
    <div class="emoji-tab-inner">
        {#if loading}
            <p class="muted">Loading emoji usage…</p>
        {:else if error}
            <p class="muted">{error}</p>
        {:else if emojiItems.length === 0}
            <p class="muted">No reaction data found.</p>
        {:else}
            {#if reactionCoverage?.supported && reactionCoverage.total_reactions > 0}
                <div class="coverage-panel">
                    <div>
                        <span class="coverage-value"
                            >{(
                                reactionCoverage.identity_coverage * 100
                            ).toFixed(1)}%</span
                        >
                        <span class="coverage-label"
                            >reaction identities available</span
                        >
                    </div>
                    <span class="coverage-detail"
                        >{reactionCoverage.identified_reactions.toLocaleString()}
                        of {reactionCoverage.total_reactions.toLocaleString()}</span
                    >
                </div>
                {#if reactionCoverage.people.length > 0}
                    <h2 class="section-title">Top Reactors</h2>
                    <div class="reactor-list">
                        {#each reactionCoverage.people as person}
                            <span
                                class="reactor-pill"
                                style={`--person-color:${person.color}`}
                            >
                                {person.display_name}
                                <strong>{person.count.toLocaleString()}</strong>
                            </span>
                        {/each}
                    </div>
                {/if}
            {/if}
            <h2 class="section-title">Top Reaction Emoji</h2>
            <div class="emoji-grid">
                {#each emojiItems as item, i}
                    {@const imgSrc = resolveImageUrl(item.image_url)}
                    <div class="emoji-card hover-title" title={item.name}>
                        <span class="emoji-rank">#{i + 1}</span>
                        <div class="emoji-face">
                            {#if imgSrc}
                                <img
                                    class="emoji-img"
                                    src={imgSrc}
                                    alt={item.name}
                                    loading="lazy"
                                />
                            {:else}
                                <span class="emoji-char">{item.name}</span>
                            {/if}
                        </div>
                        <span class="emoji-name">{item.name}</span>
                        <span class="emoji-count">{item.count.toLocaleString()}</span>
                    </div>
                {/each}
            </div>
        {/if}
    </div>
</section>

<style>
    .hidden {
        display: none;
    }

    .emoji-tab-inner {
        padding: 16px 0;
    }

    .section-title {
        font-size: 1rem;
        font-weight: 600;
        color: #f1f5f9;
        margin: 0 0 16px;
    }

    .coverage-panel {
        align-items: center;
        background: #1e293b;
        border: 1px solid #334155;
        border-radius: 12px;
        display: flex;
        justify-content: space-between;
        margin-bottom: 16px;
        padding: 14px 16px;
    }

    .coverage-value {
        color: #f1f5f9;
        font-size: 1.25rem;
        font-weight: 700;
        margin-right: 8px;
    }

    .coverage-label,
    .coverage-detail {
        color: #94a3b8;
        font-size: 0.78rem;
    }

    .reactor-list {
        display: flex;
        flex-wrap: wrap;
        gap: 8px;
        margin-bottom: 20px;
    }

    .reactor-pill {
        border: 1px solid color-mix(in srgb, var(--person-color) 55%, #334155);
        border-radius: 999px;
        color: #cbd5e1;
        font-size: 0.75rem;
        padding: 5px 9px;
    }

    .reactor-pill strong {
        color: #f1f5f9;
        margin-left: 5px;
    }

    .emoji-grid {
        display: grid;
        grid-template-columns: repeat(auto-fill, minmax(100px, 1fr));
        gap: 12px;
    }

    .emoji-card {
        background: #1e293b;
        border: 1px solid #334155;
        border-radius: 12px;
        padding: 14px 10px 10px;
        display: flex;
        flex-direction: column;
        align-items: center;
        gap: 6px;
        position: relative;
        transition: background 120ms ease;
    }

    .emoji-card:hover {
        background: #273548;
    }

    .emoji-rank {
        position: absolute;
        top: 6px;
        left: 8px;
        font-size: 0.68rem;
        color: #64748b;
    }

    .emoji-face {
        height: 40px;
        display: flex;
        align-items: center;
        justify-content: center;
    }

    .emoji-img {
        width: 36px;
        height: 36px;
        object-fit: contain;
    }

    .emoji-char {
        font-size: 2rem;
        line-height: 1;
    }

    .emoji-name {
        font-size: 0.72rem;
        color: #94a3b8;
        text-align: center;
        word-break: break-all;
        max-width: 100%;
        overflow: hidden;
        text-overflow: ellipsis;
        white-space: nowrap;
    }

    .emoji-count {
        font-size: 0.95rem;
        font-weight: 700;
        color: #f1f5f9;
    }
</style>
