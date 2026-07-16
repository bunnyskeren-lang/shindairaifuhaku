# 返信ルール

**必ず日本語で返信すること。**

**迷ったらコードを広範囲に読む前にユーザーに確認を取ること。**

**作業がひと区切りついたら、ユーザーに指摘される前に自動でメモリ（永続記憶システム）を更新すること。** 新しい機能・仕様決定・DBスキーマ変更・ユーザーの指示や好みなど、次回以降のセッションに引き継ぐべき情報は都度メモリファイルに保存し、MEMORY.mdの索引も更新する。

**このCLAUDE.mdを更新したら、必ず `AGENTS.md`（他AIツール用の同内容ファイル）にも同じ内容を反映すること。** AGENTS.mdはCLAUDE.mdの完全なコピーとして維持する（`cp CLAUDE.md AGENTS.md` でよい）。

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
- dev環境（shindairaifuhaku-1.onrender.com）に関するpush・デプロイ操作は、確認を取らず自由に実行してよい
  - `git push origin dev`（devブランチへの通常push）
  - `git push origin dev:shindairaifuhaku-dev`（dev環境へのデプロイ）
  - `python setup_richmenu.py --env dev`（devリッチメニュー更新）
  - その他 dev サービス・dev DB のみに影響する操作全般
- `git push` の push先が `origin main` または `origin shindairaifuhaku`（本番相当ブランチ）の場合は必ず確認を取ること

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
- `programing files/models.py` はルートの `models.py` とは別定義（スクリプト群専用）。scriptsが触るテーブル（`subjects`/`instructors`/`course_sections`/`syllabi`/`schedules`等）の列を追加・変更・削除したら、`programing files/models.py` 側の対応するカラム定義も忘れずに確認・更新すること

### 科目名を触るときのルール

科目名の正規化（ローマ数字表記統一）・LINE bot一覧のバリアント統合表示など、詳細は `docs/SUBJECT_NAME_RULES.md` を参照。`models.py` でこの領域を変更したら同ドキュメントも更新すること。

### LINE bot科目一覧の表示件数について（新学部・大量科目追加時に意識すること）

LINE botの1回の返信には上限（40バブル≒240科目、`line_bot/handler.py`の`_split_to_bubbles`/`messages[:5]`参照）がある。`classification`が学部単位で1つにまとまっている学部（`import_syllabus.py`で分類を細分化しなかった場合）は、科目数が閾値（`_ALPHA_SPLIT_THRESHOLD`=48件）を超えると`handle_course_list()`が自動でよみがな順の均等分割メニューを挟むため、**新しい学部・大量の科目を追加する際に明示的な分類分け作業は不要**（2026-07-15、国際人間科学部1005件のうち76%が上限超過で非表示になっていたバグの修正で導入）。

分割ラベルはよみがな（`subjects.reading`）の先頭文字を使うため、新規科目追加時に`reading`が空文字のまま残らないよう注意すること（`import_syllabus.py`は新規作成時に`reading=""`をプレースホルダで入れ、`database.py`の`init_db()`起動時バックフィルが`WHERE reading IS NULL OR reading = ''`で毎回自動生成する設計。バックフィル条件を`IS NULL`だけに戻すと空文字のまま埋まらなくなるので変更しないこと）。

## データ保護ルール

- **投稿されたレビュー（reviews テーブル）は、ユーザーから明示的な削除指示がない限り絶対に消去しないこと**
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

同期対象（この6テーブルのみ）：
- `credit_requirements`
- `display_orders`
- `subjects`
- `instructors`
- `course_sections`
- `subject_credit_categories`

`credit_requirements`は2026-07-16に同期対象へ追加した。新学部の単位要件をdevへ追加しただけでは本番に反映されず、`subject_credit_categories`の同期がFK違反で失敗する事故があったため（`sync_db_to_prod.py`ではUPSERTを`subject_credit_categories`より先に実行し、本番のみに存在するカテゴリの削除は`subject_credit_categories`洗い替え後に行う）。

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

`sync_db_to_prod.py`はUPSERTだけでなく、本番のみに存在する行（devで削除・変更済みの行）も削除する（2026-07-16追加）。ただし`display_orders`/`credit_requirements`以外（`subjects`/`instructors`/`course_sections`）は、`course_sections`経由で`syllabi`（時間割登録データ）や`reviews`が紐づく場合は削除せず、`KEEP(要確認)`としてログ表示するのみに留める。このログが出た場合は、同名科目のfaculty違いによる重複などが原因のことが多いので、手動でreviewsのcourse_section_id付け替え→旧行削除の対応が必要になる。

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

**実際の接続文字列（パスワード含む）はこのファイルには記載しない。** 以下を参照すること：
- Render各サービスの Environment タブ（`DATABASE_URL`）
- ローカル: `programing files/.env`（本番用 `DATABASE_URL` / dev用 `DEV_DATABASE_URL`）、`programing files/.env.dev`（dev用 `DATABASE_URL`）

| 環境 | Supabaseプロジェクト（リージョン） |
|---|---|
| **dev** | `ofsvkcptzngbsxtdbqzj`（aws-1-ap-northeast-1） |
| **本番** | `sagubqrhjnzrtcvlmzqy`（aws-1-ap-northeast-2） |

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
- 単位数（`subjects.credits`）のみ、ラベル側の閉じタグが `</td>` ではなく `</th>` になっている
  （例: `<td class="gaibu-syllabus-kihon">単位数</th><td>2.0</td>`）。正規表現でラベル直後の
  閉じタグを固定せず、次の `<td>` を拾う書き方にすること
- スクレイピングスクリプト: `programing files/fetch_syllabus_info.py`
  - `--env dev` で dev DB に書き込み、`--force` で既取得分も上書き
  - 0.3秒スリープ/件、バッチ単位（`Syllabus`は20件、`Subject`は40件）でセッションを切り替えてコミット・失敗時リトライ
  - 単位数（`subjects.credits`）は `run_credits()` が担当。`syllabi`レコードを持たない科目（前期のみ開講等）も
    `course_sections.syllabus_url` 経由で辿るため、`run()`（target_grades/subject_category）とは独立して全件処理する
  - `import_syllabus.py`の実行時に自動で呼ばれるため、単位数・開講年次の取得だけを目的に
    単体で実行する必要は通常ない（既存分の再取得や`--force`上書きをしたい場合のみ単体実行する）

### 時間割DBテーブル構成（新スキーマ）

| テーブル | 用途 |
|---|---|
| `syllabi` | 時間割マスタ（course_section_id, timetable_code, term, target_grades, subject_category） |
| `schedules` | 曜日・時限（syllabus_id, day_of_week, period） |
| `user_syllabi` | ユーザーの登録科目（line_user_id, syllabus_id） |

ユーザーの学部・学年・学科プロフィールは `user_profiles`（faculty/grade/department列）で管理する（旧`timetable_profiles`は2026-07-06に統合・廃止）。

インポートスクリプト: `programing files/import_syllabus.py`
- `--also-courses` を付けると `subjects`/`instructors`/`course_sections`（LINE bot用）にも登録
- `--classification` / `--faculty` で分類・学部名を指定
- **専門科目（教養教育院以外）は `--classification` 未指定でも「未分類」にはならない**: `faculty`（`--faculty`指定 or シラバスデータの所属列から自動推定）を元に `{学部名}専門科目`（例:「経営学部専門科目」）を自動で分類名に設定する。この分類が `display_orders`（kind='classification'）に無ければ `parent_group=学部名` で自動作成し、LINE bot「専門科目」→「{学部名} ▶」の中に自動的に表示される（分類名を学部名そのものにすると`parent_group`と文字列衝突しドリルダウンから選べなくなるため、必ず「専門科目」等の接尾語を付けること）

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
| HTTPクライアント | httpx（自己ping・DBバックアップのSupabase Storage API呼び出し） |

### ディレクトリ構成

main.py は 2026年7月に単一3800行ファイルから「core / line_bot / routers」パッケージ構成へリファクタリング済み。main.py 自体は app 生成・lifespan・include_router のみの薄いエントリポイント（約90行）。

```
shindairaifuhaku/          ← Renderがデプロイするルート
├── main.py                ← 薄いエントリポイント（app生成・CORS・例外ハンドラ・lifespan・include_router）
├── models.py               ← SQLAlchemy ORMモデル定義（新スキーマ）
├── database.py             ← DBエンジン生成・init_db()（マイグレーション含む）
├── requirements.txt
├── Procfile / runtime.txt
├── core/                   ← 横断的関心事（ルーター間で共有）
│   ├── config.py            ← 環境変数・定数・シラバスURL生成・よみがな変換
│   ├── security.py          ← 管理者トークン発行/検証・LINE署名検証・check_admin依存関数
│   ├── cache.py              ← 全インメモリキャッシュ・invalidate/prewarm関数（rawなdictは外部公開しない）
│   ├── activity_log.py        ← エラーログ・メッセージログ保存
│   ├── line_client.py          ← LINE APIクライアント・reply送信・自己ping
│   ├── push.py                  ← Web Push (VAPID) 通知送信
│   ├── prewarm.py                ← 起動時キャッシュウォームアップの統合
│   ├── templates.py               ← Jinja2Templates・jstフィルタ
│   ├── seiseki.py                  ← 成績表PDFの単位分類ロジック（経営学部専門科目群判定など）
│   ├── backup.py                    ← DB自動バックアップ（BACKUP_INTERVAL_HOURS間隔、既定1時間ごとに全テーブルダンプ→Supabase Storageへアップロード、BACKUP_ENABLED=trueの時のみ動作）
│   └── binran_discrepancies.py       ← `docs/学生便覧2026/ver2/IMPLEMENTATION_CANDIDATES.md`をパースし学部/ステータスで一覧化（DB非経由、ファイル読み込みのみ）
├── line_bot/                ← LINE Bot応答ロジック
│   ├── flex_builders.py      ← FlexMessage/Bubble生成関数群
│   └── handler.py             ← handle_message・handle_course_list・process_events（Webhookイベント処理）
├── routers/                 ← FastAPI APIRouter（URLプレフィックス単位）
│   ├── webhook.py             ← POST /callback（LINE Webhook）
│   ├── health.py               ← /health
│   ├── pages.py                  ← /, /register（会員登録必須ページ）, /privacy, /sw.js, /liff/course, /liff/timetable
│   ├── richmenu.py                ← /r/{name}（クリック計測付きリダイレクト）
│   ├── liff_api.py                 ← /api/courses, /api/preload, /api/instructors, /api/autofill, /api/faculties, /submit, /api/course/{id}
│   ├── timetable_api.py             ← /api/timetable/*
│   ├── seiseki_api.py                ← /api/parse_seiseki 等（成績PDF解析）
│   └── admin/                         ← /admin/* をURLプレフィックス単位でさらに分割
│       ├── auth.py                     ← /admin/login, /admin/logout
│       ├── dashboard.py                 ← /admin（メッセージログ）, /admin/push/subscribe
│       ├── courses.py                    ← /admin/courses*（科目・教員・分類CRUD、教員/学部/分類の並び替え）
│       ├── reviews.py                     ← /admin/reviews*
│       ├── users_errors.py                 ← /admin/users, /admin/errors, /admin/activity
│       ├── stats.py                         ← /admin/richmenu-stats, /admin/usage-stats
│       ├── timetable_check.py                ← /admin/timetable/check
│       ├── credit_requirements.py             ← /admin/keiei*, /admin/sysinfo*, /admin/credit_requirements/group/move（グループ並び替え）
│       ├── registration_caps.py                ← /admin/registration_caps*（学部・学科・年度別CAP＝履修登録上限単位数のCRUD）
│       └── binran_discrepancies.py               ← /admin/binran_discrepancies（学生便覧ver2×DBの齟齬一覧、`core/binran_discrepancies.py`のパーサー参照）
├── templates/
│   ├── admin/              ← courses / reviews / keiei / sysinfo / logs / users / errors /
│   │                          activity / usage_stats / richmenu / timetable_check / registration_caps / binran_discrepancies / login / base 等
│   ├── liff/
│   │   ├── course.html    ← 科目詳細・レビュー閲覧（LIFFページ）
│   │   └── timetable.html ← マイ時間割（LIFFページ）
│   ├── form_index.html    ← レビュー投稿フォーム
│   ├── form_success.html
│   ├── form_error.html
│   └── privacy.html
├── data/                  ← シラバス取り込み用テキストファイル（曜日別）
├── supabase/migrations/   ← 新スキーマ移行SQL
├── docs/                  ← ドキュメント類（.gitignore対象。ただし`学生便覧2026/ver2/IMPLEMENTATION_CANDIDATES.md`のみ`docs/*`→個別ディレクトリ・ファイル単位の`!`否定で例外的にgit管理下・デプロイ対象。管理画面`/admin/binran_discrepancies`が本番/dev環境で読む唯一のdocs配下ファイルのため。同ファイルを移動・改名する際は`.gitignore`の否定パターンも忘れず追従させること）
├── backups/               ← download_prod_backup.pyのダウンロード先（.gitignore対象、env別サブフォルダ）
└── programing files/      ← 運用・整備用スクリプト群（Renderにはデプロイされない）
    ├── import_syllabus.py         ← 時間割データをDB投入
    ├── fetch_syllabus_info.py     ← シラバスページをスクレイピング
    ├── import_kyoyo_courses.py    ← 教養科目インポート
    ├── setup_richmenu.py          ← LINEリッチメニュー設定（--env dev/prod）
    ├── sync_db_to_prod.py         ← dev→本番DBの5テーブル同期
    ├── download_prod_backup.py    ← Supabase Storage上のDBバックアップをローカルbackups/へ差分ダウンロード（--env dev/prod）
    ├── models.py / database.py    ← スクリプト群専用のDBアクセス層（ルートのmodels.pyとは別定義）
    ├── assets/richmenu.png        ← リッチメニュー原本画像（setup_richmenu.pyのデフォルト画像、git管理下）
    └── .env / .env.dev            ← 環境変数（本番・dev）
```

### DBテーブル一覧（models.py・新スキーマ）

コアドメイン（科目・シラバス・レビュー）:

| テーブル | 用途 |
|----------|------|
| `subjects` | 科目マスタ（name, faculty, classification, term, credits 等） |
| `instructors` | 教員マスタ |
| `course_sections` | 科目×教員のセクション（syllabus_url 等） |
| `syllabi` | シラバス（年度・クォーター・時間割コード・対象学年・科目分類） |
| `schedules` | 曜日・時限・教室 |
| `reviews` | 投稿レビュー（`is_approved` で承認管理） |
| `course_section_views` | 科目セクションの閲覧数 |
| `user_syllabi` | ユーザーの時間割登録 |
| `subject_credit_categories` | 科目↔単位カテゴリの紐付け |

共通・運用系:

| テーブル | 用途 |
|----------|------|
| `display_orders` | 表示順マスタ（汎用、`kind`列で対象種別を区別。`classification`=分類の表示順・親グループ、`faculty`=学部の表示順、`credit_requirement_group`=単位要件グループの表示順（`faculty`列で経営学部/システム情報学部を区別）） |
| `credit_requirements` | 単位要件定義（学部別、category_id, required_credits, label） |
| `registration_caps` | 履修登録上限単位数（CAP制、faculty/department/yearごと。departmentがNULLなら学部共通値） |
| `user_profiles` | LINEユーザーのプロフィール（氏名・学籍番号・学部・学年・学科。友だち追加時の会員登録で必須入力、旧`timetable_profiles`を統合済み） |
| `user_seiseki_raw` | 成績表PDFの解析済みJSON（line_user_id で1件） |
| `message_logs` | LINEメッセージ送受信ログ |
| `user_activity` | LINEアクション統計（user_id, action, count） |
| `error_logs` | サーバーエラーログ |
| `push_subscriptions` | Web Push VAPID 購読情報 |
| `richmenu_taps` | リッチメニュークリックログ |

### アーキテクチャ概要

**main.py の構成（薄いエントリポイント）**

```
core.line_client / core.prewarm 等のimport
→ FastAPI app 生成 → lifespan（init_db + prewarm + self-ping、core.line_client.startup/shutdown）
→ 例外ハンドラ登録（core.activity_log.save_error_log でエラーログ保存）
→ include_router（webhook / health / pages / richmenu / liff_api / timetable_api / seiseki_api /
                  admin.auth / admin.dashboard / admin.courses / admin.reviews /
                  admin.users_errors / admin.stats / admin.timetable_check / admin.credit_requirements）
```

**キャッシュ設計（core/cache.py に集約したモジュールレベルグローバル変数）**

- TTL 3600秒のインメモリキャッシュを複数保持。他モジュールは必ず `cache.get_*`/`cache.set_*`/`cache.invalidate_*` の関数経由でアクセスし、rawなdictを直接importしない（invalidate時に `global` で再代入されるため、直import すると古い参照を掴んだままになる）
- `cache.get_*_cached()` → TTL切れ or 空のとき DB取得、それ以外はキャッシュ返却
- `cache.invalidate_*_cache()` → DB更新後に呼び出してキャッシュ即時無効化
- `core.prewarm.prewarm_caches()` → サーバー起動0.5秒後に全キャッシュをウォームアップ（`core.cache.warm_query_caches()` → `line_bot.flex_builders.prewarm_flex_cache()` の順）
- **注意**: 初期値は空コレクション（`{}`/`set()`）のため、truthy チェックでキャッシュヒット判定する

**LINE Bot フロー**

```
POST /callback（routers/webhook.py） → core.security.verify_line_signature
  → core.line_client.parser.parse() → create_task(line_bot.handler.process_events())
  → FollowEvent  : ウェルカムFlexMessage
  → MessageEvent : handle_message(text, user_id) → FlexMessage or TextMessage
  → PostbackEvent: handle_message(data, user_id)（科目一覧タップ等）
返信は core.line_client.reply(reply_token, messages) 経由（生の _line_api を各所で触らない）
```

**管理画面認証**

- ログイン: `routers/admin/auth.py`、`ADMIN_PASSWORD` と POST フォームを `py_secrets.compare_digest` で比較
- トークン: `core.security.make_admin_token()` が `HMAC-SHA256(CHANNEL_SECRET + ADMIN_PASSWORD, "admin:{timestamp}")` を生成しCookieに保存
- TTL: 4時間（`core.config.ADMIN_TOKEN_TTL`）、`core.security.check_admin` を全 `/admin/*` ルートに `Depends()` で付与

**非同期クエリのルール**

- 同一 `AsyncSession` では `asyncio.gather` による並行クエリ禁止（InterfaceError）
- 並行したい場合は各コルーチン内で `async with AsyncSessionLocal() as s:` を個別に開く

**DB自動バックアップ（`core/backup.py`）**

- Supabase Freeプランには自動バックアップが無く、ローカル開発環境はNetwork Restrictionsにより本番DBへ直接接続できない。そのため「本番DBに到達できる本番アプリ自身」が定期バックアップを生成する設計にした
- `backup_loop()`（`self_ping()`と同じ無限ループ+`asyncio.sleep`パターン、`main.py`のlifespanで`asyncio.create_task`）が起動30秒後から`BACKUP_INTERVAL_HOURS`（既定1時間）間隔で`dump_all_tables_to_sql()`（`Base.metadata.sorted_tables`のFK依存順で全テーブルをSELECTしINSERT文形式のSQLを生成・gzip圧縮）を実行し、`upload_backup_to_storage()`でSupabase Storageの`BACKUP_BUCKET`（既定`db-backups`）へアップロード、`BACKUP_RETENTION_DAYS`（既定15日）より古い世代を削除する
- `BACKUP_ENABLED`が`true`でない環境では`backup_loop()`は即returnして何もしない（既定false。dev/本番それぞれ明示的に有効化するまで安全）
- ローカルPCへの取り込みは`programing files/download_prod_backup.py --env dev|prod`で、Supabase Storageからまだ無いファイルだけを`backups/{env}/`へ差分ダウンロードする（Windowsタスクスケジューラ等で定期実行する想定）
- 必要な環境変数: `SUPABASE_URL` / `SUPABASE_SERVICE_ROLE_KEY` / `BACKUP_BUCKET` / `BACKUP_ENABLED` / `BACKUP_RETENTION_DAYS` / `BACKUP_INTERVAL_HOURS`（dev/本番でSupabaseプロジェクトが別なので値も別々に設定する）
- 復元は「`init_db()`で空スキーマを作ってから、ダウンロードした`.sql.gz`を解凍してINSERT文を流し込む」想定（CREATE TABLE等のスキーマDDLは含まない）

---

## 開発ワークフロー

- 作業は機能追加・バグ修正などの単位で小さく区切って進める。1つの作業が完了するごとに必ずgit commitする。
- コミットメッセージは「何を」「なぜ」変更したかが分かるように具体的に書く（例：「LINE Webhookの署名検証を追加。不正リクエストを拒否するため」のように、変更内容と理由をセットで記載）。
- 1コミットに複数の無関係な変更を混在させない。
- 新しいセッションで作業を再開する際は、まず `git log --oneline -20` と `git diff` でこれまでの変更内容を確認し、チャット履歴に頼らず現在の状態を把握すること。
- 作業途中で次にやるべきことが明確な場合は、コミットメッセージの末尾や別途TODOコメントに次のステップを簡潔に記録する。
