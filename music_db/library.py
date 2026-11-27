"""
Library: connection to the shared database plus the operations apps use.

One Library object per thread (sqlite connections aren't shared between
threads). Several processes can use the file at once: it runs in WAL mode,
so readers don't block the writer, and a busy writer is waited on briefly.
"""
import json
import os
import sqlite3

from .schema import SCHEMA_VERSION, migrate

DB_FILENAME = 'library.sqlite'
ENV_DB_PATH = 'MUSIC_DB_PATH'
BUSY_TIMEOUT_S = 5
MAX_RENAME_HOPS = 50


class LibraryError(Exception):
    pass


def default_db_path():
    """Shared location: $MUSIC_DB_PATH, else %APPDATA%/MagpieMusicManager/library.sqlite"""
    override = os.environ.get(ENV_DB_PATH)
    if override:
        return override
    base = os.environ.get('APPDATA') or os.path.expanduser('~')
    return os.path.join(base, 'MagpieMusicManager', DB_FILENAME)


def normalize_path(path):
    """The form paths are stored in: absolute, one kind of separator.
    (Case is kept for display; the database compares paths case-insensitively.)"""
    return os.path.normpath(os.path.abspath(path))


def _is_under(path, folder):
    path, folder = os.path.normcase(path), os.path.normcase(folder)
    return path == folder or path.startswith(folder.rstrip(os.sep) + os.sep)


class Library:
    def __init__(self, conn, path, readonly):
        self._conn = conn
        self.path = path
        # Set when the file was made by a newer music_db than this one:
        # reads still work, writes are skipped so nothing newer gets damaged.
        self.readonly = readonly

    @classmethod
    def open(cls, path=None):
        path = path or default_db_path()
        try:
            os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
            conn = sqlite3.connect(path, timeout=BUSY_TIMEOUT_S)
            conn.execute('PRAGMA journal_mode = WAL')
            conn.execute('PRAGMA foreign_keys = ON')
            version = conn.execute('PRAGMA user_version').fetchone()[0]
            readonly = version > SCHEMA_VERSION
            if not readonly:
                migrate(conn)
        except (sqlite3.Error, OSError) as e:
            raise LibraryError(f'Could not open music library {path}: {e}') from e
        return cls(conn, path, readonly)

    def close(self):
        if self._conn is not None:
            try:
                self.commit()
            finally:
                self._conn.close()
                self._conn = None

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()

    def commit(self):
        try:
            self._conn.commit()
        except sqlite3.Error as e:
            raise LibraryError(e) from e

    # ---- File info ----

    def get_info(self, path, size, mtime_ns):
        """Stored info for path, or None if missing or the file has changed
        since (size or mtime differ)."""
        row = self._query_one(
            'SELECT artist, album, length_ms FROM tracks '
            'WHERE path = ? AND size = ? AND mtime_ns = ?',
            (normalize_path(path), size, mtime_ns))
        if row is None:
            return None
        return {'artist': row[0] or '', 'album': row[1] or '',
                'length_ms': row[2] if row[2] is not None else -1}

    def upsert_info(self, path, size, mtime_ns, artist, album, length_ms):
        """Add the file or update what's stored for it (keeps its track id)"""
        if self.readonly:
            return
        self._execute(
            'INSERT INTO tracks (path, size, mtime_ns, artist, album, length_ms) '
            'VALUES (?, ?, ?, ?, ?, ?) '
            'ON CONFLICT(path) DO UPDATE SET size = excluded.size, '
            'mtime_ns = excluded.mtime_ns, artist = excluded.artist, '
            'album = excluded.album, length_ms = excluded.length_ms, '
            'updated_at = CURRENT_TIMESTAMP',
            (normalize_path(path), size, mtime_ns, artist, album, length_ms))

    def track_stamps(self):
        """(id, path, size, mtime_ns) of every track; size/mtime_ns are None
        for tracks whose info was never read. Never-read tracks come first."""
        cur = self._execute('SELECT id, path, size, mtime_ns FROM tracks '
                            'ORDER BY size IS NOT NULL, path')
        return cur.fetchall()

    def set_info(self, track_id, path, size, mtime_ns, artist, album, length_ms):
        """Store info read from the file at path, by track id. Does nothing if
        the track has since moved (renamed while it was being read), so a stale
        read never creates a track at the old path. Returns True if stored."""
        if self.readonly:
            return False
        cur = self._execute(
            'UPDATE tracks SET size = ?, mtime_ns = ?, artist = ?, album = ?, '
            'length_ms = ?, updated_at = CURRENT_TIMESTAMP '
            'WHERE id = ? AND path = ?',
            (size, mtime_ns, artist, album, length_ms, track_id, normalize_path(path)))
        return cur.rowcount > 0

    def track_id(self, path):
        row = self._query_one('SELECT id FROM tracks WHERE path = ?',
                              (normalize_path(path),))
        return row[0] if row else None

    # ---- Edits ----

    def record_rename(self, old_path, new_path):
        """Note that a file was moved/renamed (the caller has already done it).
        The track keeps its id, so its history follows it. Returns the id,
        or None if the old path wasn't in the library (the event is still logged)."""
        if self.readonly:
            return None
        old, new = normalize_path(old_path), normalize_path(new_path)
        track = self.track_id(old)
        if track is not None:
            # Something already stored at the new path is replaced by this track
            self._execute('DELETE FROM tracks WHERE path = ? AND id != ?', (new, track))
            self._execute('UPDATE tracks SET path = ?, updated_at = CURRENT_TIMESTAMP '
                          'WHERE id = ?', (new, track))
        self._execute('INSERT INTO file_events (track_id, kind, old_path, new_path) '
                      "VALUES (?, 'rename', ?, ?)", (track, old, new))
        return track

    def resolve_path(self, path):
        """Where a file is now, following recorded renames (for playlists that
        still list an old path). Returns path unchanged if it was never renamed."""
        current = normalize_path(path)
        seen = {current.lower()}
        for _ in range(MAX_RENAME_HOPS):
            row = self._query_one(
                "SELECT new_path FROM file_events WHERE kind = 'rename' "
                'AND old_path = ? ORDER BY id DESC LIMIT 1', (current,))
            if row is None or row[0].lower() in seen:
                break
            current = row[0]
            seen.add(current.lower())
        return current

    def file_events(self, path):
        """History for the file now at path, oldest first, as dicts"""
        track = self.track_id(path)
        if track is None:
            return []
        try:
            cur = self._conn.execute(
                'SELECT kind, old_path, new_path, at FROM file_events '
                'WHERE track_id = ? ORDER BY id', (track,))
            return [dict(zip(('kind', 'old_path', 'new_path', 'at'), r)) for r in cur]
        except sqlite3.Error as e:
            raise LibraryError(e) from e

    # ---- Browsing ----

    def tracks(self, playlist_ids=None):
        """Every file in the library with how many playlists list it; or, given
        playlist ids (e.g. a profile's), only the files those playlists list,
        counting only those playlists"""
        if playlist_ids is None:
            cur = self._execute(
                'SELECT t.id, t.path, t.artist, t.album, t.length_ms, '
                '       COUNT(DISTINCT e.playlist_id) '
                'FROM tracks t LEFT JOIN playlist_entries e ON e.track_id = t.id '
                'GROUP BY t.id ORDER BY t.path')
        else:
            # The ids go in as one JSON array: any number of them, read-only
            cur = self._execute(
                'SELECT t.id, t.path, t.artist, t.album, t.length_ms, '
                '       COUNT(DISTINCT e.playlist_id) '
                'FROM tracks t JOIN playlist_entries e ON e.track_id = t.id '
                'JOIN json_each(?) s ON s.value = e.playlist_id '
                'GROUP BY t.id ORDER BY t.path', (json.dumps(list(playlist_ids)),))
        return [dict(zip(('id', 'path', 'artist', 'album', 'length_ms', 'playlists'), r))
                for r in cur]

    def track(self, track_id):
        row = self._query_one(
            'SELECT id, path, artist, album, length_ms FROM tracks WHERE id = ?',
            (track_id,))
        if row is None:
            return None
        return dict(zip(('id', 'path', 'artist', 'album', 'length_ms'), row))

    def playlists_for_track(self, track_id):
        """Playlists listing the track, with how many times each lists it"""
        cur = self._execute(
            'SELECT p.id, p.path, COUNT(*) FROM playlist_entries e '
            'JOIN playlists p ON p.id = e.playlist_id WHERE e.track_id = ? '
            'GROUP BY p.id ORDER BY p.path', (track_id,))
        return [dict(zip(('id', 'path', 'count'), r)) for r in cur]

    # ---- Playlists ----

    def playlist_folders(self):
        cur = self._execute('SELECT path FROM playlist_folders ORDER BY path')
        return [r[0] for r in cur]

    def add_playlist_folder(self, path):
        if not self.readonly:
            self._execute('INSERT OR IGNORE INTO playlist_folders VALUES (?)',
                          (normalize_path(path),))

    def remove_playlist_folder(self, path):
        """Stop tracking a folder and forget the playlists found only under it"""
        if self.readonly:
            return
        folder = normalize_path(path)
        self._execute('DELETE FROM playlist_folders WHERE path = ?', (folder,))
        remaining = self.playlist_folders()
        for playlist in self.playlists():
            if (_is_under(playlist['path'], folder)
                    and not any(_is_under(playlist['path'], f) for f in remaining)):
                self.delete_playlist(playlist['path'])

    def playlists(self, paths=None):
        """Stored playlists with their entry counts; given paths (e.g. a
        profile's playlists), only those of them that have been scanned"""
        cur = self._execute(
            'SELECT p.id, p.path, COUNT(e.position) FROM playlists p '
            'LEFT JOIN playlist_entries e ON e.playlist_id = p.id '
            'GROUP BY p.id ORDER BY p.path')
        rows = [dict(zip(('id', 'path', 'entries'), r)) for r in cur]
        if paths is not None:
            wanted = {normalize_path(p).lower() for p in paths}
            rows = [r for r in rows if r['path'].lower() in wanted]
        return rows

    def playlist(self, playlist_id):
        row = self._query_one('SELECT id, path FROM playlists WHERE id = ?',
                              (playlist_id,))
        return dict(zip(('id', 'path'), row)) if row else None

    def playlist_stamp(self, path):
        """(size, mtime_ns) the playlist had when last scanned, or None"""
        row = self._query_one('SELECT size, mtime_ns FROM playlists WHERE path = ?',
                              (normalize_path(path),))
        return (row[0], row[1]) if row else None

    def save_playlist(self, path, size, mtime_ns, entries):
        """Store a scanned playlist, replacing what was stored for it.
        entries: (entry as written, local file path or None for streams, title)
        in file order. Files not yet in the library are added to it."""
        if self.readonly:
            return None
        path = normalize_path(path)
        self._execute(
            'INSERT INTO playlists (path, size, mtime_ns) VALUES (?, ?, ?) '
            'ON CONFLICT(path) DO UPDATE SET size = excluded.size, '
            'mtime_ns = excluded.mtime_ns, scanned_at = CURRENT_TIMESTAMP',
            (path, size, mtime_ns))
        playlist_id = self._query_one('SELECT id FROM playlists WHERE path = ?',
                                      (path,))[0]
        self._execute('DELETE FROM playlist_entries WHERE playlist_id = ?',
                      (playlist_id,))
        rows = []
        for position, (entry, file_path, title) in enumerate(entries):
            track = None
            if file_path:
                file_path = normalize_path(file_path)
                self._execute('INSERT OR IGNORE INTO tracks (path) VALUES (?)',
                              (file_path,))
                track = self.track_id(file_path)
            rows.append((playlist_id, position, track, entry, title))
        try:
            self._conn.executemany(
                'INSERT INTO playlist_entries (playlist_id, position, track_id, '
                'entry, title) VALUES (?, ?, ?, ?, ?)', rows)
        except sqlite3.Error as e:
            raise LibraryError(e) from e
        return playlist_id

    def delete_playlist(self, path):
        if not self.readonly:
            self._execute('DELETE FROM playlists WHERE path = ?', (normalize_path(path),))

    def playlist_entries(self, playlist_id):
        """Tracks of a playlist in order; path is None for streams"""
        cur = self._execute(
            'SELECT e.position, e.entry, e.title, e.track_id, t.path '
            'FROM playlist_entries e LEFT JOIN tracks t ON t.id = e.track_id '
            'WHERE e.playlist_id = ? ORDER BY e.position', (playlist_id,))
        return [dict(zip(('position', 'entry', 'title', 'track_id', 'path'), r))
                for r in cur]

    # ---- Internals ----

    def _execute(self, sql, params=()):
        try:
            return self._conn.execute(sql, params)
        except sqlite3.Error as e:
            raise LibraryError(e) from e

    def _query_one(self, sql, params=()):
        return self._execute(sql, params).fetchone()
