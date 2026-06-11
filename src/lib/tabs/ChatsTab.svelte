<script lang="ts">
    import {
        formatAbsoluteTime,
        formatRelativeTime,
    } from "$lib/formatters";
    import {
        groupChangesByAuthor,
        isMeaningfulPrevious,
    } from "$lib/historyChanges";

    type ChatChange = {
        previous_name: string | null;
        new_name: string;
        author_name?: string | null;
        ts: string | null;
    };

    export let chats: Array<{
        id: number;
        platform: string;
        source_name: string;
        current_name: string;
        platform_channel_id: string;
        previous_names: ChatChange[];
    }>;
    export let active: boolean;
</script>

<section class="history" class:tab-hidden={!active}>
    {#each chats as chat}
        <details class="panel history-card">
            <summary>
                <div>
                    <strong>{chat.current_name}</strong>
                    <span>{chat.platform}</span>
                </div>
                <span class="history-counts">
                    {chat.previous_names.length
                        ? `${chat.previous_names.length} chat changes`
                        : ""}
                </span>
            </summary>

            {#if chat.previous_names.length}
                <div class="history-group">
                    <h3>Chat name history</h3>
                    <ol class="history-timeline">
                        {#each groupChangesByAuthor(chat.previous_names) as group}
                            <li class="history-timeline-item">
                                <div class="history-meta">
                                    {#if group.author_name}
                                        <strong>{group.author_name}</strong>
                                    {:else}
                                        <strong class="muted"
                                            >Unknown actor</strong
                                        >
                                    {/if}
                                    <span
                                        class="muted"
                                        title={formatAbsoluteTime(group.ts)}
                                    >
                                        {formatRelativeTime(group.ts)}
                                    </span>
                                </div>
                                <div class="history-changes">
                                    {#each group.changes as change}
                                        <div class="history-change">
                                            {#if isMeaningfulPrevious(change.previous_name, change.new_name)}
                                                <span class="prev"
                                                    >{change.previous_name}</span
                                                >
                                                <span class="arrow">→</span>
                                            {/if}
                                            <span class="next"
                                                >{change.new_name}</span
                                            >
                                        </div>
                                    {/each}
                                </div>
                            </li>
                        {/each}
                    </ol>
                </div>
            {/if}
        </details>
    {/each}
</section>

