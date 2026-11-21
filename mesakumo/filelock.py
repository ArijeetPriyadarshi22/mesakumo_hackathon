"""Non-blocking exclusive file lock that works on macOS, Linux and Windows.

fcntl only exists on Unix; importing it directly stopped the app from starting on Windows.
"""
try:
    import fcntl
except ImportError:  # Windows
    fcntl = None
    import msvcrt


def lock_nonblocking(handle):
    """Lock an open file exclusively; raise BlockingIOError if another process holds it.

    The lock is released when the file is closed, as with fcntl.flock.
    """
    if fcntl is not None:
        fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        return
    handle.seek(0)
    try:
        msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)  # Byte 0; locking past EOF is allowed.
    except OSError as error:
        raise BlockingIOError(str(error)) from None
