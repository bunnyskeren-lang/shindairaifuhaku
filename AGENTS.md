# 返信ルール

**必ず日本語で返信すること。**

**迷ったらコードを広範囲に読む前にユーザーに確認を取ること。**

---

# デプロイルール

## 環境変数の追加ルール（必須）

**新しい環境変数を追加したときは、必ずコードの変更と同時に以下を案内すること：**

1. `.env.dev` または `.env` に追加した変数名と値を明示する
2. **Render ダッシュボードへの登録も必ず案内する**（ローカルの .env だけでは Render に反映されない）
3. dev に追加した変数は dev サービス（shindairaifuhaku-1）へ、本番に追加した変数は本番サービス（shindairaifuhaku）へ

例：「Render dev の Environment に以下を追加してください：`KEY=value`」

---

- **本番環境（shindairaifuhaku.onrender.com）へのデプロイは、ユーザーから明示的な指示がない限り絶対に行わないこと**
- dev環境（shindairaifuhaku-1.onrender.com）のみ自由に操作してよい
- `git push` の push先が `origin main` または `origin shindairaifuhaku` の場合は必ず確認を取ること

## ブランチとRenderサービスの対応

| Renderサービス | GitHub ブランチ | コマンド |
|---|---|---|
| **dev** (shindairaifuhaku-dev) | `shindairaifuhaku-dev` | `git push origin dev:shindairaifuhaku-dev` |
| **本番** (shindairaifuhaku) | `shindairaifuhaku-prod` | `git push origin dev:shindairaifuhaku-prod` |

## setup_richmenu.py の実行ルール

- **必ず `--env` 引数を指定して実行すること**
  - dev:  `python setup_richmenu.py --env dev`   → `programing files/.env.dev` を使用
  - 本番: `python setup_richmenu.py --env prod`  → `programing files/.env` を使用（確認プロンプトあり）
- `--env prod` は**ユーザーから明示的に「本番のリッチメニューを更新して」と言われた場合のみ**実行すること
- `--env dev` はユーザーの許可のもとで自由に実行してよい

## モデル変更時のルール

- `models.py` でクラスを追加・削除したら、**必ず `database.py` の `init_db()` 内の import も同時に更新すること**
- 新しいモデルを追加した場合は import に追加、削除した場合は import から除去する

## データ保護ルール

- **投稿されたレビュー（PendingReviewテーブル）は、ユーザーから明示的な削除指示がない限り絶対に消去しないこと**
- 科目の削除・変更・マージなど、いかなる操作においても、その科目に紐づくレビューを巻き添えで削除しないこと
- レビューに影響しうるDB操作を行う前は、必ずユーザーに確認を取ること

## 本番デプロイ手順

ユーザーから明示的に本番デプロイを指示された場合、以下の順で実行する：

```bash
# 1. コードを本番ブランチにプッシュ
git push origin dev:shindairaifuhaku-prod

# 2. 本番リッチメニューを更新（programing files/ から実行すること）
cd "programing files" && python -X utf8 setup_richmenu.py --env prod
```

### DBの同期（デプロイ時に必ず実施）

**本番デプロイ時は、コードのプッシュに加えて必ず dev → prod のDB同期も行うこと。**

同期対象（この5テーブルのみ）：
- `classification_orders`
- `subjects`
- `instructors`
- `course_sections`
- `subject_credit_categories`

絶対に同期しないテーブル（ユーザーデータ・ログ・レビュー・利用履歴）：
- `reviews`
- `message_logs`
- `user_profiles`
- `user_activity`
- `error_logs`
- `push_subscriptions`
- `richmenu_taps`
- `syllabi` / `schedules` / `user_syllabi`（時間割データ・import_syllabus.py で別途管理）

同期方法：
```bash
cd "programing files"
python -X utf8 sync_db_to_prod.py
```

### LIFF ID の固定ルール

| 環境 | LIFF_ID | 科目詳細エンドポイント |
|---|---|---|
| **本番** | `2010406205-emxo5rhE` | `https://shindairaifuhaku.onrender.com/liff/course` |
| **dev** | `2010433465-R8b5k1SZ` | `https://shindairaifuhaku-1.onrender.com/liff/course` |

- 本番の科目詳細ボタンは必ず本番 LIFF ID を使い、本番エンドポイントを開くこと
- dev の科目詳細ボタンは必ず dev LIFF ID を使い、dev エンドポイントを開くこと
- **LIFF ID を dev と本番で入れ替えることは絶対に禁止**
- `LIFF_ID` は Render の各サービス環境変数で管理する（本番はコードデフォルト値と一致）

### REVIEW_FORM_URL の固定ルール
| 環境 | REVIEW_FORM_URL |
|---|---|
| **本番** | `https://shindairaifuhaku.onrender.com` |
| **dev** | `https://shindairaifuhaku-1.onrender.com` |

- `programing files/.env` の `REVIEW_FORM_URL` は必ず本番URLのままにすること
- `programing files/.env.dev` の `REVIEW_FORM_URL` は必ず dev URLのままにすること
- **絶対に入れ替えないこと**

## .env ファイル構成

| ファイル | 環境 |
|---|---|
| `programing files/.env.dev` | **dev** ボット用トークン |
| `programing files/.env` | **本番** ボット用トークン |

## データベース接続情報

| 環境 | DATABASE_URL |
|---|---|
| **dev** | `postgresql://postgres.ofsvkcptzngbsxtdbqzj:Developerr6363st@aws-1-ap-northeast-1.pooler.supabase.com:5432/postgres` |
| **本番** | `postgresql://postgres.sagubqrhjnzrtcvlmzqy:Linebot6363st@aws-1-ap-northeast-2.pooler.supabase.com:5432/postgres` |

## シラバスURL生成ルール

### シラバスと担当教員の対応について

**シラバスは科目名だけでなく担当教員に強く依存する。** 同じ科目名でも担当教員が異なればシラバスの内容（到達目標・授業計画・評価方法）は別物になる。
そのため、シラバスURLは「科目名」だけに紐づけるのではなく、**「科目名 × 担当教員」の組み合わせ**に紐づけることが望ましい。

現状の `course_sections.syllabus_url` は「科目×担当教員」単位で1本のURLを持つ設計になっており、`course_sections` テーブルが `subjects`（科目）と `instructors`（教員）を結ぶ形で管理している。

神戸大学シラバスサイトのURLは **時間割コード** から一意に決まる。

```
https://kym22-web.ofc.kobe-u.ac.jp/kobe_syllabus/2026/{path}/data/2026_{code}.html
```

| コードの2文字目 | 学部 | path |
|---|---|---|
| `U` | 教養科目（教養教育院） | `20` |
| `B` | 経営学部 | `06` |
| `X` | システム情報学部 | `15` |

例: `3U020` → `/20/` → `2026_3U020.html` / `3B379` → `/06/` → `2026_3B379.html` / `1X058` → `/15/` → `2026_1X058.html`

新しい学部のデータを追加する際は、実際のシラバスURLを確認してpathの数字を特定し、
`programing files/import_syllabus.py` と `programing files/fetch_syllabus_info.py` と
`templates/liff/timetable.html` の `FACULTY_PATH` / `FACULTY_PATH_JS` に追記すること。

### シラバスページのHTMLパース

神戸大学シラバスページの実際のHTML構造（2026年度確認済み）：

```html
<tr>
  <td class="gaibu-syllabus-kihon">科目分類</td>
  <td width="300">教養科目</td>       ← subject_category
  <td class="gaibu-syllabus-kihon">開講年次</td>
  <td width="300">1 ･ 2 ･ 3 ･ 4 年</td>  ← target_grades
</tr>
```

- ラベルは `<th>` ではなく `<td>`
- 開講年次のラベルは **「対象年次」ではなく「開講年次」**（ここを間違えると全件空になる）
- スクレイピングスクリプト: `programing files/fetch_syllabus_info.py`
  - `--env dev` で dev DB に書き込み、`--force` で既取得分も上書き
  - 0.3秒スリープ/件、20件ごとにコミット

### 時間割DBテーブル構成

| テーブル | 用途 |
|---|---|
| `syllabus_courses` | 時間割マスタ（timetable_code, term, target_grades, subject_category） |
| `course_slots` | 曜日・時限（day_of_week, period） |
| `user_courses` | ユーザーの登録科目 |
| `timetable_profiles` | ユーザーの学部・学年プロフィール |

インポートスクリプト: `programing files/import_syllabus.py`
- `--also-courses` を付けると `courses` テーブル（LINE bot用）にも登録
- `--classification` / `--faculty` で courses の分類・学部名を指定

---

## プロジェクト構成

> **ルール**: ディレクトリ構成・テーブル・技術スタック・アーキテクチャに影響する作業をしたら、作業完了時に必ずこのセクションを更新すること。

### 技術スタック

| 分類 | 技術 |
|------|------|
| 言語 | Python 3.12 |
| Webフレームワーク | FastAPI + Uvicorn |
| ORM | SQLAlchemy 2.0（async / asyncpg） |
| DB | PostgreSQL（Supabase） |
| テンプレート | Jinja2 |
| LINE連携 | line-bot-sdk v3（`linebot.v3`） |
| PDF解析 | pdfplumber（成績表パース） |
| よみがな生成 | pykakasi |
| プッシュ通知 | pywebpush（VAPID） |
| ホスティング | Render（Web Service） |

### ディレクトリ構成

```
shindairaifuhaku/          ← Renderがデプロイするルート
├── main.py                ← アプリ本体（約3500行、全ルートを含む）
├── models.py              ← SQLAlchemy ORMモデル定義（17テーブル）
├── database.py            ← DBエンジン生成・init_db()（マイグレーション含む）
├── requirements.txt
├── Procfile               ← "web: uvicorn main:app ..."
├── runtime.txt            ← "python-3.12.0"
├── templates/
│   ├── admin/
│   │   ├── base.html      ← 管理画面共通レイアウト（ナビ・ローディング・確認モーダル）
│   │   ├── courses.html   ← 科目管理（追加・編集・削除・教員管理）
│   │   ├── reviews.html   ← レビュー承認・却下
│   │   ├── keiei.html     ← 経営学部専用（単位要件・専門群分類）
│   │   ├── logs.html      ← メッセージログ
│   │   ├── users.html     ← ユーザー一覧
│   │   ├── errors.html    ← エラーログ
│   │   ├── activity.html  ← アクティビティ統計
│   │   ├── usage_stats.html   ← 利用統計
│   │   ├── richmenu.html      ← リッチメニュータップ統計
│   │   ├── timetable_check.html ← 時間割照合
│   │   ├── course_views.html  ← 科目閲覧数
│   │   └── login.html
│   ├── liff/
│   │   ├── course.html    ← 科目詳細・レビュー閲覧（LIFFページ）
│   │   └── timetable.html ← マイ時間割（LIFFページ）
│   ├── form_index.html    ← レビュー投稿フォーム
│   ├── form_success.html
│   ├── form_error.html
│   └── privacy.html
├── data/                  ← シラバス取り込み用テキストファイル（曜日別）
├── docs/                  ← ドキュメント類
└── programing files/      ← 運用・整備用スクリプト群（Renderにはデプロイされない）
    ├── import_syllabus.py         ← 時間割データをDB投入
    ├── fetch_syllabus_info.py     ← シラバスページをスクレイピング
    ├── import_kyoyo_courses.py    ← 教養科目インポート
    ├── import_keiei_instructors.py← 経営学部教員インポート
    ├── setup_richmenu.py          ← LINEリッチメニュー設定
    ├── sync_db_to_prod.py         ← dev→本番DBの3テーブル同期
    ├── seed_courses.py / cleanup_*.py / fix_dupes.py 等
    └── .env / .env.dev            ← 環境変数（本番・dev）
```

### DBテーブル一覧（models.py）

| テーブル | 用途 |
|----------|------|
| `courses` | 科目マスタ（name, classification, category, term, credits, faculty, senmon_group） |
| `course_instructors` | 科目↔教員の多対多（course_id, name, url） |
| `classification_orders` | 分類の表示順・親グループ設定 |
| `pending_reviews` | レビュー投稿（is_approved で承認管理） |
| `user_profiles` | LINEユーザーのプロフィール（氏名・学籍番号） |
| `user_activity` | LINEアクション統計（user_id, action, count） |
| `message_logs` | LINEメッセージ送受信ログ |
| `error_logs` | サーバーエラーログ |
| `push_subscriptions` | Web Push VAPID 購読情報 |
| `richmenu_taps` | リッチメニュークリックログ |
| `course_views` | 科目詳細の閲覧数 |
| `syllabus_courses` | 時間割マスタ（timetable_code で一意） |
| `course_slots` | 曜日・時限（syllabus_course_id, day_of_week, period） |
| `user_courses` | ユーザーの時間割登録（line_user_id, syllabus_course_id） |
| `timetable_profiles` | ユーザーの学部・学年設定 |
| `credit_requirements` | 単位要件定義（category_id, required_credits, label） |
| `category_courses` | 単位カテゴリ↔科目の紐付け |
| `user_seiseki_raw` | 成績表PDFの解析済みJSON（line_user_id で1件） |

### アーキテクチャ概要

**main.py の構成（単一ファイル）**

```
環境変数読み込み → LINE SDK初期化 → キャッシュ変数定義
→ FastAPI app 生成 → lifespan（init_db + prewarm + self-ping）
→ ルート定義（以下のグループ）
  - /callback          LINE Webhookエントリポイント
  - /submit, /api/*    レビューフォーム・LIFF用API
  - /liff/*            LIFFページ HTML返却
  - /r/{name}          リッチメニューリダイレクト（クリック計測付き）
  - /admin/*           管理画面（HMAC cookie認証）
  - /health            死活監視
```

**キャッシュ設計（モジュールレベルグローバル変数）**

- TTL 3600秒のインメモリキャッシュを複数保持
- `_get_*_cached()` → TTL切れ or 空のとき DB取得、それ以外はキャッシュ返却
- `_invalidate_*_cache()` → DB更新後に呼び出してキャッシュ即時無効化
- `_prewarm_caches()` → サーバー起動2秒後に全キャッシュをウォームアップ
- **注意**: 初期値は空コレクション（`{}`/`set()`）のため、truthy チェックでキャッシュヒット判定する

**LINE Bot フロー**

```
POST /callback → 署名検証 → parser.parse() → create_task(_process_events())
  → FollowEvent  : ウェルカムFlexMessage
  → MessageEvent : handle_message(text, user_id) → FlexMessage or TextMessage
  → PostbackEvent: handle_message(data, user_id)（科目一覧タップ等）
```

**管理画面認証**

- ログイン: `ADMIN_PASSWORD` と POST フォーム、`py_secrets.compare_digest` で比較
- トークン: `HMAC-SHA256(CHANNEL_SECRET + ADMIN_PASSWORD, "admin:{timestamp}")` をCookieに保存
- TTL: 4時間（`ADMIN_TOKEN_TTL = 4 * 3600`）

**非同期クエリのルール**

- 同一 `AsyncSession` では `asyncio.gather` による並行クエリ禁止（InterfaceError）
- 並行したい場合は各コルーチン内で `async with AsyncSessionLocal() as s:` を個別に開く

---

## 開発ワークフロー

- 作業は機能追加・バグ修正などの単位で小さく区切って進める。1つの作業が完了するごとに必ずgit commitする。
- コミットメッセージは「何を」「なぜ」変更したかが分かるように具体的に書く（例：「LINE Webhookの署名検証を追加。不正リクエストを拒否するため」のように、変更内容と理由をセットで記載）。
- 1コミットに複数の無関係な変更を混在させない。
- 新しいセッションで作業を再開する際は、まず `git log --oneline -20` と `git diff` でこれまでの変更内容を確認し、チャット履歴に頼らず現在の状態を把握すること。
- 作業途中で次にやるべきことが明確な場合は、コミットメッセージの末尾や別途TODOコメントに次のステップを簡潔に記録する。
