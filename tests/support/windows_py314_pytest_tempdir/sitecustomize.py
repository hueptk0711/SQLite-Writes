"""Local pytest workaround for Python 3.14 Windows tempdir permissions.

Some Python 3.14 Windows builds create directories requested with mode 0o700 in
a state that the same user cannot scan. Pytest uses 0o700 for its numbered temp
directories, which prevents tmp_path fixtures from being created in this local
environment. This support shim is enabled only when explicitly placed on
PYTHONPATH for local validation; it is not imported by project code or server
execution.
"""

from __future__ import annotations

import os

_real_mkdir = os.mkdir


def _mkdir_without_restrictive_windows_mode(
    path: str | bytes,
    mode: int = 0o777,
    *,
    dir_fd: int | None = None,
) -> None:
    if os.name == "nt" and mode == 0o700:
        mode = 0o777
    if dir_fd is None:
        _real_mkdir(path, mode)
    else:
        _real_mkdir(path, mode, dir_fd=dir_fd)


os.mkdir = _mkdir_without_restrictive_windows_mode
