SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS people (
  id INTEGER PRIMARY KEY,
  display_name TEXT NOT NULL,
  color TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS platform_identities (
  platform TEXT NOT NULL,
  platform_user_id TEXT NOT NULL,
  platform_username TEXT,
  person_id INTEGER NOT NULL,
  PRIMARY KEY (platform, platform_user_id)
);

CREATE TABLE IF NOT EXISTS sources (
  id INTEGER PRIMARY KEY,
  platform TEXT NOT NULL,
  name TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS themes (
  id INTEGER PRIMARY KEY,
  name TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS channels (
  id INTEGER PRIMARY KEY,
  source_id INTEGER NOT NULL,
  platform_channel_id TEXT NOT NULL,
  name TEXT,
  theme_id INTEGER
);

CREATE TABLE IF NOT EXISTS person_name_changes (
  id INTEGER PRIMARY KEY,
  person_id INTEGER NOT NULL,
  source_id INTEGER NOT NULL,
  kind TEXT NOT NULL,
  previous_name TEXT,
  new_name TEXT NOT NULL,
  ts TIMESTAMP NOT NULL,
  payload_json TEXT
);

CREATE TABLE IF NOT EXISTS channel_name_changes (
  id INTEGER PRIMARY KEY,
  channel_id INTEGER NOT NULL,
  source_id INTEGER NOT NULL,
  kind TEXT NOT NULL,
  previous_name TEXT,
  new_name TEXT NOT NULL,
  ts TIMESTAMP NOT NULL,
  payload_json TEXT
);

CREATE TABLE IF NOT EXISTS messages (
  id TEXT PRIMARY KEY,
  channel_id INTEGER NOT NULL,
  person_id INTEGER NOT NULL,
  ts TIMESTAMP NOT NULL,
  content TEXT,
  reply_to_id TEXT,
  word_count INTEGER,
  char_count INTEGER,
  attachment_count INTEGER DEFAULT 0,
  attachment_preview TEXT,
  reaction_count INTEGER DEFAULT 0,
  reaction_summary TEXT,
  reaction_details_json TEXT,
  is_edited BOOLEAN DEFAULT FALSE,
  is_system BOOLEAN NOT NULL DEFAULT FALSE,
  lang TEXT,
  sentiment REAL,
  conversation_id INTEGER
);

CREATE TABLE IF NOT EXISTS conversations (
  id INTEGER PRIMARY KEY,
  channel_id INTEGER NOT NULL,
  start_ts TIMESTAMP NOT NULL,
  end_ts TIMESTAMP NOT NULL,
  message_count INTEGER NOT NULL,
  participant_count INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS member_events (
  id INTEGER PRIMARY KEY,
  channel_id INTEGER NOT NULL,
  source_id INTEGER NOT NULL,
  kind TEXT NOT NULL,
  actor_person_id INTEGER,
  target_person_id INTEGER NOT NULL,
  ts TIMESTAMP NOT NULL,
  payload_json TEXT
);

CREATE TABLE IF NOT EXISTS person_stats (
  person_id INTEGER PRIMARY KEY,
  message_count INTEGER NOT NULL,
  unique_words INTEGER NOT NULL,
  total_words INTEGER NOT NULL,
  ttr DOUBLE NOT NULL,
  word_entropy DOUBLE NOT NULL,
  channel_count INTEGER NOT NULL,
  theme_count INTEGER NOT NULL,
  platform_count INTEGER NOT NULL,
  channel_hhi DOUBLE NOT NULL
);
"""
