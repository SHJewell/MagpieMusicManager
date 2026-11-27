"""
Schema and migrations. The schema version is stored in PRAGMA user_version.

To change the schema: append a SQL script to MIGRATIONS (index i upgrades
version i to i + 1). Never edit a migration that has shipped.
"""

_V1 = '''
    CREATE TABLE tracks (
        id         INTEGER PRIMARY KEY,
        path       TEXT NOT NULL UNIQUE COLLATE NOCASE,
        size       INTEGER,
        mtime_ns   INTEGER,
        artist     TEXT,
        album      TEXT,
        length_ms  INTEGER,
        updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
    );

    -- History of changes made to files (kind: 'rename', later 'retag', ...)
    CREATE TABLE file_events (
        id        INTEGER PRIMARY KEY,
        track_id  INTEGER REFERENCES tracks(id) ON DELETE SET NULL,
        kind      TEXT NOT NULL,
        old_path  TEXT COLLATE NOCASE,
        new_path  TEXT COLLATE NOCASE,
        at        TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
    );
    CREATE INDEX file_events_old_path ON file_events(old_path);
    CREATE INDEX file_events_track ON file_events(track_id);
'''

_V2 = '''
    -- Folders whose .m3u/.m3u8 files are tracked
    CREATE TABLE playlist_folders (
        path TEXT PRIMARY KEY COLLATE NOCASE
    );

    CREATE TABLE playlists (
        id         INTEGER PRIMARY KEY,
        path       TEXT NOT NULL UNIQUE COLLATE NOCASE,
        size       INTEGER,
        mtime_ns   INTEGER,
        scanned_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
    );

    -- One row per track line, in file order. entry = the line as written;
    -- track_id is NULL for stream URLs.
    CREATE TABLE playlist_entries (
        playlist_id INTEGER NOT NULL REFERENCES playlists(id) ON DELETE CASCADE,
        position    INTEGER NOT NULL,
        track_id    INTEGER REFERENCES tracks(id) ON DELETE SET NULL,
        entry       TEXT NOT NULL,
        title       TEXT,
        PRIMARY KEY (playlist_id, position)
    );
    CREATE INDEX playlist_entries_track ON playlist_entries(track_id);
'''

MIGRATIONS = [_V1, _V2]
SCHEMA_VERSION = len(MIGRATIONS)


def migrate(conn):
    """Bring the database up to SCHEMA_VERSION. Returns the version it was at.
    Each step is one transaction, so a failed step leaves the previous version."""
    version = conn.execute('PRAGMA user_version').fetchone()[0]
    conn.commit()
    for target in range(version, SCHEMA_VERSION):
        try:
            conn.executescript(f'BEGIN; {MIGRATIONS[target]}; '
                               f'PRAGMA user_version = {target + 1}; COMMIT;')
        except Exception:
            conn.rollback()
            raise
    return version
