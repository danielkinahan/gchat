# gChat

gChat is a self-hosted ingest pipeline for consolidating chat exports from Discord, Facebook, and Signal into a DuckDB database for analysis.

## Features

- Normalizes Discord JSON exports
- Normalizes Facebook Messenger archive folders
- Normalizes Signal desktop backups (`main.jsonl`) and legacy Signal SQLite exports
- Reconciles people and channel themes through YAML config
- Writes a single DuckDB database with messages, channels, people, and name-change history

## Requirements

- Python 3.11+
- `duckdb`, `beautifulsoup4`, and `PyYAML` are installed from `pyproject.toml`

## Installation

```bash
pip install -e .
```

```bash
deno task check
```

## Project layout

```text
config/
  people.yaml
  themes.yaml
data/
  discord/*.json
  facebook/<chat folder>/message_*.html
  signal/db.sqlite
  signal/main.jsonl
```

Signal also works if its files are moved up one level into `data/` directly or
kept in a nested `data/signal/` folder:

```text
data/db.sqlite
data/<export folder>/main.jsonl
data/signal/main.jsonl
```

## Configuration

`config/people.yaml` maps platform identities to canonical people:

```yaml
people:
  - name: Alice
    color: "#E24B4A"
    identities:
      - platform: discord
        id: "123456789012345678"
      - platform: signal
        id: "+15555550101"
      - platform: facebook
        name: "Alice Example"
```

`config/themes.yaml` maps source/channel pairs to a shared theme:

```yaml
themes:
  - name: Main group chat
    channels:
      - source: "Discord: Example Server"
        channel: general
      - source: "Signal: signal"
        channel: "Main Group"
```

If `config/people.yaml` or `config/themes.yaml` is missing, gChat uses the corresponding `*.example.yaml` file.

## Building the database

```bash
uv run python -m gchat build --data-dir data --output gchat.duckdb
```

The build command scans `data/`, applies reconciliation rules from `config/` (or `--config-dir`), writes a fresh DuckDB file, and prints progress while it runs.

## Running the API

```bash
uv run python -m gchat serve --db gchat.duckdb --host 127.0.0.1 --port 8000
```

## Running in Docker

```bash
docker compose -f compose.yml up --build
```

The compose stack starts with the scheduler building the DuckDB file on startup,
then starts the API on `:8000` behind the web gateway on `:3000`.

The web gateway uses HTTP basic auth and proxies `/api/*` to the internal API
service, so only the web service needs a published port.

Set these required environment variables before running compose:

- `BASIC_AUTH_PASSWORD`

It also includes two recurring-job services:

- `scheduler` for periodic DuckDB rebuilds.
- `discord-exporter-scheduler` for periodic DiscordChatExporter runs.

### Scheduler configuration

The `scheduler` service handles all recurring jobs:

- `DB_REBUILD_CRON` (default: `0 3 */14 * *`) - periodic DuckDB rebuild schedule.
- `DISCORD_EXPORT_CRON` (default: `0 2 * * 0`) - periodic Discord export schedule.
- `DISCORD_EXPORT_COMMAND` (default: empty) - shell command to run for Discord exports. If empty, export job is skipped.

The scheduler container is based on Arch Linux and installs `signalbackup-tools` and `discord-chat-exporter-cli` from the AUR.

### Signal backup decryption

If your Signal data (`data/signal/sql/db.sqlite`) is encrypted, the scheduler automatically decrypts it
before each rebuild using the encryption key stored in `data/signal/config.json`.

The decrypted backup is written to `data/signal_decrypted/` (outside the Signal working directory to avoid conflicts with the running app).
The scheduler uses [signalbackup-tools](https://github.com/bepaald/signalbackup-tools) to decrypt the backup.
The encrypted file remains on disk; only the decrypted copy is used during import.

Example:

```bash
export BASIC_AUTH_PASSWORD='replace-this'
export DISCORD_EXPORT_COMMAND='DiscordChatExporter.Cli exportall --token "$DISCORD_BOT_TOKEN" --output "/data/discord"'
docker compose -f compose.yml up --build -d
```

If `DISCORD_EXPORT_COMMAND` is empty, the export job logs a skip and does not
run.

## Output tables

- `people`
- `platform_identities`
- `sources`
- `themes`
- `channels`
- `person_name_changes`
- `channel_name_changes`
- `messages`

## Facebook archive helper

gChat also includes a helper that finds Facebook group-chat archive folders matching configured Facebook identities and copies them into `data/facebook`:

```bash
gchat-copy-facebook-groups --source /path/to/Facebook\ Data/messages/inbox --dest data/facebook --config-dir .
```

Useful flags:

- `--min-participants` to change the minimum number of matched people
- `--yes` to skip confirmation
- `--overwrite` to replace an existing destination folder

## Development

```bash
python -m unittest
```

```bash
deno task dev
```

```bash
deno task build
```
