# -*- coding: utf-8 -*-
"""
Supabase Storage に溜まった本番/dev DBバックアップを、ローカルのbackups/フォルダへダウンロードするスクリプト。
まだローカルに無いファイルだけを取得する（差分ダウンロード）。

実行方法（programing files/ から実行）:
  python download_prod_backup.py --env prod
  python download_prod_backup.py --env dev

必要な環境変数 (.env / .env.dev):
  SUPABASE_URL
  SUPABASE_SERVICE_ROLE_KEY
  BACKUP_BUCKET  (省略時 db-backups)
"""
import argparse
import os
import sys

import httpx

parser = argparse.ArgumentParser()
parser.add_argument("--env", choices=["dev", "prod"], required=True,
                     help="dev=.env.dev, prod=.env")
args = parser.parse_args()

from dotenv import load_dotenv
script_dir = os.path.dirname(os.path.abspath(__file__))
env_file = os.path.join(script_dir, ".env.dev" if args.env == "dev" else ".env")
load_dotenv(env_file, override=True)

SUPABASE_URL = os.environ.get("SUPABASE_URL", "").rstrip("/")
SUPABASE_SERVICE_ROLE_KEY = os.environ.get("SUPABASE_SERVICE_ROLE_KEY", "")
BACKUP_BUCKET = os.environ.get("BACKUP_BUCKET", "db-backups")

if not SUPABASE_URL or not SUPABASE_SERVICE_ROLE_KEY:
    print(f"{env_file} に SUPABASE_URL / SUPABASE_SERVICE_ROLE_KEY が設定されていません。")
    sys.exit(1)

HEADERS = {
    "Authorization": f"Bearer {SUPABASE_SERVICE_ROLE_KEY}",
    "apikey": SUPABASE_SERVICE_ROLE_KEY,
}

DEST_DIR = os.path.join(os.path.dirname(__file__), "..", "backups", args.env)
os.makedirs(DEST_DIR, exist_ok=True)


def main():
    with httpx.Client(timeout=60) as client:
        resp = client.post(
            f"{SUPABASE_URL}/storage/v1/object/list/{BACKUP_BUCKET}",
            headers=HEADERS,
            json={"prefix": "", "limit": 1000, "sortBy": {"column": "name", "order": "asc"}},
        )
        resp.raise_for_status()
        items = resp.json()

        new_count = 0
        for item in items:
            name = item.get("name", "")
            if not name.endswith(".sql.gz"):
                continue
            local_path = os.path.join(DEST_DIR, name)
            if os.path.exists(local_path):
                continue
            dl = client.get(f"{SUPABASE_URL}/storage/v1/object/{BACKUP_BUCKET}/{name}", headers=HEADERS)
            dl.raise_for_status()
            with open(local_path, "wb") as f:
                f.write(dl.content)
            print(f"ダウンロード: {name}")
            new_count += 1

        if new_count == 0:
            print("新規バックアップはありませんでした。")
        else:
            print(f"{new_count}件のバックアップをダウンロードしました。保存先: {DEST_DIR}")


if __name__ == "__main__":
    main()
