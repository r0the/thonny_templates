import stat
import zipfile
from pathlib import Path

import pytest

from thonnycontrib.thonny_templates.download import (
    Cancelled,
    extract_zip,
    find_main_file,
    install_zip,
    sanitize_folder_name,
    unique_folder_name,
)


def make_zip(path: Path, entries) -> Path:
    with zipfile.ZipFile(path, "w") as archive:
        for name, content in entries:
            if content is None:
                archive.writestr(name if name.endswith("/") else name + "/", b"")
            else:
                archive.writestr(name, content)
    return path


def test_extracts_single_root_and_skips_junk(tmp_path):
    zip_path = make_zip(
        tmp_path / "vorlage.zip",
        [
            ("vorlage-schneefall/", None),
            ("vorlage-schneefall/schneefall.py", "print('hallo')"),
            ("vorlage-schneefall/images/", None),
            ("vorlage-schneefall/images/schneeflocke.png", "png"),
            ("vorlage-schneefall/images/.DS_Store", "junk"),
            ("vorlage-schneefall/images/._schneeflocke.png", "junk"),
            ("__MACOSX/vorlage-schneefall/._schneefall.py", "junk"),
        ],
    )

    destination = tmp_path / "ziel"
    extract_zip(zip_path, destination)

    assert (destination / "schneefall.py").read_text() == "print('hallo')"
    assert (destination / "images" / "schneeflocke.png").read_text() == "png"
    assert sorted(path.relative_to(destination) for path in destination.rglob("*")) == [
        Path("images"),
        Path("images/schneeflocke.png"),
        Path("schneefall.py"),
    ]


def test_extracts_multiple_roots(tmp_path):
    zip_path = make_zip(
        tmp_path / "vorlage.zip",
        [("a/eins.py", "eins"), ("b/zwei.py", "zwei")],
    )

    destination = tmp_path / "ziel"
    extract_zip(zip_path, destination)

    assert (destination / "a" / "eins.py").read_text() == "eins"
    assert (destination / "b" / "zwei.py").read_text() == "zwei"


def test_single_file_keeps_its_name(tmp_path):
    zip_path = make_zip(tmp_path / "vorlage.zip", [("start.py", "start")])

    destination = tmp_path / "ziel"
    extract_zip(zip_path, destination)

    assert (destination / "start.py").read_text() == "start"


@pytest.mark.parametrize("name", ["../ausserhalb.py", "/etc/passwd", "C:/windows.py"])
def test_rejects_unsafe_paths(tmp_path, name):
    zip_path = make_zip(
        tmp_path / "vorlage.zip", [("start.py", "start"), (name, "boese")]
    )

    with pytest.raises(ValueError):
        extract_zip(zip_path, tmp_path / "ziel")

    assert not (tmp_path / "ausserhalb.py").exists()


def test_skips_symlinks(tmp_path):
    zip_path = tmp_path / "vorlage.zip"
    with zipfile.ZipFile(zip_path, "w") as archive:
        archive.writestr("start.py", "start")
        link = zipfile.ZipInfo("verknuepfung")
        link.external_attr = (stat.S_IFLNK | 0o777) << 16
        archive.writestr(link, "start.py")

    destination = tmp_path / "ziel"
    extract_zip(zip_path, destination)

    assert (destination / "start.py").is_file()
    assert not (destination / "verknuepfung").exists()


def test_refuses_existing_folder(tmp_path):
    zip_path = make_zip(tmp_path / "vorlage.zip", [("neu.py", "neu")])
    existing = tmp_path / "Vorlage"
    existing.mkdir()
    (existing / "alt.py").write_text("alt")

    with pytest.raises(FileExistsError):
        install_zip(zip_path, tmp_path, "Vorlage")

    assert (existing / "alt.py").read_text() == "alt"


def test_overwrites_existing_folder(tmp_path):
    zip_path = make_zip(
        tmp_path / "vorlage.zip",
        [("vorlage/", None), ("vorlage/neu.py", "neu")],
    )
    existing = tmp_path / "Vorlage"
    existing.mkdir()
    (existing / "alt.py").write_text("alt")

    destination = install_zip(zip_path, tmp_path, "Vorlage", overwrite=True)

    assert destination == existing
    assert (destination / "neu.py").read_text() == "neu"
    assert not (destination / "alt.py").exists()
    assert [path.name for path in tmp_path.iterdir() if path.name.startswith(".vorlagen-")] == []


def test_cancelled_install_leaves_nothing_behind(tmp_path):
    zip_path = make_zip(tmp_path / "vorlage.zip", [("start.py", "start")])

    with pytest.raises(Cancelled):
        install_zip(zip_path, tmp_path, "Vorlage", should_cancel=lambda: True)

    assert [path.name for path in tmp_path.iterdir() if path.name.startswith(".vorlagen-")] == []
    assert not (tmp_path / "Vorlage").exists()


def test_find_main_file(tmp_path):
    folder = tmp_path / "projekt"
    folder.mkdir()
    (folder / "b.py").write_text("b")
    (folder / "a.py").write_text("a")

    assert find_main_file(folder, "b.py") == folder / "b.py"
    assert find_main_file(folder, "fehlt.py") == folder / "a.py"
    assert find_main_file(folder, None) == folder / "a.py"


def test_sanitize_folder_name():
    for name in ['Schnee/f:all?', "Test.", "   ", "a\\b|c*d"]:
        cleaned = sanitize_folder_name(name)
        assert cleaned
        assert not set(cleaned) & set('<>:"/\\|?*')

    assert sanitize_folder_name("   ") == "Vorlage"
    assert sanitize_folder_name("Test.") == "Test"


def test_unique_folder_name(tmp_path):
    (tmp_path / "Test").mkdir()
    (tmp_path / "Test-2").mkdir()

    assert unique_folder_name(tmp_path, "Test") == "Test-3"
