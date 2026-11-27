"""Main window: Files and Playlists lists on the left, details on the right"""
import logging
import os

from PyQt5.QtCore import QSettings, QThread, Qt, pyqtSignal
from PyQt5.QtWidgets import (QAbstractItemView, QAction, QActionGroup, QFileDialog,
                             QHBoxLayout, QHeaderView, QLabel, QLineEdit, QMainWindow,
                             QMessageBox, QSizePolicy, QSplitter, QStackedWidget,
                             QTableView, QTabWidget, QVBoxLayout, QWidget)

from music_db import Library, LibraryError, ProfileError, ProfileStore, default_db_path

from ..media_info import update_info
from ..operations import OperationError, apply_rename, plan_rename
from ..scanner import scan
from . import theme
from .details import FileDetails, PlaylistDetails
from .profile_dialog import ProfileDialog
from .models import ID_ROLE, PLAYLIST_COLUMNS, TRACK_COLUMNS, FilterProxy, RowsModel

logger = logging.getLogger(__name__)

MAX_LISTED_PLAYLISTS = 15

_current_window = None      # keeps the open window alive when libraries are switched

PROFILES_DIR_KEY = 'profiles_dir'
PROFILE_KEY = 'profile'


def _settings():
    return QSettings('MagpieMusicManager', 'magpie')


def saved_profiles_dir():
    """The vlc_tagger config folder chosen last (Profiles > Profiles Folder),
    or None for the default"""
    return _settings().value(PROFILES_DIR_KEY, None) or None


def starting_profile(store):
    """The profile to open: Magpie's last one, else vlc_tagger's, else any"""
    name = _settings().value(PROFILE_KEY, None)
    if name and store.exists(name):
        return name
    names = store.names()
    return (store.last_profile() or ('default' if 'default' in names else None)
            or (names[0] if names else None))


def open_library_window(lib, store, profile_name, geometry=None):
    """Show a main window on lib (the app has one window at a time)"""
    global _current_window
    window = MainWindow(lib, store, profile_name)
    if geometry is not None:
        window.restoreGeometry(geometry)
    window.show()
    _current_window = window
    return window


def is_shared_library(path):
    """Whether path is the database every app uses by default (vlc_tagger too)"""
    def norm(p):
        return os.path.normcase(os.path.abspath(p))
    return norm(path) == norm(default_db_path())


class ScanThread(QThread):
    """Scans the profile's playlists with its own database connection"""
    done = pyqtSignal(object)       # ScanResult, or the exception that stopped it

    def __init__(self, db_path, playlists, parent=None):
        super().__init__(parent)
        self.db_path = db_path
        self.playlists = playlists

    def run(self):
        try:
            with Library.open(self.db_path) as lib:
                result = scan(lib, self.playlists, cancelled=self.isInterruptionRequested)
            self.done.emit(result)
        except Exception as e:          # report anything rather than die silently
            logger.exception('Scan failed')
            self.done.emit(e)


class InfoThread(QThread):
    """Reads artist/album/length for tracks that need it, with its own
    database connection, while the rest of the app carries on"""
    info_ready = pyqtSignal(object)     # list of {'id', 'artist', 'album', 'length_ms'}
    progress = pyqtSignal(int, int)     # checked, total
    done = pyqtSignal(object)           # InfoResult, or the exception that stopped it

    def __init__(self, db_path, skip, parent=None):
        super().__init__(parent)
        self.db_path = db_path
        self.skip = skip                # only this thread touches it while running

    def run(self):
        try:
            with Library.open(self.db_path) as lib:
                result = update_info(lib, cancelled=self.isInterruptionRequested,
                                     on_batch=self.info_ready.emit,
                                     on_progress=self.progress.emit, skip=self.skip)
            self.done.emit(result)
        except Exception as e:
            logger.exception('Reading file info failed')
            self.done.emit(e)


class TagStatus(QWidget):
    """Status bar indicator: a coloured dot and a word or two saying whether
    the artist/album/length columns are filled in yet"""
    COLOURS = {'idle': '#9e9e9e', 'busy': '#e6a700', 'done': '#2e9d4f',
               'error': '#c0392b'}

    def __init__(self, parent=None):
        super().__init__(parent)
        self.dot = QLabel('●')
        self.text = QLabel()
        layout = QHBoxLayout(self)
        layout.setContentsMargins(4, 0, 4, 0)
        layout.setSpacing(4)
        layout.addWidget(self.dot)
        layout.addWidget(self.text)
        self.state = None

    def set_state(self, state, text, tooltip=''):
        self.state = state
        self.dot.setStyleSheet(f'color: {self.COLOURS[state]};')
        self.text.setText(text)
        self.setToolTip(tooltip)


class ListTab(QWidget):
    """Filter box over a sortable table"""

    def __init__(self, columns, placeholder, parent=None):
        super().__init__(parent)
        self.model = RowsModel(columns, self)
        self.proxy = FilterProxy(self)
        self.proxy.setSourceModel(self.model)

        self.filter = QLineEdit()
        self.filter.setPlaceholderText(placeholder)
        self.filter.setClearButtonEnabled(True)
        self.filter.textChanged.connect(self.proxy.set_filter_text)

        self.view = QTableView()
        self.view.setModel(self.proxy)
        self.view.setSortingEnabled(True)
        self.view.sortByColumn(0, Qt.AscendingOrder)
        self.view.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.view.setSelectionMode(QAbstractItemView.SingleSelection)
        self.view.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.view.setWordWrap(False)
        self.view.verticalHeader().hide()
        self.view.verticalHeader().setDefaultSectionSize(
            self.view.fontMetrics().height() + 6)
        header = self.view.horizontalHeader()
        header.setSectionResizeMode(QHeaderView.Interactive)
        header.setStretchLastSection(False)
        header.resizeSection(0, 320)
        header.resizeSection(1, 260)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 4, 0, 0)
        layout.addWidget(self.filter)
        layout.addWidget(self.view)

    def selected_id(self):
        index = self.view.currentIndex()
        return index.data(ID_ROLE) if index.isValid() else None

    def set_rows(self, rows, keep_selection=True):
        selected = self.selected_id() if keep_selection else None
        self.model.set_rows(rows)
        if selected is not None:
            self.select_id(selected)

    def select_id(self, row_id):
        """Select the row with this id, clearing the filter if it hides it"""
        source_row = self.model.row_of_id(row_id)
        if source_row is None:
            return False
        index = self.proxy.mapFromSource(self.model.index(source_row, 0))
        if not index.isValid():
            self.filter.clear()
            index = self.proxy.mapFromSource(self.model.index(source_row, 0))
        self.view.setCurrentIndex(index)
        self.view.scrollTo(index, QAbstractItemView.PositionAtCenter)
        return True


class MainWindow(QMainWindow):
    def __init__(self, lib, store, profile_name):
        super().__init__()
        self.lib = lib
        self.store = store              # vlc_tagger's profiles (music_db.profiles)
        self.profile = None             # the active Profile; None if there isn't one
        self.profile_paths = []         # its playlists, as of the last load
        self.profile_ids = set()        # ids of those that are in the database
        self.scan_thread = None
        self._scan_again = False        # the profile changed while a scan ran
        self.info_thread = None
        self._info_again = False        # a scan finished while tags were being read
        self._unreadable = set()        # files mutagen couldn't read, not retried
        self.shared = is_shared_library(lib.path)
        self.resize(1400, 850)

        self.files_tab = ListTab(TRACK_COLUMNS, 'Filter files (name, folder, artist, album)')
        self.playlists_tab = ListTab(PLAYLIST_COLUMNS, 'Filter playlists')
        self.tabs = QTabWidget()
        self.tabs.addTab(self.files_tab, 'Files')
        self.tabs.addTab(self.playlists_tab, 'Playlists')
        self.tabs.currentChanged.connect(self._on_tab_changed)

        self.empty_page = QLabel()
        self.empty_page.setAlignment(Qt.AlignCenter)
        self.empty_page.setWordWrap(True)
        self.file_details = FileDetails()
        self.file_details.open_playlist.connect(self.open_playlist)
        self.file_details.rename_requested.connect(self.rename)
        self.playlist_details = PlaylistDetails()
        self.playlist_details.open_track.connect(self.open_track)
        self.details = QStackedWidget()
        for page in (self.empty_page, self.file_details, self.playlist_details):
            self.details.addWidget(page)

        # Neither side asks for width from its contents (long paths, wide
        # tables), so the split stays where it's put
        for widget, minimum in ((self.tabs, 300), (self.details, 300)):
            policy = widget.sizePolicy()
            policy.setHorizontalPolicy(QSizePolicy.Ignored)
            widget.setSizePolicy(policy)
            widget.setMinimumWidth(minimum)
        self.splitter = QSplitter()
        self.splitter.addWidget(self.tabs)
        self.splitter.addWidget(self.details)
        self.splitter.setStretchFactor(0, 3)
        self.splitter.setStretchFactor(1, 2)
        self.splitter.setSizes([820, 580])
        self.setCentralWidget(self.splitter)

        self.files_tab.view.selectionModel().currentRowChanged.connect(
            lambda *_: self._show_selected())
        self.playlists_tab.view.selectionModel().currentRowChanged.connect(
            lambda *_: self._show_selected())

        self.tag_status = TagStatus()
        self.tag_status.set_state('idle', 'Tags waiting for scan',
                                  'Artist, album and length are read after the '
                                  'playlist scan.')
        self.statusBar().addPermanentWidget(self.tag_status)
        self.profile_label = QLabel()
        self.statusBar().addPermanentWidget(self.profile_label)
        if not self.shared:
            # Make it obvious this isn't the library vlc_tagger uses
            library_label = QLabel(' Other library ')
            library_label.setStyleSheet('background: #e6a700; color: black; '
                                        'border-radius: 3px; font-weight: bold;')
            library_label.setToolTip(f'{lib.path}\n\nFile > Open Shared Library to go back.')
            self.statusBar().addPermanentWidget(library_label)

        self._build_menus()
        self._load_profile(profile_name, quiet=True)
        self.reload()
        if self.lib.readonly:
            QMessageBox.warning(self, 'Read-only library',
                                'This library database was made by a newer version of '
                                'Magpie, so it is opened read-only.')
        self.rescan()

    # ---- Menus ----

    def _build_menus(self):
        file_menu = self.menuBar().addMenu('&File')
        self.rescan_action = QAction('Re&scan Playlists', self)
        self.rescan_action.setShortcut('F5')
        self.rescan_action.triggered.connect(self.rescan)
        file_menu.addAction(self.rescan_action)
        file_menu.addSeparator()
        open_action = QAction('&Open Library...', self)
        open_action.triggered.connect(self.choose_library)
        file_menu.addAction(open_action)
        shared_action = QAction('Open S&hared Library', self)
        shared_action.triggered.connect(lambda: self.switch_library(default_db_path()))
        shared_action.setEnabled(not self.shared)
        file_menu.addAction(shared_action)
        file_menu.addSeparator()
        quit_action = QAction('&Quit', self)
        quit_action.setShortcut('Ctrl+Q')
        quit_action.triggered.connect(self.close)
        file_menu.addAction(quit_action)

        edit_menu = self.menuBar().addMenu('&Edit')
        rename_action = QAction('&Rename File', self)
        rename_action.setShortcut('F2')
        rename_action.triggered.connect(self._focus_rename)
        edit_menu.addAction(rename_action)
        find_action = QAction('&Find', self)
        find_action.setShortcut('Ctrl+F')
        find_action.triggered.connect(lambda: self._current_tab().filter.setFocus())
        edit_menu.addAction(find_action)

        view_menu = self.menuBar().addMenu('&View')
        self.dark_mode_action = QAction('&Dark Mode', self)
        self.dark_mode_action.setCheckable(True)
        self.dark_mode_action.setChecked(theme.is_dark())
        self.dark_mode_action.triggered.connect(self.set_dark_mode)
        view_menu.addAction(self.dark_mode_action)

        profiles_menu = self.menuBar().addMenu('&Profiles')
        self.new_profile_action = QAction('&New Profile...', self)
        self.new_profile_action.setShortcut('Ctrl+Shift+N')
        self.new_profile_action.triggered.connect(self.new_profile)
        profiles_menu.addAction(self.new_profile_action)
        self.edit_profile_action = QAction('&Edit Profile...', self)
        self.edit_profile_action.setShortcut('Ctrl+E')
        self.edit_profile_action.triggered.connect(self.edit_profile)
        profiles_menu.addAction(self.edit_profile_action)
        profiles_menu.addSeparator()
        self.switch_menu = profiles_menu.addMenu('&Switch To')
        self.switch_menu.aboutToShow.connect(self._fill_switch_menu)
        profiles_menu.addSeparator()
        folder_action = QAction('Profiles &Folder...', self)
        folder_action.triggered.connect(self.choose_profiles_folder)
        profiles_menu.addAction(folder_action)

        self.rescan_action.setEnabled(not self.lib.readonly)

    def _fill_switch_menu(self):
        menu = self.switch_menu
        menu.clear()
        names = self.store.names()
        if not names:
            menu.addAction('(no profiles yet)').setEnabled(False)
        group = QActionGroup(menu)
        for name in names:
            action = menu.addAction(name)
            action.setCheckable(True)
            action.setChecked(self.profile is not None and name == self.profile.name)
            action.triggered.connect(lambda _=False, n=name: self.switch_profile(n))
            group.addAction(action)

    def set_dark_mode(self, dark):
        theme.apply(dark)
        theme.save_dark(dark)
        self.dark_mode_action.setChecked(dark)
        # The details panel sets some colours itself (missing files): redo them,
        # without resetting a name being typed in the rename box
        page = self.details.currentWidget()
        if page is self.file_details and self.file_details.track_id is not None:
            track = self.lib.track(self.file_details.track_id)
            if track is not None:
                self.file_details.show_info(track, os.path.isfile(track['path']))
        elif page is self.playlist_details and self.playlist_details.playlist_id is not None:
            self.playlist_details.show_playlist(self.lib, self.playlist_details.playlist_id)

    # ---- Libraries ----

    def choose_library(self):
        path, _ = QFileDialog.getOpenFileName(
            self, 'Open Library', os.path.dirname(self.lib.path),
            'Library databases (*.sqlite *.db);;All files (*)')
        if path:
            self.switch_library(path)

    def switch_library(self, path):
        """Close this window and open one on another database"""
        if os.path.normcase(os.path.abspath(path)) == os.path.normcase(
                os.path.abspath(self.lib.path)):
            return
        try:
            lib = Library.open(path)
        except LibraryError as e:
            QMessageBox.warning(self, 'Open Library', str(e))
            return
        logger.info(f'Library: {lib.path}')
        open_library_window(lib, self.store,
                            self.profile.name if self.profile else None,
                            self.saveGeometry())
        self.close()

    # ---- Profiles ----

    def _load_profile(self, name, quiet=False):
        """Make name the active profile (None: no profile). Returns False if
        it couldn't be read."""
        profile = None
        if name:
            try:
                profile = self.store.load(name)
            except ProfileError as e:
                logger.warning(str(e))
                if not quiet:
                    QMessageBox.warning(self, 'Profiles', str(e))
        self.profile = profile
        self.profile_paths = profile.playlists() if profile else []
        if profile:
            _settings().setValue(PROFILE_KEY, profile.name)
            logger.info(f'Profile: {profile.name} ({len(self.profile_paths)} playlists)')

        title = 'Magpie Music Manager'
        if profile:
            title += f' - {profile.name}'
        if not self.shared:
            title += f' - {self.lib.path}'
        self.setWindowTitle(title)
        self.profile_label.setText(f' Profile: {profile.name} ' if profile
                                   else ' No profile ')
        tip = [f'Profiles from {self.store.profiles_dir}']
        if profile and profile.playlist_directory:
            tip.append(f'Playlist folder: {profile.playlist_directory}')
        self.profile_label.setToolTip('\n'.join(tip))
        self.edit_profile_action.setEnabled(profile is not None)
        return profile is not None or not name

    def switch_profile(self, name):
        if self.profile is not None and name == self.profile.name:
            return
        if self._load_profile(name):
            self.reload(keep_selection=False)
            self.rescan()

    def new_profile(self):
        dialog = ProfileDialog(self.store, None,
                               self.profile.name if self.profile else None, self)
        if dialog.exec_() == dialog.Accepted:
            self.statusBar().showMessage(f'Profile "{dialog.profile.name}" created', 15000)
            self.switch_profile(dialog.profile.name)

    def edit_profile(self):
        if self.profile is None:
            return
        try:
            profile = self.store.load(self.profile.name)    # as it is on disk now
        except ProfileError as e:
            QMessageBox.warning(self, 'Edit Profile', str(e))
            return
        dialog = ProfileDialog(self.store, profile, parent=self)
        if dialog.exec_() == dialog.Accepted:
            self._load_profile(profile.name)
            self.reload()
            self.rescan()

    def choose_profiles_folder(self):
        """Point Magpie at another vlc_tagger config folder (a vlc_tagger
        run from source keeps its profiles next to its code, in config/)"""
        folder = QFileDialog.getExistingDirectory(
            self, 'vlc_tagger config folder (the one holding "profiles")',
            self.store.config_dir)
        if not folder:
            return
        folder = os.path.normpath(folder)
        if os.path.basename(folder).lower() == 'profiles':
            folder = os.path.dirname(folder)        # picked the profiles folder itself
        store = ProfileStore(folder)
        if not store.names():
            answer = QMessageBox.question(
                self, 'Profiles Folder',
                f'There are no profiles in\n{store.profiles_dir}\n\nUse it anyway? '
                '(New profiles will be made there.)')
            if answer != QMessageBox.Yes:
                return
        self.store = store
        _settings().setValue(PROFILES_DIR_KEY, store.config_dir)
        self._load_profile(starting_profile(store), quiet=True)
        self.reload(keep_selection=False)
        self.rescan()

    # ---- Data ----

    def reload(self, keep_selection=True):
        """Show the active profile's playlists, and the files they list"""
        playlists = self.lib.playlists(self.profile_paths)
        self.profile_ids = {p['id'] for p in playlists}
        self.files_tab.set_rows(self.lib.tracks(self.profile_ids), keep_selection)
        self.playlists_tab.set_rows(playlists, keep_selection)
        self.tabs.setTabText(0, f'Files ({len(self.files_tab.model.rows)})')
        self.tabs.setTabText(1, f'Playlists ({len(self.playlists_tab.model.rows)})')
        self._show_selected()

    def rescan(self):
        if self.lib.readonly:
            return
        if self.scan_thread is not None:
            self._scan_again = True     # e.g. the profile changed: scan it when done
            return
        self._scan_again = False
        if self.profile is not None:
            # Re-read the profile: vlc_tagger may have changed it meanwhile
            self._load_profile(self.profile.name, quiet=True)
        if not self.profile_paths:
            self.statusBar().showMessage(
                'Create a profile (Profiles > New Profile...) to get started'
                if self.profile is None else
                'This profile has no playlists: Profiles > Edit Profile... to choose some')
            self.reload()
            self.read_info()
            return
        self.statusBar().showMessage(f'Scanning {len(self.profile_paths)} playlists...')
        self.rescan_action.setEnabled(False)
        self.scan_thread = ScanThread(self.lib.path, list(self.profile_paths), self)
        self.scan_thread.done.connect(self._on_scan_done)
        self.scan_thread.start()

    def _on_scan_done(self, result):
        self.scan_thread.wait()
        self.scan_thread = None
        self.rescan_action.setEnabled(True)
        if self._scan_again:
            self.reload()
            self.rescan()
            return
        if isinstance(result, Exception):
            self.statusBar().showMessage(f'Scan failed: {result}')
            self.read_info()
            return
        self.statusBar().showMessage(f'Scan done: {result.summary()}', 15000)
        self.reload()
        self._warn_missing()
        self.read_info()

    def _warn_missing(self):
        """Say if the profile's folder or some of its added playlists can't
        be found (what was scanned from them is kept in the database)"""
        if self.profile is None:
            return
        missing = list(self.profile.missing_added())
        folder = self.profile.playlist_directory
        if folder and not os.path.isdir(folder):
            missing.insert(0, folder)
        if missing:
            QMessageBox.warning(self, 'Playlists not found',
                                f'Profile "{self.profile.name}": these could not be '
                                'found (drive unplugged?):\n\n' + '\n'.join(missing))

    # ---- File info (artist, album, length) ----

    def read_info(self):
        """Fill in artist/album/length in the background. If it's already
        running, it runs again when done (to pick up newly scanned files)."""
        if self.lib.readonly:
            self.tag_status.set_state('idle', 'Tags not read',
                                      'The library is read-only, so tags are not read.')
            return
        if self.info_thread is not None:
            self._info_again = True
            return
        self._info_again = False
        self.tag_status.set_state('busy', 'Reading tags...',
                                  'Filling in artist, album and length.')
        self.info_thread = InfoThread(self.lib.path, self._unreadable, self)
        self.info_thread.info_ready.connect(self._on_info_ready)
        self.info_thread.progress.connect(self._on_info_progress)
        self.info_thread.done.connect(self._on_info_done)
        self.info_thread.start()

    def _on_info_ready(self, updates):
        self.files_tab.model.update_rows(updates)
        if (self.details.currentWidget() is self.file_details
                and self.file_details.track_id is not None):
            for update in updates:
                if update['id'] == self.file_details.track_id:
                    self.file_details.show_info(update)
                    break

    def _on_info_progress(self, checked, total):
        if checked < total:
            self.tag_status.set_state('busy', f'Reading tags {checked} / {total}',
                                      'Filling in artist, album and length.')

    def _on_info_done(self, result):
        self.info_thread.wait()
        self.info_thread = None
        if isinstance(result, Exception):
            self.tag_status.set_state('error', 'Tag reading failed', str(result))
            self.statusBar().showMessage(f'Reading file info failed: {result}', 15000)
        elif not result.cancelled:
            if result.read:
                self.statusBar().showMessage(f'File info: {result.summary()}', 15000)
            tip = ['Artist, album and length are filled in.']
            if self._unreadable:
                tip.append(f'{len(self._unreadable)} files have no readable tags '
                           '(vlc_tagger may still read them).')
            if result.missing:
                tip.append(f'{result.missing} files were not found.')
            self.tag_status.set_state('done', 'Tags up to date', '\n'.join(tip))
        if self._info_again:
            self.read_info()            # more files were scanned meanwhile

    # ---- Selection ----

    def _current_tab(self):
        return self.files_tab if self.tabs.currentIndex() == 0 else self.playlists_tab

    def _on_tab_changed(self, _index):
        self._show_selected()

    def _show_selected(self):
        tab = self._current_tab()
        selected = tab.selected_id()
        if selected is None:
            if self.profile is None:
                text = ('Choose a profile (Profiles > Switch To), or create one '
                        '(Profiles > New Profile...).\n\nProfiles are shared with '
                        'vlc_tagger; these are from\n' + self.store.profiles_dir)
            elif not self.profile_paths:
                text = (f'Profile "{self.profile.name}" has no playlists yet.\n\n'
                        'Profiles > Edit Profile... to choose a playlist folder or '
                        'add playlists.')
            elif tab is self.files_tab:
                text = 'Select a file to see which playlists it is in, or rename it.'
            else:
                text = 'Select a playlist to see its files.'
            self.empty_page.setText(text)
            self.details.setCurrentWidget(self.empty_page)
        elif tab is self.files_tab:
            self.file_details.show_track(self.lib, selected, self.profile_ids)
            self.details.setCurrentWidget(self.file_details)
        else:
            self.playlist_details.show_playlist(self.lib, selected)
            self.details.setCurrentWidget(self.playlist_details)

    def open_playlist(self, playlist_id):
        self.tabs.setCurrentWidget(self.playlists_tab)
        self.playlists_tab.select_id(playlist_id)

    def open_track(self, track_id):
        self.tabs.setCurrentWidget(self.files_tab)
        self.files_tab.select_id(track_id)

    def _focus_rename(self):
        if self.details.currentWidget() is self.file_details:
            self.file_details.focus_name()

    # ---- Rename ----

    def rename(self, track_id, new_name):
        try:
            plan = plan_rename(self.lib, track_id, new_name)
        except OperationError as e:
            QMessageBox.warning(self, 'Rename', str(e))
            return

        text = (f'Rename\n    {os.path.basename(plan.old_path)}\nto\n'
                f'    {os.path.basename(plan.new_path)}\n')
        if plan.extension_changed:
            text += '\nThe file extension changes, which can stop the file from playing.\n'
        if plan.playlists:
            shown = [os.path.basename(p) for p in plan.playlists[:MAX_LISTED_PLAYLISTS]]
            more = len(plan.playlists) - len(shown)
            text += (f'\n{len(plan.playlists)} playlist(s) will be updated to match:\n'
                     + '\n'.join(f'    {s}' for s in shown)
                     + (f'\n    ...and {more} more' if more else ''))
        else:
            text += '\nNo playlists list this file.'
        box = QMessageBox(QMessageBox.Question, 'Rename File', text,
                          QMessageBox.Ok | QMessageBox.Cancel, self)
        box.button(QMessageBox.Ok).setText('Rename')
        if box.exec_() != QMessageBox.Ok:
            return

        try:
            result = apply_rename(self.lib, plan)
        except OperationError as e:
            QMessageBox.warning(self, 'Rename', str(e))
            return
        if result.failed:
            QMessageBox.warning(
                self, 'Rename',
                'The file was renamed, but these playlists could not be updated:\n\n'
                + '\n'.join(f'{p}\n    {msg}' for p, msg in result.failed))
        self.statusBar().showMessage(
            f'Renamed to {os.path.basename(result.new_path)}; '
            f'{len(result.updated)} playlist(s) updated', 15000)
        self.reload()
        self.files_tab.select_id(track_id)

    # ---- Closing ----

    def closeEvent(self, event):
        threads = [t for t in (self.scan_thread, self.info_thread) if t is not None]
        for thread in threads:
            thread.requestInterruption()
        for thread in threads:
            thread.wait(10000)
        self.lib.close()
        super().closeEvent(event)
