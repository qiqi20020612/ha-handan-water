"""Create a small distribution using an explicit allowlist."""

import json
from hashlib import sha256
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile


def build(root: Path) -> Path:
    component = root / "custom_components/handan_water"
    version = json.loads((component / "manifest.json").read_text())["version"]
    files = [root / "README.md", root / "LICENSE", root / "hacs.json"]
    files += [
        path
        for path in component.rglob("*")
        if path.is_file()
        and path.suffix in {".py", ".json", ".png", ".svg"}
        and "__pycache__" not in path.parts
    ]
    files += sorted((root / "docs").glob("*.md"))
    files += sorted((root / "brand").glob("*.png"))
    output_dir = root / "dist"
    output_dir.mkdir(exist_ok=True)
    output = output_dir / f"ha-handan-water-{version}.zip"
    with ZipFile(output, "w", compression=ZIP_DEFLATED) as archive:
        for path in sorted(files):
            archive.write(path, path.relative_to(root))
    (output_dir / "SHA256SUMS").write_text(
        f"{sha256(output.read_bytes()).hexdigest()}  {output.name}\n"
    )
    return output


if __name__ == "__main__":
    print(build(Path(__file__).resolve().parents[1]))
