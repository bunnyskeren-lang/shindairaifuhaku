# shindairaifuhaku

神戸大学の学生向け、履修レビュー・時間割管理 LINE Bot / LIFF アプリ。LINE 経由で科目レビューの閲覧・投稿、シラバス確認、時間割登録、成績表（PDF）からの単位集計ができる。管理画面から科目・レビュー・ユーザーの管理も行える。

## 主な機能

- **LINE Bot**：科目名で検索するとレビュー・シラバス情報を Flex Message で返信（`/callback`）
- **レビュー投稿フォーム**（`/submit`）：科目レビューを投稿 → 管理画面で承認後に公開
- **LIFF ページ**
  - `/liff/course`：科目詳細・レビュー閲覧
  - `/liff/timetable`：マイ時間割（学部シラバスから科目を登録）
- **成績表 PDF 解析**（`/api/parse_seiseki` 等）：pdfplumber で成績 PDF を解析し、単位カテゴリ別に集計
- **リッチメニュー連携**（`/r/{name}`）：クリック計測付きリダイレクト
- **管理画面**（`/admin/*`）：科目・教員・分類・レビュー承認・ユーザー・エラーログ・利用統計・時間割照合・単位要件（経営学部/システム情報学部）などを管理（HMAC Cookie 認証）
- **Web Push 通知**（VAPID）

## 技術スタック

| 分類 | 技術 |
|------|------|
| 言語 | Python 3.12 |
| Web フレームワーク | FastAPI + Uvicorn |
| ORM | SQLAlchemy 2.0（async / asyncpg） |
| DB | PostgreSQL（Supabase） |
| テンプレート | Jinja2 |
| LINE 連携 | line-bot-sdk v3（`linebot.v3`） |
| PDF 解析 | pdfplumber（成績表パース） |
| よみがな生成 | pykakasi |
| プッシュ通知 | pywebpush（VAPID） |
| ホスティング | Render（Web Service） |

## アーキテクチャ概要

2026年7月に、単一 `main.py`（約3800行）から `core / line_bot / routers` パッケージ構成へリファクタリング済み。`main.py` 自体は app 生成・CORS・例外ハンドラ・`lifespan`・`include_router` のみの薄いエントリポイント（約90行）。

| レイヤー | 役割 |
|---|---|
| `core/` | 環境変数・定数（`config.py`）、管理者認証・LINE 署名検証（`security.py`）、インメモリキャッシュ（`cache.py`）、エラー/メッセージログ（`activity_log.py`）、LINE API クライアント（`line_client.py`）、Web Push（`push.py`）、起動時プリウォーム（`prewarm.py`）、Jinja2 テンプレート（`templates.py`）、成績表 PDF の単位分類ロジック（`seiseki.py`） |
| `line_bot/` | LINE Bot 応答ロジック（`flex_builders.py`：FlexMessage 生成、`handler.py`：`handle_message` / `handle_course_list` / `process_events`） |
| `routers/` | URL プレフィックス単位の FastAPI `APIRouter`（`webhook.py` / `health.py` / `pages.py` / `richmenu.py` / `liff_api.py` / `timetable_api.py` / `seiseki_api.py` / `admin/` 配下 8 ファイル） |

- **LINE Bot フロー**：`POST /callback`（`routers/webhook.py`） → `core.security.verify_line_signature` → `core.line_client.parser.parse()` → `asyncio.create_task` で `line_bot.handler.process_events()` を実行し即時200応答。`FollowEvent`（ウェルカム Flex Message）／`MessageEvent`／`PostbackEvent` を分岐処理し、各処理は25秒でタイムアウト保護。返信は `core.line_client.reply()` 経由。
- **キャッシュ設計**：`core/cache.py` に集約したモジュールレベルのグローバル変数に TTL 3600秒のインメモリキャッシュを複数保持。他モジュールは必ず `cache.get_*` / `cache.set_*` / `cache.invalidate_*` の関数経由でアクセスし、rawな dict は直接 import しない（`invalidate` 時に `global` で再代入されるため、直 import すると古い参照を掴んだままになる）。管理画面での CRUD 後に該当キャッシュを即時無効化。起動0.5秒後に `core.prewarm.prewarm_caches()` で全ウォームアップ。
- **管理画面認証**：`core.security.make_admin_token()` が `HMAC-SHA256(CHANNEL_SECRET + ADMIN_PASSWORD)` で署名した Cookie トークンを発行（TTL 4時間）。`core.security.check_admin` を全 `/admin/*` ルートに `Depends()` で付与。
- **起動処理（lifespan）**：`init_db()` → キャッシュのプリウォーム → `core.line_client.startup()`（LINE API クライアント初期化） → `SELF_URL` が設定されていれば60秒間隔で自己 ping（Render のスリープ防止）。

詳細なルート一覧・非同期クエリのルールなどは `CLAUDE.md` を参照。

## DB スキーマ（`models.py`）

2026年6月に新スキーマへ完全移行済み（適用済みマイグレーション: `supabase/migrations/`）。

### コアドメイン（科目・シラバス・レビュー）

| テーブル | 用途 |
|---|---|
| `subjects` | 科目マスタ（name, faculty, classification, term, credits 等） |
| `instructors` | 教員マスタ |
| `course_sections` | 科目×教員のセクション（syllabus_url 等） |
| `syllabi` | シラバス（年度・クォーター・時間割コード・対象学年・科目分類） |
| `schedules` | 曜日・時限・教室 |
| `reviews` | 投稿レビュー（`is_approved` で承認管理） |
| `course_section_views` | 科目セクションの閲覧数 |
| `user_syllabi` | ユーザーの時間割登録 |
| `subject_credit_categories` | 科目↔単位カテゴリの紐付け |

### 共通・運用系

| テーブル | 用途 |
|---|---|
| `classification_orders` | 分類の表示順・親グループ・学部 |
| `credit_requirements` | 単位要件定義（学部別） |
| `user_profiles` | LINE ユーザーの氏名・学籍番号 |
| `user_seiseki_raw` | 成績表 PDF の解析済み JSON |
| `timetable_profiles` | ユーザーの学部・学年 |
| `message_logs` | LINE メッセージ送受信ログ |
| `user_activity` | LINE アクション統計 |
| `error_logs` | サーバーエラーログ |
| `push_subscriptions` | Web Push VAPID 購読情報 |
| `richmenu_taps` | リッチメニュークリックログ |

## ディレクトリ構成

```
shindairaifuhaku/          ← Render がデプロイするルート
├── main.py                ← 薄いエントリポイント（app 生成・CORS・例外ハンドラ・lifespan・include_router）
├── models.py               ← SQLAlchemy ORM モデル定義（新スキーマ）
├── database.py             ← DB エンジン生成・init_db()
├── requirements.txt
├── Procfile                ← "web: uvicorn main:app ..."
├── runtime.txt              ← "python-3.12.0"
├── CLAUDE.md / AGENTS.md   ← Claude Code 向け運用ルール（内容は同一）
├── BUGS.md
├── core/                    ← 横断的関心事（config / security / cache / activity_log / line_client /
│                               push / prewarm / templates / seiseki）
├── line_bot/                ← LINE Bot 応答ロジック（flex_builders.py / handler.py）
├── routers/                  ← URL プレフィックス単位の APIRouter
│   ├── webhook.py / health.py / pages.py / richmenu.py
│   ├── liff_api.py / timetable_api.py / seiseki_api.py
│   └── admin/                 ← auth / dashboard / courses / reviews / users_errors /
│                                 stats / timetable_check / credit_requirements
├── templates/
│   ├── admin/              ← 管理画面（courses / reviews / keiei / sysinfo / logs / users /
│   │                          errors / activity / usage_stats / richmenu / timetable_check /
│   │                          course_views / login / base）
│   ├── liff/                ← course.html（科目詳細）/ timetable.html（マイ時間割）
│   ├── form_index.html / form_success.html / form_error.html  ← レビュー投稿フォーム
│   └── privacy.html
├── data/                   ← シラバス取り込み用テキストファイル（曜日別、経営学部）
├── supabase/migrations/    ← 新スキーマ移行 SQL（2026-06-27 適用: テーブル新設 → faculty 列追加 →
│                              subjects スキーマ修正の3本）
├── docs/                   ← ドキュメント類（.gitignore 対象、非公開）
└── programing files/       ← 運用・整備用スクリプト群（Render にはデプロイされない）
    ├── import_syllabus.py            ← シラバステキストを DB へインポート
    ├── fetch_syllabus_info.py        ← シラバスページから対象年次・科目分類を取得
    ├── import_kyoyo_courses.py       ← 教養科目の一括インポート（pykakasi でよみがな自動生成）
    ├── update_senmon_classification.py ← 経営学部専門科目のナンバリングコードを更新
    ├── setup_richmenu.py             ← LINE リッチメニュー新規セットアップ
    ├── sync_richmenu.py              ← リッチメニュー設定・画像を dev → 本番へコピー
    ├── sync_db_to_prod.py            ← dev → 本番 DB の一部テーブル同期（5テーブルのみ）
    ├── drop_old_tables.py            ← 旧スキーマテーブルの削除（移行完了確認後に使用）
    ├── generate_vapid.py             ← Web Push 用 VAPID 鍵ペア生成
    ├── import_keiei_instructors.py   ← 廃止済み（新スキーマ移行により不要。管理画面に統合）
    ├── models.py / database.py       ← スクリプト群専用の DB アクセス層（ルートの models.py とは別定義）
    └── .env / .env.dev               ← 環境変数（本番・dev、.gitignore 対象）
```

> **注意**: `programing files/models.py` はルート直下の `models.py` とは別ファイルで、内容が乖離している（`docs/SCHEMA_REVIEW.md` で指摘済み）。スクリプトを書く際は対象 DB のスキーマがどちらの定義と一致しているか確認すること。

## セットアップ

### 必要環境

- Python 3.12
- PostgreSQL データベース（本プロジェクトは Supabase を使用）
- LINE Developers のチャネル（Messaging API）

### インストール

```bash
pip install -r requirements.txt
```

### 環境変数

ルート直下の `.env` に以下を設定する（`python-dotenv` で読み込まれる）。

| 変数名 | 必須 | 説明 |
|---|---|---|
| `DATABASE_URL` | ✅ | PostgreSQL 接続文字列（asyncpg 形式） |
| `LINE_CHANNEL_SECRET` | ✅ | LINE Messaging API チャネルシークレット |
| `LINE_CHANNEL_ACCESS_TOKEN` | ✅ | LINE Messaging API チャネルアクセストークン |
| `ADMIN_PASSWORD` | ✅ | 管理画面ログインパスワード |
| `REVIEW_FORM_URL` | - | レビュー投稿フォームの公開 URL（デフォルト: 本番 URL） |
| `LIFF_ID` | - | 科目詳細 LIFF ページの LIFF ID |
| `TIMETABLE_LIFF_ID` | - | 時間割 LIFF ページの LIFF ID |
| `VAPID_PUBLIC_KEY` / `VAPID_PRIVATE_KEY` / `VAPID_EMAIL` | - | Web Push 通知用 VAPID 鍵 |
| `SELF_URL` / `APP_URL` | - | 自己 ping・リンク生成に使う自身の URL |
| `KYOYO_REQUIRED_CREDITS` | - | 教養科目の必要単位数（デフォルト: 1） |
| `ENABLE_SSL_VERIFY` | - | DB 接続の SSL 証明書検証を有効化 |
| `ENV` | - | `dev` を指定すると開発モード扱い |

### ローカル起動

```bash
uvicorn main:app --reload
```

`/health` で死活監視、`/callback` が LINE Webhook のエントリポイント。

## デプロイ

Render の Web Service にデプロイする（`Procfile` を参照）。本番・dev で Render サービスと DB（Supabase プロジェクト）が分かれており、コードは GitHub の別ブランチから各サービスにデプロイされる。

```bash
web: uvicorn main:app --host 0.0.0.0 --port $PORT
```

環境変数は `.env` だけでなく、Render ダッシュボードの Environment にも登録する必要がある。本番デプロイ手順（DB同期・リッチメニュー更新を含む）は `CLAUDE.md` を参照。

## 開発ワークフロー

- 機能追加・バグ修正の単位で小さくコミットする
- `models.py` にモデルを追加・削除したら `database.py` の `init_db()` 内の import も更新する
- 投稿済みレビュー（`reviews` テーブル）は明示的な指示がない限り削除しない
- 同一 `AsyncSession` 内で `asyncio.gather` による並行クエリは行わない（`InterfaceError` の原因になる）

詳細な運用ルール・シラバス URL 生成ロジック・LIFF ID / 環境ごとの固定値などは `CLAUDE.md` を参照。
