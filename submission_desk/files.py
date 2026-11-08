"""Safe, explicit file access inside one selected material folder.

Rules:
- Every path is resolved against the workspace's material root. Absolute paths are accepted
  only when they resolve inside the root. ``..`` escapes, NUL bytes and symlinks whose target
  leaves the root are refused.
- The final open uses ``O_NOFOLLOW | O_NONBLOCK`` on the fully resolved path, then ``fstat``
  must report a regular file whose device/inode match the pre-open ``stat``. A file swapped
  between the check and the open is reported as ``changed_during_read``, never followed.
- Bytes are read once. The SHA-256, the size and every content check use those same bytes,
  so a result always describes the content that was actually inspected.
- Modification times are recorded for display only. Identity is the content hash.
"""

from __future__ import annotations

import hashlib
import os
import stat
from dataclasses import dataclass, field
from pathlib import Path

MAX_CONTENT_BYTES = 64 * 1024 * 1024  # larger files are hashed but not content-checked
LIST_LIMIT = 2000
HASH_SEARCH_LIMIT = 5000
_CHUNK = 1024 * 1024


@dataclass
class Snapshot:
    rel_path: str
    sha256: str
    size: int
    mtime_ns: int
    inode: int
    device: int
    data: bytes | None  # None when the file exceeds MAX_CONTENT_BYTES
    status: str = "ok"
    detail: str = ""


@dataclass
class Problem:
    rel_path: str
    status: str  # missing | unreadable | not_regular | outside_root | invalid_path | changed_during_read | root_missing
    detail: str = ""
    candidates: list[str] = field(default_factory=list)
    sha256: str | None = None
    size: int | None = None


def resolve_root(material_root: str) -> Path:
    """Return the real path of an existing directory, or raise ValueError with a reason."""
    if not material_root or "\x00" in material_root:
        raise ValueError("Material folder path is empty or contains a NUL byte.")
    root = Path(os.path.expanduser(material_root))
    if not root.is_absolute():
        raise ValueError("Material folder must be an absolute path on the machine running Hermes.")
    real = Path(os.path.realpath(root))
    if not real.is_dir():
        raise ValueError(f"Material folder does not exist or is not a directory: {real}")
    if real == Path(real.anchor):
        raise ValueError("Refusing to use a filesystem root as the material folder.")
    hermes_home = os.environ.get("HERMES_HOME")
    for protected in filter(None, [hermes_home, os.path.expanduser("~/.hermes"), os.path.expanduser("~/.ssh")]):
        protected_real = Path(os.path.realpath(protected))
        if _is_within(real, protected_real) or _is_within(protected_real, real):
            raise ValueError(
                f"Refusing a material folder that contains or sits inside Hermes or key data ({protected_real})."
            )
    if real == Path(os.path.realpath(os.path.expanduser("~"))):
        raise ValueError("Pick the folder that holds this application's files, not your whole home directory.")
    return real


def _is_within(path: Path, root: Path) -> bool:
    try:
        return os.path.commonpath([str(path), str(root)]) == str(root)
    except ValueError:
        return False


def normalize_rel_path(root: Path, path: str) -> str:
    """Turn a user/agent supplied path into a clean POSIX path relative to ``root``."""
    if not isinstance(path, str) or not path.strip():
        raise ValueError("File path is empty.")
    if "\x00" in path:
        raise ValueError("File path contains a NUL byte.")
    raw = os.path.expanduser(path.strip())
    candidate = Path(raw)
    if candidate.is_absolute():
        lexical = Path(os.path.normpath(candidate))
        if not _is_within(lexical, root):
            # Allow absolute paths that only differ by symlinked parents of the root.
            real_parent = Path(os.path.realpath(lexical.parent))
            if not _is_within(real_parent, root):
                raise ValueError(f"{path} is outside the material folder {root}.")
            lexical = real_parent / lexical.name
        rel = os.path.relpath(lexical, root)
    else:
        rel = os.path.normpath(raw)
    if rel == "." or rel.startswith("..") or os.path.isabs(rel):
        raise ValueError(f"{path} is outside the material folder {root}.")
    return Path(rel).as_posix()


def read_snapshot(root: Path, rel_path: str, *, max_content: int = MAX_CONTENT_BYTES) -> Snapshot | Problem:
    try:
        rel = normalize_rel_path(root, rel_path)
    except ValueError as exc:
        return Problem(rel_path, "invalid_path", str(exc))
    if not root.is_dir():
        return Problem(rel, "root_missing", f"Material folder {root} is missing.")
    lexical = root / rel
    real = Path(os.path.realpath(lexical))
    if not _is_within(real, root):
        return Problem(rel, "outside_root", "The path resolves outside the material folder (symlink escape).")
    try:
        before = os.stat(real, follow_symlinks=False)
    except FileNotFoundError:
        return Problem(rel, "missing", "No file at this path.")
    except PermissionError as exc:
        return Problem(rel, "unreadable", f"Permission denied: {exc.strerror or exc}")
    except OSError as exc:
        return Problem(rel, "unreadable", f"{type(exc).__name__}: {exc.strerror or exc}")
    if not stat.S_ISREG(before.st_mode):
        return Problem(rel, "not_regular", "Not a regular file (directory, device, pipe or socket).")
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0) | getattr(os, "O_CLOEXEC", 0)
    try:
        fd = os.open(real, flags)
    except FileNotFoundError:
        return Problem(rel, "missing", "The file disappeared while it was being opened.")
    except PermissionError as exc:
        return Problem(rel, "unreadable", f"Permission denied: {exc.strerror or exc}")
    except OSError as exc:
        return Problem(rel, "changed_during_read" if exc.errno in (40,) else "unreadable",
                       f"{type(exc).__name__}: {exc.strerror or exc}")
    try:
        opened = os.fstat(fd)
        if not stat.S_ISREG(opened.st_mode):
            return Problem(rel, "not_regular", "Not a regular file.")
        if (opened.st_dev, opened.st_ino) != (before.st_dev, before.st_ino):
            return Problem(rel, "changed_during_read", "The file was replaced while it was being opened.")
        fd_path = f"/proc/self/fd/{fd}"
        if os.path.exists(fd_path):
            # The kernel's view of what was actually opened; closes the directory-swap race.
            actual = Path(os.path.realpath(fd_path))
            if not _is_within(actual, root):
                return Problem(rel, "outside_root", "The opened file lies outside the material folder.")
        digest = hashlib.sha256()
        chunks: list[bytes] = []
        total = 0
        keep = True
        while True:
            chunk = os.read(fd, _CHUNK)
            if not chunk:
                break
            digest.update(chunk)
            total += len(chunk)
            if keep:
                if total > max_content:
                    keep = False
                    chunks = []
                else:
                    chunks.append(chunk)
        after = os.fstat(fd)
    except OSError as exc:
        return Problem(rel, "unreadable", f"{type(exc).__name__}: {exc.strerror or exc}")
    finally:
        os.close(fd)
    if after.st_size != total or after.st_mtime_ns != opened.st_mtime_ns:
        return Problem(rel, "changed_during_read", "The file changed while it was being read; recheck.")
    return Snapshot(
        rel_path=rel,
        sha256=digest.hexdigest(),
        size=total,
        mtime_ns=int(opened.st_mtime_ns),
        inode=int(opened.st_ino),
        device=int(opened.st_dev),
        data=b"".join(chunks) if keep else None,
        detail="" if keep else f"Larger than {max_content} bytes: hashed, content checks skipped.",
    )


def read_with_retry(root: Path, rel_path: str) -> Snapshot | Problem:
    result = read_snapshot(root, rel_path)
    if isinstance(result, Problem) and result.status == "changed_during_read":
        result = read_snapshot(root, rel_path)
    return result


def list_files(root: Path, *, limit: int = LIST_LIMIT) -> tuple[list[dict], bool]:
    """Regular files under ``root`` (no symlinks followed), sorted by path. Returns (entries, truncated)."""
    entries: list[dict] = []
    truncated = False
    for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
        dirnames[:] = sorted(d for d in dirnames if not d.startswith(".git"))
        for name in sorted(filenames):
            full = Path(dirpath) / name
            try:
                st = os.stat(full, follow_symlinks=False)
            except OSError:
                continue
            if not stat.S_ISREG(st.st_mode):
                continue
            entries.append({
                "rel_path": full.relative_to(root).as_posix(),
                "size": int(st.st_size),
                "mtime_ns": int(st.st_mtime_ns),
            })
            if len(entries) >= limit:
                truncated = True
                return entries, truncated
    return entries, truncated


def find_by_hash(root: Path, sha256: str, size: int | None) -> list[str]:
    """Paths under ``root`` whose bytes hash to ``sha256`` (size pre-filter; bounded walk)."""
    matches: list[str] = []
    seen = 0
    for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
        dirnames[:] = sorted(d for d in dirnames if not d.startswith(".git"))
        for name in sorted(filenames):
            seen += 1
            if seen > HASH_SEARCH_LIMIT:
                return matches
            full = Path(dirpath) / name
            try:
                st = os.stat(full, follow_symlinks=False)
            except OSError:
                continue
            if not stat.S_ISREG(st.st_mode) or (size is not None and st.st_size != size):
                continue
            snap = read_snapshot(root, full.relative_to(root).as_posix(), max_content=0)
            if isinstance(snap, Snapshot) and snap.sha256 == sha256:
                matches.append(snap.rel_path)
    return matches
