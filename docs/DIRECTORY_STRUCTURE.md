# ディレクトリ構成

`shindairaifuhaku/` が Render にデプロイされるルート。`programing files/` 配下はデプロイ対象外の運用・整備用スクリプト群。

```
shindairaifuhaku/
├── main.py                  ← 薄いエントリポイント（app生成・ミドルウェア登録・例外ハンドラ・lifespan・include_router）
├── models.py                 ← SQLAlchemy ORM モデル定義（DBスキーマの正。詳細は DB_SCHEMA.md）
├── database.py                ← 非同期DBエンジン生成・init_db()（テーブル作成・マイグレーション相当の後方互換処理）
├── requirements.txt            ← 本番依存パッケージ
├── requirements-dev.txt         ← 開発用追加パッケージ（ruff / pytest / pytest-asyncio / aiosqlite）
├── Procfile                      ← Render起動コマンド（"web: uvicorn main:app --host 0.0.0.0 --port $PORT --workers ${WEB_CONCURRENCY:-1}"）
├── runtime.txt                    ← "python-3.12.0"
├── ruff.toml                       ← lint設定
├── pytest.ini                       ← テスト設定（testpaths = tests）
├── CLAUDE.md / AGENTS.md             ← Claude Code / 他AIツール向け運用ルール（内容は同一。CLAUDE.mdを更新したらAGENTS.mdにも反映）
├── certs/                             ← Supabase Root CA証明書（supabase-ca.crt、DB接続のTLS検証用）
│
├── core/                    ← 横断的関心事（ルーター間で共有するロジック）
│   ├── config.py              ← 環境変数読み込み・定数・シラバスURL生成・科目名/教員名の正規化・よみがな変換
│   ├── security.py             ← 管理者トークン発行/検証・LINE署名検証・check_admin依存関数
│   ├── cache.py                  ← 全インメモリキャッシュとinvalidate/prewarm関数（rawなdictは外部公開しない）
│   ├── activity_log.py            ← エラーログ・メッセージログの保存、古いログの定期クリーンアップ
│   ├── line_client.py              ← LINE APIクライアント（reply送信・リッチメニュー操作・自己ping）
│   ├── liff_auth.py                 ← LIFF ID token検証＋結果キャッシュ
│   ├── push.py                       ← Web Push (VAPID) 通知送信
│   ├── prewarm.py                     ← 起動時キャッシュウォームアップの統合
│   ├── templates.py                    ← Jinja2Templates・jstフィルタ
│   ├── rate_limit.py                     ← IPアドレス単位のスライディングウィンドウ・レート制限
│   ├── db_ssl.py                           ← Supabase接続用SSLコンテキスト生成
│   └── backup.py                            ← DB自動バックアップ（全テーブルダンプ→Supabase Storageアップロード）
│
├── line_bot/                ← LINE Bot応答ロジック
│   ├── flex_builders.py       ← FlexMessage/Bubble生成関数群
│   └── handler.py              ← handle_message・handle_course_list・process_events（Webhookイベント処理）
│
├── routers/                 ← URLプレフィックス単位のFastAPI APIRouter（詳細は API.md）
│   ├── webhook.py              ← POST /callback（LINE Webhook）
│   ├── health.py                ← GET /health
│   ├── pages.py                  ← HTMLページ全般（/, /register, /liff/review, /coop, /privacy, /sw.js, /liff/course）
│   ├── richmenu.py                ← GET /r/{name}（クリック計測付きリダイレクト）
│   ├── liff_api.py                 ← 科目検索・詳細（/api/courses, /api/preload, /api/instructors, /api/course/{id}）
│   ├── profile_api.py               ← 会員登録・プロフィール（/api/profile/*, /api/register, /api/autofill）
│   ├── review_submit_api.py          ← レビュー投稿（POST /submit）
│   └── admin/                         ← 管理画面（HMAC Cookie認証、全ルートにcheck_adminを付与）
│       ├── _common.py                    ← 並び替え(up/down)処理の共通ヘルパー
│       ├── auth.py                        ← /admin/login, /admin/logout
│       ├── dashboard.py                    ← /admin（メッセージログ）, /admin/push/subscribe
│       ├── courses.py                       ← /admin/courses*（科目一覧・編集・削除・並び替え）
│       ├── instructors.py                    ← /admin/courses/{id}/instructors/*（教員の追加・削除・並び替え）
│       ├── classifications.py                 ← /admin/courses/classification/*（分類の改名・削除・並び替え・親グループ設定）
│       ├── reviews.py                          ← /admin/reviews*（レビュー承認・却下・クリーンアップ）
│       ├── users_errors.py                      ← /admin/users, /admin/errors
│       └── stats.py                              ← /admin/usage-stats
│
├── templates/
│   ├── admin/                ← 管理画面テンプレート（base / login / courses / reviews / logs / users / errors /
│   │                            activity / usage_stats / richmenu）
│   ├── liff/                  ← course.html（科目詳細）/ review_redirect.html（レビュー投稿直リンク）
│   ├── form_index.html          ← レビュー投稿フォーム
│   ├── form_success.html / form_error.html
│   ├── form_register.html / form_register_success.html  ← 会員登録フォーム
│   ├── coop_redirect.html        ← 生協アプリのストアページへの中継
│   └── privacy.html
│
├── data/                    ← シラバス取り込み用テキストファイル（学部・曜日別、全学部分。import_syllabus.pyの入力）
├── supabase/migrations/     ← 適用済みスキーマ移行SQL（新規テーブル追加時のみ増える。既存列変更はdatabase.py init_db()側で後方互換処理）
├── docs/                    ← ドキュメント類（git管理下・デプロイ対象。本ファイルもここに含まれる）
│   ├── API.md / DB_SCHEMA.md / DIRECTORY_STRUCTURE.md / DEVELOPMENT.md / DEPLOYMENT.md / SETUP.md ← 本ドキュメント群
│   ├── SUBJECT_NAME_RULES.md   ← 科目名表記統一・LINE bot一覧のバリアント統合ルール
│   ├── SCHEMA_REVIEW.md         ← DBスキーマの課題洗い出し（調査メモ）
│   ├── PERFORMANCE.md            ← パフォーマンス改善メモ
│   └── TODO/                      ← 未対応タスクの記録（CREDITS_TODO.md 等）
├── backups/                 ← download_prod_backup.pyのダウンロード先（.gitignore対象、env別サブフォルダ）
├── tests/                   ← pytest（unit / e2e / integration。詳細は DEVELOPMENT.md）
│
└── programing files/        ← 運用・整備用スクリプト群（**Renderにはデプロイされない**）
    ├── README.md                  ← このディレクトリ専用の詳細README
    ├── import_syllabus.py          ← シラバスデータをDBへ投入（--also-courses / --classification / --faculty）
    ├── fetch_syllabus_info.py       ← シラバスページをスクレイピングし単位数・経営学部専門科目の群を取得
    ├── setup_richmenu.py             ← LINEリッチメニュー設定（--env dev/prod 必須）
    ├── sync_db_to_prod.py             ← dev→本番DBの一部テーブル同期（ローカル実行版）
    ├── download_prod_backup.py         ← Supabase Storage上のDBバックアップをローカルへ差分DL（--env dev/prod）
    ├── models.py / database.py          ← スクリプト群専用のDBアクセス層（ルートのmodels.py/database.pyとは別定義。乖離注意）
    ├── _env.py                            ← 各スクリプト共通の.envローダー
    ├── seeds/                              ← 一回限りの投入スクリプト置き場（本番デプロイ時に必要なため削除禁止）
    │   └── generate_vapid.py                  ← Web Push用VAPID鍵ペア生成
    ├── assets/richmenu.png                 ← リッチメニュー原本画像（git管理下）
    └── .env / .env.dev / .env.example       ← 環境変数（本番・dev、実体は.gitignore対象。.env.exampleのみ管理下）
```

## 補足

- **`programing files/models.py` はルート直下の `models.py` とは別ファイル**。内容が乖離しやすいため、スクリプトが触るテーブルの列を追加・変更・削除したら両方のモデル定義を確認・更新すること（`docs/SCHEMA_REVIEW.md` 参照）。
- `supabase/migrations/` は新規テーブル追加時に増えるSQLの記録用で、既存テーブルへの列追加・変更は基本的に `database.py` の `init_db()` 内の `ALTER TABLE ... ADD COLUMN IF NOT EXISTS` 等の後方互換処理で行っている（Alembic等のマイグレーションツールは未導入）。
- `docs/` は2026-07-17にgit管理化され、Renderのデプロイ対象にも含まれる。
- 各ファイルの役割の詳細（関数レベル）はコード内コメント、DBスキーマの詳細は [`DB_SCHEMA.md`](./DB_SCHEMA.md)、APIエンドポイントの詳細は [`API.md`](./API.md) を参照。
