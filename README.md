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
  signal/<export folder>/main.jsonl
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
