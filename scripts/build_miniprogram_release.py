#!/usr/bin/env python3
"""Build a mini-program release directory from an external personal target."""

from __future__ import annotations

import argparse
import ipaddress
from pathlib import Path
import re
import shutil
import sys


DOMAIN_PATTERN = re.compile(
    r"^(?=.{1,253}$)(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$",
    re.IGNORECASE,
)


def read_target(path: Path) -> dict[str, str]:
    if not path.is_file():
        raise ValueError(f"TENAISHI_API_DOMAIN 未配置：找不到 {path}")
    values: dict[str, str] = {}
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip().strip("'\"")
    return values


def personal_api_url(value: str) -> str:
    host = value.strip().lower().rstrip(".")
    if not host:
        raise ValueError("TENAISHI_API_DOMAIN 未配置")
    try:
        ipaddress.ip_address(host)
    except ValueError:
        pass
    else:
        raise ValueError("TENAISHI_API_DOMAIN 必须是个人 API 域名，不能是 IP")
    if not DOMAIN_PATTERN.fullmatch(host):
        raise ValueError("TENAISHI_API_DOMAIN 必须只填个人 API 域名")
    return f"https://{host}"


def build(target_file: Path, output: Path) -> None:
    repository = Path(__file__).resolve().parents[1]
    source = repository / "miniprogram"
    if output.exists():
        raise ValueError(f"输出目录已存在，请换一个新目录：{output}")
    api_url = personal_api_url(read_target(target_file).get("TENAISHI_API_DOMAIN", ""))
    shutil.copytree(source, output)
    (output / "release-config.js").write_text(
        "module.exports = {\n"
        f"  releaseBaseUrl: {api_url!r}\n"
        "}\n",
        encoding="utf-8",
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--target-file",
        type=Path,
        default=Path.home() / ".config/tenaishi/deploy-target.env",
    )
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    try:
        build(args.target_file, args.output.resolve())
    except ValueError as error:
        print(error, file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
