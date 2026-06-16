<script lang="ts">
    import { tick } from "svelte";
    import { goto } from "$app/navigation";
    import MessageCard from "$lib/components/MessageCard.svelte";
    import ChatsTab from "$lib/tabs/ChatsTab.svelte";
    import NicknamesTab from "$lib/tabs/NicknamesTab.svelte";
    import InteractionsTab from "$lib/tabs/InteractionsTab.svelte";
    import LinksTab from "$lib/tabs/LinksTab.svelte";
    import LanguageTab from "$lib/tabs/LanguageTab.svelte";
    import MessagesTab from "$lib/tabs/MessagesTab.svelte";
    import EmojiTab from "$lib/tabs/EmojiTab.svelte";
    import SearchTab from "$lib/tabs/SearchTab.svelte";
    import {
        apiUrl,
        fetchJson,
        type Overview,
        type TimePoint,
        type TopPeople,
    } from "$lib/api";
    import { formatRuntimeMtime, selectedCountLabel } from "$lib/formatters";
    import { toReactionImageUrl, toDisplayAttachmentUrl } from "$lib/mediaUrls";
    import "$lib/dashboard.css";

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
    type NicknamePersonGroup = {
        id: number;
        display_name: string;
        chats: NicknameChatGroup[];
    };
    type PeopleFilterOption = {
        name: string;
        ids: number[];
        color: string;
        avatar: string;
    };
    export let data: {
        overview: Overview;
        messagesOverTime: { granularity: string; points: TimePoint[] };
        topPeople: TopPeople;
        calendar: { points: Array<{ day: string; message_count: number }> };
        activityHeatmap: {
            points: Array<{
                weekday: number;
                hour: number;
                message_count: number;
            }>;
        };
        topChats: {
            items: Array<{
                id: number;
                name: string;
                theme_name: string;
                message_count: number;
            }>;
        };
        messagesByMonth: {
            points: Array<{ month: string; message_count: number }>;
        };
        messagesByHour: {
            points: Array<{ hour: number; message_count: number }>;
        };
        nameHistory: {
            chats: Array<{
                id: number;
                platform: string;
                source_name: string;
                current_name: string;
                platform_channel_id: string;
                previous_names: Array<{
                    previous_name: string | null;
                    new_name: string;
                    author_name?: string | null;
                    ts: string | null;
                }>;
                participants: Array<{
                    id: number;
                    display_name: string;
                    history: Array<{
                        previous_name: string | null;
                        new_name: string;
                        author_name?: string | null;
                        ts: string | null;
                    }>;
                }>;
            }>;
        };
        topThemes: {
            items: Array<{ id: number; name: string; message_count: number }>;
        };
        metadata: {
            people: Array<{ id: number; name: string; color: string; avatar: string }>;
            themes: Array<{ id: number; name: string; emoji: string }>;
            platforms: string[];
        };
        runtimeState: {
            db_path: string;
            db_exists: boolean;
            db_mtime_ns: number | null;
            config_dir: string;
            cached_signature: unknown;
            current_signature: unknown;
            up_to_date: boolean;
        };
        platformOverTime: {
            granularity: string;
            platforms: string[];
            points: Array<{
                bucket: string;
                counts: Record<string, number>;
            }>;
        };
        filters: {
            from: string;
            to: string;
            people: string;
            themes: string;
            platforms: string;
        };
    };

    const YEAR_START = 2014;
    const currentYear = new Date().getFullYear();
    const yearOptions = Array.from(
        { length: currentYear - YEAR_START + 1 },
        (_, index) => currentYear - index,
    );
    const yearRange = (year: number): { from: string; to: string } => ({
        from: `${year}-01-01`,
        to: `${year}-12-31`,
    });
    const selectedYearFromRange = (from: string, to: string): number | null => {
        if (!from || !to) return null;
        const fromMatch = /^(\d{4})-01-01$/.exec(from);
        const toMatch = /^(\d{4})-12-31$/.exec(to);
        if (!fromMatch || !toMatch) return null;
        const fromYear = Number(fromMatch[1]);
        const toYear = Number(toMatch[1]);
        return fromYear === toYear ? fromYear : null;
    };

    let fromDate = data.filters.from;
    let toDate = data.filters.to;
    let selectedPeople = data.filters.people
        ? data.filters.people.split(",").map(Number)
        : [];
    let selectedThemes = data.filters.themes
        ? data.filters.themes.split(",").map(Number)
        : [];
    let selectedPlatforms = data.filters.platforms
        ? data.filters.platforms.split(",")
        : [];
    let selectedYear: number | null = selectedYearFromRange(fromDate, toDate);
    let topLimit = 10;
    let activeTab:
        | "overview"
        | "language"
        | "chats"
        | "nicknames"
        | "links"
        | "interactions"
        | "emoji"
        | "search" = "overview";
    let peopleFilterOptions: PeopleFilterOption[] = [];
    let nicknamePeople: NicknamePersonGroup[] = [];
    let runtimeState = data.runtimeState;

    function updateFilters() {
        const params = new URLSearchParams();
        if (fromDate) params.set("from", fromDate);
        if (toDate) params.set("to", toDate);
        if (selectedPeople.length > 0)
            params.set("people", selectedPeople.join(","));
        if (selectedThemes.length > 0)
            params.set("themes", selectedThemes.join(","));
        if (selectedPlatforms.length > 0)
            params.set("platforms", selectedPlatforms.join(","));

        const path = params.toString() ? `?${params.toString()}` : "/";
        goto(path);
    }

    function currentFilterParams(): URLSearchParams {
        const params = new URLSearchParams();
        if (fromDate) params.set("from", fromDate);
        if (toDate) params.set("to", toDate);
        if (selectedPeople.length > 0)
            params.set("people", selectedPeople.join(","));
        if (selectedThemes.length > 0)
            params.set("themes", selectedThemes.join(","));
        if (selectedPlatforms.length > 0)
            params.set("platforms", selectedPlatforms.join(","));
        return params;
    }

    function openLanguageTab() {
        activeTab = "language";
    }

    function openOverviewTab() {
        activeTab = "overview";
    }

    function openChatsTab() {
        activeTab = "chats";
    }

    function openNicknamesTab() {
        activeTab = "nicknames";
    }

    let snippetMessages: Array<any> = [];
    let snippetTargetId: string | null = null;
    let snippetModalVisible = false;
    let snippetLoading = false;
    let snippetExpandLoading = false;
    let snippetError = "";
    let snippetChannelName: string | null = null;
    let snippetPlatform: string | null = null;
    let snippetSourceName: string | null = null;
    let snippetIsFull = false;
    let snippetTotalInChannel = 0;
    let snippetContext = 10;

    function closeSnippetModal() {
        snippetModalVisible = false;
        snippetMessages = [];
        snippetTargetId = null;
        snippetError = "";
        snippetChannelName = null;
        snippetPlatform = null;
        snippetSourceName = null;
        snippetIsFull = false;
        snippetTotalInChannel = 0;
        snippetContext = 10;
    }

    function _mapSnippetMessages(items: any[]) {
        return items.map((m: any) => ({
            ...m,
            attachment_url:
                toDisplayAttachmentUrl(m.attachment_url, m.attachment_preview) ||
                m.attachment_url,
        }));
    }

    async function expandContext(full: boolean) {
        if (!snippetTargetId) return;
        snippetExpandLoading = true;
        try {
            const newContext = full ? 500 : snippetContext + 25;
            const qs = `message_id=${encodeURIComponent(snippetTargetId)}&context=${newContext}${full ? "&full=true" : ""}`;
            const res = await fetch(apiUrl(`/api/message-window?${qs}`));
            if (!res.ok) return;
            const data = await res.json();
            snippetMessages = _mapSnippetMessages(data.items || []);
            snippetIsFull = data.is_full ?? false;
            snippetTotalInChannel = data.total_in_channel ?? 0;
            if (!full) snippetContext = newContext;
            await tick();
            const el = document.getElementById(
                `chatlog__message-container-${snippetTargetId}`,
            );
            if (el) el.scrollIntoView({ behavior: "smooth", block: "center" });
        } finally {
            snippetExpandLoading = false;
        }
    }

    async function openInContext(messageId: string) {
        if (!messageId) return;
        snippetLoading = true;
        snippetContext = 10;
        snippetError = "";
        try {
            // Try DB-driven JSON window (fast and reliable)
            const res = await fetch(
                apiUrl(
                    `/api/message-window?message_id=${encodeURIComponent(messageId)}&context=10`,
                ),
            );
            if (!res.ok) throw new Error(`Request failed: ${res.status}`);
            const data = await res.json();
            snippetMessages = _mapSnippetMessages(data.items || []);
            snippetChannelName = data.channel_name || null;
            snippetPlatform = data.platform || null;
            snippetSourceName = data.source_name || null;
            snippetIsFull = data.is_full ?? false;
            snippetTotalInChannel = data.total_in_channel ?? 0;
            snippetTargetId = messageId;
            snippetModalVisible = true;
            snippetLoading = false;
            // scroll to target element after insertion
            await tick();
            const el = document.getElementById(
                `chatlog__message-container-${messageId}`,
            );
            if (el) el.scrollIntoView({ behavior: "auto", block: "center" });
            return;
        } catch (err) {
            snippetLoading = false;
            // Fallback: try the existing message-context / anchored flow (opens new tab)
            try {
                const res2 = await fetchJson<{
                    url: string;
                    fragment?: string;
                }>(
                    `/api/message-context?message_id=${encodeURIComponent(messageId)}`,
                );
                if (res2?.url) {
                    const full =
                        apiUrl(res2.url) +
                        (res2.fragment ? `#${res2.fragment}` : "");
                    window.open(full, "_blank", "noopener");
                    return;
                }
            } catch (err2) {
                // ignore and proceed to anchored media fallback
            }
            const fallback = `/api/media-anchored?message_id=${encodeURIComponent(messageId)}`;
            window.open(
                apiUrl(fallback) + `#chatlog__message-container-${messageId}`,
                "_blank",
                "noopener",
            );
        }
    }

    function openLinksTab() {
        activeTab = "links";
    }

    function openInteractionsTab() {
        activeTab = "interactions";
    }

    $: fromDate = data.filters.from;
    $: toDate = data.filters.to;
    $: selectedYear = selectedYearFromRange(fromDate, toDate);
    $: selectedPeople = data.filters.people
        ? data.filters.people.split(",").map(Number)
        : [];
    $: selectedThemes = data.filters.themes
        ? data.filters.themes.split(",").map(Number)
        : [];
    $: selectedPlatforms = data.filters.platforms
        ? data.filters.platforms.split(",")
        : [];
    $: filterSignature = [
        fromDate,
        toDate,
        selectedPeople.join(","),
        selectedThemes.join(","),
        selectedPlatforms.join(","),
        String(topLimit),
    ].join("|");

    $: runtimeState = data.runtimeState;

    function togglePerson(ids: number[]) {
        const hasAnySelected = ids.some((id) => selectedPeople.includes(id));
        if (hasAnySelected) {
            selectedPeople = selectedPeople.filter((p) => !ids.includes(p));
        } else {
            selectedPeople = [...new Set([...selectedPeople, ...ids])];
        }
        updateFilters();
    }

    function toggleTheme(id: number) {
        if (selectedThemes.includes(id)) {
            selectedThemes = selectedThemes.filter((t) => t !== id);
        } else {
            selectedThemes = [...selectedThemes, id];
        }
        updateFilters();
    }

    function togglePlatform(platform: string) {
        if (selectedPlatforms.includes(platform)) {
            selectedPlatforms = selectedPlatforms.filter((p) => p !== platform);
        } else {
            selectedPlatforms = [...selectedPlatforms, platform];
        }
        updateFilters();
    }

    function selectYear(year: number | null) {
        if (year === null) {
            fromDate = "";
            toDate = "";
            selectedYear = null;
            updateFilters();
            return;
        }
        if (selectedYear === year) {
            selectYear(null);
            return;
        }
        const range = yearRange(year);
        fromDate = range.from;
        toDate = range.to;
        selectedYear = year;
        updateFilters();
    }

    $: nicknamePeople = (() => {
        const grouped = new Map<string, NicknamePersonGroup>();
        for (const chat of data.nameHistory.chats) {
            if (chat.platform !== "facebook") continue;
            for (const person of chat.participants) {
                if (!person.history.length) continue;
                const personKey = person.display_name
                    .trim()
                    .toLocaleLowerCase();
                const existing = grouped.get(personKey) ?? {
                    id: person.id,
                    display_name: person.display_name,
                    chats: [],
                };
                const existingChat = existing.chats.find(
                    (item) => item.id === chat.id,
                );
                if (existingChat) {
                    existingChat.history = [
                        ...existingChat.history,
                        ...person.history,
                    ];
                } else {
                    existing.chats.push({
                        id: chat.id,
                        current_name: chat.current_name,
                        platform: chat.platform,
                        source_name: chat.source_name,
                        history: [...person.history],
                    });
                }
                grouped.set(personKey, existing);
            }
        }
        const sortedPeople = [...grouped.values()].sort((a, b) =>
            a.display_name.localeCompare(b.display_name, undefined, {
                sensitivity: "base",
            }),
        );
        for (const person of sortedPeople) {
            person.chats.sort((a, b) =>
                a.current_name.localeCompare(b.current_name, undefined, {
                    sensitivity: "base",
                }),
            );
            for (const chat of person.chats) {
                const seen = new Set<string>();
                chat.history = chat.history
                    .filter((change) => {
                        const dedupeKey = `${change.ts ?? ""}|${change.new_name}|${change.author_name ?? ""}`;
                        if (seen.has(dedupeKey)) return false;
                        seen.add(dedupeKey);
                        return true;
                    })
                    .sort(
                        (a, b) =>
                            (a.ts ?? "").localeCompare(b.ts ?? "") ||
                            a.new_name.localeCompare(b.new_name, undefined, {
                                sensitivity: "base",
                            }),
                    );
            }
        }
        return sortedPeople;
    })();
    $: peopleFilterOptions = (() => {
        const grouped = new Map<string, PeopleFilterOption>();
        for (const person of data.metadata.people) {
            const key = person.name.trim().toLocaleLowerCase();
            const existing = grouped.get(key);
            if (existing) {
                existing.ids.push(person.id);
            } else {
                grouped.set(key, {
                    name: person.name,
                    ids: [person.id],
                    color: person.color || "",
                    avatar: person.avatar || "",
                });
            }
        }
        return [...grouped.values()]
            .map((option) => ({ ...option, ids: [...new Set(option.ids)] }))
            .sort((a, b) =>
                a.name.localeCompare(b.name, undefined, {
                    sensitivity: "base",
                }),
            );
    })();
</script>

<svelte:head>
    <title>gChat</title>
</svelte:head>

<main class="page">

    <section class="filters">
        <div class="filter-row">
        <div class="filter-group filter-card">
            <fieldset class="date-range-fieldset">
                <legend>
                    <span>Year</span>
                    <small>{selectedYear ?? "All years"}</small>
                </legend>
                <div class="year-list">
                    <button
                        type="button"
                        class="year-chip"
                        class:selected={selectedYear === null}
                        on:click={() => selectYear(null)}
                    >
                        All
                    </button>
                    {#each yearOptions as year}
                        <button
                            type="button"
                            class="year-chip"
                            class:selected={selectedYear === year}
                            on:click={() => selectYear(year)}
                        >
                            {year}
                        </button>
                    {/each}
                </div>
            </fieldset>
        </div>

        <div class="filter-group filter-card">
            <fieldset class="date-range-fieldset">
                <legend>
                    <span>Platforms</span>
                    <small
                        >{selectedCountLabel(
                            selectedPlatforms.length,
                            data.metadata.platforms.length,
                        )}</small
                    >
                </legend>
                <div class="year-list platform-chips">
                    {#each data.metadata.platforms as platform}
                        {@const isSelected = selectedPlatforms.includes(platform)}
                        {@const platformColor = { discord: "#5865f2", facebook: "#1877f2", signal: "#3a76f0" }[platform] ?? "#2563eb"}
                        <button
                            type="button"
                            class="platform-chip"
                            class:selected={isSelected}
                            style="--platform-color: {platformColor}"
                            on:click={() => togglePlatform(platform)}
                            aria-pressed={isSelected}
                            title={platform}
                        >
                            <span class="platform-chip-icon" data-platform={platform}></span>
                            <span class="platform-chip-name">{platform}</span>
                        </button>
                    {/each}
                </div>
            </fieldset>
        </div>

        <div class="filter-group filter-card">
            <fieldset class="date-range-fieldset">
                <legend>
                    <span>People</span>
                    <small
                        >{selectedCountLabel(
                            selectedPeople.length,
                            peopleFilterOptions.length,
                        )}</small
                    >
                </legend>
                <div class="chip-list">
                    {#each peopleFilterOptions as person}
                        {@const isSelected = person.ids.some((id) => selectedPeople.includes(id))}
                        <button
                            type="button"
                            class="person-chip"
                            class:selected={isSelected}
                            style="--person-color: {person.color || '#94a3b8'}"
                            on:click={() => togglePerson(person.ids)}
                            aria-pressed={isSelected}
                        >
                            {#if person.avatar}
                                <img
                                    class="person-chip-avatar"
                                    src={person.avatar}
                                    alt={person.name}
                                    loading="lazy"
                                />
                            {:else}
                                <span
                                    class="person-chip-avatar person-chip-initials"
                                    style="background: {person.color || '#475569'}"
                                >{person.name.charAt(0).toUpperCase()}</span>
                            {/if}
                            <span class="person-chip-name">{person.name}</span>
                        </button>
                    {/each}
                </div>
            </fieldset>
        </div>

        <div class="filter-group filter-card">
            <fieldset class="date-range-fieldset">
                <legend>
                    <span>Limit</span>
                    <small>{topLimit}</small>
                </legend>
                <div class="year-list">
                    {#each [5, 10, 25, 50] as n}
                        <button
                            type="button"
                            class="year-chip"
                            class:selected={topLimit === n}
                            on:click={() => (topLimit = n)}
                        >{n}</button>
                    {/each}
                </div>
            </fieldset>
        </div>

        </div><!-- /.filter-row -->

        <div class="filter-themes-row">
            <fieldset class="date-range-fieldset">
                <legend>
                    <span>Themes</span>
                    <small
                        >{selectedCountLabel(
                            selectedThemes.length,
                            data.metadata.themes.length,
                        )}</small
                    >
                </legend>
                <div class="chip-list">
                    {#each data.metadata.themes as theme}
                        {@const isSelected = selectedThemes.includes(theme.id)}
                        <button
                            type="button"
                            class="year-chip theme-chip"
                            class:selected={isSelected}
                            on:click={() => toggleTheme(theme.id)}
                            aria-pressed={isSelected}
                            title={theme.name}
                        >
                            {#if theme.emoji}
                                <span class="theme-chip-emoji">{theme.emoji}</span>
                            {/if}
                            <span class="theme-chip-name">{theme.name}</span>
                        </button>
                    {/each}
                </div>
            </fieldset>
        </div>
    </section>

    <section class="tabs">
        <button
            class:active={activeTab === "overview"}
            type="button"
            on:click={() => (activeTab = "overview")}>Messages</button
        >
        <button
            class:active={activeTab === "language"}
            type="button"
            on:click={openLanguageTab}>Language</button
        >
        <button
            class:active={activeTab === "links"}
            type="button"
            on:click={openLinksTab}>Links</button
        >
        <button
            class:active={activeTab === "chats"}
            type="button"
            on:click={openChatsTab}>Chats</button
        >
        <button
            class:active={activeTab === "nicknames"}
            type="button"
            on:click={openNicknamesTab}>Nicknames</button
        >
        <button
            class:active={activeTab === "interactions"}
            type="button"
            on:click={openInteractionsTab}>Interactions</button
        >
        <button
            class:active={activeTab === "emoji"}
            type="button"
            on:click={() => (activeTab = "emoji")}>Emoji</button
        >
        <button
            class:active={activeTab === "search"}
            type="button"
            on:click={() => (activeTab = "search")}>Search</button
        >
    </section>

    <MessagesTab
        active={activeTab === "overview"}
        {filterSignature}
        {currentFilterParams}
        {topLimit}
        baseData={{
            overview: data.overview,
            topPeople: data.topPeople,
            calendar: data.calendar,
            activityHeatmap: data.activityHeatmap,
            messagesByMonth: data.messagesByMonth,
            messagesByHour: data.messagesByHour,
            topChats: data.topChats,
            topThemes: data.topThemes,
            platformOverTime: data.platformOverTime,
        }}
    />

    <LanguageTab
        active={activeTab === "language"}
        {filterSignature}
        {currentFilterParams}
        {topLimit}
        {openInContext}
    />

    <LinksTab
        active={activeTab === "links"}
        {filterSignature}
        {currentFilterParams}
        {topLimit}
        {openInContext}
    />

    <InteractionsTab
        active={activeTab === "interactions"}
        {filterSignature}
        {currentFilterParams}
        {topLimit}
        {openInContext}
        resolveReactionImage={toReactionImageUrl}
    />

    <ChatsTab chats={data.nameHistory.chats} active={activeTab === "chats"} />

    <NicknamesTab people={nicknamePeople} active={activeTab === "nicknames"} />

    <EmojiTab
        active={activeTab === "emoji"}
        {filterSignature}
        {currentFilterParams}
        {topLimit}
    />

    <SearchTab
        active={activeTab === "search"}
        {currentFilterParams}
        {openInContext}
    />
    {#if snippetModalVisible}
        <div
            class="snippet-modal-backdrop"
            role="button"
            tabindex="0"
            aria-label="Close message context"
            on:click={() => closeSnippetModal()}
            on:keydown={(e) => {
                if (e.key === "Escape" || e.key === "Enter" || e.key === " ") {
                    e.preventDefault();
                    closeSnippetModal();
                }
            }}
        >
            <div
                class="snippet-modal"
                role="dialog"
                aria-modal="true"
                tabindex="-1"
                on:click|stopPropagation
                on:keydown|stopPropagation
            >
                <div class="snippet-modal-header">
                    {#if snippetChannelName}
                        <div class="snippet-channel-info">
                            {#if snippetPlatform}
                                <span class="platform-chip-icon snippet-platform-icon" data-platform={snippetPlatform}></span>
                            {/if}
                            <strong class="snippet-channel-name">{snippetChannelName}</strong>
                            {#if snippetSourceName && snippetSourceName !== snippetChannelName}
                                <span class="muted snippet-source-name">{snippetSourceName}</span>
                            {/if}
                        </div>
                    {/if}
                    <button class="close" on:click={closeSnippetModal}>✕</button>
                </div>
                {#if snippetLoading}
                    <p class="muted">Loading...</p>
                {:else if snippetError}
                    <p class="muted">{snippetError}</p>
                {:else}
                    <div class="snippet-body">
                        {#each snippetMessages as message}
                            <MessageCard
                                {message}
                                highlight={message.id === snippetTargetId}
                                resolveReactionImage={toReactionImageUrl}
                                large={true}
                            />
                        {/each}
                    </div>
                    {#if !snippetIsFull}
                        <div class="snippet-expand-row">
                            <button
                                type="button"
                                class="snippet-expand-btn"
                                on:click={() => expandContext(false)}
                                disabled={snippetExpandLoading}
                            >
                                {snippetExpandLoading ? "Loading…" : "Show more context"}
                            </button>
                            <button
                                type="button"
                                class="snippet-expand-btn"
                                on:click={() => expandContext(true)}
                                disabled={snippetExpandLoading}
                            >
                                Show full conversation ({snippetTotalInChannel} messages)
                            </button>
                        </div>
                    {/if}
                {/if}
            </div>
        </div>
    {/if}
</main>

<footer class="runtime-footer">
    <div class="runtime-footer-inner">
        <strong>Runtime</strong>
        <span
            class:runtime-ok={runtimeState.up_to_date}
            class:runtime-stale={!runtimeState.up_to_date}
        >
            {runtimeState.up_to_date ? "Up to date" : "Needs refresh"}
        </span>
        <span class="runtime-footer-mtime">
            DB updated {formatRuntimeMtime(runtimeState.db_mtime_ns)}
        </span>
    </div>
</footer>
