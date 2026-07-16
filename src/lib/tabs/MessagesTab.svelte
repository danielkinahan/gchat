<script lang="ts">
    import {
        fetchJson,
        type Overview,
        type ReactionsOverTime,
        type TopPeople,
    } from "$lib/api";
    import {
        formatDuration,
        formatGapRange,
        formatMostActiveYear,
        formatMostActiveMonth,
        formatMostActiveDay,
        formatMostActiveHour,
    } from "$lib/formatters";
    import {
        hourlyTotals,
        monthlyTotals,
    } from "$lib/dashboardMetrics";
    import ReactionTrendChart from "$lib/components/ReactionTrendChart.svelte";
    import PlatformTrendChart from "$lib/components/PlatformTrendChart.svelte";
    import type { PlatformOverTime } from "$lib/api";

    type CountMetric = "messages" | "words" | "conversations";

    type MessageTabData = {
        overview: Overview;
        topPeople: TopPeople;
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

    export let active: boolean;
    export let filterSignature: string;
    export let currentFilterParams: () => URLSearchParams;
    export let topLimit: number = 10;
    export let openInContext: (messageId: string) => void;
    export let baseData: {
        overview: Overview;
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
        platformOverTime: {
            granularity: string;
            platforms: string[];
            points: Array<{
                bucket: string;
                counts: Record<string, number>;
            }>;
        };
        reactionsOverTime: ReactionsOverTime;
    };

    const weekdayLabels = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"];

    let messageMetric: CountMetric = "messages";
    let overviewData: MessageTabData = {
        overview: baseData.overview,
        topPeople: { items: baseData.overview.people.slice(0, topLimit) },
        activityHeatmap: baseData.activityHeatmap,
        messagesByMonth: baseData.messagesByMonth,
        messagesByHour: baseData.messagesByHour,
        topChats: baseData.topChats,
        topThemes: baseData.topThemes,
    };
    let wordMetricData: MessageTabData | null = null;
    let wordMetricError = "";
    let isLoadingWordMetric = false;
    let wordMetricRequestId = 0;
    let loadedWordMetricFilterKey: string | null = null;

    let conversationMetricData: MessageTabData | null = null;
    let conversationMetricError = "";
    let isLoadingConversationMetric = false;
    let conversationMetricRequestId = 0;
    let loadedConversationMetricFilterKey: string | null = null;

    type MessagesTopOverride = {
        topChats: MessageTabData["topChats"];
        topThemes: MessageTabData["topThemes"];
    };
    let messagesTopOverride: MessagesTopOverride | null = null;
    let messagesTopOverrideKey: string | null = null;
    let isLoadingMessagesTop = false;
    let messagesTopRequestId = 0;

    function messageCountLabel(metric: CountMetric): string {
        if (metric === "words") return "words";
        if (metric === "conversations") return "conversations";
        return "messages";
    }

    function messageCountTitle(metric: CountMetric): string {
        if (metric === "words") return "Word count";
        if (metric === "conversations") return "Conversation count";
        return "Message count";
    }

    async function fetchMetricTabData(
        metric: "words" | "conversations",
    ): Promise<MessageTabData> {
        const baseParams = currentFilterParams();
        baseParams.set("metric", metric);
        const query = baseParams.toString();
        const [
            overview,
            activityHeatmap,
            platformOverTime,
            topChats,
            topThemes,
        ] = await Promise.all([
            fetchJson<Overview>(`/api/overview?${query}`),
            fetchJson<{
                points: Array<{
                    weekday: number;
                    hour: number;
                    message_count: number;
                }>;
            }>(`/api/activity-heatmap?${query}`),
            fetchJson<PlatformOverTime>(
                `/api/platform-over-time?granularity=month&${query}`,
            ),
            fetchJson<{
                items: Array<{
                    id: number;
                    name: string;
                    theme_name: string;
                    message_count: number;
                }>;
            }>(`/api/top-chats?limit=${topLimit}&${query}`),
            fetchJson<{
                items: Array<{
                    id: number;
                    name: string;
                    message_count: number;
                }>;
            }>(`/api/top-themes?limit=${topLimit}&${query}`),
        ]);
        return {
            overview,
            topPeople: { items: overview.people.slice(0, topLimit) },
            activityHeatmap,
            messagesByMonth: monthlyTotals(platformOverTime),
            messagesByHour: hourlyTotals(activityHeatmap.points),
            topChats,
            topThemes,
        };
    }

    async function loadWordMetricData() {
        const filterKey = filterSignature;
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
            const result = await fetchMetricTabData("words");
            if (requestId !== wordMetricRequestId) return;
            wordMetricData = result;
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

    async function loadConversationMetricData() {
        const filterKey = filterSignature;
        if (
            isLoadingConversationMetric ||
            (loadedConversationMetricFilterKey === filterKey &&
                conversationMetricData)
        )
            return;
        isLoadingConversationMetric = true;
        conversationMetricError = "";
        conversationMetricData = null;
        const requestId = ++conversationMetricRequestId;
        try {
            const result = await fetchMetricTabData("conversations");
            if (requestId !== conversationMetricRequestId) return;
            conversationMetricData = result;
            overviewData = conversationMetricData;
            loadedConversationMetricFilterKey = filterKey;
        } catch (err) {
            if (requestId !== conversationMetricRequestId) return;
            conversationMetricError =
                err instanceof Error
                    ? err.message
                    : "Failed to load conversation counts";
        } finally {
            if (requestId === conversationMetricRequestId) {
                loadedConversationMetricFilterKey = filterKey;
                isLoadingConversationMetric = false;
            }
        }
    }

    function setMessageMetric(metric: CountMetric) {
        messageMetric = metric;
        if (
            metric === "words" &&
            active &&
            (loadedWordMetricFilterKey !== filterSignature || !wordMetricData)
        ) {
            void loadWordMetricData();
        }
        if (
            metric === "conversations" &&
            active &&
            (loadedConversationMetricFilterKey !== filterSignature ||
                !conversationMetricData)
        ) {
            void loadConversationMetricData();
        }
    }

    $: if (
        messageMetric === "words" &&
        active &&
        !isLoadingWordMetric &&
        loadedWordMetricFilterKey !== filterSignature
    ) {
        void loadWordMetricData();
    }

    $: if (
        messageMetric === "conversations" &&
        active &&
        !isLoadingConversationMetric &&
        loadedConversationMetricFilterKey !== filterSignature
    ) {
        void loadConversationMetricData();
    }

    async function loadMessagesTopOverride() {
        const filterKey = filterSignature;
        if (
            isLoadingMessagesTop ||
            messagesTopOverrideKey === filterKey
        )
            return;
        isLoadingMessagesTop = true;
        const requestId = ++messagesTopRequestId;
        try {
            const baseParams = currentFilterParams();
            const query = baseParams.toString();
            const [topChats, topThemes] = await Promise.all([
                fetchJson<MessageTabData["topChats"]>(
                    `/api/top-chats?limit=${topLimit}&${query}`,
                ),
                fetchJson<MessageTabData["topThemes"]>(
                    `/api/top-themes?limit=${topLimit}&${query}`,
                ),
            ]);
            if (requestId !== messagesTopRequestId) return;
            messagesTopOverride = { topChats, topThemes };
            messagesTopOverrideKey = filterKey;
        } catch {
            // fall back to base data silently
        } finally {
            if (requestId === messagesTopRequestId) {
                isLoadingMessagesTop = false;
            }
        }
    }

    $: if (
        messageMetric === "messages" &&
        active &&
        topLimit !== 10 &&
        !isLoadingMessagesTop &&
        messagesTopOverrideKey !== filterSignature
    ) {
        void loadMessagesTopOverride();
    }

    $: if (messageMetric === "messages") {
        overviewData = {
            overview: baseData.overview,
            topPeople: {
                items: baseData.overview.people.slice(0, topLimit),
            },
            activityHeatmap: baseData.activityHeatmap,
            messagesByMonth: baseData.messagesByMonth,
            messagesByHour: baseData.messagesByHour,
            topChats:
                topLimit !== 10 && messagesTopOverride
                    ? messagesTopOverride.topChats
                    : baseData.topChats,
            topThemes:
                topLimit !== 10 && messagesTopOverride
                    ? messagesTopOverride.topThemes
                    : baseData.topThemes,
        };
    }
    $: if (messageMetric === "words" && wordMetricData) {
        overviewData = wordMetricData;
    }
    $: if (messageMetric === "conversations" && conversationMetricData) {
        overviewData = conversationMetricData;
    }
    $: overviewMetricLabel = messageCountLabel(messageMetric);
    $: overviewMetricTitle = messageCountTitle(messageMetric);
    $: messageStats = overviewData.overview.message_stats;
    $: gapRange = formatGapRange(
        messageStats.longest_period_without_messages_start,
        messageStats.longest_period_without_messages_end,
    );

    function maxMessageCount(points: Array<{ message_count: number }>): number {
        return Math.max(...points.map((point) => point.message_count || 0), 1);
    }

    $: monthLabelStep = Math.max(
        1,
        Math.ceil(overviewData.messagesByMonth.points.length / 8),
    );
    $: monthMax = maxMessageCount(overviewData.messagesByMonth.points);

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
</script>

{#if messageMetric === "words" && !wordMetricData}
    <section class="overview-top" class:tab-hidden={!active}>
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
{:else if messageMetric === "conversations" && !conversationMetricData}
    <section class="overview-top" class:tab-hidden={!active}>
        <div class="panel timeline-panel">
            <div class="panel-head">
                <h2>Conversation counts</h2>
            </div>
            {#if isLoadingConversationMetric}
                <p class="muted">Loading conversation counts...</p>
            {:else}
                <p class="muted">
                    {conversationMetricError ||
                        "Conversation counts are unavailable."}
                </p>
            {/if}
        </div>
    </section>
{:else}
    <section class="overview-top" class:tab-hidden={!active}>
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
                        on:click={() => setMessageMetric("words")}>Words</button
                    >
                    <button
                        type="button"
                        class:active={messageMetric === "conversations"}
                        on:click={() => setMessageMetric("conversations")}
                        >Conversations</button
                    >
                </div>
            </div>
            <div class="timeline-plot">
                <div class="timeline-axis">
                    <span>{Math.round(monthMax).toLocaleString()}</span>
                    <span
                        >{Math.round(monthMax * 0.75).toLocaleString()}</span
                    >
                    <span>{Math.round(monthMax * 0.5).toLocaleString()}</span>
                    <span
                        >{Math.round(monthMax * 0.25).toLocaleString()}</span
                    >
                    <span>0</span>
                </div>
                <div class="timeline-chart">
                    {#each overviewData.messagesByMonth.points as point, i}
                        <div
                            class="timeline-bar-wrap hover-title"
                            title={`${new Date(point.month).toLocaleDateString("en-US", { month: "long", year: "numeric" })}: ${point.message_count.toLocaleString()} ${overviewMetricLabel}`}
                        >
                            <div class="timeline-bar-slot">
                                <div
                                    class="timeline-bar"
                                    style={`height:${(point.message_count / monthMax) * 100}%`}
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
            <h2>
                {messageMetric === "messages"
                    ? "Message statistics"
                    : `${overviewMetricTitle} statistics`}
            </h2>
            <div class="stats-list">
                <div>
                    <span>Total {overviewMetricLabel} sent</span><strong
                        >{overviewData.overview.total_messages.toLocaleString()}</strong
                    >
                </div>
                {#if messageMetric === "messages"}
                    <div>
                        <span>✏️ with text</span><strong
                            >{messageStats.with_text.toLocaleString()}</strong
                        >
                    </div>
                    <div>
                        <span>🔗 with links</span><strong
                            >{messageStats.with_links.toLocaleString()}</strong
                        >
                    </div>
                    <div>
                        <span>📷 with images</span><strong
                            >{messageStats.with_images.toLocaleString()}</strong
                        >
                    </div>
                    <div>
                        <span>👾 with GIFs</span><strong
                            >{messageStats.with_gifs.toLocaleString()}</strong
                        >
                    </div>
                    <div>
                        <span>📹 with videos</span><strong
                            >{messageStats.with_videos.toLocaleString()}</strong
                        >
                    </div>
                    <div>
                        <span>🎉 with stickers</span><strong
                            >{messageStats.with_stickers.toLocaleString()}</strong
                        >
                    </div>
                    <div>
                        <span>🎵 with audio files</span><strong
                            >{messageStats.with_audio_files.toLocaleString()}</strong
                        >
                    </div>
                    <div>
                        <span>📄 with documents</span><strong
                            >{messageStats.with_documents.toLocaleString()}</strong
                        >
                    </div>
                    <div>
                        <span>📁 with other files</span><strong
                            >{messageStats.with_other_files.toLocaleString()}</strong
                        >
                    </div>
                    <div>
                        <span>Edited messages</span><strong
                            >{messageStats.edited_messages.toLocaleString()}</strong
                        >
                    </div>
                    <div>
                        <span>Average messages per day</span><strong
                            >{messageStats.average_per_day.toFixed(2)}</strong
                        >
                    </div>
                    <div>
                        <span>Longest period without messages</span>
                        {#if gapRange}
                            <strong class="hover-title" title={gapRange}>
                                {formatDuration(
                                    messageStats.longest_period_without_messages_seconds,
                                )}
                            </strong>
                        {:else}
                            <strong
                                >{formatDuration(
                                    messageStats.longest_period_without_messages_seconds,
                                )}</strong
                            >
                        {/if}
                    </div>
                    <div>
                        <span>Longest active conversation</span>
                        {#if messageStats.longest_active_conversation_message_id}
                            <button
                                type="button"
                                class="stat-action hover-title"
                                title="View the start of this conversation"
                                on:click={() =>
                                    openInContext(
                                        messageStats.longest_active_conversation_message_id!,
                                    )}
                            >
                                {formatDuration(
                                    messageStats.longest_active_conversation_seconds,
                                )}
                            </button>
                        {:else}
                            <strong
                                >{formatDuration(
                                    messageStats.longest_active_conversation_seconds,
                                )}</strong
                            >
                        {/if}
                    </div>
                    <div>
                        <span>Most active year</span><strong
                            >{formatMostActiveYear(
                                messageStats.most_active_year.bucket,
                            )}</strong
                        >
                    </div>
                    <div>
                        <span>Most active month</span><strong
                            >{formatMostActiveMonth(
                                messageStats.most_active_month.bucket,
                            )}</strong
                        >
                    </div>
                    <div>
                        <span>Most active day</span><strong
                            >{formatMostActiveDay(
                                messageStats.most_active_day.bucket,
                            )}</strong
                        >
                    </div>
                    <div>
                        <span>Most active hour</span><strong
                            >{formatMostActiveHour(
                                messageStats.most_active_hour.bucket,
                            )}</strong
                        >
                    </div>
                    <div>
                        <span
                            >💬 Conversations
                            <small class="hint"
                                >(30 min idle = new convo)</small
                            ></span
                        ><strong
                            >{messageStats.conversation_count.toLocaleString()}</strong
                        >
                    </div>
                    <div>
                        <span>Avg messages per conversation</span><strong
                            >{messageStats.avg_messages_per_conversation.toFixed(
                                1,
                            )}</strong
                        >
                    </div>
                    <div>
                        <span>Longest conversation</span><strong
                            >{messageStats.longest_conversation_message_count.toLocaleString()}
                            messages</strong
                        >
                    </div>
                {:else}
                    <div>
                        <span>Average {overviewMetricLabel} per day</span
                        ><strong
                            >{overviewData.overview.message_stats.average_per_day.toFixed(
                                2,
                            )}</strong
                        >
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
                {/if}
            </div>
        </div>
    </section>

    <section class="overview-bottom" class:tab-hidden={!active}>
        <div class="panel split-panel">
            <h2>{overviewMetricTitle} by week day & hour</h2>
            <div class="weekday-chart">
                {#each weekdayLabels as label, i}
                    <div
                        class="weekday-bar-wrap hover-title"
                        title={`${label}: ${weekdayTotals[i].toLocaleString()} ${overviewMetricLabel}`}
                    >
                        <div
                            class="weekday-bar"
                            style={`height:${(weekdayTotals[i] / weekdayMax) * 100}%`}
                        ></div>
                        <span>{label}</span>
                    </div>
                {/each}
            </div>
            <div class="hour-chart">
                {#each hourTotals as count, hour}
                    <div
                        class="hour-bar-wrap hover-title"
                        title={`${hour}:00 - ${count.toLocaleString()} ${overviewMetricLabel}`}
                    >
                        <div class="hour-bar-slot">
                            <div
                                class="hour-bar"
                                style={`height:${(count / hourTotalsMax) * 100}%`}
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
                    <div
                        class="rank-row hover-title"
                        title={`${person.display_name}: ${person.message_count.toLocaleString()} ${overviewMetricLabel}`}
                    >
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

    <section class="overview-bottom-secondary" class:tab-hidden={!active}>
        <div class="panel rank-panel">
            <h2>{overviewMetricTitle} sent by channel</h2>
            <div class="rank-list">
                {#each overviewData.topChats.items as chat}
                    <div
                        class="rank-row hover-title"
                        title={`${chat.name}: ${chat.message_count.toLocaleString()} ${overviewMetricLabel}`}
                    >
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
                    <div
                        class="rank-row hover-title"
                        title={`${theme.name}: ${theme.message_count.toLocaleString()} ${overviewMetricLabel}`}
                    >
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

    <section class="overview-bottom-secondary" class:tab-hidden={!active}>
        <PlatformTrendChart data={baseData.platformOverTime} />

        <ReactionTrendChart data={baseData.reactionsOverTime} />
    </section>
{/if}
