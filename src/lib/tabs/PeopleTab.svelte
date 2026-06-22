<script lang="ts">
    import TooltipHeader from "$lib/components/TooltipHeader.svelte";
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

    function formatMtld(value: number): string {
        return value.toFixed(1);
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
        {#if loading}
            <p class="muted">Loading diversity stats…</p>
        {:else if error}
            <p class="muted">{error}</p>
        {:else if items.length === 0}
            <p class="muted">No diversity data found.</p>
        {:else}
            <p class="metric-hint">Hover column headers for metric definitions.</p>
            <div class="diversity-table-wrap">
                <table class="diversity-table">
                    <thead>
                        <tr>
                            <th class="diversity-person-header">Person</th>
                            <TooltipHeader
                                title="Total messages from this person in the current filter."
                                >Messages</TooltipHeader
                            >
                            <TooltipHeader
                                title="Distinct words used (3+ letters, common stop words removed)."
                                >Unique words</TooltipHeader
                            >
                            <TooltipHeader
                                title="Measure of Textual Lexical Diversity. Average length of word sequences before vocabulary repeats, adjusted for corpus length. Higher values mean richer, less repetitive vocabulary."
                                >MTLD</TooltipHeader
                            >
                            <TooltipHeader
                                title="Shannon entropy of word usage. Higher values mean word choice is spread more evenly across the vocabulary."
                                >Entropy</TooltipHeader
                            >
                            <TooltipHeader
                                title="Number of distinct chats this person sent messages in."
                                >Chats</TooltipHeader
                            >
                            <TooltipHeader
                                title="Number of distinct themes this person participated in."
                                >Themes</TooltipHeader
                            >
                            <TooltipHeader
                                title="Number of distinct platforms this person used."
                                >Platforms</TooltipHeader
                            >
                            <TooltipHeader
                                title="Herfindahl index of message share across chats. Near 1.0 means most messages are concentrated in one chat; lower values mean broader participation."
                                >Chat focus</TooltipHeader
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
                                    class="hover-title"
                                    title={`${person.total_words.toLocaleString()} total words`}
                                    >{formatMtld(person.mtld)}</td
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

    .diversity-table-wrap {
        margin-top: 12px;
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
        text-align: center;
        white-space: nowrap;
        vertical-align: middle;
    }

    .diversity-person-header,
    .diversity-name {
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
