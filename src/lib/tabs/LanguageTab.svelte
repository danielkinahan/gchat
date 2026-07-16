<script lang="ts">
    import LinkPreview from "$lib/LinkPreview.svelte";
    import { fetchJson } from "$lib/api";
    import {
        extractMessageLinks,
        isUrlOnlyMessage,
    } from "$lib/messageLinks";

    type WordExample = {
        id: string;
        ts: string | null;
        content: string;
        person_name: string;
        person_color: string;
        channel_name: string;
        source_name: string;
    };

    type FirstMessage = WordExample | null;

    type WordBreakdown = {
        word: string;
        people: Array<{
            id: number;
            display_name: string;
            color: string;
            count: number;
        }>;
        chats: Array<{
            id: number;
            name: string;
            source_name: string;
            count: number;
        }>;
    };

    type WordOverTime = {
        word: string;
        points: Array<{
            month: string;
            count: number;
            total_words: number;
            percent: number;
        }>;
    };

    export let active: boolean;
    export let filterSignature: string;
    export let currentFilterParams: () => URLSearchParams;
    export let topLimit: number = 10;
    export let openInContext: (messageId: string) => void;

    const WORD_LIST_LIMIT = 500;

    let isLoadingWords = false;
    let isLoadingBreakdown = false;
    let error = "";
    let wordSearch = "";
    let wordsRequestId = 0;
    let topWords: Array<{ word: string; count: number }> = [];
    let loadedWordsFilterKey: string | null = null;
    let selectedWord = "";
    let wordBreakdown: WordBreakdown = { word: "", people: [], chats: [] };
    let wordOverTime: WordOverTime = { word: "", points: [] };
    let isLoadingOverTime = false;
    let showWordExamples = false;
    let isLoadingExamples = false;
    let isAppendingExamples = false;
    let wordExamples: WordExample[] = [];
    let hasMoreWordExamples = false;
    let wordFirstMessage: FirstMessage = null;

    $: normalizedWordSearch = wordSearch.trim().toLowerCase();
    $: filteredWords = normalizedWordSearch
        ? topWords.filter((item) => item.word.includes(normalizedWordSearch))
        : topWords;
    $: visibleWords = filteredWords.slice(0, topLimit);

    function maxCount(items: Array<{ count: number }>): number {
        return Math.max(...items.map((item) => item.count || 0), 1);
    }

    async function loadTopWords() {
        const filterKey = filterSignature;
        if (isLoadingWords || loadedWordsFilterKey === filterKey) return;
        const requestId = ++wordsRequestId;
        isLoadingWords = true;
        error = "";
        try {
            const params = currentFilterParams();
            params.set("all", "true");
            const response = await fetchJson<{
                items: Array<{ word: string; count: number }>;
            }>(`/api/top-words?${params.toString()}`);
            if (requestId !== wordsRequestId) return;
            topWords = response.items;
            if (
                topWords.length > 0 &&
                (!selectedWord ||
                    !topWords.some((item) => item.word === selectedWord))
            ) {
                selectedWord = topWords[0].word;
            }
            if (selectedWord) {
                await loadWordBreakdown(selectedWord);
            }
        } catch (err) {
            if (requestId !== wordsRequestId) return;
            error = err instanceof Error ? err.message : "Failed to load words";
        } finally {
            loadedWordsFilterKey = filterKey;
            isLoadingWords = false;
        }
    }

    async function loadWordBreakdown(word: string) {
        if (!word) return;
        isLoadingBreakdown = true;
        isLoadingOverTime = true;
        error = "";
        try {
            const params = currentFilterParams();
            params.set("word", word);
            params.set("limit", String(topLimit));
            const otParams = currentFilterParams();
            otParams.set("word", word);
            const [breakdown, overTime] = await Promise.all([
                fetchJson<WordBreakdown>(`/api/word-breakdown?${params.toString()}`),
                fetchJson<WordOverTime>(`/api/word-over-time?${otParams.toString()}`),
            ]);
            wordBreakdown = breakdown;
            wordOverTime = overTime;
        } catch (err) {
            error =
                err instanceof Error
                    ? err.message
                    : "Failed to load word details";
            wordBreakdown = { word: word.toLowerCase(), people: [], chats: [] };
            wordOverTime = { word: word.toLowerCase(), points: [] };
        } finally {
            isLoadingBreakdown = false;
            isLoadingOverTime = false;
        }
    }

    async function selectWord(word: string) {
        selectedWord = word;
        showWordExamples = false;
        wordExamples = [];
        hasMoreWordExamples = false;
        wordFirstMessage = null;
        await loadWordBreakdown(word);
    }

    $: chartPoints = wordOverTime.points;
    $: chartMax = Math.max(...chartPoints.map((p) => p.percent), 0.01);
    $: labelStep = Math.max(1, Math.ceil(chartPoints.length / 7));
    $: peopleMaxCount = maxCount(wordBreakdown.people);
    $: chatMaxCount = maxCount(wordBreakdown.chats);

    function formatPercent(value: number): string {
        if (value >= 10) return `${value.toFixed(1)}%`;
        if (value >= 1) return `${value.toFixed(2)}%`;
        return `${value.toFixed(3)}%`;
    }

    async function loadWordExamples(offset = 0, append = false) {
        if (!selectedWord) return;
        if (append) {
            isAppendingExamples = true;
        } else if (showWordExamples) {
            showWordExamples = false;
            return;
        } else {
            showWordExamples = true;
            if (wordExamples.length > 0) return;
            isLoadingExamples = true;
        }
        error = "";
        try {
            const params = currentFilterParams();
            params.set("word", selectedWord);
            params.set("limit", offset > 0 ? "5" : "6");
            if (offset > 0) params.set("offset", String(offset));
            const response = await fetchJson<{
                word: string;
                has_more: boolean;
                first_message: FirstMessage;
                messages: WordExample[];
            }>(`/api/word-examples?${params.toString()}`);
            if (response.word === selectedWord) {
                wordExamples = append
                    ? [...wordExamples, ...response.messages]
                    : response.messages;
                hasMoreWordExamples = response.has_more;
                if (!append) wordFirstMessage = response.first_message ?? null;
            }
        } catch (err) {
            error =
                err instanceof Error
                    ? err.message
                    : "Failed to load example messages";
            showWordExamples = false;
        } finally {
            isLoadingExamples = false;
            isAppendingExamples = false;
        }
    }

    async function toggleWordExamples() {
        if (showWordExamples) {
            showWordExamples = false;
            return;
        }
        await loadWordExamples();
    }

    async function showMoreWordExamples() {
        const oldScroll = typeof window !== "undefined" ? window.scrollY : 0;
        const oldHeight =
            typeof document !== "undefined"
                ? document.documentElement.scrollHeight
                : 0;
        await loadWordExamples(wordExamples.length, true);
        const newHeight =
            typeof document !== "undefined"
                ? document.documentElement.scrollHeight
                : 0;
        const delta = newHeight - oldHeight;
        if (typeof window !== "undefined" && delta !== 0) {
            window.scrollTo({ top: oldScroll + delta });
        }
    }

    $: if (
        active &&
        !isLoadingWords &&
        filteredWords.length > 0 &&
        !filteredWords.some((item) => item.word === selectedWord)
    ) {
        void selectWord(filteredWords[0].word);
    }

    $: if (
        active &&
        !isLoadingWords &&
        loadedWordsFilterKey !== filterSignature
    ) {
        void loadTopWords();
    }
</script>

<section class="language" class:tab-hidden={!active}>
    <div class="panel language-words">
        <h2>Most used words</h2>
        <input
            type="text"
            placeholder="Filter words..."
            bind:value={wordSearch}
            autocomplete="off"
            autocapitalize="off"
            spellcheck="false"
            data-lpignore="true"
            data-1p-ignore="true"
            class="word-filter"
        />
        {#if isLoadingWords}
            <p class="muted">Loading words...</p>
        {:else}
            <div class="word-list">
                {#each visibleWords as item}
                    <button
                        type="button"
                        class="word-row"
                        class:selected={selectedWord === item.word}
                        on:click={() => selectWord(item.word)}
                    >
                        <span>{item.word}</span>
                        <strong>{item.count.toLocaleString()}</strong>
                    </button>
                {/each}
            </div>
            {#if filteredWords.length > visibleWords.length}
                <p class="muted">
                    Showing {visibleWords.length.toLocaleString()} of {filteredWords.length.toLocaleString()}
                    words.
                </p>
            {/if}
        {/if}
    </div>

    <div class="panel language-detail">
        <h2>"{selectedWord || "Select a word"}"</h2>
        {#if error}
            <p class="muted">{error}</p>
        {:else if isLoadingBreakdown}
            <p class="muted">Loading breakdown...</p>
        {:else}
            <div class="detail-columns">
                <div class="detail-bar-panel">
                    <h3>Top authors</h3>
                    <div class="bar-list">
                        {#each wordBreakdown.people as person}
                            <div class="bar-row">
                                <span>{person.display_name}</span>
                                <div
                                    class="mini-bar-track hover-title"
                                    title={`${person.display_name}: ${person.count.toLocaleString()}`}
                                >
                                    <div
                                        class="mini-bar"
                                        style={`width:${(person.count / peopleMaxCount) * 100}% ; background:${person.color}`}
                                    ></div>
                                </div>
                                <strong>{person.count.toLocaleString()}</strong>
                            </div>
                        {/each}
                    </div>
                </div>
                <div class="detail-bar-panel">
                    <h3>Top chats</h3>
                    <div class="bar-list">
                        {#each wordBreakdown.chats as chat}
                            <div class="bar-row">
                                <span>{chat.name}</span>
                                <div
                                    class="mini-bar-track hover-title"
                                    title={`${chat.name}: ${chat.count.toLocaleString()}`}
                                >
                                    <div
                                        class="mini-bar chat"
                                        style={`width:${(chat.count / chatMaxCount) * 100}%`}
                                    ></div>
                                </div>
                                <strong>{chat.count.toLocaleString()}</strong>
                            </div>
                        {/each}
                    </div>
                </div>
            </div>
            {#if chartPoints.length > 0}
                <div class="wt-section">
                    <h3>Usage over time</h3>
                    <div class="wt-plot">
                        <div class="wt-chart">
                            {#each chartPoints as point}
                                <div
                                    class="wt-bar-wrap hover-title"
                                    title={`${new Date(point.month).toLocaleDateString("en-US", { month: "short", year: "numeric" })}: ${formatPercent(point.percent)} (${point.count.toLocaleString()} of ${point.total_words.toLocaleString()} words)`}
                                >
                                    <div class="wt-bar-slot">
                                        <div
                                            class="wt-bar"
                                            style={`height:${point.percent > 0 ? (point.percent / chartMax) * 100 : 0}%`}
                                        ></div>
                                    </div>
                                </div>
                            {/each}
                        </div>
                        <div class="wt-label-row">
                            {#each chartPoints as point, i}
                                <span class="wt-label">
                                    {i % labelStep === 0
                                        ? new Date(point.month).toLocaleDateString("en-US", { month: "short", year: "2-digit" })
                                        : ""}
                                </span>
                            {/each}
                        </div>
                    </div>
                </div>
            {/if}
            {#if selectedWord}
                <button
                    type="button"
                    class="examples-toggle"
                    on:click={toggleWordExamples}
                >
                    {showWordExamples
                        ? "Hide example messages"
                        : "Show example messages"}
                </button>
            {/if}
            {#if showWordExamples}
                {#if isLoadingExamples && !isAppendingExamples}
                    <p class="muted">Loading examples...</p>
                {:else}
                    {#if wordFirstMessage}
                        <div class="first-usage-banner">
                            <span class="first-usage-label">First usage</span>
                            <div class="example-meta">
                                <strong style={`color:${wordFirstMessage.person_color}`}>{wordFirstMessage.person_name}</strong>
                                <span>{wordFirstMessage.channel_name}</span>
                                <time>{wordFirstMessage.ts ? new Date(wordFirstMessage.ts).toLocaleString() : "N/A"}</time>
                                <button type="button" class="show-context" on:click={() => openInContext(wordFirstMessage!.id)}>Show in context</button>
                            </div>
                            {#if wordFirstMessage.content && !isUrlOnlyMessage(wordFirstMessage.content)}
                                <p>{wordFirstMessage.content}</p>
                            {/if}
                        </div>
                    {/if}
                    {#if wordExamples.length === 0 && !wordFirstMessage}
                        <p class="muted">No example messages found.</p>
                    {/if}
                    {#if wordExamples.length > 0}
                        <div class="example-list">
                            {#each wordExamples as message}
                                <div class="example-message">
                                    <div class="example-meta">
                                        <strong style={`color:${message.person_color}`}>{message.person_name}</strong>
                                        <span>{message.channel_name}</span>
                                        <time>{message.ts ? new Date(message.ts).toLocaleString() : "N/A"}</time>
                                        <button
                                            type="button"
                                            class="show-context"
                                            on:click={() => openInContext(message.id)}
                                        >Show in context</button>
                                    </div>
                                    {#if message.content && !isUrlOnlyMessage(message.content)}
                                        <p>{message.content}</p>
                                    {/if}
                                    {#each extractMessageLinks(message.content) as link (link)}
                                        <LinkPreview url={link} />
                                    {/each}
                                </div>
                            {/each}
                        </div>
                        {#if hasMoreWordExamples}
                            <button
                                type="button"
                                class="examples-toggle"
                                on:click={showMoreWordExamples}
                                disabled={isAppendingExamples}
                            >
                                {isAppendingExamples ? "Loading..." : "Show 5 more messages"}
                            </button>
                        {/if}
                    {/if}
                {/if}
            {/if}
        {/if}
    </div>
</section>
