"""Start the Magpie Music Manager GUI:
    python -m magpie [--db PATH] [--profile NAME] [--profiles-dir FOLDER]"""
import argparse
import logging
import sys

from PyQt5.QtWidgets import QApplication, QMessageBox

from music_db import Library, LibraryError, ProfileStore


def main(argv=None):
    parser = argparse.ArgumentParser(prog='magpie', description='Magpie Music Manager')
    parser.add_argument('--db', help='library database file (default: shared location)')
    parser.add_argument('--profile', help='vlc_tagger profile to open (default: the last one)')
    parser.add_argument('--profiles-dir',
                        help="vlc_tagger's config folder, holding profiles/ "
                             "(default: the one chosen last, else the installed player's)")
    parser.add_argument('--debug', action='store_true', help='more detailed logging')
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.DEBUG if args.debug else logging.INFO,
                        format='%(asctime)s %(levelname)s %(name)s: %(message)s')

    app = QApplication(sys.argv[:1])
    app.setApplicationName('Magpie Music Manager')

    from .gui import theme
    theme.apply(theme.saved_dark())     # before any window, so nothing flashes light
    try:
        lib = Library.open(args.db)
    except LibraryError as e:
        QMessageBox.critical(None, 'Magpie Music Manager', str(e))
        return 1
    logging.getLogger(__name__).info(f'Library: {lib.path}')

    from .gui.main_window import open_library_window, saved_profiles_dir, starting_profile
    store = ProfileStore(args.profiles_dir or saved_profiles_dir())
    profile = args.profile or starting_profile(store)
    logging.getLogger(__name__).info(f'Profiles: {store.profiles_dir}')
    open_library_window(lib, store, profile)
    return app.exec_()


if __name__ == '__main__':
    sys.exit(main())
