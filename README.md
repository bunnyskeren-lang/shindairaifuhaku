# shindairaifuhaku

神戸大学の学生向け、履修レビュー LINE Bot / LIFF アプリ。LINE 経由で科目レビューの閲覧・投稿、シラバス確認ができる。管理画面から科目・レビュー・ユーザーの管理も行える。

## ドキュメント一覧

| ドキュメント | 内容 |
|---|---|
| [`docs/SETUP.md`](docs/SETUP.md) | 環境構築（必要環境・.env・Supabase/LINE Developers/リッチメニューの準備） |
| [`docs/DEVELOPMENT.md`](docs/DEVELOPMENT.md) | 開発手順（ローカル起動・テスト・lint・開発ワークフロー） |
| [`docs/DEPLOYMENT.md`](docs/DEPLOYMENT.md) | デプロイ方法（dev/本番・DB同期・リッチメニュー更新・環境変数追加ルール） |
| [`docs/API.md`](docs/API.md) | API仕様（全エンドポイント・認証方式・レート制限） |
| [`docs/DB_SCHEMA.md`](docs/DB_SCHEMA.md) | DB設計書（全テーブルの列・制約・FK・削除挙動） |
| [`docs/DIRECTORY_STRUCTURE.md`](docs/DIRECTORY_STRUCTURE.md) | ディレクトリ構成（全ファイルの役割） |
| `CLAUDE.md` / `AGENTS.md` | AIエージェント（Claude Code等）向けの運用ルール（デプロイ確認要否・データ保護ルール等の正） |

## 主な機能

- **LINE Bot**：科目名で検索するとレビュー・シラバス情報を Flex Message で返信（`/callback`）。「専門」メニューは学部一覧 → 学科・専攻一覧（or 群科目一覧）→ 科目一覧のドリルダウン方式
- **友だち追加時の会員登録必須化**：氏名・学籍番号・学部・学年・学科を登録するまでリッチメニューの各機能は登録画面へ誘導（`user_profiles`）
- **レビュー投稿フォーム**（`/submit`）：科目レビューを投稿 → 管理画面で承認後に公開。承認画面でコメント・評価・教員名等を編集してから承認可能
- **LIFF ページ**
  - `/liff/course`：科目詳細・レビュー閲覧
  - `/liff/review`：レビュー投稿への直接リダイレクト
  - `/register`：会員登録フォーム
- **リッチメニュー連携**（`/r/{name}`）：クリック計測付きリダイレクト
- **管理画面**（`/admin/*`）：科目・教員・分類・レビュー承認・ユーザー・エラーログ・利用統計・学生便覧×DB齟齬一覧などを管理（HMAC Cookie 認証）
- **Web Push 通知**（VAPID、購読者への送信は非同期・並列化済み）
- **DB 自動バックアップ**：本番アプリ自身が定期的に全テーブルを Supabase Storage へダンプ
- **レート制限・リクエストサイズ制限**：ログイン試行・レビュー投稿・成績表解析等をIPアドレス単位で制限、リクエストボディサイズにも上限

エンドポイントの詳細は [`docs/API.md`](docs/API.md) を参照。

## 技術スタック

| 分類 | 技術 |
|------|------|
| 言語 | Python 3.12 |
| Web フレームワーク | FastAPI + Uvicorn |
| ORM | SQLAlchemy 2.0（async / asyncpg） |
| DB | PostgreSQL（Supabase） |
| テンプレート | Jinja2 |
| LINE 連携 | line-bot-sdk v3（`linebot.v3`） |
| よみがな生成 | pykakasi |
| プッシュ通知 | pywebpush（VAPID） |
| HTTP クライアント | httpx（LIFF ID token 検証・DB自動バックアップの Supabase Storage API 呼び出し等） |
| ホスティング | Render（Web Service） |
| テスト / Lint | pytest + pytest-asyncio（SQLiteインメモリDBで実DB接続不要） / ruff |

## アーキテクチャ概要

`main.py` は app 生成・ミドルウェア登録・例外ハンドラ・`lifespan`・`include_router` のみの薄いエントリポイント（`core / line_bot / routers` パッケージ構成）。

| レイヤー | 役割 |
|---|---|
| `core/` | 環境変数・定数・シラバス URL 生成（`config.py`）、管理者認証・LINE 署名検証（`security.py`）、インメモリキャッシュ（`cache.py`）、エラー/メッセージログ（`activity_log.py`）、LINE API クライアント（`line_client.py`）、LIFF ID token 検証＋キャッシュ（`liff_auth.py`）、Web Push（`push.py`）、起動時プリウォーム（`prewarm.py`）、Jinja2 テンプレート（`templates.py`）、レート制限（`rate_limit.py`）、Supabase 接続用 SSL コンテキスト（`db_ssl.py`）、DB 自動バックアップ（`backup.py`）、学生便覧×DB齟齬パーサー（`binran_discrepancies.py`） |
| `line_bot/` | LINE Bot 応答ロジック（`flex_builders.py`：FlexMessage 生成、`handler.py`：`handle_message` / `handle_course_list` / `process_events`） |
| `routers/` | URL プレフィックス単位の FastAPI `APIRouter`（`webhook` / `health` / `pages` / `richmenu` / `liff_api` / `profile_api` / `review_submit_api`、`admin/` 配下 8 ファイル：`auth` / `dashboard` / `courses` / `instructors` / `classifications` / `reviews` / `users_errors` / `stats` / `binran_discrepancies`） |

- **LINE Bot フロー**：`POST /callback`（`routers/webhook.py`） → `core.security.verify_line_signature` → `core.line_client.parser.parse()` → `asyncio.create_task` で `line_bot.handler.process_events()` を実行し即時200応答。`FollowEvent`（ウェルカム Flex Message、未登録なら会員登録LIFFへ誘導）／`MessageEvent`／`PostbackEvent` を分岐処理。返信は `core.line_client.reply()` 経由。
- **キャッシュ設計**：`core/cache.py` に集約したモジュールレベルのグローバル変数に TTL 3600秒のインメモリキャッシュを複数保持。他モジュールは必ず `cache.get_*` / `cache.set_*` / `cache.invalidate_*` の関数経由でアクセスする。管理画面での CRUD 後に該当キャッシュを即時無効化。起動時に `core.prewarm.prewarm_caches()` で全ウォームアップ。
- **管理画面認証**：`core.security.make_admin_token()` が `HMAC-SHA256(CHANNEL_SECRET + ADMIN_PASSWORD)` で署名した Cookie トークンを発行（TTL 4時間）。`core.security.check_admin` を全 `/admin/*` ルートに `Depends()` で付与。
- **ミドルウェア（`main.py`、外側から順に適用）**：`RequestTimingMiddleware`（アクセスログ、3秒以上はSLOWマーカー）→ `BodySizeLimitMiddleware`（リクエストボディ2MB上限）→ `CORSMiddleware`（LIFF/LINEドメインのみ許可）→ `GZipMiddleware`（JSON応答等を圧縮）。前2つは Starlette の `BaseHTTPMiddleware` オーバーヘッドを避けるため素の ASGI 実装。加えて `/admin/login`・`/submit` 等には `core.rate_limit.rate_limiter()` を個別に付与しIPアドレス単位で制限（詳細は [`docs/API.md`](docs/API.md)）。
- **起動処理（lifespan）**：`init_db()` → `prewarm.prewarm_caches()` を非同期起動 → `line_client.startup()` / `liff_auth.startup()` → 自己 ping（`SELF_URL` 設定時）・`backup.backup_loop()`（`BACKUP_ENABLED=true` 時のみ）・`activity_log.log_cleanup_loop()`・`rate_limit.rate_limit_cleanup_loop()` をバックグラウンドタスクとして起動。
- **dev → 本番マスタデータ同期**：`programing files/sync_db_to_prod.py` をローカルから実行し、4テーブルを自然キー（id直コピーではなく name 等）でUPSERTする。詳細は [`docs/DEPLOYMENT.md`](docs/DEPLOYMENT.md)。

## DB スキーマ

主要テーブル（詳細は [`docs/DB_SCHEMA.md`](docs/DB_SCHEMA.md)）:

| テーブル | 用途 |
|---|---|
| `subjects` / `instructors` / `course_sections` | 科目・教員マスタと科目×教員セクション |
| `syllabi` | シラバス（年度別）。シラバスURLは`timetable_code`から動的生成（列としては持たない） |
| `reviews` | 投稿レビュー（`is_approved`で承認管理、`ON DELETE RESTRICT`で誤削除を防止） |
| `user_profiles` | LINEユーザーのプロフィール（氏名・学籍番号・学部・学年・学科） |
| `display_orders` | 表示順マスタ（`kind`列で分類/学部を区別） |
| `message_logs` / `user_activity` / `error_logs` / `push_subscriptions` / `richmenu_taps` / `course_section_views` | 運用・ログ系 |

## ディレクトリ構成

```
shindairaifuhaku/          ← Render がデプロイするルート
├── main.py                ← 薄いエントリポイント
├── models.py               ← SQLAlchemy ORM モデル定義
├── database.py              ← DB エンジン生成・init_db()
├── core/                     ← 横断的関心事
├── line_bot/                  ← LINE Bot 応答ロジック
├── routers/                    ← URL プレフィックス単位の APIRouter（admin/ 含む）
├── templates/                   ← Jinja2 テンプレート（admin / liff / フォーム類）
├── data/                          ← シラバス取り込み用テキストファイル
├── supabase/migrations/            ← 新規テーブル追加時の移行 SQL
├── docs/                             ← ドキュメント類（git管理・デプロイ対象）
├── tests/                              ← pytest（SQLiteインメモリDBで実DB不要）
├── backups/                             ← DBバックアップのローカル差分DL先（.gitignore対象）
└── programing files/                     ← 運用・整備用スクリプト群（Render にはデプロイされない）
```

全ファイルの役割は [`docs/DIRECTORY_STRUCTURE.md`](docs/DIRECTORY_STRUCTURE.md) を参照。

> **注意**: `programing files/models.py` はルート直下の `models.py` とは別ファイルで、内容が乖離しやすい。スクリプトが触るテーブルの列を追加・変更したら両方のモデル定義を確認・更新すること。

## クイックスタート

```bash
pip install -r requirements.txt
# ルート直下に .env を作成し DATABASE_URL / LINE_CHANNEL_SECRET / LINE_CHANNEL_ACCESS_TOKEN / ADMIN_PASSWORD を設定
uvicorn main:app --reload
```

詳細な環境構築手順（Supabase・LINE Developers・リッチメニュー・VAPID鍵の準備等）は [`docs/SETUP.md`](docs/SETUP.md) を参照。

## テスト / Lint

```bash
pip install -r requirements-dev.txt
pytest        # 実DB接続不要（SQLiteインメモリDBで代替）
ruff check .
```

詳細は [`docs/DEVELOPMENT.md`](docs/DEVELOPMENT.md) を参照。

## デプロイ

Render の Web Service にデプロイする（`Procfile`: `uvicorn main:app --host 0.0.0.0 --port $PORT --workers ${WEB_CONCURRENCY:-1}`）。本番・dev で Render サービスと DB（Supabase プロジェクト）が分かれており、コードは GitHub の別ブランチから各サービスにデプロイされる。環境変数は `.env` だけでなく Render ダッシュボードの Environment にも登録する必要がある。

本番デプロイ手順（DB同期・リッチメニュー更新を含む）・dev/本番の環境対応表は [`docs/DEPLOYMENT.md`](docs/DEPLOYMENT.md) を参照。

Supabase Free プランには自動バックアップが無いため、`BACKUP_ENABLED=true` の環境では本番アプリ自身が定期的に全テーブルを Supabase Storage へダンプする（ローカルへの取り込みは `programing files/download_prod_backup.py`）。
