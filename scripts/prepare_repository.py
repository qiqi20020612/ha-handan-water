"""Fill only public release metadata; never create a remote or push files."""

import argparse
import json
import re
from pathlib import Path


def prepare(repository: str, codeowner: str | None = None) -> None:
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9-]*/[A-Za-z0-9_.-]+", repository):
        raise ValueError("请提供 GitHub 用户名/仓库名")
    owner = codeowner or repository.split("/", 1)[0]
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9-]*", owner):
        raise ValueError("维护者必须是 GitHub 用户名")
    path = Path(__file__).resolve().parents[1] / "custom_components/handan_water/manifest.json"
    manifest = json.loads(path.read_text())
    manifest["documentation"] = f"https://github.com/{repository}/blob/main/README.md"
    manifest["issue_tracker"] = f"https://github.com/{repository}/issues"
    manifest["codeowners"] = [f"@{owner}"]
    path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n")
    print("已填写本地发布元数据；未创建仓库或推送代码。")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repository", required=True)
    parser.add_argument("--codeowner")
    args = parser.parse_args()
    prepare(args.repository, args.codeowner)
