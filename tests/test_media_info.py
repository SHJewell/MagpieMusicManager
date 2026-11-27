import os
import tempfile
import unittest
import wave

from music_db import Library

from magpie.media_info import update_info


def _write_wav(path, seconds=1.0, rate=8000):
    with wave.open(path, 'wb') as w:
        w.setnchannels(1)
        w.setsampwidth(1)
        w.setframerate(rate)
        w.writeframes(b'\x80' * int(rate * seconds))


class UpdateInfoTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.lib = Library.open(os.path.join(self.tmp.name, 'library.sqlite'))
        self.song = os.path.join(self.tmp.name, 'song.wav')
        _write_wav(self.song)
        self.junk = os.path.join(self.tmp.name, 'junk.mp3')
        with open(self.junk, 'wb') as f:
            f.write(b'not audio at all')
        self.gone = os.path.join(self.tmp.name, 'gone.mp3')
        # Tracks as the scanner adds them: path only, nothing read yet
        self.lib.save_playlist(os.path.join(self.tmp.name, 'p.m3u'), 1, 1,
                               [(p, p, None) for p in (self.song, self.junk, self.gone)])
        self.lib.commit()

    def tearDown(self):
        self.lib.close()
        self.tmp.cleanup()

    def _row(self, path):
        return self.lib.track(self.lib.track_id(path))

    def test_reads_new_tracks_and_reports_them(self):
        batches, skip = [], set()
        result = update_info(self.lib, on_batch=batches.append, skip=skip)
        self.assertEqual((result.read, result.unreadable, result.missing), (1, 1, 1))
        self.assertEqual(self._row(self.song)['length_ms'], 1000)
        updates = [u for b in batches for u in b]
        self.assertEqual([u['id'] for u in updates], [self.lib.track_id(self.song)])
        # Unreadable files aren't stored (left for vlc_tagger), but are skipped next time
        self.assertIsNone(self._row(self.junk)['length_ms'])
        self.assertIn(self._row(self.junk)['path'], skip)

    def test_second_run_skips_unchanged_and_rereads_changed(self):
        update_info(self.lib, skip=set())
        self.assertEqual(update_info(self.lib, skip=set()).read, 0)
        _write_wav(self.song, seconds=2.0)
        update_info(self.lib, skip=set())
        self.assertEqual(self._row(self.song)['length_ms'], 2000)

    def test_cancel_stops_early(self):
        result = update_info(self.lib, cancelled=lambda: True)
        self.assertTrue(result.cancelled)
        self.assertEqual(result.checked, 0)

    def test_set_info_ignores_track_renamed_meanwhile(self):
        track = self.lib.track_id(self.song)
        moved = os.path.join(self.tmp.name, 'moved.wav')
        self.lib.record_rename(self.song, moved)
        self.assertFalse(self.lib.set_info(track, self.song, 1, 1, 'A', 'B', 5))
        self.assertIsNone(self.lib.track_id(self.song))     # no ghost track at old path


if __name__ == '__main__':
    unittest.main()
