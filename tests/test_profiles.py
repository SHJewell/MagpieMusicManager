import json
import os
import tempfile
import unittest

from music_db import Profile, ProfileError, ProfileStore
from music_db.profiles import sanitize_name


def touch(path):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, 'w') as f:
        f.write('#EXTM3U\n')


class ProfileTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = self.tmp.name
        self.lists = os.path.join(root, 'Playlists')
        self.a = os.path.join(self.lists, 'A.m3u')
        self.b = os.path.join(self.lists, 'b.m3u8')
        self.nested = os.path.join(self.lists, 'sub', 'nested.m3u')   # not listed
        self.outside = os.path.join(root, 'Elsewhere', 'out.m3u')
        for p in (self.a, self.b, self.nested, self.outside):
            touch(p)
        with open(os.path.join(self.lists, 'notes.txt'), 'w') as f:
            f.write('x')
        self.store = ProfileStore(os.path.join(root, 'config'))

    def tearDown(self):
        self.tmp.cleanup()

    def test_playlists_like_vlc_tagger(self):
        profile = Profile('p', self.lists, [self.outside, self.a, 'Z:/gone.m3u'], [self.b])
        self.assertEqual(profile.playlists(), [self.a, self.outside])
        self.assertEqual(profile.missing_added(), ['Z:/gone.m3u'])
        self.assertEqual(Profile('none').playlists(), [])

    def test_add_and_remove(self):
        profile = Profile('p', self.lists)
        profile.remove_playlist(self.a)             # in the folder: hidden
        self.assertEqual(profile.hidden, [os.path.abspath(self.a)])
        profile.add_playlists([self.a.upper(), self.outside])
        self.assertEqual(profile.hidden, [])        # un-hidden; not also "added"
        self.assertEqual(profile.added, [os.path.abspath(self.outside)])
        profile.remove_playlist(self.outside)       # added: just dropped
        self.assertEqual((profile.added, profile.hidden), ([], []))

    def test_save_keeps_vlc_tagger_settings(self):
        os.makedirs(self.store.profiles_dir)
        path = os.path.join(self.store.profiles_dir, 'music.json')
        with open(path, 'w', encoding='utf-8') as f:
            json.dump({'volume': 40, 'playlist_directory': None,
                       'window_positions': {'player': {'x': 1}}}, f)
        profile = self.store.load('music')
        profile.playlist_directory = self.lists
        profile.hidden = [self.b]
        self.store.save(profile)
        with open(path, encoding='utf-8') as f:
            data = json.load(f)
        self.assertEqual(data['volume'], 40)
        self.assertEqual(data['window_positions'], {'player': {'x': 1}})
        self.assertEqual(data['playlist_directory'], self.lists)
        self.assertEqual(data['hidden_playlists'], [self.b])
        self.assertEqual(data['default_playlists'], [])
        self.assertEqual(os.listdir(self.store.profiles_dir), ['music.json'])  # no temp left

    def test_create_list_and_last_profile(self):
        self.assertEqual(self.store.names(), [])
        self.store.create(Profile('Test: copy?', self.lists))
        self.assertEqual(self.store.names(), ['Test copy'])
        with self.assertRaises(ProfileError):
            self.store.create(Profile('Test copy'))
        with self.assertRaises(ProfileError):
            self.store.create(Profile('???'))
        with self.assertRaises(ProfileError):
            self.store.load('nope')

        self.assertIsNone(self.store.last_profile())
        with open(self.store.settings_file, 'w') as f:
            json.dump({'last_profile': 'Test copy'}, f)
        self.assertEqual(self.store.last_profile(), 'Test copy')

    def test_bad_profile_file(self):
        os.makedirs(self.store.profiles_dir)
        with open(os.path.join(self.store.profiles_dir, 'bad.json'), 'w') as f:
            f.write('{not json')
        with self.assertRaises(ProfileError):
            self.store.load('bad')

    def test_sanitize(self):
        self.assertEqual(sanitize_name(' a<b>. '), 'ab')
        self.assertIsNone(sanitize_name(''))


if __name__ == '__main__':
    unittest.main()
