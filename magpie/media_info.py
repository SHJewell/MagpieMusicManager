"""
Media info - fills in artist, album and length for library tracks by reading
their tags with mutagen (just the file header, so it's quick).

Meant to run on a background thread with its own Library (see InfoThread in
gui/main_window.py). A track is read when it has never been read, or when its
file's size or modified time has changed since. Results are committed and
reported in small batches, so the GUI fills in as it goes and the database is
never locked for long (the scanner and renames write to it too).

Files mutagen can't read, or that have no length, are not stored: vlc_tagger
reads those with VLC, and storing a blank result would stop it from trying.
"""
import logging
import os
import time
from dataclasses import dataclass

import mutagen

BATCH_SECONDS = 0.5

logger = logging.getLogger(__name__)


@dataclass
class InfoResult:
    checked: int = 0
    read: int = 0
    unreadable: int = 0
    missing: int = 0
    cancelled: bool = False

    def summary(self):
        parts = [f'{self.read} files read']
        if self.unreadable:
            parts.append(f'{self.unreadable} without readable tags')
        if self.missing:
            parts.append(f'{self.missing} not found')
        return ', '.join(parts)


def read_tags(path):
    """{'artist', 'album', 'length_ms' (-1 = unknown)}, or None if mutagen
    can't read the file"""
    try:
        f = mutagen.File(path, easy=True)
    except Exception:
        return None
    if f is None:
        return None
    tags = f.tags or {}
    length = getattr(f.info, 'length', 0) or 0
    return {'artist': _first_tag(tags, 'artist'),
            'album': _first_tag(tags, 'album'),
            'length_ms': int(round(length * 1000)) if length > 0 else -1}


def _first_tag(tags, key):
    try:
        value = tags.get(key)
    except Exception:
        return ''
    if not value:
        return ''
    if isinstance(value, (list, tuple)):
        value = value[0]
    return str(value).strip()


def file_stamp(path):
    """(size, mtime_ns) to tell whether a file changed, or None if unreadable"""
    try:
        st = os.stat(path)
    except OSError:
        return None
    return st.st_size, st.st_mtime_ns


def update_info(lib, cancelled=lambda: False, on_batch=None, on_progress=None,
                skip=None):
    """Read tags for every track that needs it, storing them as it goes.

    on_batch(list of {'id', 'artist', 'album', 'length_ms'}): tracks just stored.
    on_progress(checked, total): called with each batch.
    skip: a set of paths not to try again (files already found unreadable);
    paths found unreadable this time are added to it.
    """
    rows = lib.track_stamps()
    total = len(rows)
    result = InfoResult()
    batch = []
    last_flush = time.monotonic()

    def flush():
        lib.commit()
        if batch and on_batch:
            on_batch(list(batch))
        batch.clear()
        if on_progress:
            on_progress(result.checked, total)

    for track_id, path, size, mtime_ns in rows:
        if cancelled():
            result.cancelled = True
            break
        result.checked += 1
        stamp = file_stamp(path)
        if stamp is None:
            result.missing += 1
        elif stamp != (size, mtime_ns) and not (skip and path in skip):
            info = read_tags(path)
            if info is None or info['length_ms'] < 0:
                result.unreadable += 1
                if skip is not None:
                    skip.add(path)
            elif lib.set_info(track_id, path, stamp[0], stamp[1], info['artist'],
                              info['album'], info['length_ms']):
                result.read += 1
                batch.append({'id': track_id, **info})
        if time.monotonic() - last_flush >= BATCH_SECONDS:
            flush()
            last_flush = time.monotonic()
    flush()
    logger.debug(f'File info: {result}')
    return result
