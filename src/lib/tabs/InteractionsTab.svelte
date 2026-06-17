<script lang="ts">
    import MessageCard from "$lib/components/MessageCard.svelte";
    import { fetchJson } from "$lib/api";
    import { toMediaUrl, toDisplayAttachmentUrl } from "$lib/mediaUrls";

    type ReactionDetail = {
        name: string;
        count: number;
        emoji_id: string | null;
        image_url: string | null;
        code: string | null;
        is_animated: boolean;
    };

    type ReactionMessage = {
        id: string;
        ts: string | null;
        content: string;
        attachment_preview: string | null;
        attachment_url: string | null;
        attachment_display_url: string | null;
        person_name: string;
        person_color: string;
        channel_name: string;
        source_name: string;
        reaction_count: number;
        reaction_summary: string | null;
        reaction_details: ReactionDetail[];
    };

    type ReactionAuthor = {
        id: number;
        display_name: string;
        color: string;
        count: number;
    };

    type MemberEventBucket = {
        by_actor: Array<{
            id: number;
            display_name: string;
            color: string;
            count: number;
        }>;
        by_target: Array<{
            id: number;
            display_name: string;
            color: string;
            count: number;
        }>;
        by_chat: Array<{
            id: number;
            name: string;
            source_name: string;
            platform: string;
            count: number;
        }>;
    };

    export let active: boolean;
    export let filterSignature: string;
    export let currentFilterParams: () => URLSearchParams;
    export let topLimit: number = 10;
    export let openInContext: (messageId: string) => void;
    export let resolveReactionImage: (
        sourceName: string,
        imageUrl: string | null,
    ) => string | null;
    const EMPTY_BUCKET: MemberEventBucket = {
        by_actor: [],
        by_target: [],
        by_chat: [],
    };

    let isLoading = false;
    let error = "";
    let mentionSearch = "";
    let mostMentioned: Array<{ mention: string; count: number }> = [];
    let topReactedMessages: ReactionMessage[] = [];
    let hasMoreReacted = false;
    let isLoadingMoreReacted = false;
    let reactionAuthors: ReactionAuthor[] = [];
    let removedEvents: MemberEventBucket = EMPTY_BUCKET;
    let leftEvents: MemberEventBucket = EMPTY_BUCKET;
    let loadedFilterKey: string | null = null;

    $: normalizedMentionSearch = mentionSearch.trim().toLowerCase();
    $: filteredMentions = normalizedMentionSearch
        ? mostMentioned.filter((item) =>
              item.mention.includes(normalizedMentionSearch),
          )
        : mostMentioned;
    $: visibleMentions = filteredMentions.slice(0, topLimit);

    async function loadData() {
        const filterKey = currentFilterParams().toString();
        if (isLoading || loadedFilterKey === filterKey) return;
        isLoading = true;
        error = "";
        try {
            const baseParams = currentFilterParams();
            const mentionsParams = new URLSearchParams(baseParams);
            mentionsParams.set("limit", "200");
            const reactedParams = new URLSearchParams(baseParams);
            reactedParams.set("limit", String(topLimit));
            const authorsParams = new URLSearchParams(baseParams);
            authorsParams.set("limit", String(topLimit));
            const removedParams = new URLSearchParams(baseParams);
            removedParams.set("kind", "removed");
            removedParams.set("limit", String(topLimit));
            const leftParams = new URLSearchParams(baseParams);
            leftParams.set("kind", "left");
            leftParams.set("limit", String(topLimit));
            const [mentions, reactedMessages, authors, removedResp, leftResp] =
                await Promise.all([
                    fetchJson<{
                        items: Array<{ mention: string; count: number }>;
                    }>(`/api/most-mentioned?${mentionsParams.toString()}`),
                    fetchJson<{ has_more: boolean; items: Omit<ReactionMessage, "attachment_display_url">[] }>(
                        `/api/top-reacted-messages?${reactedParams.toString()}`,
                    ),
                    fetchJson<{ items: ReactionAuthor[] }>(
                        `/api/reaction-authors?${authorsParams.toString()}`,
                    ),
                    fetchJson<MemberEventBucket>(
                        `/api/member-events?${removedParams.toString()}`,
                    ),
                    fetchJson<MemberEventBucket>(
                        `/api/member-events?${leftParams.toString()}`,
                    ),
                ]);
            mostMentioned = mentions.items;
            topReactedMessages = reactedMessages.items.map((message) => ({
                ...message,
                attachment_url: toMediaUrl(message.attachment_url),
                attachment_display_url: toDisplayAttachmentUrl(
                    message.attachment_url,
                    message.attachment_preview,
                ),
            }));
            hasMoreReacted = reactedMessages.has_more ?? false;
            reactionAuthors = authors.items;
            removedEvents = removedResp;
            leftEvents = leftResp;
        } catch (err) {
            error = err instanceof Error ? err.message : "Failed to load interactions";
        } finally {
            loadedFilterKey = filterKey;
            isLoading = false;
        }
    }

    async function loadMoreReacted() {
        if (isLoadingMoreReacted) return;
        isLoadingMoreReacted = true;
        try {
            const baseParams = currentFilterParams();
            baseParams.set("limit", String(topLimit));
            baseParams.set("offset", String(topReactedMessages.length));
            const resp = await fetchJson<{
                has_more: boolean;
                items: Omit<ReactionMessage, "attachment_display_url">[];
            }>(`/api/top-reacted-messages?${baseParams.toString()}`);
            const mapped = resp.items.map((m) => ({
                ...m,
                attachment_url: toMediaUrl(m.attachment_url),
                attachment_display_url: toDisplayAttachmentUrl(
                    m.attachment_url,
                    m.attachment_preview,
                ),
            }));
            topReactedMessages = [...topReactedMessages, ...mapped];
            hasMoreReacted = resp.has_more ?? false;
        } catch {
            // silently ignore
        } finally {
            isLoadingMoreReacted = false;
        }
    }

    $: if (active && !isLoading && loadedFilterKey !== filterSignature) {
        void loadData();
    }
</script>

<section class="interactions" class:tab-hidden={!active}>
    <div class="panel rank-panel">
        <h2>Most mentioned</h2>
        <input
            type="text"
            placeholder="Filter @mentions..."
            bind:value={mentionSearch}
            autocomplete="off"
            autocapitalize="off"
            spellcheck="false"
            data-lpignore="true"
            data-1p-ignore="true"
            class="word-filter"
        />
        {#if error}
            <p class="muted">{error}</p>
        {:else if isLoading}
            <p class="muted">Loading interactions...</p>
        {:else}
            <div class="rank-list">
                {#each visibleMentions as mention}
                    <div
                        class="rank-row"
                        title={`${mention.mention}: ${mention.count.toLocaleString()} mentions`}
                    >
                        <span class="rank-name">{mention.mention}</span>
                        <div class="rank-track">
                            <div
                                class="rank-fill mention"
                                style={`width:${(mention.count / Math.max(filteredMentions[0]?.count || 1, 1)) * 100}%`}
                            ></div>
                        </div>
                        <strong>{mention.count.toLocaleString()}</strong>
                    </div>
                {/each}
            </div>
        {/if}
    </div>

    <div class="panel interactions-feed">
        <h2>Top reacted messages</h2>
        {#if isLoading}
            <p class="muted">Loading interactions...</p>
        {:else if topReactedMessages.length === 0}
            <p class="muted">No reacted messages found.</p>
        {:else}
            <div class="reaction-list">
                {#each topReactedMessages as message}
                    <article class="reaction-card">
                        <div class="reaction-card-head">
                            <strong
                                >{message.reaction_count.toLocaleString()} reactions</strong
                            >
                            <time
                                >{message.ts
                                    ? new Date(message.ts).toLocaleDateString()
                                    : "N/A"}</time
                            >
                        </div>
                        {#if message.reaction_summary && !message.reaction_details.length}
                            <p class="reaction-summary">
                                {message.reaction_summary}
                            </p>
                        {/if}
                        <div class="example-meta">
                            <strong style={`color:${message.person_color}`}
                                >{message.person_name}</strong
                            >
                            <span>{message.channel_name}</span>
                            <button
                                type="button"
                                class="show-context"
                                on:click={() => openInContext(message.id)}
                            >
                                Show in context
                            </button>
                        </div>
                        <MessageCard
                            message={{
                                id: message.id,
                                ts: message.ts,
                                content: message.content,
                                attachment_url:
                                    message.attachment_url ||
                                    message.attachment_display_url,
                                attachment_preview: message.attachment_preview,
                                reaction_count: message.reaction_count,
                                reaction_summary: message.reaction_summary,
                                reaction_details: message.reaction_details,
                                person_name: message.person_name,
                                person_color: message.person_color,
                                channel_name: message.channel_name,
                                source_name: message.source_name,
                            }}
                            highlight={false}
                            bare={true}
                            showMeta={false}
                            {resolveReactionImage}
                        />
                    </article>
                {/each}
            </div>
            {#if hasMoreReacted}
                <button
                    type="button"
                    class="examples-toggle"
                    on:click={loadMoreReacted}
                    disabled={isLoadingMoreReacted}
                >
                    {isLoadingMoreReacted ? "Loading..." : "Show more"}
                </button>
            {/if}
        {/if}
    </div>

    <div class="panel rank-panel">
        <h2>Authors that get the most reactions</h2>
        {#if isLoading}
            <p class="muted">Loading interactions...</p>
        {:else}
            <div class="rank-list">
                {#each reactionAuthors as person}
                    <div
                        class="rank-row"
                        title={`${person.display_name}: ${person.count.toLocaleString()} reactions received`}
                    >
                        <span class="rank-name">
                            <span
                                class="swatch"
                                style={`background:${person.color}`}
                            ></span>{person.display_name}
                        </span>
                        <div class="rank-track">
                            <div
                                class="rank-fill reactions"
                                style={`width:${(person.count / Math.max(reactionAuthors[0]?.count || 1, 1)) * 100}%`}
                            ></div>
                        </div>
                        <strong>{person.count.toLocaleString()}</strong>
                    </div>
                {/each}
            </div>
        {/if}
    </div>

    {#if removedEvents.by_actor.length || removedEvents.by_target.length || removedEvents.by_chat.length}
        <div class="panel rank-panel">
            <h2>Top kickers</h2>
            <div class="rank-list">
                {#each removedEvents.by_actor as person}
                    <div
                        class="rank-row"
                        title={`${person.display_name}: ${person.count.toLocaleString()} kicks`}
                    >
                        <span class="rank-name">
                            <span
                                class="swatch"
                                style={`background:${person.color}`}
                            ></span>{person.display_name}
                        </span>
                        <div class="rank-track">
                            <div
                                class="rank-fill author"
                                style={`width:${(person.count / Math.max(removedEvents.by_actor[0]?.count || 1, 1)) * 100}%`}
                            ></div>
                        </div>
                        <strong>{person.count.toLocaleString()}</strong>
                    </div>
                {/each}
            </div>
        </div>

        <div class="panel rank-panel">
            <h2>Most kicked</h2>
            <div class="rank-list">
                {#each removedEvents.by_target as person}
                    <div
                        class="rank-row"
                        title={`${person.display_name}: ${person.count.toLocaleString()} times kicked`}
                    >
                        <span class="rank-name">
                            <span
                                class="swatch"
                                style={`background:${person.color}`}
                            ></span>{person.display_name}
                        </span>
                        <div class="rank-track">
                            <div
                                class="rank-fill mention"
                                style={`width:${(person.count / Math.max(removedEvents.by_target[0]?.count || 1, 1)) * 100}%`}
                            ></div>
                        </div>
                        <strong>{person.count.toLocaleString()}</strong>
                    </div>
                {/each}
            </div>
        </div>

        <div class="panel rank-panel">
            <h2>Chats with the most kicks</h2>
            <div class="rank-list">
                {#each removedEvents.by_chat as chat}
                    <div
                        class="rank-row"
                        title={`${chat.name}: ${chat.count.toLocaleString()} kicks`}
                    >
                        <span class="rank-name">{chat.name}</span>
                        <div class="rank-track">
                            <div
                                class="rank-fill channel"
                                style={`width:${(chat.count / Math.max(removedEvents.by_chat[0]?.count || 1, 1)) * 100}%`}
                            ></div>
                        </div>
                        <strong>{chat.count.toLocaleString()}</strong>
                    </div>
                {/each}
            </div>
        </div>
    {/if}

    {#if leftEvents.by_target.length}
        <div class="panel rank-panel">
            <h2>Most departures</h2>
            <div class="rank-list">
                {#each leftEvents.by_target as person}
                    <div
                        class="rank-row"
                        title={`${person.display_name}: ${person.count.toLocaleString()} departures`}
                    >
                        <span class="rank-name">
                            <span
                                class="swatch"
                                style={`background:${person.color}`}
                            ></span>{person.display_name}
                        </span>
                        <div class="rank-track">
                            <div
                                class="rank-fill links-author"
                                style={`width:${(person.count / Math.max(leftEvents.by_target[0]?.count || 1, 1)) * 100}%`}
                            ></div>
                        </div>
                        <strong>{person.count.toLocaleString()}</strong>
                    </div>
                {/each}
            </div>
        </div>
    {/if}
</section>
