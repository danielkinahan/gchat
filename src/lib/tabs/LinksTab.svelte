<script lang="ts">
    import LinkPreview from "$lib/LinkPreview.svelte";
    import { fetchJson } from "$lib/api";
    import {
        extractMessageLinks,
        isUrlOnlyMessage,
    } from "$lib/messageLinks";

    type DomainExample = {
        id: string;
        ts: string | null;
        content: string;
        person_name: string;
        person_color: string;
        channel_name: string;
        source_name: string;
    };

    export let active: boolean;
    export let filterSignature: string;
    export let currentFilterParams: () => URLSearchParams;
    export let topLimit: number = 10;
    export let openInContext: (messageId: string) => void;

    let isLoading = false;
    let error = "";
    let domainSearch = "";
    let linkedDomains: Array<{ domain: string; count: number }> = [];
    let linksByAuthor: Array<{
        id: number;
        display_name: string;
        color: string;
        count: number;
    }> = [];
    let loadedFilterKey: string | null = null;

    let selectedDomain = "";
    let domainExamples: DomainExample[] = [];
    let showDomainExamples = false;
    let isLoadingDomainExamples = false;
    let isAppendingDomainExamples = false;
    let hasMoreDomainExamples = false;
    let domainExamplesError = "";
    let domainExamplesRequestId = 0;

    $: normalizedDomainSearch = domainSearch.trim().toLowerCase();
    $: filteredDomains = normalizedDomainSearch
        ? linkedDomains.filter((item) =>
              item.domain.includes(normalizedDomainSearch),
          )
        : linkedDomains;
    $: visibleDomains = filteredDomains.slice(0, topLimit);

    async function loadLinksData() {
        const filterKey = currentFilterParams().toString();
        if (isLoading || loadedFilterKey === filterKey) return;
        isLoading = true;
        error = "";
        try {
            const baseParams = currentFilterParams();
            const domainsParams = new URLSearchParams(baseParams);
            domainsParams.set("limit", "200");
            const authorsParams = new URLSearchParams(baseParams);
            authorsParams.set("limit", String(topLimit));
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
            error = err instanceof Error ? err.message : "Failed to load links";
        } finally {
            loadedFilterKey = filterKey;
            isLoading = false;
        }
    }

    async function selectDomain(domain: string) {
        if (selectedDomain === domain && showDomainExamples) {
            showDomainExamples = false;
            return;
        }
        selectedDomain = domain;
        domainExamples = [];
        hasMoreDomainExamples = false;
        showDomainExamples = true;
        await loadDomainExamples(0, false);
    }

    async function loadDomainExamples(offset = 0, append = false) {
        if (!selectedDomain) return;
        const requestId = ++domainExamplesRequestId;
        if (append) {
            isAppendingDomainExamples = true;
        } else {
            isLoadingDomainExamples = true;
        }
        domainExamplesError = "";
        try {
            const params = currentFilterParams();
            params.set("domain", selectedDomain);
            params.set("limit", offset > 0 ? "5" : "6");
            if (offset > 0) params.set("offset", String(offset));
            const response = await fetchJson<{
                domain: string;
                has_more: boolean;
                messages: DomainExample[];
            }>(`/api/domain-examples?${params.toString()}`);
            if (requestId !== domainExamplesRequestId) return;
            if (response.domain === selectedDomain) {
                domainExamples = append
                    ? [...domainExamples, ...response.messages]
                    : response.messages;
                hasMoreDomainExamples = response.has_more;
            }
        } catch (err) {
            if (requestId !== domainExamplesRequestId) return;
            domainExamplesError =
                err instanceof Error
                    ? err.message
                    : "Failed to load example messages";
        } finally {
            if (requestId === domainExamplesRequestId) {
                isLoadingDomainExamples = false;
                isAppendingDomainExamples = false;
            }
        }
    }

    async function showMoreDomainExamples() {
        const oldScroll = typeof window !== "undefined" ? window.scrollY : 0;
        const oldHeight =
            typeof document !== "undefined"
                ? document.documentElement.scrollHeight
                : 0;
        await loadDomainExamples(domainExamples.length, true);
        const newHeight =
            typeof document !== "undefined"
                ? document.documentElement.scrollHeight
                : 0;
        const delta = newHeight - oldHeight;
        if (typeof window !== "undefined" && delta > 0) {
            window.scrollTo({ top: oldScroll + delta });
        }
    }

    let lastDomainExamplesFilter = filterSignature;
    $: if (filterSignature !== lastDomainExamplesFilter) {
        lastDomainExamplesFilter = filterSignature;
        showDomainExamples = false;
        selectedDomain = "";
        domainExamples = [];
        hasMoreDomainExamples = false;
    }

    $: if (active && !isLoading && loadedFilterKey !== filterSignature) {
        void loadLinksData();
    }
</script>

<section class="links" class:tab-hidden={!active}>
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
        {#if error}
            <p class="muted">{error}</p>
        {:else if isLoading}
            <p class="muted">Loading links...</p>
        {:else}
            <div class="rank-list">
                {#each visibleDomains as domain}
                    <button
                        type="button"
                        class="rank-row domain-row"
                        class:selected={selectedDomain === domain.domain}
                        on:click={() => selectDomain(domain.domain)}
                    >
                        <span class="rank-name">{domain.domain}</span>
                        <div class="rank-track">
                            <div
                                class="rank-fill domain"
                                style={`width:${(domain.count / Math.max(filteredDomains[0]?.count || 1, 1)) * 100}%`}
                            ></div>
                        </div>
                        <strong>{domain.count.toLocaleString()}</strong>
                    </button>
                {/each}
            </div>
        {/if}
    </div>

    <div class="panel rank-panel">
        <h2>Most links sent by author</h2>
        {#if isLoading}
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

    {#if showDomainExamples && selectedDomain}
        <div class="panel example-panel domain-examples-panel">
            <div class="panel-head">
                <h2>
                    Messages linking
                    <a
                        href={`https://${selectedDomain}`}
                        target="_blank"
                        rel="noreferrer">{selectedDomain}</a
                    >
                </h2>
                <button
                    type="button"
                    class="examples-toggle"
                    on:click={() => (showDomainExamples = false)}
                    >Hide</button
                >
            </div>
            {#if domainExamplesError}
                <p class="muted">{domainExamplesError}</p>
            {:else if isLoadingDomainExamples}
                <p class="muted">Loading example messages...</p>
            {:else if domainExamples.length === 0}
                <p class="muted">No example messages found.</p>
            {:else}
                <div class="example-list">
                    {#each domainExamples as message}
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
                {#if hasMoreDomainExamples}
                    <button
                        type="button"
                        class="examples-toggle"
                        on:click={showMoreDomainExamples}
                        disabled={isAppendingDomainExamples}
                    >
                        {isAppendingDomainExamples
                            ? "Loading..."
                            : "Show 5 more messages"}
                    </button>
                {/if}
            {/if}
        </div>
    {/if}
</section>
