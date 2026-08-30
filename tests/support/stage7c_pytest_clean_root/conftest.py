from __future__ import annotations

import os
from pathlib import Path


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


def pytest_configure(config) -> None:
    if config.option.basetemp is None:
        config.option.basetemp = str(Path.cwd() / ".pytest_tmp")
