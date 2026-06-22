<script lang="ts">
    import { fetchJson, type PersonDiversity } from "$lib/api";

    type PersonDiversityItem = PersonDiversity["items"][number];

    export let active = false;
    export let filterSignature: string;
    export let currentFilterParams: () => URLSearchParams;
    export let topLimit: number = 10;

    let items: PersonDiversityItem[] = [];
    let loading = false;
    let error = "";
    let loadedFilterKey = "";

    $: if (
        active &&
        !loading &&
        loadedFilterKey !== filterSignature + topLimit
    ) {
        void loadData();
    }

    async function loadData() {
        loading = true;
        error = "";
        try {
            const params = currentFilterParams();
            params.set("limit", String(Math.max(topLimit, 10)));
            const data = await fetchJson<PersonDiversity>(
                `/api/person-diversity?${params.toString()}`,
            );
            items = data.items ?? [];
            loadedFilterKey = filterSignature + topLimit;
        } catch (e: unknown) {
            error =
                e instanceof Error
                    ? e.message
                    : "Failed to load diversity stats";
        } finally {
            loading = false;
        }
    }

    function formatTtr(value: number): string {
        return `${(value * 100).toFixed(2)}%`;
    }

    function formatEntropy(value: number): string {
        return value.toFixed(2);
    }

    function formatHhi(value: number): string {
        return value.toFixed(2);
    }
</script>

<section class="people-tab" class:tab-hidden={!active}>
    <div class="panel diversity-panel">
        <h2>Message diversity</h2>
        <!-- <p class="muted diversity-lede">
            Vocabulary breadth (unique words, type-token ratio, entropy) and
            participation spread (channels, themes, platforms). Chat focus near
            1.0 means most messages are in one chat.
        </p> -->
        {#if loading}
            <p class="muted">Loading diversity stats…</p>
        {:else if error}
            <p class="muted">{error}</p>
        {:else if items.length === 0}
            <p class="muted">No diversity data found.</p>
        {:else}
            <div class="diversity-table-wrap">
                <table class="diversity-table">
                    <thead>
                        <tr>
                            <th>Person</th>
                            <th>Messages</th>
                            <th>Unique words</th>
                            <th
                                title="The type-token ratio (TTR) is a statistical measure used in corpus linguistics to quantify the lexical diversity of a text or a corpus. It is calculated by dividing the number of unique words (types) by the total number of words (tokens) in a given text."
                                >TTR</th
                            >
                            <th
                                title="Unpredictability (entropy) is a measure of the randomness or unpredictability of a system. In the context of chat diversity, it quantifies how well the system can predict the next word or message based on the previous ones."
                                >Entropy</th
                            >
                            <th>Chats</th>
                            <th>Themes</th>
                            <th>Platforms</th>
                            <th
                                title="Higher = more concentrated in fewer chats"
                                >Chat focus</th
                            >
                        </tr>
                    </thead>
                    <tbody>
                        {#each items as person}
                            <tr>
                                <td class="diversity-name">
                                    {#if person.avatar}
                                        <img
                                            class="diversity-avatar"
                                            src={person.avatar}
                                            alt={person.display_name}
                                            loading="lazy"
                                        />
                                    {:else}
                                        <span
                                            class="diversity-avatar diversity-avatar-initials"
                                            style={`background:${person.color || "#475569"}`}
                                            >{person.display_name
                                                .charAt(0)
                                                .toUpperCase()}</span
                                        >
                                    {/if}
                                    <span
                                        style={`color:${person.color || "#fff"}`}
                                        >{person.display_name}</span
                                    >
                                </td>
                                <td>{person.message_count.toLocaleString()}</td>
                                <td>{person.unique_words.toLocaleString()}</td>
                                <td
                                    title={`${person.total_words.toLocaleString()} total words`}
                                    >{formatTtr(person.ttr)}</td
                                >
                                <td>{formatEntropy(person.word_entropy)}</td>
                                <td>{person.channel_count.toLocaleString()}</td>
                                <td>{person.theme_count.toLocaleString()}</td>
                                <td>{person.platform_count.toLocaleString()}</td
                                >
                                <td>{formatHhi(person.channel_hhi)}</td>
                            </tr>
                        {/each}
                    </tbody>
                </table>
            </div>
        {/if}
    </div>
</section>

<style>
    .people-tab {
        margin-top: 16px;
    }

    .diversity-lede {
        margin-top: 8px;
        max-width: 72ch;
    }

    .diversity-table-wrap {
        margin-top: 16px;
        overflow-x: auto;
    }

    .diversity-table {
        width: 100%;
        border-collapse: collapse;
        font-size: 0.85rem;
    }

    .diversity-table th,
    .diversity-table td {
        padding: 10px 8px;
        border-bottom: 1px solid #1f2937;
        text-align: right;
        white-space: nowrap;
    }

    .diversity-table th:first-child,
    .diversity-table td:first-child {
        text-align: left;
    }

    .diversity-table th {
        color: #94a3b8;
        font-size: 0.75rem;
        font-weight: 600;
        text-transform: uppercase;
        letter-spacing: 0.04em;
    }

    .diversity-name {
        display: flex;
        align-items: center;
        gap: 8px;
        min-width: 140px;
    }

    .diversity-avatar {
        width: 22px;
        height: 22px;
        border-radius: 50%;
        flex-shrink: 0;
        object-fit: cover;
    }

    .diversity-avatar-initials {
        display: inline-flex;
        align-items: center;
        justify-content: center;
        font-size: 0.7rem;
        font-weight: 700;
        color: #fff;
    }
</style>
