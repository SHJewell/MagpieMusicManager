"""
Scanner - reads playlists (those of the active profile, see
music_db.profiles) into the database. Playlists that haven't changed since
the last scan (same size and modified time) are skipped, so rescans are cheap.
"""
import logging
import os
from dataclasses import dataclass, field

from music_db.m3u_file import M3UFile

logger = logging.getLogger(__name__)


@dataclass
class ScanResult:
    read: int = 0
    unchanged: int = 0
    removed: int = 0
    errors: list = field(default_factory=list)      # (path, message)

    def summary(self):
        parts = [f'{self.read} playlists read', f'{self.unchanged} unchanged']
        if self.removed:
            parts.append(f'{self.removed} removed')
        if self.errors:
            parts.append(f'{len(self.errors)} errors')
        return ', '.join(parts)


def scan_playlist(lib, path):
    """(Re)read one playlist into the database; raises OSError if unreadable"""
    st = os.stat(path)
    m3u = M3UFile.read(path)
    entries = [(t.lines[-1].strip(), None if '://' in t.path else t.path, t.title)
               for t in m3u.tracks]
    lib.save_playlist(path, st.st_size, st.st_mtime_ns, entries)


def scan(lib, playlists, cancelled=lambda: False):
    """Bring the database up to date with the playlists (paths) and commit.

    Playlists stored in the database whose file has been deleted are
    forgotten, whichever profile they belong to; one whose folder can't be
    found (e.g. an unplugged drive) is kept."""
    result = ScanResult()
    for path in playlists:
        if cancelled():
            lib.commit()
            return result
        try:
            st = os.stat(path)
            if lib.playlist_stamp(path) == (st.st_size, st.st_mtime_ns):
                result.unchanged += 1
                continue
            scan_playlist(lib, path)
            lib.commit()        # short writes, so the GUI can rename meanwhile
            result.read += 1
        except OSError as e:
            result.errors.append((path, str(e)))
            logger.warning(f'Could not read playlist {path}: {e}')

    for playlist in lib.playlists():
        path = playlist['path']
        if not os.path.isfile(path) and os.path.isdir(os.path.dirname(path)):
            lib.delete_playlist(path)
            result.removed += 1
    lib.commit()
    return result
