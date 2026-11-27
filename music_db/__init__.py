"""
music_db - the shared music library database.

This package owns the schema and is the only code that should open the
database. vlc_tagger (the player) and the library handler both use it, so
they always agree on the layout.

    from music_db import Library
    lib = Library.open()            # shared file, see default_db_path()
    lib.upsert_info(path, size, mtime_ns, artist, album, length_ms)
    lib.commit()
"""
from .library import (Library, LibraryError, SCHEMA_VERSION, default_db_path,
                      normalize_path)
from .profiles import Profile, ProfileError, ProfileStore

__all__ = ['Library', 'LibraryError', 'SCHEMA_VERSION', 'default_db_path',
           'normalize_path', 'Profile', 'ProfileError', 'ProfileStore']
