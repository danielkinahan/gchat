<script lang="ts">
    import { goto } from "$app/navigation";
    import {
        apiUrl,
        fetchJson,
        type Overview,
        type TimePoint,
        type TopPeople,
    } from "$lib/api";

    type CountMetric = "messages" | "words";
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
    };
    type MessageTabData = {
        overview: Overview;
        topPeople: TopPeople;
        calendar: { points: Array<{ day: string; message_count: number }> };
        activityHeatmap: {
            points: Array<{
                weekday: number;
                hour: number;
                message_count: number;
            }>;
        };
        messagesByMonth: {
            points: Array<{ month: string; message_count: number }>;
        };
        messagesByHour: {
            points: Array<{ hour: number; message_count: number }>;
        };
        topChats: {
            items: Array<{
                id: number;
                name: string;
                theme_name: string;
                message_count: number;
            }>;
        };
        topThemes: {
            items: Array<{ id: number; name: string; message_count: number }>;
        };
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
            people: Array<{ id: number; name: string }>;
            themes: Array<{ id: number; name: string }>;
            platforms: string[];
        };
        filters: {
            from: string;
            to: string;
            people: string;
            themes: string;
            platforms: string;
        };
    };

    const weekdayLabels = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"];

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
    let activeTab:
        | "overview"
        | "language"
        | "chats"
        | "nicknames"
        | "links"
        | "interactions" = "overview";
    let messageMetric: CountMetric = "messages";
    let overviewData: MessageTabData = {
        overview: data.overview,
        topPeople: data.topPeople,
        calendar: data.calendar,
        activityHeatmap: data.activityHeatmap,
        messagesByMonth: data.messagesByMonth,
        messagesByHour: data.messagesByHour,
        topChats: data.topChats,
        topThemes: data.topThemes,
    };
    let wordMetricData: MessageTabData | null = null;
    let wordMetricError = "";
    let isLoadingWordMetric = false;
    let wordMetricRequestId = 0;
    let loadedWordMetricFilterKey: string | null = null;

    let isLoadingWords = false;
    let isLoadingBreakdown = false;
    let languageError = "";
    let wordSearch = "";
    let wordsRequestId = 0;
    let topWords: Array<{ word: string; count: number }> = [];
    let selectedWord = "";
    let wordBreakdown: {
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
    } = { word: "", people: [], chats: [] };
    let showWordExamples = false;
    let isLoadingExamples = false;
    let wordExamples: Array<{
        id: string;
        ts: string | null;
        content: string;
        person_name: string;
        person_color: string;
        channel_name: string;
        source_name: string;
    }> = [];
    let hasMoreWordExamples = false;
    let isLoadingLinks = false;
    let linksError = "";
    let domainSearch = "";
    let linkedDomains: Array<{ domain: string; count: number }> = [];
    let linksByAuthor: Array<{
        id: number;
        display_name: string;
        color: string;
        count: number;
    }> = [];
    let loadedLinksFilterKey: string | null = null;
    let isLoadingInteractions = false;
    let interactionsError = "";
    let mentionSearch = "";
    let mostMentioned: Array<{ mention: string; count: number }> = [];
    let topReactedMessages: Array<{
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
    }> = [];
    let reactionAuthors: Array<{
        id: number;
        display_name: string;
        color: string;
        count: number;
    }> = [];
    let peopleFilterOptions: PeopleFilterOption[] = [];
    const toMediaUrl = (value: string | null): string | null =>
        value && value.startsWith("/api/") ? apiUrl(value) : value;
    const toDisplayAttachmentUrl = (
        attachmentUrl: string | null,
        attachmentPreview: string | null,
    ): string | null => {
        const resolved = toMediaUrl(attachmentUrl);
        if (resolved) return resolved;
        const preview = (attachmentPreview ?? "").trim();
        if (!preview) return null;
        if (preview.startsWith("http://") || preview.startsWith("https://")) {
            try {
                const parsed = new URL(preview);
                if (
                    parsed.hostname === "localhost" ||
                    parsed.hostname === "127.0.0.1"
                ) {
                    return null;
                }
                return preview;
            } catch {
                return null;
            }
        }
        return null;
    };
    let loadedInteractionsFilterKey: string | null = null;
    let nicknamePeople: NicknamePersonGroup[] = [];

    function messageCountLabel(metric: CountMetric): string {
        return metric === "words" ? "words" : "messages";
    }

    function messageCountTitle(metric: CountMetric): string {
        return metric === "words" ? "Word count" : "Message count";
    }

    function attachmentKind(
        preview: string | null,
    ): "image" | "video" | "audio" | "link" | "label" | null {
        if (!preview) return null;
        const normalized = preview.toLowerCase();
        const hasProtocol =
            normalized.startsWith("http://") ||
            normalized.startsWith("https://");
        if (/\.(png|jpe?g|gif|webp|bmp|svg)(\?|#|$)/.test(normalized))
            return "image";
        if (/\.(mp4|mov|webm|m4v)(\?|#|$)/.test(normalized)) return "video";
        if (/\.(mp3|wav|ogg|m4a)(\?|#|$)/.test(normalized)) return "audio";
        if (hasProtocol) return "link";
        return "label";
    }

    function attachmentLabel(preview: string): string {
        try {
            const url = new URL(preview);
            const segment = url.pathname.split("/").filter(Boolean).at(-1);
            return segment ? decodeURIComponent(segment) : preview;
        } catch {
            return preview;
        }
    }

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

    async function loadTopWords(searchQuery = "") {
        const requestId = ++wordsRequestId;
        isLoadingWords = true;
        languageError = "";
        try {
            const params = currentFilterParams();
            const q = searchQuery.trim().toLowerCase();
            if (q) {
                params.set("q", q);
                params.set("limit", "200");
            } else {
                params.set("all", "true");
            }
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
            languageError =
                err instanceof Error ? err.message : "Failed to load words";
        } finally {
            if (requestId === wordsRequestId) {
                isLoadingWords = false;
            }
        }
    }

    async function loadWordBreakdown(word: string) {
        if (!word) return;
        isLoadingBreakdown = true;
        languageError = "";
        try {
            const params = currentFilterParams();
            params.set("word", word);
            params.set("limit", "12");
            wordBreakdown = await fetchJson<{
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
            }>(`/api/word-breakdown?${params.toString()}`);
        } catch (err) {
            languageError =
                err instanceof Error
                    ? err.message
                    : "Failed to load word details";
            wordBreakdown = { word: word.toLowerCase(), people: [], chats: [] };
        } finally {
            isLoadingBreakdown = false;
        }
    }

    async function openLanguageTab() {
        activeTab = "language";
        if (topWords.length === 0) {
            await loadTopWords(wordSearch);
        }
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

    async function loadLinksData() {
        const filterKey = currentFilterParams().toString();
        if (isLoadingLinks || loadedLinksFilterKey === filterKey) return;
        isLoadingLinks = true;
        linksError = "";
        try {
            const baseParams = currentFilterParams();
            const domainsParams = new URLSearchParams(baseParams);
            domainsParams.set("limit", "200");
            const authorsParams = new URLSearchParams(baseParams);
            authorsParams.set("limit", "15");
            const [domains, authors] = await Promise.all([
                fetchJson<{ items: Array<{ domain: string; count: number }> }>(
                    `/api/linked-domains?${domainsParams.toString()}`,
                ),
                fetchJson<{
                    items: Array<{
                        id: number;
                        display_name: string;
                        color: string;
                        count: number;
                    }>;
                }>(`/api/links-by-author?${authorsParams.toString()}`),
            ]);
            linkedDomains = domains.items;
            linksByAuthor = authors.items;
        } catch (err) {
            linksError =
                err instanceof Error ? err.message : "Failed to load links";
        } finally {
            loadedLinksFilterKey = filterKey;
            isLoadingLinks = false;
        }
    }

    async function loadInteractionsData() {
        const filterKey = currentFilterParams().toString();
        if (isLoadingInteractions || loadedInteractionsFilterKey === filterKey)
            return;
        isLoadingInteractions = true;
        interactionsError = "";
        try {
            const baseParams = currentFilterParams();
            const mentionsParams = new URLSearchParams(baseParams);
            mentionsParams.set("limit", "200");
            const reactedParams = new URLSearchParams(baseParams);
            reactedParams.set("limit", "6");
            const authorsParams = new URLSearchParams(baseParams);
            authorsParams.set("limit", "15");
            const [mentions, reactedMessages, authors] = await Promise.all([
                fetchJson<{ items: Array<{ mention: string; count: number }> }>(
                    `/api/most-mentioned?${mentionsParams.toString()}`,
                ),
                fetchJson<{
                    items: Array<{
                        id: string;
                        ts: string | null;
                        content: string;
                        attachment_preview: string | null;
                        attachment_url: string | null;
                        person_name: string;
                        person_color: string;
                        channel_name: string;
                        source_name: string;
                        reaction_count: number;
                        reaction_summary: string | null;
                    }>;
                }>(`/api/top-reacted-messages?${reactedParams.toString()}`),
                fetchJson<{
                    items: Array<{
                        id: number;
                        display_name: string;
                        color: string;
                        count: number;
                    }>;
                }>(`/api/reaction-authors?${authorsParams.toString()}`),
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
            reactionAuthors = authors.items;
        } catch (err) {
            interactionsError =
                err instanceof Error
                    ? err.message
                    : "Failed to load interactions";
        } finally {
            loadedInteractionsFilterKey = filterKey;
            isLoadingInteractions = false;
        }
    }

    async function loadWordMetricData() {
        const filterKey = currentFilterParams().toString();
        if (
            isLoadingWordMetric ||
            (loadedWordMetricFilterKey === filterKey && wordMetricData)
        )
            return;
        isLoadingWordMetric = true;
        wordMetricError = "";
        wordMetricData = null;
        const requestId = ++wordMetricRequestId;
        try {
            const baseParams = currentFilterParams();
            baseParams.set("metric", "words");
            const query = baseParams.toString();
            const [
                overview,
                topPeople,
                calendar,
                activityHeatmap,
                messagesByMonth,
                messagesByHour,
                topChats,
                topThemes,
            ] = await Promise.all([
                fetchJson<Overview>(`/api/overview?${query}`),
                fetchJson<TopPeople>(`/api/top-people?limit=10&${query}`),
                fetchJson<{
                    points: Array<{ day: string; message_count: number }>;
                }>(`/api/calendar?${query}`),
                fetchJson<{
                    points: Array<{
                        weekday: number;
                        hour: number;
                        message_count: number;
                    }>;
                }>(`/api/activity-heatmap?${query}`),
                fetchJson<{
                    points: Array<{ month: string; message_count: number }>;
                }>(`/api/messages-by-month?${query}`),
                fetchJson<{
                    points: Array<{ hour: number; message_count: number }>;
                }>(`/api/messages-by-hour?${query}`),
                fetchJson<{
                    items: Array<{
                        id: number;
                        name: string;
                        theme_name: string;
                        message_count: number;
                    }>;
                }>(`/api/top-chats?limit=15&${query}`),
                fetchJson<{
                    items: Array<{
                        id: number;
                        name: string;
                        message_count: number;
                    }>;
                }>(`/api/top-themes?limit=15&${query}`),
            ]);
            if (requestId !== wordMetricRequestId) return;
            wordMetricData = {
                overview,
                topPeople,
                calendar,
                activityHeatmap,
                messagesByMonth,
                messagesByHour,
                topChats,
                topThemes,
            };
            overviewData = wordMetricData;
            loadedWordMetricFilterKey = filterKey;
        } catch (err) {
            if (requestId !== wordMetricRequestId) return;
            wordMetricError =
                err instanceof Error
                    ? err.message
                    : "Failed to load word counts";
        } finally {
            if (requestId === wordMetricRequestId) {
                loadedWordMetricFilterKey = filterKey;
                isLoadingWordMetric = false;
            }
        }
    }

    function formatChange(newName: string): string {
        return newName;
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
            isLoadingExamples = true;
        } else if (showWordExamples) {
            showWordExamples = false;
            return;
        } else {
            showWordExamples = true;
            if (wordExamples.length > 0) return;
            isLoadingExamples = true;
        }
        languageError = "";
        try {
            const params = currentFilterParams();
            params.set("word", selectedWord);
            params.set("limit", offset > 0 ? "5" : "6");
            if (offset > 0) params.set("offset", String(offset));
            const response = await fetchJson<{
                word: string;
                has_more: boolean;
                messages: Array<{
                    id: string;
                    ts: string | null;
                    content: string;
                    person_name: string;
                    person_color: string;
                    channel_name: string;
                    source_name: string;
                }>;
            }>(`/api/word-examples?${params.toString()}`);
            if (response.word === selectedWord) {
                wordExamples = append
                    ? [...wordExamples, ...response.messages]
                    : response.messages;
                hasMoreWordExamples = response.has_more;
            }
        } catch (err) {
            languageError =
                err instanceof Error
                    ? err.message
                    : "Failed to load example messages";
            showWordExamples = false;
        } finally {
            isLoadingExamples = false;
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
        await loadWordExamples(wordExamples.length, true);
    }

    async function openLinksTab() {
        activeTab = "links";
        if (
            linkedDomains.length === 0 &&
            linksByAuthor.length === 0 &&
            !isLoadingLinks
        ) {
            await loadLinksData();
        }
    }

    async function openInteractionsTab() {
        activeTab = "interactions";
        if (
            mostMentioned.length === 0 &&
            topReactedMessages.length === 0 &&
            reactionAuthors.length === 0 &&
            !isLoadingInteractions
        ) {
            await loadInteractionsData();
        }
    }

    function setMessageMetric(metric: CountMetric) {
        messageMetric = metric;
        if (
            metric === "words" &&
            activeTab === "overview" &&
            (loadedWordMetricFilterKey !== filterSignature || !wordMetricData)
        ) {
            void loadWordMetricData();
        }
    }

    $: fromDate = data.filters.from;
    $: toDate = data.filters.to;
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
    ].join("|");

    $: if (
        messageMetric === "words" &&
        activeTab === "overview" &&
        !isLoadingWordMetric &&
        loadedWordMetricFilterKey !== filterSignature
    ) {
        void loadWordMetricData();
    }

    $: if (messageMetric === "messages") {
        overviewData = {
            overview: data.overview,
            topPeople: data.topPeople,
            calendar: data.calendar,
            activityHeatmap: data.activityHeatmap,
            messagesByMonth: data.messagesByMonth,
            messagesByHour: data.messagesByHour,
            topChats: data.topChats,
            topThemes: data.topThemes,
        };
    }
    $: if (messageMetric === "words" && wordMetricData) {
        overviewData = wordMetricData;
    }
    $: overviewMetricLabel = messageCountLabel(messageMetric);
    $: overviewMetricTitle = messageCountTitle(messageMetric);

    $: normalizedWordSearch = wordSearch.trim().toLowerCase();
    $: filteredWords = normalizedWordSearch
        ? topWords.filter((item) => item.word.includes(normalizedWordSearch))
        : topWords;
    const WORD_LIST_LIMIT = 500;
    $: visibleWords = filteredWords.slice(0, WORD_LIST_LIMIT);
    $: if (
        activeTab === "language" &&
        !isLoadingWords &&
        filteredWords.length > 0 &&
        !filteredWords.some((item) => item.word === selectedWord)
    ) {
        void selectWord(filteredWords[0].word);
    }

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

    function handleDateChange() {
        updateFilters();
    }

    function maxCount(items: Array<{ count: number }>): number {
        return Math.max(...items.map((item) => item.count || 0), 1);
    }

    function maxMessageCount(points: Array<{ message_count: number }>): number {
        return Math.max(...points.map((point) => point.message_count || 0), 1);
    }

    $: monthLabelStep = Math.max(
        1,
        Math.ceil(overviewData.messagesByMonth.points.length / 8),
    );
    $: monthMax = maxMessageCount(overviewData.messagesByMonth.points);
    $: hourMax = maxMessageCount(overviewData.messagesByHour.points);
    $: weekdayTotals = weekdayLabels.map((_, index) =>
        overviewData.activityHeatmap.points
            .filter((point) => point.weekday === index + 1)
            .reduce((sum, point) => sum + point.message_count, 0),
    );
    $: weekdayMax = Math.max(...weekdayTotals, 1);
    $: hourTotals = Array.from({ length: 24 }, (_, hour) => {
        const point = overviewData.messagesByHour.points.find(
            (item) => item.hour === hour,
        );
        return point?.message_count ?? 0;
    });
    $: hourTotalsMax = Math.max(...hourTotals, 1);
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
                grouped.set(key, { name: person.name, ids: [person.id] });
            }
        }
        return [...grouped.values()]
            .map((option) => ({ ...option, ids: [...new Set(option.ids)] }))
            .sort((a, b) =>
                a.name.localeCompare(b.name, undefined, { sensitivity: "base" }),
            );
    })();
    $: dayStart = data.overview.date_range.start
        ? new Date(data.overview.date_range.start)
        : null;
    $: dayEnd = data.overview.date_range.end
        ? new Date(data.overview.date_range.end)
        : null;
    $: totalDays =
        dayStart && dayEnd
            ? Math.max(
                  1,
                  Math.ceil(
                      (dayEnd.getTime() - dayStart.getTime()) /
                          (1000 * 60 * 60 * 24),
                  ),
              )
            : 1;
    $: avgMessagesPerDay = Math.round(
        overviewData.overview.total_messages / totalDays,
    );

    $: mostActiveMonth = overviewData.messagesByMonth.points.reduce(
        (best, current) =>
            current.message_count > best.message_count ? current : best,
        overviewData.messagesByMonth.points[0] ?? {
            month: "",
            message_count: 0,
        },
    );
    $: mostActiveDay = overviewData.calendar.points.reduce(
        (best, current) =>
            current.message_count > best.message_count ? current : best,
        overviewData.calendar.points[0] ?? { day: "", message_count: 0 },
    );
    $: mostActiveHour = overviewData.messagesByHour.points.reduce(
        (best, current) =>
            current.message_count > best.message_count ? current : best,
        overviewData.messagesByHour.points[0] ?? { hour: 0, message_count: 0 },
    );
    $: normalizedDomainSearch = domainSearch.trim().toLowerCase();
    $: filteredDomains = normalizedDomainSearch
        ? linkedDomains.filter((item) =>
              item.domain.includes(normalizedDomainSearch),
          )
        : linkedDomains;
    const DOMAIN_LIMIT = 20;
    $: visibleDomains = filteredDomains.slice(0, DOMAIN_LIMIT);
    $: normalizedMentionSearch = mentionSearch.trim().toLowerCase();
    $: filteredMentions = normalizedMentionSearch
        ? mostMentioned.filter((item) =>
              item.mention.includes(normalizedMentionSearch),
          )
        : mostMentioned;
    const MENTION_LIMIT = 20;
    $: visibleMentions = filteredMentions.slice(0, MENTION_LIMIT);
    $: linksFilterKey = filterSignature;
    $: if (
        activeTab === "links" &&
        !isLoadingLinks &&
        loadedLinksFilterKey !== linksFilterKey
    ) {
        void loadLinksData();
    }
    $: interactionsFilterKey = filterSignature;
    $: if (
        activeTab === "interactions" &&
        !isLoadingInteractions &&
        loadedInteractionsFilterKey !== interactionsFilterKey
    ) {
        void loadInteractionsData();
    }
</script>

<svelte:head>
    <title>gChat</title>
</svelte:head>

<main class="page">
    <header class="hero">
        <div>
            <p class="eyebrow">gChat</p>
            <h1>Multi-platform chat analytics</h1>
            <p class="lede">
                A private dashboard for Discord, Facebook, and Signal history.
            </p>
        </div>
    </header>

    <section class="filters">
        <div class="filter-group">
            <fieldset class="date-range-fieldset">
            <legend>Date Range</legend>
            <div class="date-inputs">
                <input
                    type="date"
                    bind:value={fromDate}
                    on:change={handleDateChange}
                    placeholder="From"
                />
                <input
                    type="date"
                    bind:value={toDate}
                    on:change={handleDateChange}
                    placeholder="To"
                />
            </div>
            </fieldset>
        </div>

        <div class="filter-group">
            <fieldset class="date-range-fieldset">
            <legend>Platforms</legend>
            <div class="dropdown-list">
                {#each data.metadata.platforms as platform}
                    <label class="checkbox-label">
                        <input
                            type="checkbox"
                            checked={selectedPlatforms.includes(platform)}
                            on:change={() => togglePlatform(platform)}
                        />
                        {platform}
                    </label>
                {/each}
            </div>
            </fieldset>
        </div>

        <div class="filter-group">
            <fieldset class="date-range-fieldset">
            <legend>People</legend>
            <div class="dropdown-list">
                {#each peopleFilterOptions as person}
                    <label class="checkbox-label">
                        <input
                            type="checkbox"
                            checked={person.ids.some((id) =>
                                selectedPeople.includes(id),
                            )}
                            on:change={() => togglePerson(person.ids)}
                        />
                        {person.name}
                    </label>
                {/each}
            </div>
            </fieldset>
        </div>

        <div class="filter-group">
            <fieldset class="date-range-fieldset">
            <legend>Themes (group chat collections)</legend>
            <div class="themes-scroll">
                {#each data.metadata.themes as theme}
                    <label class="checkbox-label">
                        <input
                            type="checkbox"
                            checked={selectedThemes.includes(theme.id)}
                            on:change={() => toggleTheme(theme.id)}
                        />
                        {theme.name}
                    </label>
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
            class:active={activeTab === "links"}
            type="button"
            on:click={openLinksTab}>Links</button
        >
        <button
            class:active={activeTab === "interactions"}
            type="button"
            on:click={openInteractionsTab}>Interactions</button
        >
    </section>

    {#if messageMetric === "words" && !wordMetricData}
        <section
            class="overview-top"
            class:tab-hidden={activeTab !== "overview"}
        >
            <div class="panel timeline-panel">
                <div class="panel-head">
                    <h2>Word counts</h2>
                </div>
                {#if isLoadingWordMetric}
                    <p class="muted">Loading word counts...</p>
                {:else}
                    <p class="muted">
                        {wordMetricError || "Word counts are unavailable."}
                    </p>
                {/if}
            </div>
        </section>
    {:else}
        <section
            class="overview-top"
            class:tab-hidden={activeTab !== "overview"}
        >
            <div class="panel timeline-panel">
                <div class="panel-head">
                    <h2>{overviewMetricTitle} sent over time by month</h2>
                    <div
                        class="metric-switch"
                        role="group"
                        aria-label="Message metric"
                    >
                        <button
                            type="button"
                            class:active={messageMetric === "messages"}
                            on:click={() => setMessageMetric("messages")}
                            >Messages</button
                        >
                        <button
                            type="button"
                            class:active={messageMetric === "words"}
                            on:click={() => setMessageMetric("words")}
                            >Words</button
                        >
                    </div>
                </div>
                <div class="timeline-plot">
                    <div class="timeline-axis">
                        <span>{Math.round(monthMax).toLocaleString()}</span>
                        <span
                            >{Math.round(
                                monthMax * 0.75,
                            ).toLocaleString()}</span
                        >
                        <span
                            >{Math.round(monthMax * 0.5).toLocaleString()}</span
                        >
                        <span
                            >{Math.round(
                                monthMax * 0.25,
                            ).toLocaleString()}</span
                        >
                        <span>0</span>
                    </div>
                    <div class="timeline-chart">
                        {#each overviewData.messagesByMonth.points as point, i}
                            <div class="timeline-bar-wrap">
                                <div class="timeline-bar-slot">
                                    <div
                                        class="timeline-bar"
                                        style={`height:${(point.message_count / monthMax) * 100}%`}
                                        title={`${new Date(point.month).toLocaleDateString("en-US", { month: "long", year: "numeric" })}: ${point.message_count.toLocaleString()} ${overviewMetricLabel}`}
                                    ></div>
                                </div>
                                <span class="timeline-label"
                                    >{i % monthLabelStep === 0
                                        ? new Date(
                                              point.month,
                                          ).toLocaleDateString("en-US", {
                                              month: "short",
                                              year: "2-digit",
                                          })
                                        : ""}</span
                                >
                            </div>
                        {/each}
                    </div>
                </div>
            </div>

            <div class="panel stats-panel">
                <h2>{overviewMetricTitle} statistics</h2>
                <div class="stats-list">
                    <div>
                        <span>Total {overviewMetricLabel} sent</span><strong
                            >{overviewData.overview.total_messages.toLocaleString()}</strong
                        >
                    </div>
                    <div>
                        <span>Average {overviewMetricLabel} per day</span
                        ><strong>{avgMessagesPerDay.toLocaleString()}</strong>
                    </div>
                    <div>
                        <span>Date range start</span><strong
                            >{overviewData.overview.date_range.start
                                ? overviewData.overview.date_range.start.split(
                                      "T",
                                  )[0]
                                : "N/A"}</strong
                        >
                    </div>
                    <div>
                        <span>Date range end</span><strong
                            >{overviewData.overview.date_range.end
                                ? overviewData.overview.date_range.end.split(
                                      "T",
                                  )[0]
                                : "N/A"}</strong
                        >
                    </div>
                    <div>
                        <span>Most active month</span><strong
                            >{mostActiveMonth.month
                                ? new Date(
                                      mostActiveMonth.month,
                                  ).toLocaleDateString("en-US", {
                                      month: "long",
                                      year: "numeric",
                                  })
                                : "N/A"}</strong
                        >
                    </div>
                    <div>
                        <span>Most active day</span><strong
                            >{mostActiveDay.day || "N/A"}</strong
                        >
                    </div>
                    <div>
                        <span>Most active hour</span><strong
                            >{`${mostActiveHour.hour}:00`}</strong
                        >
                    </div>
                </div>
            </div>
        </section>

        <section
            class="overview-bottom"
            class:tab-hidden={activeTab !== "overview"}
        >
            <div class="panel split-panel">
                <h2>{overviewMetricTitle} by week day & hour (split)</h2>
                <div class="weekday-chart">
                    {#each weekdayLabels as label, i}
                        <div class="weekday-bar-wrap">
                            <div
                                class="weekday-bar"
                                style={`height:${(weekdayTotals[i] / weekdayMax) * 100}%`}
                                title={`${label}: ${weekdayTotals[i].toLocaleString()} ${overviewMetricLabel}`}
                            ></div>
                            <span>{label}</span>
                        </div>
                    {/each}
                </div>
                <div class="hour-chart">
                    {#each hourTotals as count, hour}
                        <div class="hour-bar-wrap">
                            <div class="hour-bar-slot">
                                <div
                                    class="hour-bar"
                                    style={`height:${(count / hourTotalsMax) * 100}%`}
                                    title={`${hour}:00 - ${count.toLocaleString()} ${overviewMetricLabel}`}
                                ></div>
                            </div>
                            <span>{hour % 3 === 0 ? hour : ""}</span>
                        </div>
                    {/each}
                </div>
            </div>

            <div class="panel rank-panel">
                <h2>{overviewMetricTitle} sent by author</h2>
                <div class="rank-list">
                    {#each overviewData.topPeople.items as person}
                        <div class="rank-row">
                            <span class="rank-name">
                                <span
                                    class="swatch"
                                    style={`background:${person.color}`}
                                ></span>{person.display_name}
                            </span>
                            <div class="rank-track">
                                <div
                                    class="rank-fill author"
                                    style={`width:${(person.message_count / Math.max(overviewData.topPeople.items[0]?.message_count || 1, 1)) * 100}%`}
                                ></div>
                            </div>
                            <strong
                                >{person.message_count.toLocaleString()}</strong
                            >
                        </div>
                    {/each}
                </div>
            </div>
        </section>

        <section
            class="overview-bottom-secondary"
            class:tab-hidden={activeTab !== "overview"}
        >
            <div class="panel rank-panel">
                <h2>{overviewMetricTitle} sent by channel</h2>
                <div class="rank-list">
                    {#each overviewData.topChats.items as chat}
                        <div class="rank-row">
                            <span class="rank-name">{chat.name}</span>
                            <div class="rank-track">
                                <div
                                    class="rank-fill channel"
                                    style={`width:${(chat.message_count / Math.max(overviewData.topChats.items[0]?.message_count || 1, 1)) * 100}%`}
                                ></div>
                            </div>
                            <strong
                                >{chat.message_count.toLocaleString()}</strong
                            >
                        </div>
                    {/each}
                </div>
            </div>

            <div class="panel rank-panel">
                <h2>{overviewMetricTitle} sent by theme</h2>
                <div class="rank-list">
                    {#each overviewData.topThemes.items as theme}
                        <div class="rank-row">
                            <span class="rank-name">{theme.name}</span>
                            <div class="rank-track">
                                <div
                                    class="rank-fill theme"
                                    style={`width:${(theme.message_count / Math.max(overviewData.topThemes.items[0]?.message_count || 1, 1)) * 100}%`}
                                ></div>
                            </div>
                            <strong
                                >{theme.message_count.toLocaleString()}</strong
                            >
                        </div>
                    {/each}
                </div>
            </div>
        </section>
    {/if}

    <section class="language" class:tab-hidden={activeTab !== "language"}>
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
            {#if languageError}
                <p class="muted">{languageError}</p>
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
                    {#if isLoadingExamples}
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
                                    </div>
                                    <p>{message.content}</p>
                                </div>
                            {/each}
                        </div>
                        {#if hasMoreWordExamples}
                            <button
                                type="button"
                                class="examples-toggle"
                                on:click={showMoreWordExamples}
                            >
                                Show 5 more messages
                            </button>
                        {/if}
                    {:else}
                        <p class="muted">No example messages found.</p>
                    {/if}
                {/if}
            {/if}
        </div>
    </section>

    <section class="links" class:tab-hidden={activeTab !== "links"}>
        <div class="panel rank-panel">
            <h2>Most linked domains</h2>
            <input
                type="text"
                placeholder="Filter domains..."
                bind:value={domainSearch}
                autocomplete="off"
                autocapitalize="off"
                spellcheck="false"
                data-lpignore="true"
                data-1p-ignore="true"
                class="word-filter"
            />
            {#if linksError}
                <p class="muted">{linksError}</p>
            {:else if isLoadingLinks}
                <p class="muted">Loading links...</p>
            {:else}
                <div class="rank-list">
                    {#each visibleDomains as domain}
                        <div class="rank-row">
                            <span class="rank-name">
                                <a
                                    href={`https://${domain.domain}`}
                                    target="_blank"
                                    rel="noreferrer">{domain.domain}</a
                                >
                            </span>
                            <div class="rank-track">
                                <div
                                    class="rank-fill domain"
                                    style={`width:${(domain.count / Math.max(filteredDomains[0]?.count || 1, 1)) * 100}%`}
                                ></div>
                            </div>
                            <strong>{domain.count.toLocaleString()}</strong>
                        </div>
                    {/each}
                </div>
            {/if}
        </div>

        <div class="panel rank-panel">
            <h2>Most links sent by author</h2>
            {#if isLoadingLinks}
                <p class="muted">Loading links...</p>
            {:else}
                <div class="rank-list">
                    {#each linksByAuthor as person}
                        <div class="rank-row">
                            <span class="rank-name">
                                <span
                                    class="swatch"
                                    style={`background:${person.color}`}
                                ></span>{person.display_name}
                            </span>
                            <div class="rank-track">
                                <div
                                    class="rank-fill links-author"
                                    style={`width:${(person.count / Math.max(linksByAuthor[0]?.count || 1, 1)) * 100}%`}
                                ></div>
                            </div>
                            <strong>{person.count.toLocaleString()}</strong>
                        </div>
                    {/each}
                </div>
            {/if}
        </div>
    </section>

    <section
        class="interactions"
        class:tab-hidden={activeTab !== "interactions"}
    >
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
            {#if interactionsError}
                <p class="muted">{interactionsError}</p>
            {:else if isLoadingInteractions}
                <p class="muted">Loading interactions...</p>
            {:else}
                <div class="rank-list">
                    {#each visibleMentions as mention}
                        <div class="rank-row">
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
            <h2>Top reacted messages (total)</h2>
            {#if isLoadingInteractions}
                <p class="muted">Loading interactions...</p>
            {:else}
                <div class="reaction-list">
                    {#each topReactedMessages as message, index}
                        <article class="reaction-card">
                            <div class="reaction-card-head">
                                <strong>
                                    {message.reaction_count.toLocaleString()} reactions</strong
                                >
                                <time
                                    >{message.ts
                                        ? new Date(
                                              message.ts,
                                          ).toLocaleDateString()
                                        : "N/A"}</time
                                >
                            </div>
                            {#if message.reaction_summary}
                                <p class="reaction-summary">
                                    {message.reaction_summary}
                                </p>
                            {/if}
                            <div class="example-meta">
                                <strong style={`color:${message.person_color}`}
                                    >{message.person_name}</strong
                                >
                                <span>{message.channel_name}</span>
                            </div>
                            {#if message.attachment_display_url && attachmentKind(message.attachment_display_url) === "image"}
                                <img
                                    class="reaction-attachment-image"
                                    src={message.attachment_display_url}
                                    alt={attachmentLabel(
                                        message.attachment_preview ||
                                            message.attachment_url ||
                                            "attachment",
                                    )}
                                    loading="lazy"
                                />
                            {:else if message.attachment_display_url && attachmentKind(message.attachment_display_url) === "video"}
                                <p>
                                    <a
                                        href={message.attachment_display_url}
                                        target="_blank"
                                        rel="noreferrer"
                                        >{attachmentLabel(
                                            message.attachment_preview ||
                                                message.attachment_url ||
                                                "attachment",
                                        )}</a
                                    >
                                </p>
                            {:else if message.attachment_display_url && attachmentKind(message.attachment_display_url) === "audio"}
                                <audio
                                    class="reaction-attachment-audio"
                                    src={message.attachment_display_url}
                                    controls
                                    preload="none"
                                ></audio>
                            {:else if message.attachment_display_url && attachmentKind(message.attachment_display_url) === "link"}
                                <p>
                                    <a
                                        href={message.attachment_display_url}
                                        target="_blank"
                                        rel="noreferrer"
                                        >{attachmentLabel(
                                            message.attachment_preview ||
                                                message.attachment_url ||
                                                "attachment",
                                        )}</a
                                    >
                                </p>
                            {:else if message.attachment_preview}
                                <p>
                                    Attachment: {attachmentLabel(
                                        message.attachment_preview,
                                    )}
                                </p>
                            {:else}
                                <p>{message.content}</p>
                            {/if}
                        </article>
                    {/each}
                </div>
            {/if}
        </div>

        <div class="panel rank-panel">
            <h2>Authors that get the most reactions</h2>
            {#if isLoadingInteractions}
                <p class="muted">Loading interactions...</p>
            {:else}
                <div class="rank-list">
                    {#each reactionAuthors as person}
                        <div class="rank-row">
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
    </section>

    <section class="history" class:tab-hidden={activeTab !== "chats"}>
        {#each data.nameHistory.chats as chat}
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
                                        {formatChange(change.new_name)}
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

    <section class="history" class:tab-hidden={activeTab !== "nicknames"}>
        {#if nicknamePeople.length === 0}
            <div class="panel">
                <p class="muted">No nickname changes found.</p>
            </div>
        {:else}
            {#each nicknamePeople as person}
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
                                                {formatChange(change.new_name)}
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
</main>

<style>
    :global(body) {
        margin: 0;
        font-family: Inter, ui-sans-serif, system-ui, sans-serif;
        background: #0f172a;
        color: #e2e8f0;
    }

    .page {
        max-width: 1200px;
        margin: 0 auto;
        padding: 32px 24px 48px;
    }

    .hero {
        display: flex;
        gap: 24px;
        justify-content: space-between;
        align-items: flex-end;
        margin-bottom: 24px;
    }

    .eyebrow {
        text-transform: uppercase;
        letter-spacing: 0.12em;
        color: #94a3b8;
        margin: 0 0 8px;
        font-size: 0.75rem;
    }

    h1,
    h2,
    p {
        margin: 0;
    }

    h1 {
        font-size: clamp(2rem, 4vw, 3.5rem);
        line-height: 1.05;
    }

    .lede,
    .muted {
        color: #94a3b8;
        margin-top: 8px;
    }

    .filters {
        display: grid;
        grid-template-columns: repeat(auto-fit, minmax(200px, 1fr));
        gap: 24px;
        background: #111827;
        border: 1px solid #1f2937;
        border-radius: 18px;
        padding: 18px;
        margin-bottom: 24px;
    }

    .tabs {
        display: flex;
        gap: 8px;
        margin-bottom: 12px;
    }

    .tabs button {
        border: 1px solid #334155;
        border-radius: 999px;
        background: #111827;
        color: #cbd5e1;
        padding: 8px 14px;
        cursor: pointer;
        font-weight: 600;
    }

    .tabs button.active {
        background: #1d4ed8;
        border-color: #2563eb;
        color: #fff;
    }

    .tab-hidden {
        display: none !important;
    }

    .filter-group {
        display: flex;
        flex-direction: column;
        gap: 8px;
    }

    .filter-group label:first-child {
        font-size: 0.85rem;
        color: #cbd5e1;
        font-weight: 600;
    }

    .date-range-fieldset {
        border: none;
        margin: 0;
        padding: 0;
    }

    .date-range-fieldset legend {
        font-size: 0.85rem;
        color: #cbd5e1;
        font-weight: 600;
        padding: 0;
        margin-bottom: 8px;
    }

    .date-inputs {
        display: flex;
        gap: 8px;
    }

    .date-inputs input {
        flex: 1;
    }

    input[type="date"],
    input[type="checkbox"] {
        border-radius: 10px;
        border: 1px solid #334155;
        background: #0b1220;
        color: #e2e8f0;
        padding: 10px 12px;
    }

    input[type="checkbox"] {
        width: auto;
        padding: 0;
        margin-right: 8px;
    }

    .dropdown-list {
        display: flex;
        flex-direction: column;
        gap: 6px;
        max-height: 200px;
        overflow-y: auto;
    }

    .themes-scroll {
        display: flex;
        flex-direction: column;
        gap: 6px;
        max-height: 200px;
        overflow-y: auto;
        border: 1px solid #334155;
        border-radius: 10px;
        padding: 8px;
    }

    .checkbox-label {
        display: flex;
        align-items: center;
        font-size: 0.85rem;
        cursor: pointer;
        padding: 4px;
    }

    .overview-top {
        display: grid;
        grid-template-columns: minmax(0, 2.3fr) minmax(320px, 1fr);
        gap: 16px;
        margin-top: 16px;
    }

    .overview-bottom {
        display: grid;
        grid-template-columns: repeat(2, minmax(0, 1fr));
        gap: 16px;
    }

    .examples-toggle {
        margin-top: 16px;
        border: 1px solid #334155;
        border-radius: 999px;
        background: #0b1220;
        color: #e2e8f0;
        padding: 8px 14px;
        cursor: pointer;
        font-weight: 600;
    }

    .example-list {
        display: grid;
        gap: 12px;
        margin-top: 16px;
    }

    .example-message {
        border: 1px solid #334155;
        border-radius: 14px;
        padding: 12px 14px;
        background: #0b1220;
    }

    .example-meta {
        display: flex;
        flex-wrap: wrap;
        gap: 8px;
        align-items: center;
        font-size: 0.8rem;
        color: #94a3b8;
        margin-bottom: 8px;
    }

    .example-message p {
        white-space: pre-wrap;
        word-break: break-word;
        margin-top: 16px;
    }

    .overview-bottom-secondary {
        display: grid;
        grid-template-columns: repeat(2, minmax(0, 1fr));
        gap: 16px;
        margin-top: 16px;
    }

    .history {
        display: grid;
        gap: 16px;
        margin-top: 16px;
    }

    .panel {
        background: #111827;
        border: 1px solid #1f2937;
        border-radius: 18px;
        padding: 18px;
    }

    .timeline-panel {
        display: flex;
        flex-direction: column;
    }

    .timeline-plot {
        margin-top: 12px;
        display: grid;
        grid-template-columns: 52px minmax(0, 1fr);
        gap: 10px;
        align-items: stretch;
    }

    .timeline-axis {
        display: grid;
        grid-template-rows: repeat(5, 1fr);
        align-items: end;
        color: #94a3b8;
        font-size: 0.68rem;
        text-align: right;
        padding-bottom: 18px;
    }

    .timeline-axis span {
        line-height: 1;
    }

    .timeline-chart {
        height: 250px;
        display: flex;
        flex-wrap: nowrap;
        gap: 3px;
        min-width: 0;
    }

    .timeline-bar-wrap {
        flex: 1 1 0;
        min-width: 0;
        display: flex;
        flex-direction: column;
        align-items: center;
        height: 100%;
    }

    .timeline-bar-slot {
        flex: 1;
        width: 100%;
        display: flex;
        align-items: flex-end;
    }

    .timeline-bar {
        width: 100%;
        min-height: 2px;
        border-radius: 3px;
        background: linear-gradient(180deg, #7dd3fc 0%, #0ea5e9 100%);
    }

    .timeline-label {
        margin-top: 6px;
        min-height: 12px;
        font-size: 0.65rem;
        color: #94a3b8;
        line-height: 1;
        white-space: nowrap;
    }

    .stats-panel {
        display: flex;
        flex-direction: column;
    }

    .stats-list {
        margin-top: 12px;
        display: grid;
        gap: 10px;
    }

    .stats-list div {
        display: flex;
        justify-content: space-between;
        gap: 12px;
        border-bottom: 1px solid #1f2937;
        padding-bottom: 8px;
        font-size: 0.85rem;
    }

    .stats-list span {
        color: #94a3b8;
    }

    .stats-list strong {
        color: #f8fafc;
        text-align: right;
    }

    .swatch {
        display: inline-block;
        width: 10px;
        height: 10px;
        border-radius: 999px;
        margin-right: 8px;
        vertical-align: middle;
    }

    .split-panel {
        display: flex;
        flex-direction: column;
        gap: 14px;
    }

    .weekday-chart,
    .hour-chart {
        display: grid;
        align-items: end;
        gap: 4px;
        height: 120px;
    }

    .weekday-chart {
        grid-template-columns: repeat(7, minmax(0, 1fr));
    }

    .hour-chart {
        grid-template-columns: repeat(24, minmax(0, 1fr));
    }

    .weekday-bar-wrap,
    .hour-bar-wrap {
        display: flex;
        flex-direction: column;
        align-items: center;
        height: 100%;
    }

    .weekday-bar,
    .hour-bar {
        width: 100%;
        height: 100%;
        min-height: 2px;
        border-radius: 3px;
    }

    .hour-bar-slot {
        flex: 1;
        width: 100%;
        display: flex;
        align-items: flex-end;
    }

    .weekday-bar {
        background: linear-gradient(180deg, #34d399 0%, #10b981 100%);
    }

    .hour-bar {
        background: linear-gradient(180deg, #c4b5fd 0%, #8b5cf6 100%);
    }

    .weekday-bar-wrap span,
    .hour-bar-wrap span {
        margin-top: 4px;
        min-height: 10px;
        color: #94a3b8;
        font-size: 0.65rem;
        line-height: 1;
    }

    .rank-panel {
        display: flex;
        flex-direction: column;
    }

    .history-card {
        display: block;
    }

    .history-card > summary {
        list-style: none;
        cursor: pointer;
        display: flex;
        justify-content: space-between;
        align-items: center;
        gap: 16px;
    }

    .history-card > summary::-webkit-details-marker {
        display: none;
    }

    .history-card > summary strong {
        display: block;
        font-size: 1rem;
    }

    .history-card > summary span {
        color: #94a3b8;
        font-size: 0.8rem;
    }

    .history-counts {
        color: #94a3b8;
        font-size: 0.8rem;
        text-align: right;
        white-space: nowrap;
    }

    .history-group {
        margin-top: 14px;
        padding-top: 14px;
        border-top: 1px solid #1f2937;
    }

    .history-group h3 {
        margin: 0 0 10px;
        font-size: 0.95rem;
        color: #cbd5e1;
    }

    .participant-list {
        display: grid;
        gap: 12px;
    }

    .participant-card {
        background: #0b1220;
        border: 1px solid #1f2937;
        border-radius: 12px;
        padding: 12px;
    }

    .participant-card strong {
        display: block;
        margin-bottom: 8px;
    }

    .history-list {
        display: grid;
        gap: 8px;
    }

    .history-row {
        display: flex;
        justify-content: space-between;
        gap: 12px;
        padding-bottom: 8px;
        border-bottom: 1px solid #1f2937;
        font-size: 0.8rem;
    }

    .history-row time {
        color: #94a3b8;
        white-space: nowrap;
        flex: 0 0 auto;
    }

    .history-change {
        color: #e2e8f0;
        min-width: 0;
        overflow-wrap: anywhere;
    }

    .rank-list {
        margin-top: 12px;
        display: grid;
        gap: 8px;
    }

    .rank-row {
        display: grid;
        grid-template-columns: minmax(120px, 1fr) minmax(80px, 1.4fr) auto;
        gap: 8px;
        align-items: center;
        font-size: 0.82rem;
    }

    .rank-name {
        overflow: hidden;
        text-overflow: ellipsis;
        white-space: nowrap;
    }

    .rank-track {
        background: #0b1220;
        border: 1px solid #1f2937;
        border-radius: 999px;
        height: 9px;
        overflow: hidden;
    }

    .rank-fill {
        height: 100%;
        border-radius: 999px;
        min-width: 2px;
    }

    .rank-fill.author {
        background: #38bdf8;
    }

    .rank-fill.channel {
        background: #f59e0b;
    }

    .rank-fill.theme {
        background: #10b981;
    }

    .rank-fill.domain {
        background: #4f46e5;
    }

    .rank-fill.links-author {
        background: #6366f1;
    }

    .rank-fill.mention {
        background: #f59e0b;
    }

    .rank-fill.reactions {
        background: #8b5cf6;
    }

    .rank-name a {
        color: inherit;
        text-decoration: none;
    }

    .rank-name a:hover {
        text-decoration: underline;
    }

    .interactions-feed {
        display: flex;
        flex-direction: column;
    }

    .reaction-list {
        margin-top: 12px;
        display: grid;
        gap: 12px;
    }

    .reaction-card {
        border: 1px solid #1f2937;
        border-radius: 14px;
        padding: 12px;
        background: #0b1220;
    }

    .reaction-card-head {
        display: flex;
        justify-content: space-between;
        gap: 12px;
        align-items: center;
        margin-bottom: 8px;
        font-size: 0.82rem;
        color: #cbd5e1;
    }

    .reaction-card p {
        margin-top: 8px;
        white-space: pre-wrap;
        word-break: break-word;
    }

    .reaction-summary {
        margin-top: 0;
        margin-bottom: 8px;
        color: #cbd5e1;
        font-size: 0.8rem;
    }

    .reaction-attachment-image {
        margin-top: 8px;
        max-width: min(100%, 520px);
        max-height: 320px;
        border-radius: 10px;
        border: 1px solid #1f2937;
        background: #020617;
    }

    .reaction-attachment-audio {
        margin-top: 8px;
        width: min(100%, 380px);
    }

    .language {
        margin-top: 16px;
        display: grid;
        grid-template-columns: minmax(280px, 0.9fr) minmax(0, 1.4fr);
        gap: 16px;
    }

    .links,
    .interactions {
        margin-top: 16px;
        display: grid;
        gap: 16px;
    }

    .links {
        grid-template-columns: minmax(0, 1fr) minmax(0, 1fr);
    }

    .interactions {
        grid-template-columns: minmax(280px, 0.9fr) minmax(0, 1.2fr) minmax(
                0,
                1fr
            );
    }

    .word-filter {
        width: 100%;
        box-sizing: border-box;
        margin-top: 10px;
    }

    .word-list {
        margin-top: 12px;
        display: flex;
        flex-direction: column;
        gap: 6px;
        max-height: 540px;
        overflow-y: auto;
    }

    .word-row {
        width: 100%;
        display: flex;
        justify-content: space-between;
        align-items: center;
        border: 1px solid #1f2937;
        background: #0b1220;
        color: #e2e8f0;
        border-radius: 10px;
        padding: 8px 10px;
        cursor: pointer;
        font: inherit;
    }

    .word-row.selected {
        border-color: #2563eb;
        background: #0f1f43;
    }

    .detail-columns {
        margin-top: 12px;
        display: grid;
        gap: 18px;
        grid-template-columns: repeat(2, minmax(0, 1fr));
    }

    .detail-columns h3 {
        margin: 0 0 10px;
        font-size: 0.95rem;
        color: #cbd5e1;
    }

    .bar-row {
        display: grid;
        grid-template-columns: minmax(120px, 1.1fr) minmax(100px, 2fr) auto;
        align-items: center;
        gap: 8px;
        margin-bottom: 8px;
        font-size: 0.85rem;
    }

    .mini-bar-track {
        background: #0b1220;
        border: 1px solid #1f2937;
        border-radius: 999px;
        height: 10px;
        overflow: hidden;
    }

    .mini-bar {
        height: 100%;
        border-radius: 999px;
        background: #38bdf8;
        min-width: 2px;
    }

    .mini-bar.chat {
        background: #8b5cf6;
    }

    @media (max-width: 980px) {
        .hero,
        .overview-top,
        .overview-bottom,
        .overview-bottom-secondary {
            grid-template-columns: 1fr;
            display: grid;
        }

        .filters {
            grid-template-columns: 1fr;
        }

        .language,
        .links,
        .interactions,
        .history,
        .detail-columns {
            grid-template-columns: 1fr;
        }
    }
</style>
