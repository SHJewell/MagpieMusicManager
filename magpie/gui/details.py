"""Right-hand panels: details of the selected file or playlist"""
import os
from datetime import datetime, timezone

from PyQt5.QtCore import Qt, pyqtSignal
from PyQt5.QtGui import QFont
from PyQt5.QtWidgets import (QAbstractItemView, QGroupBox, QHBoxLayout, QHeaderView,
                             QLabel, QLineEdit, QListWidget, QListWidgetItem,
                             QPushButton, QTableWidget, QTableWidgetItem, QVBoxLayout,
                             QWidget)

from . import theme
from .models import format_length


def _local_time(stamp):
    """SQLite CURRENT_TIMESTAMP (UTC text) shown in local time"""
    try:
        utc = datetime.strptime(stamp, '%Y-%m-%d %H:%M:%S').replace(tzinfo=timezone.utc)
    except (TypeError, ValueError):
        return stamp or ''
    return utc.astimezone().strftime('%Y-%m-%d %H:%M')


def _title_label():
    label = QLabel()
    font = QFont()
    font.setPointSize(font.pointSize() + 4)
    font.setBold(True)
    label.setFont(font)
    label.setWordWrap(True)
    label.setTextInteractionFlags(Qt.TextSelectableByMouse)
    return label


def _path_label():
    label = QLabel()
    label.setWordWrap(True)
    label.setTextInteractionFlags(Qt.TextSelectableByMouse)
    label.setStyleSheet('color: gray;')
    return label


class FileDetails(QWidget):
    open_playlist = pyqtSignal(int)          # playlist id
    rename_requested = pyqtSignal(int, str)  # track id, new file name

    def __init__(self, parent=None):
        super().__init__(parent)
        self.track_id = None

        self.title = _title_label()
        self.path = _path_label()
        self.info = QLabel()
        self.info.setWordWrap(True)

        rename_box = QGroupBox('Rename')
        self.name_edit = QLineEdit()
        self.name_edit.returnPressed.connect(self._on_rename)
        self.rename_button = QPushButton('Rename')
        self.rename_button.clicked.connect(self._on_rename)
        row = QHBoxLayout(rename_box)
        row.addWidget(self.name_edit, 1)
        row.addWidget(self.rename_button)

        self.playlists_box = QGroupBox('In playlists')
        self.playlists = QListWidget()
        self.playlists.itemActivated.connect(
            lambda item: self.open_playlist.emit(item.data(Qt.UserRole)))
        self.playlists.setToolTip('Double-click to open the playlist')
        QVBoxLayout(self.playlists_box).addWidget(self.playlists)

        history_box = QGroupBox('History')
        self.history = QListWidget()
        QVBoxLayout(history_box).addWidget(self.history)

        layout = QVBoxLayout(self)
        layout.addWidget(self.title)
        layout.addWidget(self.path)
        layout.addWidget(self.info)
        layout.addWidget(rename_box)
        layout.addWidget(self.playlists_box, 3)
        layout.addWidget(history_box, 1)

    def show_track(self, lib, track_id, profile_ids=None):
        """profile_ids: ids of the active profile's playlists; others are
        marked, since a rename updates them too"""
        track = lib.track(track_id)
        self.track_id = track_id
        if track is None:
            self.title.setText('(not in the library)')
            self.path.clear()
            return
        path = track['path']
        name = os.path.basename(path)
        self.title.setText(name)
        self.path.setText(path)

        exists = os.path.isfile(path)
        self.show_info(track, exists)

        self.name_edit.setText(name)
        self.name_edit.setEnabled(exists and not lib.readonly)
        self.rename_button.setEnabled(exists and not lib.readonly)
        stem = os.path.splitext(name)[0]
        self.name_edit.setSelection(0, len(stem))     # like Explorer: name, not extension

        self.playlists.clear()
        found = lib.playlists_for_track(track_id)
        self.playlists_box.setTitle(f'In playlists ({len(found)})')
        for p in found:
            text = os.path.basename(p['path'])
            if p['count'] > 1:
                text += f'  (listed {p["count"]} times)'
            outside = profile_ids is not None and p['id'] not in profile_ids
            if outside:
                text += '  (not in this profile)'
            item = QListWidgetItem(text)
            if outside:
                item.setForeground(Qt.gray)
            item.setToolTip(p['path'])
            item.setData(Qt.UserRole, p['id'])
            self.playlists.addItem(item)
        if not found:
            self.playlists.addItem('(none)')
            self.playlists.item(0).setFlags(Qt.NoItemFlags)

        self.history.clear()
        for e in lib.file_events(path):
            when = _local_time(e['at'])
            if e['kind'] == 'rename':
                text = (f"{when}  renamed from {os.path.basename(e['old_path'])}"
                        if os.path.dirname(e['old_path']) == os.path.dirname(e['new_path'])
                        else f"{when}  moved from {e['old_path']}")
            else:
                text = f"{when}  {e['kind']}"
            self.history.addItem(text)

    def show_info(self, track, exists=True):
        """Artist · album · length line. Called on its own when tags finish
        reading, so a name being typed in the rename box isn't disturbed."""
        bits = [b for b in (track.get('artist'), track.get('album'),
                            format_length(track.get('length_ms'))) if b]
        if not exists:
            bits.insert(0, 'File not found')
        self.info.setText('  ·  '.join(bits))
        self.info.setStyleSheet('' if exists else f'color: {theme.missing_colour().name()};')

    def focus_name(self):
        self.name_edit.setFocus()

    def _on_rename(self):
        if self.track_id is not None and self.rename_button.isEnabled():
            self.rename_requested.emit(self.track_id, self.name_edit.text())


class PlaylistDetails(QWidget):
    open_track = pyqtSignal(int)        # track id

    COLUMNS = ['#', 'Name', 'Folder', 'Status']

    def __init__(self, parent=None):
        super().__init__(parent)
        self.playlist_id = None
        self.title = _title_label()
        self.path = _path_label()
        self.info = QLabel()

        self.entries = QTableWidget(0, len(self.COLUMNS))
        self.entries.setHorizontalHeaderLabels(self.COLUMNS)
        self.entries.verticalHeader().hide()
        self.entries.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.entries.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.entries.setSelectionMode(QAbstractItemView.SingleSelection)
        self.entries.setWordWrap(False)
        header = self.entries.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(1, QHeaderView.Interactive)
        header.setSectionResizeMode(2, QHeaderView.Stretch)
        header.setSectionResizeMode(3, QHeaderView.ResizeToContents)
        self.entries.setToolTip('Double-click a file to open it')
        self.entries.itemActivated.connect(self._on_activated)

        layout = QVBoxLayout(self)
        layout.addWidget(self.title)
        layout.addWidget(self.path)
        layout.addWidget(self.info)
        layout.addWidget(self.entries, 1)

    def show_playlist(self, lib, playlist_id):
        playlist = lib.playlist(playlist_id)
        self.playlist_id = playlist_id
        if playlist is None:
            self.title.setText('(not in the library)')
            self.path.clear()
            self.entries.setRowCount(0)
            return
        self.title.setText(os.path.basename(playlist['path']))
        self.path.setText(playlist['path'])

        entries = lib.playlist_entries(playlist_id)
        self.entries.setRowCount(len(entries))
        missing = 0
        for row, e in enumerate(entries):
            if e['path']:
                name, folder = os.path.basename(e['path']), os.path.dirname(e['path'])
                status = '' if os.path.isfile(e['path']) else 'Missing'
                tip = e['path']
            else:
                name, folder, status, tip = e['title'] or e['entry'], '', 'Stream', e['entry']
            missing += status == 'Missing'
            values = [str(row + 1), name, folder, status]
            for col, value in enumerate(values):
                item = QTableWidgetItem(value)
                item.setToolTip(tip)
                item.setData(Qt.UserRole, e['track_id'])
                if col == 0:
                    item.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
                if status == 'Missing':
                    item.setForeground(theme.missing_colour())
                self.entries.setItem(row, col, item)
        self.entries.resizeColumnToContents(1)
        self.entries.setColumnWidth(1, min(self.entries.columnWidth(1), 400))

        text = f'{len(entries)} tracks'
        if missing:
            text += f', {missing} missing'
        self.info.setText(text)

    def _on_activated(self, item):
        track_id = item.data(Qt.UserRole)
        if track_id is not None:
            self.open_track.emit(track_id)
