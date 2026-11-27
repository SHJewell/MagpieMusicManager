"""
Profiles - vlc_tagger's profiles, read and edited here so Magpie and the
player agree on which playlists a profile holds.

A profile is a JSON file in <config>/profiles/<name>.json. vlc_tagger keeps
many settings in it (volume, window positions, ...); only these three say
which playlists belong to the profile, and they are the only keys this module
changes (everything else in the file is kept as it is):

    playlist_directory   folder whose .m3u/.m3u8 files belong (not subfolders)
    default_playlists    playlists added from outside that folder
    hidden_playlists     playlists in that folder removed from the profile

The playlists of a profile are worked out the same way vlc_tagger's playlist
panel does it (see Profile.playlists()).

Where the profiles are: $VLC_TAGGER_CONFIG_DIR, else the installed player's
%APPDATA%/VLC Tagger/config. (A vlc_tagger run from source uses the config
folder next to its code instead; pass that folder to ProfileStore.)

Note: vlc_tagger keeps its active profile in memory and rewrites the file
when its settings change, so edits made here to the profile it has open can
be overwritten until it switches profile or restarts.
"""
import json
import os
import re
from dataclasses import dataclass, field
from typing import List, Optional

ENV_CONFIG_DIR = 'VLC_TAGGER_CONFIG_DIR'
PLAYLIST_EXTENSIONS = ('.m3u', '.m3u8')


class ProfileError(Exception):
    pass


def default_config_dir():
    override = os.environ.get(ENV_CONFIG_DIR)
    if override:
        return override
    base = os.environ.get('APPDATA') or os.path.expanduser('~')
    return os.path.join(base, 'VLC Tagger', 'config')


def sanitize_name(name):
    """A profile name made safe for a file name (as vlc_tagger does it), or
    None if nothing usable is left"""
    if not name:
        return None
    name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', '', name).strip().rstrip('.')
    return name or None


def same_path(path):
    """Comparable form of a path (Windows paths ignore case)"""
    return os.path.normcase(os.path.abspath(path))


def _is_playlist(name):
    return name.lower().endswith(PLAYLIST_EXTENSIONS)


@dataclass
class Profile:
    name: str
    playlist_directory: Optional[str] = None
    added: List[str] = field(default_factory=list)     # default_playlists
    hidden: List[str] = field(default_factory=list)    # hidden_playlists

    def in_directory(self, path):
        return bool(self.playlist_directory) and \
            same_path(os.path.dirname(path)) == same_path(self.playlist_directory)

    def directory_playlists(self):
        """Every playlist file in the directory, hidden ones included"""
        folder = self.playlist_directory
        if not folder or not os.path.isdir(folder):
            return []
        try:
            names = sorted(os.listdir(folder), key=str.lower)
        except OSError:
            return []
        return [os.path.join(folder, n) for n in names
                if _is_playlist(n) and os.path.isfile(os.path.join(folder, n))]

    def is_hidden(self, path):
        return same_path(path) in {same_path(p) for p in self.hidden}

    def playlists(self):
        """The profile's playlists, as vlc_tagger lists them: those in the
        directory (minus hidden ones), then added ones that exist"""
        hidden = {same_path(p) for p in self.hidden}
        paths = [p for p in self.directory_playlists() if same_path(p) not in hidden]
        seen = {same_path(p) for p in paths}
        for path in self.added:
            key = same_path(path)
            if key not in seen and os.path.isfile(path):
                seen.add(key)
                paths.append(path)
        return paths

    def missing_added(self):
        """Added playlists whose file can't be found (e.g. unplugged drive)"""
        return [p for p in self.added if not os.path.isfile(p)]

    def add_playlists(self, paths):
        """Bring playlists into the profile (un-hiding them if needed)"""
        for path in paths:
            path = os.path.abspath(path)
            key = same_path(path)
            self.hidden = [p for p in self.hidden if same_path(p) != key]
            if not self.in_directory(path) and \
                    key not in {same_path(p) for p in self.added}:
                self.added.append(path)

    def remove_playlist(self, path):
        """Take a playlist out of the profile (the file is not touched)"""
        key = same_path(path)
        self.added = [p for p in self.added if same_path(p) != key]
        if self.in_directory(path) and not self.is_hidden(path):
            self.hidden.append(os.path.abspath(path))

    def copy(self, name):
        return Profile(name, self.playlist_directory, list(self.added), list(self.hidden))


class ProfileStore:
    """The profiles in one vlc_tagger config folder"""

    def __init__(self, config_dir=None):
        self.config_dir = os.path.abspath(config_dir or default_config_dir())
        self.profiles_dir = os.path.join(self.config_dir, 'profiles')
        self.settings_file = os.path.join(self.config_dir, 'settings.json')

    def _file(self, name):
        return os.path.join(self.profiles_dir, f'{name}.json')

    def names(self):
        try:
            return sorted((f[:-5] for f in os.listdir(self.profiles_dir)
                           if f.lower().endswith('.json')), key=str.lower)
        except OSError:
            return []

    def exists(self, name):
        return os.path.isfile(self._file(name))

    def last_profile(self):
        """The profile vlc_tagger had open last, if it exists"""
        try:
            with open(self.settings_file, encoding='utf-8') as f:
                name = json.load(f).get('last_profile')
        except (OSError, ValueError, AttributeError):
            return None
        return name if name and self.exists(name) else None

    def load(self, name):
        data = self._read(name)
        if data is None:
            raise ProfileError(f'There is no profile named "{name}".')

        def path_list(key):
            value = data.get(key) or []
            return [str(p) for p in value if p] if isinstance(value, list) else []

        directory = data.get('playlist_directory')
        return Profile(name, str(directory) if directory else None,
                       path_list('default_playlists'), path_list('hidden_playlists'))

    def save(self, profile):
        """Write the profile's playlist settings, keeping the rest of the file"""
        data = self._read(profile.name) or {}
        data['playlist_directory'] = profile.playlist_directory
        data['default_playlists'] = list(profile.added)
        data['hidden_playlists'] = list(profile.hidden)
        self._write(profile.name, data)

    def create(self, profile):
        """Write a new profile; refuses to replace one that exists. vlc_tagger
        fills in its other settings from its defaults when it opens it."""
        name = sanitize_name(profile.name)
        if not name:
            raise ProfileError("That name can't be used for a profile.")
        if self.exists(name):
            raise ProfileError(f'A profile named "{name}" already exists.')
        profile.name = name
        self.save(profile)
        return profile

    def _read(self, name):
        path = self._file(name)
        if not os.path.isfile(path):
            return None
        try:
            with open(path, encoding='utf-8') as f:
                data = json.load(f)
        except (OSError, ValueError) as e:
            raise ProfileError(f'Could not read profile {path}: {e}') from e
        if not isinstance(data, dict):
            raise ProfileError(f'Profile {path} is not in the expected format.')
        return data

    def _write(self, name, data):
        """Via a temp file, so a crash can't leave the profile half written
        (the same way vlc_tagger saves it)"""
        path = self._file(name)
        tmp = f'{path}.{os.getpid()}.tmp'
        try:
            os.makedirs(self.profiles_dir, exist_ok=True)
            with open(tmp, 'w', encoding='utf-8') as f:
                json.dump(data, f, indent=4)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp, path)
        except OSError as e:
            try:
                os.remove(tmp)
            except OSError:
                pass
            raise ProfileError(f'Could not save profile {path}: {e}') from e
