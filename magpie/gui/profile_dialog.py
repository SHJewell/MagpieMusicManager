"""Profiles > New Profile / Edit Profile: choose which playlists a profile
holds. Profiles are vlc_tagger's (see music_db/profiles.py), so the player
sees the same playlists."""
import os

from PyQt5.QtCore import Qt
from PyQt5.QtWidgets import (QComboBox, QDialog, QDialogButtonBox, QFileDialog,
                             QFormLayout, QGroupBox, QHBoxLayout, QLabel, QLineEdit,
                             QListWidget, QListWidgetItem, QMessageBox, QPushButton,
                             QVBoxLayout)

from music_db.profiles import Profile, ProfileError, sanitize_name

from . import theme

EMPTY_TEMPLATE = '(no playlists)'


class ProfileDialog(QDialog):
    """Edit a copy of a profile; on Save it is written to the store and is
    then in self.profile. Pass profile=None to make a new one."""

    def __init__(self, store, profile=None, current=None, parent=None):
        super().__init__(parent)
        self.store = store
        self.is_new = profile is None
        self.profile = profile.copy(profile.name) if profile else Profile('')
        self.setWindowTitle('New Profile' if self.is_new else f'Edit Profile - {profile.name}')
        self.resize(640, 620)

        form = QFormLayout()
        self.name = QLineEdit(self.profile.name)
        if self.is_new:
            self.name.setPlaceholderText('e.g. Test')
            form.addRow('Name:', self.name)
            # Start from an existing profile's playlists (like vlc_tagger's
            # "keep the current files and folders")
            self.template = QComboBox()
            self.template.addItem(EMPTY_TEMPLATE)
            for name in store.names():
                self.template.addItem(name)
            if current:
                self.template.setCurrentText(current)
            self.template.currentTextChanged.connect(self._use_template)
            form.addRow('Start from:', self.template)
        else:
            self.name.setReadOnly(True)     # vlc_tagger remembers profiles by name
            form.addRow('Name:', self.name)

        self.directory = QLineEdit()
        self.directory.setReadOnly(True)
        self.directory.setPlaceholderText('(none)')
        browse = QPushButton('Browse...')
        browse.clicked.connect(self._browse_directory)
        clear = QPushButton('Clear')
        clear.clicked.connect(lambda: self._set_directory(None))
        dir_row = QHBoxLayout()
        dir_row.addWidget(self.directory, 1)
        dir_row.addWidget(browse)
        dir_row.addWidget(clear)
        form.addRow('Playlist folder:', dir_row)

        dir_box = QGroupBox('Playlists in the folder (uncheck to leave one out)')
        self.dir_list = QListWidget()
        QVBoxLayout(dir_box).addWidget(self.dir_list)

        added_box = QGroupBox('Playlists added from elsewhere')
        self.added_list = QListWidget()
        self.added_list.setSelectionMode(QListWidget.ExtendedSelection)
        add = QPushButton('Add...')
        add.clicked.connect(self._add_playlists)
        self.remove = QPushButton('Remove')
        self.remove.clicked.connect(self._remove_selected)
        self.added_list.itemSelectionChanged.connect(
            lambda: self.remove.setEnabled(bool(self.added_list.selectedItems())))
        added_buttons = QVBoxLayout()
        added_buttons.addWidget(add)
        added_buttons.addWidget(self.remove)
        added_buttons.addStretch(1)
        added_row = QHBoxLayout(added_box)
        added_row.addWidget(self.added_list, 1)
        added_row.addLayout(added_buttons)

        note = QLabel('Profiles are shared with vlc_tagger. If vlc_tagger has this '
                      'profile open, switch profile or restart it there to see the '
                      'change. Until you do, it can save over these edits.')
        note.setWordWrap(True)
        note.setStyleSheet('color: gray;')
        self.summary = QLabel()

        buttons = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)

        layout = QVBoxLayout(self)
        layout.addLayout(form)
        layout.addWidget(dir_box, 3)
        layout.addWidget(added_box, 2)
        layout.addWidget(self.summary)
        layout.addWidget(note)
        layout.addWidget(buttons)

        self.dir_list.itemChanged.connect(self._update_summary)
        if self.is_new:
            self._use_template(self.template.currentText())
        else:
            self._refresh()

    # ---- Filling the lists ----

    def _refresh(self):
        """Show self.profile; the checkboxes are the hidden list"""
        self.directory.setText(self.profile.playlist_directory or '')
        self.directory.setToolTip(self.profile.playlist_directory or '')

        self.dir_list.blockSignals(True)
        self.dir_list.clear()
        folder = self.profile.playlist_directory
        if folder and not os.path.isdir(folder):
            self._placeholder(self.dir_list, 'The folder can\'t be found (drive unplugged?)')
        else:
            playlists = self.profile.directory_playlists()
            for path in playlists:
                item = QListWidgetItem(os.path.basename(path))
                item.setToolTip(path)
                item.setData(Qt.UserRole, path)
                item.setFlags(item.flags() | Qt.ItemIsUserCheckable)
                item.setCheckState(Qt.Unchecked if self.profile.is_hidden(path)
                                   else Qt.Checked)
                self.dir_list.addItem(item)
            if folder and not playlists:
                self._placeholder(self.dir_list, '(no .m3u/.m3u8 files in this folder)')
        self.dir_list.blockSignals(False)

        self.added_list.clear()
        for path in self.profile.added:
            exists = os.path.isfile(path)
            item = QListWidgetItem(path if exists else f'{path}   (not found)')
            item.setToolTip(path)
            item.setData(Qt.UserRole, path)
            if not exists:
                item.setForeground(theme.missing_colour())
            self.added_list.addItem(item)
        self.remove.setEnabled(False)
        self._update_summary()

    @staticmethod
    def _placeholder(widget, text):
        widget.addItem(text)
        widget.item(widget.count() - 1).setFlags(Qt.NoItemFlags)

    def _update_summary(self, *_):
        self._apply_checks()
        count = len(self.profile.playlists())
        self.summary.setText(f'{count} playlist{"" if count == 1 else "s"} in this profile')

    def _apply_checks(self):
        """Checkboxes -> profile.hidden. Hidden entries outside the folder now
        shown are kept, as vlc_tagger keeps them."""
        shown = {}
        for i in range(self.dir_list.count()):
            item = self.dir_list.item(i)
            path = item.data(Qt.UserRole)
            if path:
                shown[path] = item.checkState() == Qt.Checked
        if not shown:
            return
        keys = {os.path.normcase(os.path.abspath(p)) for p in shown}
        hidden = [p for p in self.profile.hidden
                  if os.path.normcase(os.path.abspath(p)) not in keys]
        hidden += [os.path.abspath(p) for p, checked in shown.items() if not checked]
        self.profile.hidden = hidden

    # ---- Actions ----

    def _use_template(self, name):
        if name == EMPTY_TEMPLATE:
            source = Profile('')
        else:
            try:
                source = self.store.load(name)
            except ProfileError as e:
                QMessageBox.warning(self, 'New Profile', str(e))
                source = Profile('')
        self.profile = source.copy(self.profile.name)
        self._refresh()

    def _browse_directory(self):
        start = self.profile.playlist_directory or ''
        folder = QFileDialog.getExistingDirectory(self, 'Playlist Folder', start)
        if folder:
            self._set_directory(os.path.normpath(folder))

    def _set_directory(self, folder):
        self._apply_checks()
        self.profile.playlist_directory = folder
        self._refresh()

    def _add_playlists(self):
        start = self.profile.playlist_directory or ''
        paths, _ = QFileDialog.getOpenFileNames(self, 'Add Playlists', start,
                                                'M3U Playlists (*.m3u *.m3u8)')
        if paths:
            self._apply_checks()
            self.profile.add_playlists([os.path.normpath(p) for p in paths])
            self._refresh()

    def _remove_selected(self):
        self._apply_checks()
        for item in self.added_list.selectedItems():
            self.profile.remove_playlist(item.data(Qt.UserRole))
        self._refresh()

    def accept(self):
        self._apply_checks()
        try:
            if self.is_new:
                name = sanitize_name(self.name.text())
                if not name:
                    raise ProfileError('Enter a name for the profile (it can\'t use '
                                       '< > : " / \\ | ? *).')
                self.profile.name = name
                self.store.create(self.profile)
            else:
                self.store.save(self.profile)
        except ProfileError as e:
            QMessageBox.warning(self, self.windowTitle(), str(e))
            return
        super().accept()
