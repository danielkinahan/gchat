# Self-Hosted Multi-Platform Chat Analytics — Project Brief

> Handoff document. Read this top to bottom before suggesting code. It captures the decisions already made and the constraints that shape them. Where something is still open, it's marked **OPEN**.

## 1. What this is

A self-hosted web app that ingests group-chat history from several messaging platforms, reconciles it into one canonical dataset, and serves interactive charts (think a private, multi-source version of the open-source `chat-analytics` project by mlomb). It's for a fixed group of ~10 friends, viewing analytics about their own conversations.

Scale: **~750k messages total, ~10 people.** This is small. Do not introduce distributed systems, queues, streaming, or a heavyweight database. The whole dataset fits comfortably in a single embedded-DB file, and analytical queries over it run in single-digit milliseconds.

## 2. Core principles

- **Data stays on the server.** Unlike `chat-analytics` (which ships all data to the browser and computes client-side), raw messages never leave the server. The frontend only ever receives small, aggregated JSON.
- **Don't over-engineer for the data size.** 750k rows is tiny; favor the simplest thing that works.
- **Reconciliation is configuration, not ML.** With ~10 people, mapping usernames to canonical identities and chats to themes is a hand-maintained config file.
- **Update cadence is monthly.** Batch, not real-time. The pipeline can fully rebuild the database from scratch each run.

## 3. Decided tech stack

- **Backend / pipeline:** Python.
- **Query engine + store:** DuckDB (single file, on disk).
- **Read API:** FastAPI (read-only; queries DuckDB live).
- **Frontend:** SvelteKit.
- **Charts:** Apache ECharts (Apache-2.0, no attribution requirement). Use the vanilla `echarts` package wired into Svelte via a small Svelte action (`use:chart`) that inits/updates/disposes the instance; `svelte-echarts` is an optional convenience wrapper. ECharts has built-in `calendar`, `heatmap`, and `graph` series, which cover the harder visualizations natively.
- **Static serving / reverse proxy:** Caddy or nginx.
- **Orchestration:** `docker compose`.

### Note on the visual reference

`chat-analytics` is the look-and-feel target. It is React + amCharts 5, plus `react-window` (virtualized lists), `react-countup` (animated counters), and `@tippyjs/react` (tooltips). **Because we chose Svelte, we reference its design and chart inventory.** Reproduce the equivalents in Svelte: virtualized lists (e.g. `svelte-virtual-list` or a windowing util), a count-up animation, and a tooltip approach (Floating UI / tippy.js core).

## 4. Data sources & the `data/` folder

Data fetching/exporting is already handled externally; this project consumes the exported files. A top-level `data/` folder holds a copy of each format's dataset:

```
data/
  facebook/       # Facebook HTML message archive (static, never updated) — ~75 group chats
  discord/        # DiscordChatExporter JSON exports — 2 servers, ~70 channels (ongoing)
  signal-export/  # one-time historical Signal export (sigtop JSON/text) — ~20 group chats
  signal-live/    # ongoing Signal messages captured live (signal-cli) — same ~20 chats
```

Format expectations for the normalizers:

- **Facebook:** HTML. Watch for mojibake — FB exports double-encode UTF-8 as Latin-1; fix on ingest with `text.encode('latin-1').decode('utf-8')`. Message IDs are unreliable, so derive a deterministic ID by hashing (author + timestamp + content).
- **Discord:** JSON from DiscordChatExporter. Native message IDs are stable; use them. Ongoing data arrives as monthly incremental exports — overlaps are possible, so ingestion must be idempotent.
- **Signal (historical + live):** JSON/text. **Signal has no server-side history** — the live capture is the only record going forward; never assume it can be re-fetched.

## 5. Architecture / pipeline

```
data/ (raw exports)
   -> normalize: one adapter per source format -> canonical rows
   -> reconcile: apply identity map (usernames -> people) and theme map (channels -> themes)
   -> load: idempotent upsert into DuckDB
   -> (monthly cron rebuilds the DuckDB file, then atomically swaps it in)
FastAPI read API -> live GROUP BY queries over DuckDB
SvelteKit SPA -> fetches aggregated JSON -> renders ECharts
```

The monthly job can rebuild the DB file offline and swap it atomically (single file = zero-downtime replace). Live cross-filtering is cheap enough at this scale that we do **not** precompute aggregates; the API runs `GROUP BY` directly on the messages table per request.

## 6. Canonical data model (DuckDB DDL)

```sql
-- Canonical people (~10 rows). `color` is a stable hex reused across every chart.
CREATE TABLE people (
    id           INTEGER PRIMARY KEY,
    display_name TEXT NOT NULL,
    color        TEXT NOT NULL
);

-- Username-reconciliation map (sourced from YAML, see §7).
CREATE TABLE platform_identities (
    platform          TEXT NOT NULL,        -- 'facebook' | 'discord' | 'signal'
    platform_user_id  TEXT NOT NULL,        -- native id (or username for FB)
    platform_username TEXT,
    person_id         INTEGER NOT NULL REFERENCES people(id),
    PRIMARY KEY (platform, platform_user_id)
);

-- One row per distinct origin.
CREATE TABLE sources (
    id       INTEGER PRIMARY KEY,
    platform TEXT NOT NULL,
    name     TEXT NOT NULL                  -- 'Discord: ServerA', 'FB archive', 'Signal export', 'Signal live'
);

-- Canonical "same theme" conversations (theme-reconciliation target).
CREATE TABLE themes (
    id   INTEGER PRIMARY KEY,
    name TEXT NOT NULL
);

-- Raw channels/chats, each mapped to a theme.
CREATE TABLE channels (
    id                  INTEGER PRIMARY KEY,
    source_id           INTEGER NOT NULL REFERENCES sources(id),
    platform_channel_id TEXT NOT NULL,
    name                TEXT,
    theme_id            INTEGER REFERENCES themes(id)
);

-- Fact table (~750k rows).
CREATE TABLE messages (
    id               TEXT PRIMARY KEY,      -- deterministic: native id, or hash(author+ts+content) for FB
    channel_id       INTEGER NOT NULL REFERENCES channels(id),
    person_id        INTEGER NOT NULL REFERENCES people(id),
    ts               TIMESTAMP NOT NULL,
    content          TEXT,
    reply_to_id      TEXT,                  -- for the interaction graph
    word_count       INTEGER,
    char_count       INTEGER,
    attachment_count INTEGER DEFAULT 0,
    reaction_count   INTEGER DEFAULT 0,
    lang             TEXT,                  -- OPEN: optional language detection on ingest
    sentiment        REAL                   -- OPEN: optional sentiment scoring on ingest
);
```

Idempotency: upsert on `messages.id` (`INSERT ... ON CONFLICT (id) DO UPDATE`, or a full rebuild each month, which sidesteps the issue entirely).

## 7. Reconciliation (the key differentiator)

Two hand-maintained YAML files, checked into the repo, drive the `platform_identities` and `channels.theme_id` mappings. This is what `chat-analytics` cannot do (it is single-platform, single-source).

```yaml
# people.yaml — map every platform identity to one canonical person
people:
  - name: Alice
    color: "#E24B4A"
    identities:
      - { platform: discord,  id: "112233445566" }
      - { platform: signal,   id: "+15555550101" }
      - { platform: facebook, id: "Alice Smith" }   # FB has no stable id; match on name
  # ... ~10 people

# themes.yaml — map each raw channel to a canonical theme
themes:
  - name: "Main group chat"
    channels:
      - { source: "Discord: ServerA", channel: "general" }
      - { source: "Signal export",    channel: "The Crew" }
      - { source: "FB archive",       channel: "the crew \U0001F389" }
  # ... per theme
```

The normalizer resolves every incoming message's author and channel through these maps before insert. Unmapped identities/channels should fail loudly (or land in an "unmapped" bucket for review) rather than silently dropping data.

## 8. Read API (FastAPI)

Read-only. Every endpoint accepts the same optional filter params, applied as SQL `WHERE` clauses, so one filter bar in the UI drives every chart:

- `from`, `to` — ISO dates (range)
- `people` — comma-separated `people.id` list
- `themes` — comma-separated `themes.id` list

Endpoints (each returns small aggregated JSON):

- `GET /api/overview` — headline counters: total messages, per-person totals, date span.
- `GET /api/messages-over-time?granularity=day|week|month` — time series.
- `GET /api/calendar` — per-day counts (for the GitHub-style year heatmap).
- `GET /api/activity-heatmap` — hour-of-day x day-of-week matrix.
- `GET /api/top-people` — message counts per person.
- `GET /api/top-words`, `GET /api/top-emojis` — ranked lists, paginated.
- `GET /api/interactions` — reply/mention edges between people (graph nodes + edges).
- `GET /api/sentiment-over-time` — time series (only if sentiment is computed).

## 9. Frontend / display spec (SvelteKit + ECharts)

Chart inventory and the ECharts feature behind each:

- **Messages over time / growth** — line/area series.
- **Year calendar heatmap** — ECharts `calendar` coordinate + `heatmap` series.
- **Hour x weekday activity grid** — ECharts `heatmap` on category x category axes.
- **Per-person rankings** — horizontal `bar` series.
- **Most-used words / emojis** — virtualized scrollable lists (not charts); optional word cloud via `echarts-wordcloud`.
- **Interaction graph (who replies to/mentions whom)** — ECharts `graph` series, force layout.
- **Sentiment over time** — line series.
- **Headline numbers** — count-up animation on big stat figures.

Cross-cutting display details that produce the `chat-analytics` "feel":

- **Stable per-person color.** Use `people.color` everywhere so each person is the same color across every chart and every platform. (This is the payoff of reconciliation.)
- **One global filter bar** (date-range brush + multi-select people + multi-select themes) whose state drives all charts through a single shared query layer.
- **Tabbed sections** mirroring chat-analytics: messages, language, emoji, links, interaction, sentiment, timeline.
- **Consistent hover tooltips** on every data point.
- **Virtualize long lists** (top words/emojis can be thousands of rows).

## 10. Deployment

`docker compose` with three concerns:

1. **Static SPA** — built SvelteKit output served by Caddy/nginx.
2. **Read API** — the FastAPI container.
3. **DuckDB file** — mounted as a volume; the monthly job rebuilds it offline and swaps it in atomically (single file makes this trivial and downtime-free).

## 11. Suggested build order

1. Define the canonical schema in DuckDB; write the two reconciliation YAMLs with a couple of real people/themes.
2. Build the Facebook normalizer first (static, self-contained, exercises the encoding + hashed-ID + reconciliation path end to end).
3. Add the Discord normalizer, then the two Signal normalizers.
4. Stand up the FastAPI read layer with `/api/overview` and `/api/messages-over-time`.
5. Scaffold SvelteKit + the ECharts Svelte action; render those two endpoints.
6. Add the global filter bar wired to one shared query store.
7. Fill in remaining charts (calendar, activity heatmap, interaction graph, lists).
8. Containerize and wire up the monthly rebuild job.

## 12. Open decisions

- **OPEN:** language detection and sentiment scoring on ingest (chat-analytics uses FastText for language + an AFINN-style lexicon for sentiment). Decide whether to include these; they add ingest-time cost but enable the language/sentiment tabs.
- **OPEN:** whether arbitrary cross-filtering is needed, or a fixed set of precomputed views would suffice. (Default assumption: live queries, since the data is small.)
- **OPEN:** attachment/media handling — currently only counts are stored, not the media itself.
