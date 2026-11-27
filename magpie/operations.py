"""
Operations - changes to files in the library that keep the database and the
playlists in step.

Each operation is planned first (checked, nothing touched), so the GUI can
show what will happen, then applied.
"""
import logging
import os
from dataclasses import dataclass, field

from music_db.m3u_file import M3UFile, track_key
from .scanner import scan_playlist

logger = logging.getLogger(__name__)

# Characters Windows doesn't allow in file names
_BAD_CHARS = set('<>:"/\\|?*') | {chr(c) for c in range(32)}
_RESERVED = {'CON', 'PRN', 'AUX', 'NUL', *(f'COM{i}' for i in range(1, 10)),
             *(f'LPT{i}' for i in range(1, 10))}


class OperationError(Exception):
    pass


@dataclass
class RenamePlan:
    track_id: int
    old_path: str
    new_path: str
    playlists: list                 # paths of playlists that list the file
    extension_changed: bool = False


@dataclass
class RenameResult:
    new_path: str
    updated: list = field(default_factory=list)     # playlist paths rewritten
    failed: list = field(default_factory=list)      # (playlist path, message)


def check_file_name(name):
    """Reason the name can't be used, or None if it's fine"""
    if not name or not name.strip():
        return 'The name is empty.'
    if name != name.strip() or name.endswith('.'):
        return "A name can't start or end with a space, or end with a dot."
    bad = sorted(set(name) & _BAD_CHARS)
    if bad:
        shown = ' '.join(repr(c)[1:-1] for c in bad)
        return f"A name can't contain: {shown}"
    if os.path.splitext(name)[0].upper() in _RESERVED:
        return f'"{name}" is a reserved name on Windows.'
    return None


def plan_rename(lib, track_id, new_name):
    """Check a rename of the track's file to new_name (same folder)"""
    track = lib.track(track_id)
    if track is None:
        raise OperationError('That file is no longer in the library.')
    old_path = track['path']
    problem = check_file_name(new_name)
    if problem:
        raise OperationError(problem)
    new_path = os.path.join(os.path.dirname(old_path), new_name)
    if new_path == old_path:
        raise OperationError('That is already its name.')
    if not os.path.isfile(old_path):
        raise OperationError(f'The file is missing:\n{old_path}')
    # A case-only change is the same file on Windows, so it's allowed
    same_file = os.path.normcase(new_path) == os.path.normcase(old_path)
    if os.path.exists(new_path) and not same_file:
        raise OperationError(f'A file with that name already exists:\n{new_path}')

    playlists = [p['path'] for p in lib.playlists_for_track(track_id)]
    ext_changed = (os.path.splitext(new_name)[1].lower()
                   != os.path.splitext(old_path)[1].lower())
    return RenamePlan(track_id, old_path, new_path, playlists, ext_changed)


def apply_rename(lib, plan):
    """Rename the file, record it, then rewrite the playlists that list it.

    The file is renamed first: if that fails, nothing has changed. A playlist
    that can't be rewritten is reported; the database still records the
    rename, so players using music_db can find the file."""
    if lib.readonly:
        raise OperationError('The library database is read-only '
                             '(it was made by a newer version).')
    try:
        os.rename(plan.old_path, plan.new_path)
    except OSError as e:
        raise OperationError(f'Could not rename the file: {e.strerror or e}\n'
                             '(Is it open in a player?)') from e
    logger.info(f'Renamed {plan.old_path} -> {plan.new_path}')
    lib.record_rename(plan.old_path, plan.new_path)
    lib.commit()

    result = RenameResult(plan.new_path)
    key = track_key(plan.old_path)
    for playlist in plan.playlists:
        try:
            m3u = M3UFile.read(playlist)
            if m3u.replace_path(key, plan.new_path):
                m3u.write()
                result.updated.append(playlist)
            scan_playlist(lib, playlist)
        except OSError as e:
            result.failed.append((playlist, e.strerror or str(e)))
            logger.warning(f'Could not update playlist {playlist}: {e}')
    lib.commit()
    return result
