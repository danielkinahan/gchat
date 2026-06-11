<script lang="ts">
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
                            <div class="history-list">
                                {#each chat.history as change}
                                    <div class="history-row">
                                        <div class="history-change">
                                            {change.new_name === "(cleared)"
                                                ? "cleared"
                                                : change.new_name}
                                            {#if change.author_name}
                                                <span class="muted">
                                                    · by {change.author_name}
                                                </span>
                                            {/if}
                                        </div>
                                        <time
                                            >{change.ts
                                                ? new Date(
                                                      change.ts,
                                                  ).toLocaleString()
                                                : "N/A"}</time
                                        >
                                    </div>
                                {/each}
                            </div>
                        </div>
                    {/each}
                </div>
            </details>
        {/each}
    {/if}
</section>
