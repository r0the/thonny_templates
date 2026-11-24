import pytest

from thonnycontrib.thonny_templates.manifest import (
    DEFAULT_MANIFEST_SOURCES,
    ManifestError,
    ManifestSource,
    parse_manifest,
    parse_sources,
)


def manifest_entry(**overrides):
    entry = {
        "id": "test",
        "title": "Test",
        "project_url": "informatik/test/",
        "url": "informatik/test.zip",
    }
    entry.update(overrides)
    return entry


def test_default_sources():
    sources = parse_sources("")

    assert [(source.name, source.url) for source in sources] == [
        ("teach.rothe.io", "https://teach.rothe.io/thonny-templates.json"),
        ("in.mygymer.ch", "https://in.mygymer.ch/thonny-templates.json"),
    ]


def test_custom_sources():
    sources = parse_sources(
        "Lokal|http://localhost:5173/thonny-templates.json;"
        "https://example.org/thonny-templates.json"
    )

    assert sources[0] == ManifestSource(
        "Lokal", "http://localhost:5173/thonny-templates.json"
    )
    assert sources[1] == ManifestSource(
        "https://example.org/thonny-templates.json",
        "https://example.org/thonny-templates.json",
    )


def test_relative_urls_are_resolved():
    data = {"schema": 1, "templates": [manifest_entry()]}

    templates = parse_manifest(data, "https://example.org/vorlagen/thonny-templates.json")

    assert templates[0].url == "https://example.org/vorlagen/informatik/test.zip"
    assert templates[0].project_url == "https://example.org/vorlagen/informatik/test/"
    assert templates[0].main is None
    assert templates[0].packages == ()


def test_packages_are_parsed():
    data = {"schema": 1, "templates": [manifest_entry(packages=["pgturbo", "pgzero"])]}

    templates = parse_manifest(data, "https://example.org/thonny-templates.json")

    assert templates[0].packages == ("pgturbo", "pgzero")


def test_invalid_packages_are_rejected():
    data = {"schema": 1, "templates": [manifest_entry(packages="pgturbo")]}

    with pytest.raises(ManifestError):
        parse_manifest(data, "https://example.org/thonny-templates.json")


def test_unknown_schema_is_rejected():
    data = {"schema": 99, "templates": [manifest_entry()]}

    with pytest.raises(ManifestError):
        parse_manifest(data, "https://example.org/thonny-templates.json")


def test_default_source_url_is_used_by_plugin():
    assert DEFAULT_MANIFEST_SOURCES[0].url.endswith("thonny-templates.json")
