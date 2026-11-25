"""Nonblocking advisory lock for the legacy extraction paths."""
import os


def acquire(file):
    if os.name == 'nt':
        import msvcrt

        file.seek(0)
        try:
            msvcrt.locking(file.fileno(), msvcrt.LK_NBLCK, 1)
        except OSError as error:
            raise BlockingIOError('Extraction is already in progress.') from error
    else:
        import fcntl

        fcntl.flock(file, fcntl.LOCK_EX | fcntl.LOCK_NB)
