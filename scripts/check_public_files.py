"""Validate the publication allowlist, synthetic fixtures and secret patterns."""

import importlib.util
import json
import re
import subprocess
import tempfile
from pathlib import Path


def check(root: Path) -> None:
    result = subprocess.run(
        ["git", "ls-files", "--cached", "--others", "--exclude-standard", "-z"],
        cwd=root,
        capture_output=True,
        check=True,
    )
    names = sorted({name.decode() for name in result.stdout.split(b"\0") if name})
    top_level = {
        ".gitignore",
        "LICENSE",
        "README.md",
        "hacs.json",
        "pyproject.toml",
        "requirements_test.txt",
    }
    prefixes = (
        ".github/workflows/",
        "brand/",
        "custom_components/handan_water/",
        "docs/",
        "scripts/",
        "tests/",
    )
    extensions = {".py", ".json", ".md", ".yml", ".png"}
    problems = []
    patterns = [
        re.compile(rb"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
        re.compile(rb"(?:gh[pousr]_[A-Za-z0-9]{30,}|github_pat_[A-Za-z0-9_]{40,})"),
        re.compile(rb"/(?:Users|home)/[^/\s]+/"),
    ]
    for name in names:
        path = root / name
        if not path.is_file() or path.is_symlink():
            problems.append(f"不允许的文件类型：{name}")
            continue
        if name not in top_level and not (name.startswith(prefixes) and path.suffix in extensions):
            problems.append(f"不在公开白名单：{name}")
        if any(pattern.search(path.read_bytes()) for pattern in patterns):
            problems.append(f"疑似凭据或本地个人路径：{name}")

    spec = importlib.util.spec_from_file_location(
        "fixture_generator", root / "scripts/generate_fixtures.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    with tempfile.TemporaryDirectory() as temporary:
        generated = Path(temporary)
        module.generate(generated)
        for path in generated.glob("*.json"):
            actual = root / "tests/fixtures" / path.name
            if json.loads(path.read_text()) != json.loads(actual.read_text()):
                problems.append(f"响应不是可重建的合成数据：{actual.name}")

    identifying_keys = {
        "consNo",
        "queryValue",
        "consName",
        "addr",
        "phone",
        "sftel",
        "openid",
        "opfid",
        "rlmcode",
        "rlid",
        "pid",
    }

    def validate(value, filename):
        if isinstance(value, dict):
            for key, item in value.items():
                if (
                    key in identifying_keys
                    and item
                    and not (isinstance(item, str) and item.startswith("DEMO_"))
                ):
                    problems.append(f"测试标识未合成：{filename} 字段 {key}")
                validate(item, filename)
        elif isinstance(value, list):
            for item in value:
                validate(item, filename)

    for path in (root / "tests/fixtures").glob("*.json"):
        validate(json.loads(path.read_text()), path.name)
    if problems:
        raise SystemExit("\n".join(problems))
    print(
        f"公开文件检查通过：{len(names)} 个文件；响应可由合成生成器完整重建，无凭据或本地个人路径匹配。"
    )


if __name__ == "__main__":
    check(Path(__file__).resolve().parents[1])
