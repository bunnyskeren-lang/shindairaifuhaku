# API仕様

FastAPI製。ベースURLは環境ごとに異なる（[`DEPLOYMENT.md`](./DEPLOYMENT.md) 参照）。

- 本番: `https://shindairaifuhaku.onrender.com`
- dev: `https://shindairaifuhaku-1.onrender.com`

## 認証方式

| 方式 | 対象 | 説明 |
|---|---|---|
| **LIFF ID token** | `/api/*` の大半 | フロントがLINEログインで取得した `id_token` をリクエストボディ or `X-Liff-Id-Token` ヘッダーで送信。`core.liff_auth.verify_liff_id_token()` がLINEプラットフォームに問い合わせて検証し、120秒キャッシュする。検証失敗時は `line_user_id` が得られず、多くのエンドポイントは401またはログイン前扱いの空データを返す |
| **管理者Cookie** | `/admin/*`（`/admin/login`除く） | `core.security.check_admin` が全 `/admin/*` ルートに `Depends()` で付与。Cookie名 `admin_tok`、`HMAC-SHA256(CHANNEL_SECRET + ADMIN_PASSWORD, "admin:{timestamp}")`、TTL 4時間 |
| **LINE署名検証** | `POST /callback` | `X-Line-Signature` ヘッダーをチャネルシークレットで検証（`core.security.verify_line_signature`） |
| 認証なし | `/api/courses`, `/api/preload`, `/api/instructors`, `/api/course/{id}`, `/health`, 各種ページ | 公開情報のみ返す |

## レート制限

`core.rate_limit.rate_limiter()` によるIPアドレス単位のスライディングウィンドウ制限を、書き込み系・重い処理・総当たり攻撃を受けやすいエンドポイントに個別付与している。

| エンドポイント | 上限 | 理由 |
|---|---|---|
| `POST /submit` | 3回/分 | レビュー連投スパム・審査キュー圧迫防止 |
| `POST /api/register` | 5回/分 | 無制限の書き込み連打防止 |
| `POST /api/autofill` | 10回/分 | 学籍番号総当たりによる氏名取得防止 |
| `GET /api/courses`, `GET /api/instructors` | 30回/分 | 無制限ILIKE全文検索の連打防止 |
| `GET /r/{name}` | 20回/分 | ボタン名が予測可能なため無制限INSERT連打防止 |
| `POST /admin/login` | （`routers/admin/auth.py`参照） | ログイン総当たり防止 |

超過時は `429 Too Many Requests` を返す。

## LINE Bot Webhook

| メソッド/パス | 説明 |
|---|---|
| `POST /callback` | LINE Messaging APIのWebhook。署名検証後、`asyncio.create_task`でバックグラウンド処理し即時200を返す。`FollowEvent`（友だち追加）/`MessageEvent`（テキスト送信・科目検索等）/`PostbackEvent`（リッチメニュー・カルーセルのタップ）を処理し、応答はLINE API経由で返す（このエンドポイント自体はJSONを返さない） |

## ヘルスチェック

| メソッド/パス | 説明 |
|---|---|
| `GET /health` | 死活監視用（Render・自己pingが使用） |

## ページ（HTML）

| メソッド/パス | 説明 |
|---|---|
| `GET /` | レビュー投稿フォームのトップ（`?uid=`はプリフィル対象のヒントのみで、実際の個人情報取得はLIFF ID token検証済みの `/api/profile/prefill` 経由） |
| `GET /register` | 会員登録フォーム |
| `GET /liff/review` | レビュー投稿LIFFへの直接リダイレクト用ページ |
| `GET /coop` | 生協アプリのストアページへのhttps中継 |
| `GET /privacy` | プライバシーポリシー |
| `GET /sw.js` | Service Worker（Web Push通知の受信・クリック処理） |
| `GET /liff/course` | 科目詳細・レビュー閲覧LIFFページ |
| `GET /r/{name}` | リッチメニューのクリック計測付きリダイレクト（`richmenu_taps`にINSERT後302）。`name`は`review`/`beefplus`/`uribop`/`shokudo`/`toshokan`/`bus`/`kyoyoin`のいずれか |

## 科目検索・詳細（`routers/liff_api.py`）

| メソッド/パス | パラメータ | 説明 |
|---|---|---|
| `GET /api/courses` | `q`（検索語、空白区切りAND） | 科目名・よみがなをILIKE部分一致検索（記号除去した正規化検索へのフォールバック付き）。最大50件、教員一覧付き |
| `GET /api/preload` | - | 全科目・全教員の軽量一覧（キャッシュ、`Cache-Control: public, max-age=300`）。フロントの検索インデックス用 |
| `GET /api/instructors` | `q`（検索語） | 教員名の部分一致検索、担当科目一覧付き（最大50件） |
| `GET /api/course/{course_id}` | - | 科目詳細（担当教員一覧・平均評価・楽単度分布・承認済みレビュー最大20件・最新シラバスURL）。閲覧時に`course_section_views`をインクリメント |

## 会員登録・プロフィール（`routers/profile_api.py`）

| メソッド/パス | 認証 | 説明 |
|---|---|---|
| `POST /api/profile/status` | LIFF token | ボディ`{id_token}`。会員登録が完了しているか（`{complete: bool}`） |
| `POST /api/profile/prefill` | LIFF token | ボディ`{id_token}`。本人の既存プロフィールを返す（登録フォームのプリフィル用。IDOR対策でuid直指定は不可） |
| `POST /api/register` | LIFF token（Form） | `id_token`/`name`/`student_id`/`faculty`/`grade`/`department`（Form）。会員登録。学籍番号の重複チェックあり。登録完了後、未登録者用リッチメニューの解除を行う |
| `POST /api/autofill` | LIFF token | ボディ`{id_token, student_id}`。過去のレビュー投稿から氏名を自動補完 |

## レビュー投稿（`routers/review_submit_api.py`）

| メソッド/パス | 認証 | 説明 |
|---|---|---|
| `POST /submit` | LIFF token（Form） | `course_name`/`rating`(1-5)/`ease_rating`(SS,S,A,B,C)/`grading_method`/`comment`/`id_token`/`reg_name`/`student_id`/`selected_instructor`/`nickname`/`academic_year`。バリデーション後 `is_approved=False` で保存し、管理画面の承認待ちに積む。投稿成功後、Web Push通知をバックグラウンド送信 |

## 管理画面 API（`routers/admin/*`、要管理者Cookie認証）

管理画面はHTML画面＋フォームPOSTの構成。主な機能単位は以下の通り（完全なルート一覧は [`DIRECTORY_STRUCTURE.md`](./DIRECTORY_STRUCTURE.md) のroutersセクション、またはコード内 `@router.get/post` を参照）。

| パス prefix | 機能 |
|---|---|
| `/admin/login`, `/admin/logout` | ログイン/ログアウト |
| `/admin`, `/admin/push/subscribe` | ダッシュボード（メッセージログ）、Web Push購読 |
| `/admin/courses*` | 科目一覧・編集・削除・並び替え |
| `/admin/courses/{id}/instructors/*`, `/admin/courses/instructor/move`, `/admin/courses/faculty/move` | 教員の追加・削除・並び替え、学部の並び替え |
| `/admin/courses/classification/*` | 分類の改名・削除・並び替え・親グループ設定 |
| `/admin/reviews*` | レビュー承認・却下・古い未承認レビューのクリーンアップ（承認画面で内容編集可） |
| `/admin/users`, `/admin/errors`, `/admin/activity` | ユーザー一覧、エラーログ、利用統計 |
| `/admin/richmenu-stats`, `/admin/usage-stats` | リッチメニュークリック統計、機能別利用統計 |
| `/admin/binran_discrepancies` | 学生便覧×DBの齟齬一覧（読み取り専用） |

## エラーレスポンス形式

- FastAPIの標準的な `{"detail": "..."}` 形式（`HTTPException`）
- バリデーションエラー（422）は `{"detail": [...]}`（Pydanticのエラー配列）
- 未捕捉例外は全て `core.activity_log.save_error_log()` で`error_logs`テーブルに記録した上で `500 {"detail": "Internal Server Error"}` を返す（`main.py`のグローバル例外ハンドラ）
