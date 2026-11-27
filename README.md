# MagpieMusicManager

A music library manager on a shared database. Today it can:

- work on the playlists of a vlc_tagger profile (switch, create and edit
  profiles from Magpie),
- show which playlists a file is in, and which files a playlist holds
  (flagging files that are missing),
- rename a file and update every playlist that lists it, keeping each
  playlist's encoding, line endings, comments and path style,
- fill in artist, album and length in the background (tags read with
  mutagen; a file is re-read only when its size or modified time changes).

Planned: exporting playlists, copying the library to a device, bulk
file-name cleanup.

## Run

    .venv\Scripts\python -m magpie            # or: magpie  (after pip install)
    .venv\Scripts\python -m magpie --db other.sqlite

    .venv\Scripts\python -m magpie --profile Test --profiles-dir ..lc_tagger\config

F5 rescans, F2 renames the selected file, Ctrl+F filters. View > Dark Mode
switches theme (remembered between runs).

## Profiles

Magpie uses vlc_tagger's profiles, so both apps show the same playlists. A
profile is a playlist folder (its .m3u/.m3u8 files, not subfolders), minus any
left out, plus any added from elsewhere. The Files tab shows the files the
profile's playlists list.

- Profiles > Switch To: pick a profile (Magpie remembers its own choice; it
  starts on vlc_tagger's last profile the first time).
- Profiles > New Profile... (Ctrl+Shift+N): name it, optionally starting from
  another profile's playlists.
- Profiles > Edit Profile... (Ctrl+E): choose the folder, uncheck playlists to
  leave out, add playlists from elsewhere.
- Profiles > Profiles Folder...: which vlc_tagger config folder to use. The
  default is the installed player's `%APPDATA%\VLC Tagger\config` (or
  `VLC_TAGGER_CONFIG_DIR`); vlc_tagger run from source uses its own `config\`.

Only the profile's playlist settings (`playlist_directory`,
`default_playlists`, `hidden_playlists`) are changed; vlc_tagger's other
settings in the file are kept. vlc_tagger rewrites the profile it has open
whenever its settings change, so after editing that profile, switch profile
or restart vlc_tagger before using it there.

To try things out safely, make a profile pointing at a copy of your playlist
folder. Renames still rename the real music files (and update every playlist
that lists them, in any profile); the file's details show which playlists
are outside the current profile.

## Setup

    py -3.12 -m venv .venv
    .venv\Scripts\pip install -e ".[gui]" --config-settings editable_mode=compat

## Layout

- `music_db/` - shared by vlc_tagger and Magpie (standard library only): the
  database, `m3u_file.py` (playlist reading/editing) and `profiles.py`
  (vlc_tagger's profiles).
- `magpie/` - the manager: `scanner.py` (playlists -> database),
  `operations.py` (rename), `media_info.py` (tags -> database),
  `gui/` (PyQt5 window; `profile_dialog.py` is New/Edit Profile).

## music_db

The package that owns the database schema. Every app that touches the
library database (vlc_tagger, Magpie) goes through it, so they always agree.

- File: `%APPDATA%\MagpieMusicManager\library.sqlite`, or set `MUSIC_DB_PATH`.
- Schema version lives in `PRAGMA user_version`; migrations are in `music_db/schema.py`.
  An app with an older `music_db` opens a newer database read-only.
- WAL mode, so the player can read while the manager writes.
- Tracks have stable ids; renames are recorded in `file_events` and
  `Library.resolve_path()` follows them for playlists with old paths.

Use it from another project (e.g. vlc_tagger):

    pip install -e ../MagpieMusicManager --config-settings editable_mode=compat

(`compat` mode adds a plain path entry, which PyInstaller understands.)

## Tests

    .venv\Scripts\python -m unittest discover tests
