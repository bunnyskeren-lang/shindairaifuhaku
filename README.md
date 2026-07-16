# shindairaifuhaku

神戸大学の学生向け、履修レビュー・時間割管理 LINE Bot / LIFF アプリ。LINE 経由で科目レビューの閲覧・投稿、シラバス確認、時間割登録、成績表（PDF）からの単位集計ができる。管理画面から科目・レビュー・ユーザーの管理も行える。

## 主な機能

- **LINE Bot**：科目名で検索するとレビュー・シラバス情報を Flex Message で返信（`/callback`）。「専門」メニューは学部一覧 → 学科・専攻一覧（or 群科目一覧）→ 科目一覧のドリルダウン方式
- **友だち追加時の会員登録必須化**：氏名・学籍番号・学部・学年・学科を登録するまでリッチメニューの各機能は登録画面へ誘導（`user_profiles`）
- **レビュー投稿フォーム**（`/submit`）：科目レビューを投稿 → 管理画面で承認後に公開。承認画面でコメント・評価・教員名等を編集してから承認可能
- **LIFF ページ**
  - `/liff/course`：科目詳細・レビュー閲覧
  - `/liff/timetable`：マイ時間割（学部シラバスから科目を登録、年度・クォーター切替、教室名の自由入力、教養/専門/共通専門基礎科目の色分け表示）
  - `/liff/review`：レビュー投稿への直接リダイレクト
  - `/register`：会員登録フォーム
- **成績表 PDF 解析 / 単位チェッカー**（`/api/parse_seiseki` 等）：pdfplumber で成績 PDF を解析し、学部・学科ごとの卒業要件（`credit_requirements`。合算制約 `combined_of`・上限 `max_credits` に対応）に対して進捗バー付きで達成状況を表示。対応学部・学科: 経営学部、システム情報学部、工学部5学科、農学部（2年次からの6コース分岐）、文学部
- **履修登録上限（CAP制）**：学部・学科・年度ごとの上限単位数（`registration_caps`）を超えるとマイ時間割で警告表示
- **必修科目の自動時間割登録**：学部・学科・学年を選択すると該当する必修科目（`required_subjects`）を自動で `user_syllabi` に登録（工学部機械工学科1年で試験導入）
- **リッチメニュー連携**（`/r/{name}`）：クリック計測付きリダイレクト
- **管理画面**（`/admin/*`）：科目・教員・分類・レビュー承認・ユーザー・エラーログ・利用統計・時間割照合・単位要件（経営/システム情報/工学部各学科/農学部/文学部）・履修登録上限CAP・dev→本番マスタデータ同期などを管理（HMAC Cookie 認証）
- **Web Push 通知**（VAPID、購読者への送信は非同期・並列化済み）
- **DB 自動バックアップ**：本番アプリ自身が定期的に全テーブルを Supabase Storage へダンプ
- **レート制限・リクエストサイズ制限**：ログイン試行・レビュー投稿・成績表解析等をIPアドレス単位で制限、リクエストボディサイズにも上限

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
| HTTP クライアント | httpx（LIFF ID token 検証・DB自動バックアップの Supabase Storage API 呼び出し等） |
| ホスティング | Render（Web Service） |

## アーキテクチャ概要

2026年7月に、単一 `main.py`（約3800行）から `core / line_bot / routers` パッケージ構成へリファクタリング済み。`main.py` 自体は app 生成・ミドルウェア登録・例外ハンドラ・`lifespan`・`include_router` のみの薄いエントリポイント。

| レイヤー | 役割 |
|---|---|
| `core/` | 環境変数・定数・シラバス URL 生成（`config.py`）、管理者認証・LINE 署名検証（`security.py`）、インメモリキャッシュ（`cache.py`）、エラー/メッセージログ（`activity_log.py`）、LINE API クライアント（`line_client.py`）、LIFF ID token 検証＋キャッシュ（`liff_auth.py`）、Web Push（`push.py`）、起動時プリウォーム（`prewarm.py`）、Jinja2 テンプレート（`templates.py`）、成績表 PDF の単位分類ロジック（`seiseki.py`）、レート制限（`rate_limit.py`）、Supabase 接続用 SSL コンテキスト（`db_ssl.py`）、DB 自動バックアップ（`backup.py`）、必修科目自動登録ロジック（`required_subjects.py`） |
| `line_bot/` | LINE Bot 応答ロジック（`flex_builders.py`：FlexMessage 生成、`handler.py`：`handle_message` / `handle_course_list` / `process_events`） |
| `routers/` | URL プレフィックス単位の FastAPI `APIRouter`（`webhook.py` / `health.py` / `pages.py` / `richmenu.py` / `liff_api.py` / `timetable_api.py` / `seiseki_api.py` / `admin/` 配下 9 ファイル：`auth` / `dashboard` / `courses` / `reviews` / `users_errors` / `stats` / `timetable_check` / `credit_requirements` / `registration_caps` / `sync`） |

- **LINE Bot フロー**：`POST /callback`（`routers/webhook.py`） → `core.security.verify_line_signature` → `core.line_client.parser.parse()` → `asyncio.create_task` で `line_bot.handler.process_events()` を実行し即時200応答。`FollowEvent`（ウェルカム Flex Message、未登録なら会員登録LIFFへ誘導）／`MessageEvent`／`PostbackEvent` を分岐処理し、各処理は25秒でタイムアウト保護。返信は `core.line_client.reply()` 経由。
- **キャッシュ設計**：`core/cache.py` に集約したモジュールレベルのグローバル変数に TTL 3600秒のインメモリキャッシュを複数保持（分類/学部/群判定用の `senmon_cache` は起動時に `reload_senmon_cache()` で構築）。他モジュールは必ず `cache.get_*` / `cache.set_*` / `cache.invalidate_*` の関数経由でアクセスし、rawな dict は直接 import しない（`invalidate` 時に `global` で再代入されるため、直 import すると古い参照を掴んだままになる）。管理画面での CRUD 後に該当キャッシュを即時無効化。起動時に `core.prewarm.prewarm_caches()` で全ウォームアップ。
- **管理画面認証**：`core.security.make_admin_token()` が `HMAC-SHA256(CHANNEL_SECRET + ADMIN_PASSWORD)` で署名した Cookie トークンを発行（TTL 4時間）。`core.security.check_admin` を全 `/admin/*` ルートに `Depends()` で付与。
- **ミドルウェア（`main.py`、外側から順に適用）**：`GZipMiddleware`（JSON応答等を圧縮）→ `CORSMiddleware`（LIFF/LINEドメインのみ許可）→ `BodySizeLimitMiddleware`（リクエストボディ2MB上限、`/api/parse_seiseki`除く）→ `RequestTimingMiddleware`（アクセスログ・3秒以上はSLOWマーカー付き標準出力）。後者2つは Starlette の `BaseHTTPMiddleware` だと全リクエストにタスク生成・anyio中継のオーバーヘッドが乗るため、素の ASGI（scope/receive/send）実装。加えて `/admin/login`・`/submit`・`/api/parse_seiseki` 等には `core.rate_limit.rate_limiter()` を個別に `Depends()` 付与しIPアドレス単位でスライディングウィンドウ制限。
- **起動処理（lifespan）**：`init_db()` → `cache.reload_senmon_cache()` / `prewarm.prewarm_caches()` を非同期起動 → `line_client.startup()` / `liff_auth.startup()` → 自己 ping（`SELF_URL` 設定時）・`backup.backup_loop()`（`BACKUP_ENABLED=true` 時のみ）・`activity_log.log_cleanup_loop()` をバックグラウンドタスクとして起動。
- **dev → 本番マスタデータ同期**：`programing files/sync_db_to_prod.py` をローカルから実行し、`credit_requirements`/`display_orders`/`subjects`/`instructors`/`course_sections`/`subject_credit_categories` の6テーブルを自然キー（id直コピーではなく name 等）でUPSERTする。本番のみに存在する行（devで削除・変更済みの行）も削除するが、`syllabi`/`reviews`が紐づく行は保護し「KEEP(要確認)」としてログ表示のみ行う。

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
| `user_syllabi` | ユーザーの時間割登録（`classroom`：自由入力の教室名） |
| `subject_credit_categories` | 科目↔単位カテゴリの紐付け |
| `required_subjects` | 学部・学科・学年ごとの必修科目マスタ（時間割登録時に自動登録する対象） |

### 共通・運用系

| テーブル | 用途 |
|---|---|
| `display_orders` | 表示順マスタ（汎用、`kind`列で対象種別を区別。`classification`=分類の表示順・親グループ、`faculty`=学部の表示順、`credit_requirement_group`=単位要件グループの表示順） |
| `credit_requirements` | 単位要件定義（学部・学科別。`combined_of`＝複数区分の合算制約、`max_credits`＝取得単位の上限） |
| `registration_caps` | 履修登録上限単位数（CAP制、学部/学科/年度ごと。department が NULL なら学部共通値） |
| `user_profiles` | LINE ユーザーのプロフィール（氏名・学籍番号・学部・学年・学科。友だち追加時の会員登録で必須入力） |
| `user_seiseki_raw` | 成績表 PDF の解析済み JSON（GPA含む） |
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
├── certs/                  ← Supabase Root CA証明書（supabase-ca.crt、DB接続のTLS検証用）
├── core/                    ← 横断的関心事（config / security / cache / activity_log / line_client /
│                               liff_auth / push / prewarm / templates / seiseki / rate_limit /
│                               db_ssl / backup / required_subjects）
├── line_bot/                ← LINE Bot 応答ロジック（flex_builders.py / handler.py）
├── routers/                  ← URL プレフィックス単位の APIRouter
│   ├── webhook.py / health.py / pages.py / richmenu.py
│   ├── liff_api.py / timetable_api.py / seiseki_api.py
│   └── admin/                 ← auth / dashboard / courses / reviews / users_errors /
│                                 stats / timetable_check / credit_requirements /
│                                 registration_caps / sync（dev→本番マスタデータ同期）
├── templates/
│   ├── admin/              ← 管理画面（courses / reviews / keiei / sysinfo / bungaku /
│   │                          koubu_dept（工学部各学科） / registration_caps / logs / users /
│   │                          errors / activity / usage_stats / richmenu / timetable_check /
│   │                          course_views（orphaned） / login / base）
│   ├── liff/                ← course.html（科目詳細）/ timetable.html（マイ時間割）/
│   │                          review_redirect.html（レビュー投稿直リンク）
│   ├── form_index.html / form_success.html / form_error.html  ← レビュー投稿フォーム
│   ├── coop_redirect.html  ← 生協アプリのストアページへの中継
│   └── privacy.html
├── data/                   ← シラバス取り込み用テキストファイル（学部・曜日別、全学部分）
├── supabase/migrations/    ← 新スキーマ移行 SQL
├── docs/                   ← ドキュメント類（.gitignore 対象、非公開。学生便覧読解ガイド・
│                              科目名表記ルール・パフォーマンスメモ等）
└── programing files/       ← 運用・整備用スクリプト群（Render にはデプロイされない）
    ├── import_syllabus.py            ← シラバステキスト（全学部共通フォーマット）を DB へ
    │                                     インポート（--also-courses / --classification / --faculty、
    │                                     fetch_syllabus_info.py を自動呼び出し）
    ├── fetch_syllabus_info.py        ← シラバスページから対象年次・科目分類・単位数・
    │                                     ナンバリングコードを取得
    ├── setup_richmenu.py             ← LINE リッチメニュー新規セットアップ（--env dev/prod）
    ├── sync_db_to_prod.py            ← dev → 本番 DB の一部テーブル同期（5テーブルのみ、
    │                                     ローカル実行版。同等の処理は管理画面からも実行可能）
    ├── download_prod_backup.py       ← Supabase Storage 上の DB バックアップをローカルへ差分DL
    ├── generate_vapid.py             ← Web Push 用 VAPID 鍵ペア生成
    ├── seed_bungaku_credit_requirements.py / seed_nogaku_credit_requirements.py /
    │   seed_nogaku_subject_categories.py / update_nogaku_credit_notes.py
    │                                 ← 学部別の単位要件・科目カテゴリ紐付けの投入スクリプト
    │                                    （一回限りの実行を想定、学部追加時のテンプレートとしても参照）
    ├── models.py / database.py       ← スクリプト群専用の DB アクセス層（ルートの models.py とは別定義）
    └── .env / .env.dev               ← 環境変数（本番・dev、.gitignore 対象）
```

> **注意**: `programing files/models.py` はルート直下の `models.py` とは別ファイルで、内容が乖離しやすい（`docs/SCHEMA_REVIEW.md` で指摘済み）。スクリプトが触るテーブルの列を追加・変更したら両方のモデル定義を確認・更新すること。

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
| `REGISTER_LIFF_ID` | - | 会員登録 LIFF ページの LIFF ID |
| `REVIEW_LIFF_ID` | - | レビュー投稿 LIFF ページの LIFF ID |
| `RICHMENU_ID_PREREGISTER` | - | 未登録ユーザー用リッチメニューの ID |
| `VAPID_PUBLIC_KEY` / `VAPID_PRIVATE_KEY` / `VAPID_EMAIL` | - | Web Push 通知用 VAPID 鍵 |
| `SELF_URL` / `APP_URL` | - | 自己 ping・リンク生成に使う自身の URL |
| `KYOYO_REQUIRED_CREDITS` | - | 教養科目の必要単位数（デフォルト: 1） |
| `DB_POOL_SIZE` / `DB_POOL_MAX_OVERFLOW` | - | DB接続プールのサイズ調整（デフォルト: 10 / 20） |
| `DISABLE_SSL_VERIFY` | - | DB 接続の SSL 証明書検証を無効化（`core/db_ssl.py`、緊急時の切り戻し用） |
| `SUPABASE_URL` / `SUPABASE_SERVICE_ROLE_KEY` | - | DB自動バックアップのアップロード先 Supabase Storage API |
| `BACKUP_ENABLED` / `BACKUP_BUCKET` / `BACKUP_RETENTION_DAYS` / `BACKUP_INTERVAL_HOURS` | - | DB自動バックアップの有効化・保存先バケット・保持日数・実行間隔（既定: false / `db-backups` / 15日 / 1時間） |
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

Supabase Free プランには自動バックアップが無いため、`BACKUP_ENABLED=true` の環境では本番アプリ自身が `core/backup.py` の `backup_loop()` で定期的に全テーブルを Supabase Storage へダンプする（ローカルへの取り込みは `programing files/download_prod_backup.py`）。

## 開発ワークフロー

- 機能追加・バグ修正の単位で小さくコミットする
- `models.py` にモデルを追加・削除したら `database.py` の `init_db()` 内の import も更新する
- 投稿済みレビュー（`reviews` テーブル）は明示的な指示がない限り削除しない
- 同一 `AsyncSession` 内で `asyncio.gather` による並行クエリは行わない（`InterfaceError` の原因になる）

詳細な運用ルール・シラバス URL 生成ロジック・LIFF ID / 環境ごとの固定値などは `CLAUDE.md` を参照。
