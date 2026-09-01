"""打包脚本：siwu-arknights-fun → dist zip（zip 内文件放根目录，assets/ 随包携带）。"""

import os
import re
import zipfile

import yaml

PLUGIN_DIR = os.path.dirname(os.path.abspath(__file__))
ASTRBOT_DIR = os.path.dirname(PLUGIN_DIR)
PLUGIN_SLUG = "siwu-arknights-fun"

EXCLUDE_NAMES = {
    os.path.basename(__file__),
    "__pycache__",
    ".ruff_cache",
    ".git",
    ".gitignore",
    "data",
}


def plugin_version() -> str:
    with open(os.path.join(PLUGIN_DIR, "metadata.yaml"), encoding="utf-8") as f:
        metadata = yaml.safe_load(f)
    version = (metadata or {}).get("version", "")
    m = re.search(r"(\d+\.\d+\.\d+)", str(version))
    return m.group(1) if m else "1.0.0"


def output_zip() -> str:
    return os.path.join(ASTRBOT_DIR, "dist", f"{PLUGIN_SLUG}-{plugin_version()}.zip")


def _collect_files(base: str) -> list[str]:
    files = []
    for root, dirs, names in os.walk(base):
        dirs[:] = [d for d in dirs if d not in EXCLUDE_NAMES]
        for name in names:
            if name.endswith((".pyc", ".pyo")) or name in EXCLUDE_NAMES:
                continue
            files.append(os.path.relpath(os.path.join(root, name), base))
    return sorted(files)


def build() -> str:
    output = output_zip()
    os.makedirs(os.path.dirname(output), exist_ok=True)
    with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as zf:
        for rel in _collect_files(PLUGIN_DIR):
            arcname = rel.replace(os.sep, "/")
            print(f"  + {arcname}")
            zf.write(os.path.join(PLUGIN_DIR, rel), arcname=arcname)
    print(f"\ncreated: {output} ({os.path.getsize(output)} bytes)")
    return output


if __name__ == "__main__":
    build()
