# 環境構築

## 必要環境

- Python 3.12（`runtime.txt`）
- PostgreSQL データベース（本プロジェクトは [Supabase](https://supabase.com/) を使用。dev/本番で別プロジェクト）
- LINE Developers のチャネル（Messaging API）＋ LIFF アプリ（会員登録／レビュー投稿用に2つ）

## リポジトリのクローンとインストール

```bash
git clone <このリポジトリのURL>
cd shindairaifuhaku
pip install -r requirements.txt
# 開発用（lint/test）も使う場合
pip install -r requirements-dev.txt
```

## 環境変数

ルート直下に `.env` ファイルを作成する（`python-dotenv` で `core/config.py` 起動時に読み込まれる）。

### 必須

| 変数名 | 説明 |
|---|---|
| `DATABASE_URL` | PostgreSQL 接続文字列（`postgres://`/`postgresql://` どちらでも可、内部で `postgresql+asyncpg://` に変換される） |
| `LINE_CHANNEL_SECRET` | LINE Messaging API チャネルシークレット |
| `LINE_CHANNEL_ACCESS_TOKEN` | LINE Messaging API チャネルアクセストークン |
| `ADMIN_PASSWORD` | 管理画面ログインパスワード（未設定だと起動時に `RuntimeError`） |

### 任意（デフォルト値あり・省略可）

| 変数名 | デフォルト | 説明 |
|---|---|---|
| `REVIEW_FORM_URL` | `https://shindairaifuhaku.onrender.com` | レビュー投稿フォームの公開URL（本番/devで固定値が異なる。[`DEPLOYMENT.md`](./DEPLOYMENT.md)参照、絶対に入れ替えないこと） |
| `LIFF_ID` | `2010406205-emxo5rhE`（本番値） | 科目詳細LIFFページのLIFF ID |
| `REGISTER_LIFF_ID` | `""` | 会員登録LIFFページのLIFF ID |
| `REVIEW_LIFF_ID` | `""` | レビュー投稿LIFFページのLIFF ID |
| `RICHMENU_ID_PREREGISTER` | `""` | 未登録ユーザー用リッチメニューのID（`setup_richmenu.py`実行後に払い出される。LINE側のデフォルトリッチメニューにも設定される） |
| `RICHMENU_ID_MAIN` | `""` | 登録済みユーザー用（通常）リッチメニューのID（`setup_richmenu.py`実行後に払い出される。登録完了時に個別リンクするため必須） |
| `VAPID_PUBLIC_KEY` / `VAPID_PRIVATE_KEY` / `VAPID_EMAIL` | `""` / `""` / `admin@example.com` | Web Push通知用VAPID鍵（`programing files/seeds/generate_vapid.py`で生成） |
| `SELF_URL` | `""` | 自己ping先の自身のURL（Render無料/Starterプランのスリープ防止） |
| `APP_URL` | `https://shindairaifuhaku.onrender.com` | リンク生成に使う自身のURL |
| `DB_POOL_SIZE` | `10` | DB接続プールのサイズ |
| `DB_POOL_MAX_OVERFLOW` | `20` | DB接続プールの最大オーバーフロー |
| `DISABLE_SSL_VERIFY` | 未設定 | DB接続のSSL証明書検証を無効化（`core/db_ssl.py`、緊急時の切り戻し用。通常は設定しない） |
| `SUPABASE_URL` / `SUPABASE_SERVICE_ROLE_KEY` | `""` | DB自動バックアップのアップロード先 Supabase Storage API |
| `BACKUP_ENABLED` | `false` | DB自動バックアップの有効化 |
| `BACKUP_BUCKET` | `db-backups` | バックアップ保存先バケット |
| `BACKUP_RETENTION_DAYS` | `15` | バックアップ保持日数 |
| `BACKUP_INTERVAL_HOURS` | `1` | バックアップ実行間隔（時間） |
| `ENV` | `prod` | `dev` を指定すると開発モード扱い（`IS_DEV`） |
| `WEB_CONCURRENCY` | `1` | uvicornワーカー数（`Procfile`が参照）。複数ワーカー時の注意点は下記参照 |

`programing files/` 配下のスクリプト群は別途 `programing files/.env`（本番用）・`programing files/.env.dev`（dev用）を使う。テンプレートは `programing files/.env.example` を参照（`DEV_DATABASE_URL`・`ONEDRIVE_BACKUP_DIR`等、スクリプト固有の変数を含む）。

## Supabase（DB）の準備

1. Supabaseで新規プロジェクトを作成（dev用・本番用で別プロジェクトにする運用）
2. プロジェクトの接続文字列（Connection string、`asyncpg`互換）を `DATABASE_URL` に設定
3. アプリ初回起動時に `database.py` の `init_db()` が `Base.metadata.create_all` で全テーブルを自動作成する（マイグレーションツールは未導入、テーブル作成はコード起動時に冪等実行）
4. Supabase Storageを使う場合（DB自動バックアップ）は `SUPABASE_URL` / `SUPABASE_SERVICE_ROLE_KEY` とバケットを用意する

DBスキーマの詳細は [`DB_SCHEMA.md`](./DB_SCHEMA.md) を参照。

## LINE Developers の準備

1. [LINE Developers Console](https://developers.line.biz/) でMessaging APIチャネルを作成
2. チャネルシークレット・チャネルアクセストークンを `.env` に設定
3. Webhook URLを `https://<デプロイ先>/callback` に設定し、Webhookを有効化
4. LIFFアプリを2つ作成（会員登録用・レビュー投稿用）し、各LIFF URLをそれぞれ以下に向ける
   - 会員登録: `/register`
   - レビュー投稿: `/`（フォームトップ）
   - （科目詳細は `/liff/course`。上記2つとは別に、科目一覧・レビュー閲覧用のLIFF IDも本番/devそれぞれ固定値がある。[`DEPLOYMENT.md`](./DEPLOYMENT.md)参照）
5. 発行されたLIFF IDを `.env` の `LIFF_ID` / `REGISTER_LIFF_ID` / `REVIEW_LIFF_ID` に設定

## リッチメニューの準備

```bash
cd "programing files"
python setup_richmenu.py --env dev   # 本番の場合は --env prod（要明示的な指示。DEPLOYMENT.md参照）
```

出力された通常リッチメニューID・未登録ユーザー用リッチメニューIDを `.env` の `RICHMENU_ID_MAIN` / `RICHMENU_ID_PREREGISTER` にそれぞれ設定する（LINE側のデフォルトは登録前メニューになるよう設定される。未登録ユーザーに全機能が使えてしまう抜け道を防ぐためのフェイルセーフ設計）。

## Web Push (VAPID) の準備（任意）

```bash
cd "programing files"
python seeds/generate_vapid.py
```

出力された公開鍵・秘密鍵を `VAPID_PUBLIC_KEY` / `VAPID_PRIVATE_KEY` に設定する。

## ローカル起動

```bash
uvicorn main:app --reload
```

- `/health` で死活確認
- LINE Webhookをローカルで受けたい場合はngrok等でトンネルを張り、LINE Developersの Webhook URL を一時的に切り替える

## シラバスデータの投入（初回・新学部追加時）

シラバスの生データ（`data/`配下のテキストファイル）はユーザーが神戸大学シラバスサイトを手動コピペで用意したもの（自動取得はできない）。データが揃った状態で:

```bash
cd "programing files"
python import_syllabus.py --also-courses --faculty "学部名" [--classification "分類名"]
```

`fetch_syllabus_info.py`（単位数・経営学部専門科目の群のスクレイピング）は`import_syllabus.py`実行時に自動で呼ばれるため、通常は単体実行不要。

## 次に読むドキュメント

- API仕様: [`API.md`](./API.md)
- DB設計書: [`DB_SCHEMA.md`](./DB_SCHEMA.md)
- ディレクトリ構成: [`DIRECTORY_STRUCTURE.md`](./DIRECTORY_STRUCTURE.md)
- 開発手順: [`DEVELOPMENT.md`](./DEVELOPMENT.md)
- デプロイ方法: [`DEPLOYMENT.md`](./DEPLOYMENT.md)
