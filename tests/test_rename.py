import os
import tempfile
import unittest

from music_db import Library, Profile
from music_db.m3u_file import M3UFile
from magpie.operations import OperationError, apply_rename, check_file_name, plan_rename
from magpie.scanner import scan


def write(path, data, mode='w', **kw):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, mode, **kw) as f:
        f.write(data)


class RenameTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = self.tmp.name
        self.music = os.path.join(root, 'Music')
        self.lists = os.path.join(root, 'Playlists')
        self.song = os.path.join(self.music, '01 - Song.mp3')
        self.other = os.path.join(self.music, 'Other.mp3')
        write(self.song, 'x')
        write(self.other, 'y')

        # Absolute, Windows-style, with EXTINF title = file name, CRLF endings
        self.abs_list = os.path.join(self.lists, 'abs.m3u')
        write(self.abs_list,
              f'#EXTM3U\r\n#EXTINF:-1,01 - Song.mp3\r\n{self.song}\r\n'
              f'#EXTINF:-1,Other\r\n{self.other}\r\n', newline='')
        # Relative with forward slashes, a comment, a stream, cp1252 text
        self.rel_list = os.path.join(self.lists, 'sub', 'rel.m3u')
        write(self.rel_list,
              '# caf\xe9 mix\n../../Music/01 - Song.mp3\nhttp://radio.example/stream\n',
              encoding='cp1252', newline='')
        # file: URL in an m3u8
        self.url_list = os.path.join(self.lists, 'url.m3u8')
        write(self.url_list, 'file:///' + self.song.replace('\\', '/').replace(' ', '%20')
              + '\n', newline='', encoding='utf-8')

        self.lib = Library.open(os.path.join(root, 'library.sqlite'))
        # The profile's folder holds abs/url; rel (in a subfolder) is added
        self.profile = Profile('test', self.lists, [self.rel_list], [])
        self.result = scan(self.lib, self.profile.playlists())
        self.track = self.lib.track_id(self.song)

    def tearDown(self):
        self.lib.close()
        self.tmp.cleanup()

    def test_scan_finds_playlists_and_tracks(self):
        self.assertEqual(self.result.read, 3)
        self.assertIsNotNone(self.track)
        names = sorted(os.path.basename(p['path'])
                       for p in self.lib.playlists_for_track(self.track))
        self.assertEqual(names, ['abs.m3u', 'rel.m3u', 'url.m3u8'])
        rel = [p for p in self.lib.playlists() if p['path'].endswith('rel.m3u')][0]
        entries = self.lib.playlist_entries(rel['id'])
        self.assertEqual(len(entries), 2)
        self.assertIsNone(entries[1]['track_id'])           # the stream

    def test_rescan_skips_unchanged(self):
        again = scan(self.lib, self.profile.playlists())
        self.assertEqual((again.read, again.unchanged), (0, 3))

    def test_rename_rewrites_playlists_keeping_style(self):
        plan = plan_rename(self.lib, self.track, 'Song.mp3')
        self.assertEqual(len(plan.playlists), 3)
        result = apply_rename(self.lib, plan)
        self.assertEqual(result.failed, [])
        new = os.path.join(self.music, 'Song.mp3')
        self.assertTrue(os.path.isfile(new))
        self.assertFalse(os.path.exists(self.song))

        with open(self.abs_list, 'rb') as f:
            data = f.read()
        self.assertEqual(data, (f'#EXTM3U\r\n#EXTINF:-1,Song.mp3\r\n{new}\r\n'
                                f'#EXTINF:-1,Other\r\n{self.other}\r\n').encode())
        with open(self.rel_list, 'rb') as f:
            self.assertEqual(f.read(), '# caf\xe9 mix\n../../Music/Song.mp3\n'
                             'http://radio.example/stream\n'.encode('cp1252'))
        with open(self.url_list, encoding='utf-8') as f:
            self.assertIn('Music/Song.mp3', f.read())

        # Database follows: same track id, new path, all playlists still list it
        self.assertEqual(self.lib.track_id(new), self.track)
        self.assertEqual(len(self.lib.playlists_for_track(self.track)), 3)
        self.assertEqual(self.lib.resolve_path(self.song), os.path.normpath(new))
        for path in (self.abs_list, self.rel_list, self.url_list):
            self.assertIn(os.path.normpath(new).lower(),
                          [t.path.lower() for t in M3UFile.read(path).tracks])

    def test_case_only_rename(self):
        result = apply_rename(self.lib, plan_rename(self.lib, self.track, '01 - SONG.mp3'))
        self.assertEqual(result.failed, [])
        self.assertIn('01 - SONG.mp3', os.listdir(self.music))
        self.assertEqual(self.lib.track_id(os.path.join(self.music, '01 - SONG.mp3')),
                         self.track)

    def test_rejects_bad_names_and_clashes(self):
        for bad in ('', 'a/b.mp3', 'a?.mp3', 'CON.mp3', 'x.mp3 ', 'x.'):
            with self.subTest(bad=bad):
                self.assertIsNotNone(check_file_name(bad))
        with self.assertRaises(OperationError):
            plan_rename(self.lib, self.track, 'Other.mp3')       # already exists
        with self.assertRaises(OperationError):
            plan_rename(self.lib, self.track, '01 - Song.mp3')   # unchanged

    def test_missing_folder_keeps_playlists(self):
        # A playlist scanned earlier whose drive is now unplugged
        gone = os.path.join(self.tmp.name, 'Unplugged', 'old.m3u')
        self.lib.save_playlist(gone, 1, 1, [])
        result = scan(self.lib, self.profile.playlists())
        self.assertEqual(result.removed, 0)
        self.assertEqual(len(self.lib.playlists()), 4)

    def test_deleted_playlist_is_forgotten(self):
        os.remove(self.url_list)
        self.assertEqual(scan(self.lib, self.profile.playlists()).removed, 1)
        self.assertEqual(len(self.lib.playlists()), 2)

    def test_views_follow_the_profile(self):
        only_abs = Profile('abs', self.lists, [], [self.url_list])
        playlists = self.lib.playlists(only_abs.playlists())
        self.assertEqual([os.path.basename(p['path']) for p in playlists], ['abs.m3u'])
        tracks = self.lib.tracks([p['id'] for p in playlists])
        self.assertEqual(sorted(os.path.basename(t['path']) for t in tracks),
                         ['01 - Song.mp3', 'Other.mp3'])
        song = [t for t in tracks if t['id'] == self.track][0]
        self.assertEqual(song['playlists'], 1)      # counts this profile's only
        self.assertEqual(self.lib.tracks([]), [])
        everything = {t['id']: t['playlists'] for t in self.lib.tracks()}
        self.assertEqual(everything[self.track], 3)


if __name__ == '__main__':
    unittest.main()
