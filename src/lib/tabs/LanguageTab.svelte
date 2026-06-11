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

    export let active: boolean;
    export let filterSignature: string;
    export let currentFilterParams: () => URLSearchParams;
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
    let showWordExamples = false;
    let isLoadingExamples = false;
    let isAppendingExamples = false;
    let wordExamples: WordExample[] = [];
    let hasMoreWordExamples = false;

    $: normalizedWordSearch = wordSearch.trim().toLowerCase();
    $: filteredWords = normalizedWordSearch
        ? topWords.filter((item) => item.word.includes(normalizedWordSearch))
        : topWords;
    $: visibleWords = filteredWords.slice(0, WORD_LIST_LIMIT);

    function maxCount(items: Array<{ count: number }>): number {
        return Math.max(...items.map((item) => item.count || 0), 1);
    }

    async function loadTopWords() {
        const filterKey = currentFilterParams().toString();
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
        error = "";
        try {
            const params = currentFilterParams();
            params.set("word", word);
            params.set("limit", "12");
            wordBreakdown = await fetchJson<WordBreakdown>(
                `/api/word-breakdown?${params.toString()}`,
            );
        } catch (err) {
            error =
                err instanceof Error
                    ? err.message
                    : "Failed to load word details";
            wordBreakdown = { word: word.toLowerCase(), people: [], chats: [] };
        } finally {
            isLoadingBreakdown = false;
        }
    }

    async function selectWord(word: string) {
        selectedWord = word;
        showWordExamples = false;
        wordExamples = [];
        hasMoreWordExamples = false;
        await loadWordBreakdown(word);
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
                messages: WordExample[];
            }>(`/api/word-examples?${params.toString()}`);
            if (response.word === selectedWord) {
                wordExamples = append
                    ? [...wordExamples, ...response.messages]
                    : response.messages;
                hasMoreWordExamples = response.has_more;
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
                <div>
                    <h3>Top authors</h3>
                    {#each wordBreakdown.people as person}
                        <div class="bar-row">
                            <span>{person.display_name}</span>
                            <div class="mini-bar-track">
                                <div
                                    class="mini-bar"
                                    style={`width:${(person.count / maxCount(wordBreakdown.people)) * 100}% ; background:${person.color}`}
                                ></div>
                            </div>
                            <strong>{person.count.toLocaleString()}</strong>
                        </div>
                    {/each}
                </div>
                <div>
                    <h3>Top chats</h3>
                    {#each wordBreakdown.chats as chat}
                        <div class="bar-row">
                            <span>{chat.name}</span>
                            <div class="mini-bar-track">
                                <div
                                    class="mini-bar chat"
                                    style={`width:${(chat.count / maxCount(wordBreakdown.chats)) * 100}%`}
                                ></div>
                            </div>
                            <strong>{chat.count.toLocaleString()}</strong>
                        </div>
                    {/each}
                </div>
            </div>
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
                {:else if wordExamples.length > 0}
                    <div class="example-list">
                        {#each wordExamples as message}
                            <div class="example-message">
                                <div class="example-meta">
                                    <strong
                                        style={`color:${message.person_color}`}
                                        >{message.person_name}</strong
                                    >
                                    <span>{message.channel_name}</span>
                                    <time
                                        >{message.ts
                                            ? new Date(
                                                  message.ts,
                                              ).toLocaleString()
                                            : "N/A"}</time
                                    >
                                    <button
                                        type="button"
                                        class="show-context"
                                        on:click={() =>
                                            openInContext(message.id)}
                                    >
                                        Show in context
                                    </button>
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
                            {isAppendingExamples
                                ? "Loading..."
                                : "Show 5 more messages"}
                        </button>
                    {/if}
                {:else}
                    <p class="muted">No example messages found.</p>
                {/if}
            {/if}
        {/if}
    </div>
</section>
