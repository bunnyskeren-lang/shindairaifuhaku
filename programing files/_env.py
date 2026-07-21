"""programing files/ 配下の運用スクリプト共通の.envローダー。

python-dotenvを使う一部スクリプト（setup_richmenu.py等）とは別に、
6つのスクリプトがこの手動パース版load_env()をそれぞれ個別に複製していたため集約した。
"""
import os
import sys
from pathlib import Path


def load_env(env: str) -> None:
    env_file = Path(__file__).parent / (".env.dev" if env == "dev" else ".env")
    if not env_file.exists():
        print(f"ERROR: {env_file} が見つかりません", file=sys.stderr)
        sys.exit(1)
    for line in env_file.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip())
