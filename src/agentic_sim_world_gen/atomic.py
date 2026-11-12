"""Write a file that other agentic-sim-world-gen processes may be writing or reading at the same moment.

The caches (AGENTIC_SIM_WORLD_GEN_CACHE) and the preferences file are shared by every server on the machine: two Claude Code
sessions, or two runs of the benchmark. A temporary file with a fixed name (`<name>.part`) is then one file for
both, and the second rename finds it gone. Here every writer has its own temporary file next to the target, and
the rename is atomic: readers see the old file or the whole new one, and the last writer wins.
"""

from __future__ import annotations

import os
import uuid
from pathlib import Path


def temp_for(path: str | Path, keep_suffix: bool = False) -> Path:
    """A name of this process's own for a file (or directory) to rename onto `path`: same directory, hidden.
    `keep_suffix`: the name ends in the suffix of `path` (a writer that picks the format by the extension)."""
    path = Path(path)
    return path.with_name(f".{path.name}.{os.getpid()}.{uuid.uuid4().hex[:8]}.part{path.suffix if keep_suffix else ''}")


def export_layer(layer, path: str | Path) -> None:
    """Write a USD layer (Sdf.Layer) to `path` whole or not at all: other servers that open `path` meanwhile
    read the old file or the new one, never one being written. Sdf picks the format by the extension, so the
    temporary file keeps it. A layer of this process already open at `path` is reloaded."""
    from pxr import Sdf

    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = temp_for(path, keep_suffix=True)
    try:
        if not layer.Export(str(tmp)):
            raise OSError(f"cannot write {path}")
        os.replace(tmp, path)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise
    old = Sdf.Layer.Find(str(path))
    if old is not None:
        old.Reload(True)


def write(path: str | Path, data: bytes | str) -> None:
    """Write `data` to `path` whole or not at all (str as UTF-8); makes the directory."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = temp_for(path)
    try:
        tmp.write_bytes(data.encode("utf-8") if isinstance(data, str) else data)
        os.replace(tmp, path)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise
