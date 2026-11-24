from __future__ import annotations

import json
import urllib.request
from dataclasses import dataclass
from typing import Any, List, Optional, Tuple
from urllib.parse import urljoin

@dataclass(frozen=True)
class ManifestSource:
    name: str
    url: str


DEFAULT_MANIFEST_SOURCES = (
    ManifestSource("teach.rothe.io", "https://teach.rothe.io/thonny-templates.json"),
    ManifestSource("in.mygymer.ch", "https://in.mygymer.ch/thonny-templates.json"),
)

_SCHEMA_VERSION = 1
_TIMEOUT_SECONDS = 15
_USER_AGENT = "thonny_templates/0.2"
_SOURCE_SEPARATOR = ";"


class ManifestError(Exception):
    """Die Vorlagenliste konnte nicht geladen oder gelesen werden."""


def parse_sources(value: str) -> List[ManifestSource]:
    value = (value or "").strip()
    if not value:
        return list(DEFAULT_MANIFEST_SOURCES)

    sources = []
    for item in value.split(_SOURCE_SEPARATOR):
        item = item.strip()
        if not item:
            continue

        if "|" in item:
            name, url = item.split("|", 1)
        else:
            name, url = item, item

        name = name.strip()
        url = url.strip()
        if not name or not url:
            continue
        sources.append(ManifestSource(name, url))

    return sources or list(DEFAULT_MANIFEST_SOURCES)


@dataclass(frozen=True)
class Template:
    id: str
    title: str
    project_url: str
    url: str
    main: Optional[str]
    packages: Tuple[str, ...] = ()


def fetch_manifest(url: str) -> List[Template]:
    request = urllib.request.Request(url, headers={"User-Agent": _USER_AGENT})
    try:
        with urllib.request.urlopen(request, timeout=_TIMEOUT_SECONDS) as response:
            charset = response.headers.get_content_charset() or "utf-8"
            payload = response.read()
    except OSError as error:
        raise ManifestError(
            "Die Vorlagenliste konnte nicht geladen werden.\n\n" + str(error)
        ) from error

    try:
        data = json.loads(payload.decode(charset))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ManifestError("Die Vorlagenliste ist beschädigt.") from error

    return parse_manifest(data, url)


def parse_manifest(data: Any, base_url: str) -> List[Template]:
    if not isinstance(data, dict):
        raise ManifestError("Die Vorlagenliste hat ein unbekanntes Format.")

    if data.get("schema") != _SCHEMA_VERSION:
        raise ManifestError("Die Vorlagenliste hat eine unbekannte Version.")

    entries = data.get("templates")
    if not isinstance(entries, list) or not entries:
        raise ManifestError("Die Vorlagenliste enthält keine Vorlagen.")

    templates = []
    for entry in entries:
        if not isinstance(entry, dict):
            raise ManifestError("Die Vorlagenliste enthält einen ungültigen Eintrag.")

        template_id = _require_string(entry, "id")
        title = _require_string(entry, "title")
        url = _require_string(entry, "url")
        project_url = _require_string(entry, "project_url")

        main = entry.get("main")
        if main is not None and not isinstance(main, str):
            raise ManifestError("Die Startdatei von «%s» ist ungültig." % title)

        packages = entry.get("packages", [])
        if not isinstance(packages, list) or not all(
            isinstance(package, str) and package.strip() for package in packages
        ):
            raise ManifestError("Die Pakete von «%s» sind ungültig." % title)

        templates.append(
            Template(
                id=template_id,
                title=title,
                project_url=urljoin(base_url, project_url),
                url=urljoin(base_url, url),
                main=main,
                packages=tuple(package.strip() for package in packages),
            )
        )

    return templates


def _require_string(entry: dict, key: str) -> str:
    value = entry.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ManifestError("In der Vorlagenliste fehlt das Feld «%s»." % key)
    return value
