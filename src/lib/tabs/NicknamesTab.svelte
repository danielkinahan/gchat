<script lang="ts">
    import {
        formatAbsoluteTime,
        formatRelativeTime,
    } from "$lib/formatters";
    import {
        groupChangesByAuthor,
        isMeaningfulPrevious,
    } from "$lib/historyChanges";

    type NicknameChange = {
        previous_name?: string | null;
        new_name: string;
        author_name?: string | null;
        ts: string | null;
    };

    type NicknameChatGroup = {
        id: number;
        current_name: string;
        platform: string;
        source_name: string;
        history: NicknameChange[];
    };

    export let people: Array<{
        id: number;
        display_name: string;
        chats: NicknameChatGroup[];
    }>;
    export let active: boolean;
</script>

<section class="history" class:tab-hidden={!active}>
    {#if people.length === 0}
        <div class="panel">
            <p class="muted">No nickname changes found.</p>
        </div>
    {:else}
        {#each people as person}
            <details class="panel history-card">
                <summary>
                    <div>
                        <strong>{person.display_name}</strong>
                        <span>{person.chats.length} chats</span>
                    </div>
                    <span class="history-counts">
                        {person.chats.reduce(
                            (sum, chat) => sum + chat.history.length,
                            0,
                        )} nickname changes
                    </span>
                </summary>
                <div class="participant-list">
                    {#each person.chats as chat}
                        <div class="participant-card">
                            <strong>{chat.current_name}</strong>
                            <span class="muted">{chat.platform}</span>
                            <ol class="history-timeline">
                                {#each groupChangesByAuthor(chat.history) as group}
                                    <li class="history-timeline-item">
                                        <div class="history-meta">
                                            {#if group.author_name}
                                                <strong
                                                    >{group.author_name}</strong
                                                >
                                            {:else}
                                                <strong class="muted"
                                                    >Unknown actor</strong
                                                >
                                            {/if}
                                            <span
                                                class="muted"
                                                title={formatAbsoluteTime(
                                                    group.ts,
                                                )}
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
                                                        <span class="arrow"
                                                            >→</span
                                                        >
                                                    {/if}
                                                    {#if change.new_name === "(cleared)"}
                                                        <span
                                                            class="next muted"
                                                            >cleared</span
                                                        >
                                                    {:else}
                                                        <span class="next"
                                                            >{change.new_name}</span
                                                        >
                                                    {/if}
                                                </div>
                                            {/each}
                                        </div>
                                    </li>
                                {/each}
                            </ol>
                        </div>
                    {/each}
                </div>
            </details>
        {/each}
    {/if}
</section>

