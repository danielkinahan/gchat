<script lang="ts">
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
                    <div class="history-list">
                        {#each chat.previous_names as change}
                            <div class="history-row">
                                <div class="history-change">
                                    {change.new_name}
                                    {#if change.author_name}
                                        <span class="muted">
                                            · by {change.author_name}</span
                                        >
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
            {/if}
        </details>
    {/each}
</section>
