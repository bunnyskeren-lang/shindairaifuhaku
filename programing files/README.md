# programing files

運用・整備用スクリプト群。**Renderにはデプロイされない**（ルート直下の `main.py` 等とは別系統）。
`models.py` / `database.py` はこのディレクトリ専用の定義で、ルートの同名ファイルとは別物。

## セットアップ

- `.env` … 本番ボット用トークン・接続情報
- `.env.dev` … devボット用トークン・接続情報
- `.env.example` … 環境変数のテンプレート

## データ投入・同期

| スクリプト | 用途 |
|---|---|
| `import_syllabus.py` | シラバスデータをDBへ投入するメインスクリプト。`--also-courses`で`subjects`/`instructors`/`course_sections`（LINE bot用）にも登録、`--classification`/`--faculty`で分類・学部名を指定。実行時に`fetch_syllabus_info.py`を自動呼び出し |
| `fetch_syllabus_info.py` | 神戸大学シラバスページをスクレイピングし、単位数・経営学部専門科目の群をDBへ書き込む。`--env dev`でdev DB、`--force`で既取得分も上書き |
| `sync_db_to_prod.py` | dev→本番DBの同期（`display_orders`/`subjects`/`instructors`/`course_sections`の4テーブルのみ）。UPSERTに加え、本番のみに存在する行の削除も行うが、`reviews`/`syllabi`が紐づく行は`KEEP(要確認)`表示に留めて削除しない |
| `download_prod_backup.py` | Supabase Storage上のDBバックアップ（`core/backup.py`が生成）を差分ダウンロードし`backups/{env}/`へ保存。`--env dev\|prod` |

## リッチメニュー

| スクリプト | 用途 |
|---|---|
| `setup_richmenu.py` | LINEリッチメニュー設定。**必ず`--env dev\|prod`を指定**（dev: `.env.dev`使用、prod: `.env`使用・確認プロンプトあり） |
| `assets/richmenu.png` | リッチメニュー原本画像（`setup_richmenu.py`のデフォルト画像、git管理下） |

## その他

| スクリプト | 用途 |
|---|---|
| `generate_vapid.py` | Web Push用VAPIDキーの生成（初回のみ実行、出力値を両Renderサービスの環境変数に設定） |

## 詳細ルール

デプロイ手順・環境変数・DB接続情報・モデル変更時の注意点などは、プロジェクトルートの `CLAUDE.md` を参照。
