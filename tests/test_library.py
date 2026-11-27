import os
import sqlite3
import tempfile
import threading
import unittest

from music_db import Library, SCHEMA_VERSION, normalize_path


class LibraryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db_path = os.path.join(self.tmp.name, 'sub', 'library.sqlite')
        self.lib = Library.open(self.db_path)

    def tearDown(self):
        self.lib.close()
        self.tmp.cleanup()

    def test_creates_schema_and_wal(self):
        conn = sqlite3.connect(self.db_path)
        self.assertEqual(conn.execute('PRAGMA user_version').fetchone()[0], SCHEMA_VERSION)
        self.assertEqual(conn.execute('PRAGMA journal_mode').fetchone()[0], 'wal')
        conn.close()

    def test_info_round_trip_and_stale_check(self):
        self.lib.upsert_info('C:/Music/a.mp3', 10, 111, 'Art', 'Alb', 1234)
        self.lib.commit()
        # Mixed separators / case still find the same row
        self.assertEqual(self.lib.get_info(r'c:\music\A.mp3', 10, 111),
                         {'artist': 'Art', 'album': 'Alb', 'length_ms': 1234})
        self.assertIsNone(self.lib.get_info('C:/Music/a.mp3', 10, 222))   # changed

    def test_upsert_keeps_track_id(self):
        self.lib.upsert_info('C:/Music/a.mp3', 10, 1, '', '', 1)
        first = self.lib.track_id('C:/Music/a.mp3')
        self.lib.upsert_info('C:/Music/a.mp3', 20, 2, 'X', '', 2)
        self.assertEqual(self.lib.track_id('C:/Music/a.mp3'), first)

    def test_rename_moves_track_and_logs(self):
        self.lib.upsert_info('C:/Music/01 - song.mp3', 10, 1, 'A', 'B', 5)
        tid = self.lib.track_id('C:/Music/01 - song.mp3')
        self.assertEqual(self.lib.record_rename('C:/Music/01 - song.mp3',
                                                'C:/Music/song.mp3'), tid)
        self.assertIsNone(self.lib.track_id('C:/Music/01 - song.mp3'))
        self.assertEqual(self.lib.track_id('C:/Music/song.mp3'), tid)
        events = self.lib.file_events('C:/Music/song.mp3')
        self.assertEqual([e['kind'] for e in events], ['rename'])

    def test_resolve_follows_chain(self):
        self.lib.record_rename('C:/a.mp3', 'C:/b.mp3')
        self.lib.record_rename('C:/b.mp3', 'C:/c.mp3')
        self.assertEqual(self.lib.resolve_path('C:/a.mp3'), normalize_path('C:/c.mp3'))
        self.assertEqual(self.lib.resolve_path('C:/z.mp3'), normalize_path('C:/z.mp3'))

    def test_resolve_stops_on_cycle(self):
        self.lib.record_rename('C:/a.mp3', 'C:/b.mp3')
        self.lib.record_rename('C:/b.mp3', 'C:/a.mp3')
        self.assertIn(self.lib.resolve_path('C:/a.mp3'),
                      (normalize_path('C:/a.mp3'), normalize_path('C:/b.mp3')))

    def test_newer_schema_opens_readonly(self):
        self.lib.close()
        conn = sqlite3.connect(self.db_path)
        conn.execute(f'PRAGMA user_version = {SCHEMA_VERSION + 1}')
        conn.close()
        self.lib = Library.open(self.db_path)
        self.assertTrue(self.lib.readonly)
        self.lib.upsert_info('C:/x.mp3', 1, 1, '', '', 1)    # skipped, no error
        self.assertIsNone(self.lib.track_id('C:/x.mp3'))

    def test_reader_while_other_connection_writes(self):
        self.lib.upsert_info('C:/a.mp3', 1, 1, '', '', 1)     # open write transaction
        result = {}

        def read():
            with Library.open(self.db_path) as other:
                result['row'] = other.get_info('C:/a.mp3', 1, 1)

        t = threading.Thread(target=read)
        t.start()
        t.join(10)
        self.assertIn('row', result)          # didn't hang or raise
        self.assertIsNone(result['row'])      # uncommitted, so not visible yet


if __name__ == '__main__':
    unittest.main()
