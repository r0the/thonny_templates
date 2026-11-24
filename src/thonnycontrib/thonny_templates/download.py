from __future__ import annotations

import os
import re
import shutil
import stat
import tempfile
import urllib.request
import zipfile
from pathlib import Path
from typing import Callable, Iterable, List, Optional, Tuple

MAX_UNCOMPRESSED_SIZE = 100 * 1024 * 1024
CHUNK_SIZE = 64 * 1024
DOWNLOAD_TIMEOUT_SECONDS = 30
USER_AGENT = "thonny_templates/0.2"

_INVALID_FOLDER_CHARS = '<>:"/\\|?*'
_DRIVE_PATTERN = re.compile(r"^[A-Za-z]:")

ProgressCallback = Callable[[int, Optional[int]], None]
CancelCheck = Callable[[], bool]
Member = Tuple[zipfile.ZipInfo, List[str]]


class Cancelled(Exception):
    """Der Vorgang wurde abgebrochen."""


def download_zip(
    url: str,
    destination: str,
    on_progress: Optional[ProgressCallback] = None,
    should_cancel: Optional[CancelCheck] = None,
) -> None:
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(request, timeout=DOWNLOAD_TIMEOUT_SECONDS) as response:
        total = _content_length(response)
        received = 0
        with open(destination, "wb") as output:
            while True:
                if should_cancel and should_cancel():
                    raise Cancelled()
                chunk = response.read(CHUNK_SIZE)
                if not chunk:
                    break
                output.write(chunk)
                received += len(chunk)
                if on_progress:
                    on_progress(received, total)


def extract_zip(
    zip_path: str,
    destination: str,
    on_progress: Optional[ProgressCallback] = None,
    should_cancel: Optional[CancelCheck] = None,
) -> None:
    destination_path = Path(destination)
    destination_path.mkdir(parents=True, exist_ok=True)

    with zipfile.ZipFile(zip_path) as archive:
        members = _select_members(archive.infolist())
        if not members:
            raise ValueError("Die Vorlage enthält keine Dateien.")

        common_root = _common_root(members)
        total = len(members)

        for index, (member, parts) in enumerate(members, start=1):
            if should_cancel and should_cancel():
                raise Cancelled()

            relative_parts = parts[1:] if common_root and parts[0] == common_root else parts
            if not relative_parts:
                continue

            target = _safe_target(destination_path, relative_parts)
            if target is None:
                raise ValueError(
                    "Die Vorlage enthält einen ungültigen Pfad: %s" % member.filename
                )

            if member.is_dir():
                target.mkdir(parents=True, exist_ok=True)
            else:
                target.parent.mkdir(parents=True, exist_ok=True)
                with archive.open(member) as source, open(target, "wb") as output:
                    shutil.copyfileobj(source, output, CHUNK_SIZE)

            if on_progress:
                on_progress(index, total)

    if not any(destination_path.iterdir()):
        raise ValueError("Die Vorlage ist leer.")


def install_zip(
    zip_path: str,
    target_dir: str,
    folder_name: str,
    overwrite: bool = False,
    on_progress: Optional[ProgressCallback] = None,
    should_cancel: Optional[CancelCheck] = None,
) -> Path:
    target_path = Path(target_dir)
    target_path.mkdir(parents=True, exist_ok=True)

    folder_name = sanitize_folder_name(folder_name)
    destination = target_path / folder_name
    staging = Path(tempfile.mkdtemp(prefix=".vorlagen-", dir=str(target_path)))

    try:
        extract_zip(zip_path, str(staging), on_progress, should_cancel)

        if should_cancel and should_cancel():
            raise Cancelled()

        if destination.exists():
            if not overwrite:
                raise FileExistsError("Der Ordner «%s» existiert bereits." % destination.name)
            _remove(destination)

        os.replace(str(staging), str(destination))
        return destination
    except BaseException:
        shutil.rmtree(staging, ignore_errors=True)
        raise


def find_main_file(folder: Path, main: Optional[str]) -> Optional[Path]:
    if main:
        parts = _normalized_parts(main)
        if parts:
            candidate = _safe_target(folder, parts)
            if candidate is not None and candidate.is_file():
                return candidate

    candidates = sorted(path for path in folder.glob("*.py") if path.is_file())
    return candidates[0] if candidates else None


def sanitize_folder_name(name: str) -> str:
    cleaned = "".join(
        "-" if char in _INVALID_FOLDER_CHARS or ord(char) < 32 else char
        for char in name.strip()
    )
    cleaned = cleaned.strip().rstrip(".")
    return cleaned or "Vorlage"


def unique_folder_name(target_dir: str, name: str) -> str:
    base = sanitize_folder_name(name)
    candidate = base
    index = 2
    while (Path(target_dir) / candidate).exists():
        candidate = "%s-%d" % (base, index)
        index += 1
    return candidate


def _select_members(infolist: Iterable[zipfile.ZipInfo]) -> List[Member]:
    selected = []
    for member in infolist:
        parts = _normalized_parts(member.filename)
        if parts is None:
            raise ValueError(
                "Die Vorlage enthält einen ungültigen Pfad: %s" % member.filename
            )
        if not parts or _is_junk(parts) or _is_link(member):
            continue
        selected.append((member, parts))

    total_size = sum(
        member.file_size for member, _ in selected if not member.is_dir()
    )
    if total_size > MAX_UNCOMPRESSED_SIZE:
        raise ValueError("Die Vorlage ist zu gross.")

    return selected


def _normalized_parts(name: str) -> Optional[List[str]]:
    name = name.replace("\\", "/")
    if name.startswith("/") or _DRIVE_PATTERN.match(name):
        return None

    parts = []
    for part in name.split("/"):
        if part in ("", "."):
            continue
        if part == "..":
            return None
        parts.append(part)

    return parts


def _is_junk(parts: List[str]) -> bool:
    for part in parts:
        if part == "__MACOSX" or part == ".DS_Store" or part.startswith("._"):
            return True
    return False


def _is_link(member: zipfile.ZipInfo) -> bool:
    mode = member.external_attr >> 16
    file_type = stat.S_IFMT(mode)
    if file_type == stat.S_IFLNK:
        return True
    return file_type not in (0, stat.S_IFREG, stat.S_IFDIR)


def _common_root(members: List[Member]) -> Optional[str]:
    roots = {parts[0] for _, parts in members}
    if len(roots) != 1:
        return None
    if not any(len(parts) > 1 for _, parts in members):
        return None
    return roots.pop()


def _safe_target(destination: Path, parts: List[str]) -> Optional[Path]:
    candidate = destination.joinpath(*parts)
    try:
        if not candidate.resolve().is_relative_to(destination.resolve()):
            return None
    except (OSError, ValueError):
        return None
    return candidate


def _remove(path: Path) -> None:
    if path.is_dir() and not path.is_symlink():
        shutil.rmtree(path)
    else:
        path.unlink()


def _content_length(response) -> Optional[int]:
    header = response.headers.get("Content-Length")
    if header and header.isdigit():
        return int(header)
    return None
