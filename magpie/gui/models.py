"""Table models for lists of rows from music_db (each row is a dict with an 'id')"""
import os

from PyQt5.QtCore import QAbstractTableModel, QModelIndex, QSortFilterProxyModel, Qt

SORT_ROLE = Qt.UserRole
ID_ROLE = Qt.UserRole + 1


def format_length(length_ms):
    """3:07, or 1:02:03 for an hour or more; blank when unknown"""
    if length_ms is None or length_ms < 0:
        return ''
    seconds = int(round(length_ms / 1000))
    hours, rest = divmod(seconds, 3600)
    minutes, seconds = divmod(rest, 60)
    if hours:
        return f'{hours}:{minutes:02d}:{seconds:02d}'
    return f'{minutes}:{seconds:02d}'


class Column:
    def __init__(self, title, display, sort=None, align=None):
        self.title = title
        self.display = display          # row -> str
        self.sort = sort                # row -> sort key (default: display, no case)
        self.align = align


class RowsModel(QAbstractTableModel):
    def __init__(self, columns, parent=None):
        super().__init__(parent)
        self.columns = columns
        self.rows = []
        self._row_of_id = {}

    def set_rows(self, rows):
        self.beginResetModel()
        self.rows = rows
        self._row_of_id = {r['id']: i for i, r in enumerate(rows)}
        self.endResetModel()

    def update_rows(self, updates):
        """Change fields of rows in place: updates is a list of dicts with an
        'id' plus the fields to set. Ids not in the model are ignored."""
        changed = []
        for update in updates:
            row = self._row_of_id.get(update['id'])
            if row is not None:
                self.rows[row].update(update)
                changed.append(row)
        if changed:
            # One signal for the span, so the sort/filter proxy redoes it once
            self.dataChanged.emit(self.index(min(changed), 0),
                                  self.index(max(changed), len(self.columns) - 1))
        return len(changed)

    def row_of_id(self, row_id):
        return self._row_of_id.get(row_id)

    def rowCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else len(self.rows)

    def columnCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else len(self.columns)

    def headerData(self, section, orientation, role=Qt.DisplayRole):
        if orientation == Qt.Horizontal and role == Qt.DisplayRole:
            return self.columns[section].title
        return None

    def data(self, index, role=Qt.DisplayRole):
        if not index.isValid():
            return None
        row = self.rows[index.row()]
        col = self.columns[index.column()]
        if role == Qt.DisplayRole:
            return col.display(row)
        if role == SORT_ROLE:
            return col.sort(row) if col.sort else (col.display(row) or '').lower()
        if role == ID_ROLE:
            return row['id']
        if role == Qt.ToolTipRole:
            return row.get('path')
        if role == Qt.TextAlignmentRole and col.align is not None:
            return col.align
        return None


class FilterProxy(QSortFilterProxyModel):
    """Sorts by SORT_ROLE; filter text matches any column (all words must match)"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setSortRole(SORT_ROLE)
        self._words = []

    def set_filter_text(self, text):
        self._words = text.lower().split()
        self.invalidateFilter()

    def filterAcceptsRow(self, source_row, source_parent):
        if not self._words:
            return True
        model = self.sourceModel()
        row = model.rows[source_row]
        haystack = ' '.join(c.display(row) or '' for c in model.columns).lower()
        haystack += ' ' + (row.get('path') or '').lower()
        return all(w in haystack for w in self._words)


RIGHT = Qt.AlignRight | Qt.AlignVCenter

TRACK_COLUMNS = [
    Column('Name', lambda r: os.path.basename(r['path'])),
    Column('Folder', lambda r: os.path.dirname(r['path'])),
    Column('Artist', lambda r: r['artist'] or ''),
    Column('Album', lambda r: r['album'] or ''),
    Column('Length', lambda r: format_length(r['length_ms']),
           sort=lambda r: r['length_ms'] if r['length_ms'] is not None else -1, align=RIGHT),
    Column('Playlists', lambda r: str(r['playlists']), sort=lambda r: r['playlists'],
           align=RIGHT),
]

PLAYLIST_COLUMNS = [
    Column('Name', lambda r: os.path.basename(r['path'])),
    Column('Folder', lambda r: os.path.dirname(r['path'])),
    Column('Tracks', lambda r: str(r['entries']), sort=lambda r: r['entries'], align=RIGHT),
]
