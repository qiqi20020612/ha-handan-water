"""Verify the actual release archive, version metadata and publication boundary."""

import json
import shutil
import tomllib
from hashlib import sha256
from pathlib import Path
from zipfile import ZipFile

from scripts.build_release import build


def test_release_archive_contains_installable_component_and_no_private_files(tmp_path):
    root = Path(__file__).resolve().parents[1]
    for name in ("README.md", "LICENSE", "hacs.json"):
        shutil.copy2(root / name, tmp_path / name)
    for directory in ("custom_components/handan_water", "docs", "brand"):
        shutil.copytree(root / directory, tmp_path / directory)
    marker = "DEMO_EXCLUDED_RELEASE_CONTENT"
    for name in (
        "private/capture.json",
        "tests/fixtures/response.json",
        ".env",
        "custom_components/handan_water/__pycache__/excluded.py",
    ):
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(marker)

    output = build(tmp_path)
    with ZipFile(output) as archive:
        assert archive.testzip() is None
        names = set(archive.namelist())
        assert {"README.md", "LICENSE", "hacs.json"} <= names
        assert all(
            name in {"README.md", "LICENSE", "hacs.json"}
            or name.startswith(("custom_components/handan_water/", "docs/", "brand/"))
            for name in names
        )
        assert all("__pycache__" not in name and ".." not in Path(name).parts for name in names)
        assert all(marker.encode() not in archive.read(name) for name in names)
        component = root / "custom_components/handan_water"
        required = {
            str(path.relative_to(root))
            for path in component.rglob("*")
            if path.is_file()
            and path.suffix in {".py", ".json", ".png"}
            and "__pycache__" not in path.parts
        }
        assert required <= names
        for name in names:
            if name.endswith(".json"):
                json.loads(archive.read(name))
        manifest = json.loads(archive.read("custom_components/handan_water/manifest.json"))
        project = tomllib.loads((root / "pyproject.toml").read_text())["project"]
        assert manifest["version"] == project["version"]
        assert manifest["config_flow"] and not manifest["requirements"]
        hacs = json.loads(archive.read("hacs.json"))
        assert hacs["homeassistant"] == "2026.10.0"
        # HACS installs the component from the source tree/tag. This manual
        # archive has a repository layout, not a zip_release component layout.
        assert not hacs.get("zip_release", False)
    assert output.name == f"ha-handan-water-{manifest['version']}.zip"
    assert (output.parent / "SHA256SUMS").read_text() == (
        f"{sha256(output.read_bytes()).hexdigest()}  {output.name}\n"
    )
