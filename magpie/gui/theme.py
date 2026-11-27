"""
Theme - Light/dark mode for the whole app (same look as vlc_tagger's).

Dark mode uses the Fusion style with a dark palette; light mode restores the
exact style and palette the app started with. On Windows the title bars are
switched too (Qt doesn't draw those). The choice is saved with QSettings.

No project imports allowed in this file.
"""
import ctypes
import logging
import sys

from PyQt5.QtCore import QEvent, QObject, QSettings, Qt
from PyQt5.QtGui import QColor, QPalette
from PyQt5.QtWidgets import QApplication, QStyleFactory

logger = logging.getLogger(__name__)

# "File not found" text: a darker red reads badly on the dark background
LIGHT_MISSING = QColor('#c0392b')
DARK_MISSING = QColor('#ff6b5b')

SETTINGS_KEY = 'dark_mode'

_dark = False
_original = None        # (style name, palette) captured before the first change
_title_bar_filter = None


def is_dark() -> bool:
    return _dark


def missing_colour() -> QColor:
    return DARK_MISSING if _dark else LIGHT_MISSING


def _settings():
    return QSettings('MagpieMusicManager', 'magpie')


def saved_dark() -> bool:
    return _settings().value(SETTINGS_KEY, False, type=bool)


def save_dark(dark: bool):
    _settings().setValue(SETTINGS_KEY, bool(dark))


def _dark_palette() -> QPalette:
    p = QPalette()
    window, base, text = QColor(53, 53, 53), QColor(35, 35, 35), QColor(230, 230, 230)
    accent, disabled = QColor(42, 130, 218), QColor(127, 127, 127)

    p.setColor(QPalette.Window, window)
    p.setColor(QPalette.WindowText, text)
    p.setColor(QPalette.Base, base)
    p.setColor(QPalette.AlternateBase, window)
    p.setColor(QPalette.ToolTipBase, QColor(25, 25, 25))
    p.setColor(QPalette.ToolTipText, text)
    p.setColor(QPalette.Text, text)
    p.setColor(QPalette.Button, window)
    p.setColor(QPalette.ButtonText, text)
    p.setColor(QPalette.BrightText, Qt.red)
    p.setColor(QPalette.Link, accent)
    p.setColor(QPalette.Highlight, accent)
    p.setColor(QPalette.HighlightedText, Qt.white)
    p.setColor(QPalette.PlaceholderText, disabled)
    for role in (QPalette.Text, QPalette.ButtonText, QPalette.WindowText):
        p.setColor(QPalette.Disabled, role, disabled)
    return p


def apply(dark: bool):
    """Switch the whole app to dark or light mode"""
    global _dark, _original
    app = QApplication.instance()
    if _original is None:
        _original = (app.style().objectName(), QPalette(app.palette()))
        _install_title_bar_filter(app)

    _dark = dark
    if dark:
        app.setStyle('Fusion')
        app.setPalette(_dark_palette())
    else:
        app.setStyle(QStyleFactory.create(_original[0]))
        app.setPalette(_original[1])

    for widget in app.topLevelWidgets():
        if widget.isVisible() and _is_titled_window(widget):
            _set_title_bar_dark(widget, dark)

    logger.info(f'Theme: {"dark" if dark else "light"}')


def _is_titled_window(widget) -> bool:
    """Real windows and dialogs, not menus/tooltips (which have no title bar)"""
    return widget.isWindow() and widget.windowType() in (Qt.Window, Qt.Dialog)


def _set_title_bar_dark(widget, dark: bool):
    """Windows 10/11: ask DWM to draw this window's title bar dark or light"""
    if sys.platform != 'win32':
        return
    try:
        hwnd = int(widget.winId())
        value = ctypes.c_int(1 if dark else 0)
        dwm = ctypes.windll.dwmapi
        # 20 = DWMWA_USE_IMMERSIVE_DARK_MODE; 19 on Windows 10 before 20H1
        for attribute in (20, 19):
            if dwm.DwmSetWindowAttribute(hwnd, attribute, ctypes.byref(value),
                                         ctypes.sizeof(value)) == 0:
                break
        # Redraw the frame now (Windows 10 otherwise waits for a focus change)
        SWP_FLAGS = 0x0001 | 0x0002 | 0x0004 | 0x0010 | 0x0020  # NOSIZE|NOMOVE|NOZORDER|NOACTIVATE|FRAMECHANGED
        ctypes.windll.user32.SetWindowPos(hwnd, 0, 0, 0, 0, 0, SWP_FLAGS)
    except Exception as e:
        logger.debug(f'Could not set title bar theme: {e}')


class _TitleBarFilter(QObject):
    """Give every window/dialog the current title bar theme when it's shown"""

    def eventFilter(self, obj, event):
        if (event.type() == QEvent.Show and obj.isWidgetType()
                and _is_titled_window(obj)):
            _set_title_bar_dark(obj, _dark)
        return False


def _install_title_bar_filter(app):
    global _title_bar_filter
    if sys.platform == 'win32':
        _title_bar_filter = _TitleBarFilter()
        app.installEventFilter(_title_bar_filter)
