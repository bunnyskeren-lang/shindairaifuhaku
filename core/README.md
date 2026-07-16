# core/

ルーター間で共有する横断的関心事をまとめたパッケージ。`routers/` や `line_bot/` から呼び出される、DBアクセス層(`models.py`/`database.py`)より一段上のユーティリティ・共通ロジックが置かれる。

## ファイル一覧

| ファイル | 役割 |
|---|---|
| `config.py` | 環境変数・定数の読み込み、シラバスURL生成（`FACULTY_PATH`等）、よみがな変換 |
| `security.py` | 管理者トークンの発行/検証（HMAC-SHA256）、LINE Webhook署名検証、`check_admin`依存関数 |
| `db_ssl.py` | Supabase(Supavisorプーラー)向けSSLコンテキスト生成。リポジトリ同梱の`certs/supabase-ca.crt`を明示的に信頼する。`DISABLE_SSL_VERIFY=1`で緊急時のみ検証無効化可能 |
| `rate_limit.py` | IPアドレス単位のスライディングウィンドウ・レート制限（`/admin/login`総当たり対策等）。`client_ip()`はX-Forwarded-Forの右端（Renderプロキシが付与する値）のみ信用し、偽装ヘッダーによるバイパスを防ぐ |
| `liff_auth.py` | LIFF ID Tokenのサーバー側検証（LINEの`/oauth2/v2.1/verify`を叩く）。クライアントが送るline_user_idは偽装可能なため、書き込み系・個人情報を返すエンドポイントは必ず`verify_liff_id_token()`を経由すること。検証結果は120秒キャッシュ、失敗時はIP付きで`error_logs`に記録 |
| `cache.py` | 全インメモリキャッシュ（TTL 3600秒）を集約。他モジュールは必ず`get_*`/`set_*`/`invalidate_*`関数経由でアクセスすること（rawなdictを直import禁止。invalidate時に`global`で再代入されるため） |
| `prewarm.py` | 起動0.5秒後にキャッシュを一括ウォームアップ（`cache.warm_query_caches()` → `line_bot.flex_builders.prewarm_flex_cache()`の順） |
| `activity_log.py` | サーバーエラーログ（`error_logs`）・LINEメッセージ送受信ログ（`message_logs`）の保存 |
| `line_client.py` | LINE Messaging APIクライアント。`reply()`経由の返信送信、自己ping（`self_ping()`）。生の`_line_api`を各所で直接触らないこと |
| `push.py` | Web Push（VAPID）通知の送信 |
| `templates.py` | `Jinja2Templates`インスタンスと`jst`（JST変換）カスタムフィルタ |
| `required_subjects.py` | 時間割の「1コマ1科目」自動差し替えロジック（`register_syllabus_for_user`）と、プロフィール（学部・学科・学年）に応じた必修科目の自動登録（`auto_register_required_subjects`） |
| `seiseki.py` | 成績表PDFの単位分類ロジック（経営学部専門科目群判定など） |
| `backup.py` | DB自動バックアップ。`BACKUP_ENABLED=true`の環境で`BACKUP_INTERVAL_HOURS`間隔（既定1時間）に全テーブルをダンプしSupabase Storageへアップロード |
| `binran_discrepancies.py` | `docs/学生便覧2026/IMPLEMENTATION_CANDIDATES.md`をパースし学部/ステータス別に一覧化（DB非経由、ファイル読み込みのみ） |

## 設計ルール

- **キャッシュは必ず`cache.py`の関数経由でアクセスする**。初期値は空コレクション（`{}`/`set()`）なので、truthyチェックでヒット判定すること。
- **同一`AsyncSession`では`asyncio.gather`による並行クエリを行わない**（InterfaceError）。並行したい場合は各コルーチン内で`AsyncSessionLocal()`を個別に開く。
- クライアントから送られてくるLINEユーザーIDは偽装可能。書き込み系・個人情報系のLIFF APIは`liff_auth.verify_liff_id_token()`で検証したsubを使うこと。

詳細な全体アーキテクチャ・DBテーブル一覧はリポジトリルートの`CLAUDE.md`を参照。
