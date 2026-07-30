# DB設計書

PostgreSQL（Supabase）。ORMは SQLAlchemy 2.0（`models.py` が正）。テーブル作成・列追加は `database.py` の `init_db()`（`CREATE TABLE IF NOT EXISTS` 相当 + 後方互換用の `ALTER TABLE ... ADD COLUMN IF NOT EXISTS`）で冪等に行われる。新規テーブル追加時のみ `supabase/migrations/` にSQLを残す運用。

dev環境と本番環境は別々のSupabaseプロジェクト（別リージョン）。データの同期方法は [`DEPLOYMENT.md`](./DEPLOYMENT.md) を参照。

## ER概要（テキスト表現）

```
subjects ─┬─< course_sections >─┬─ instructors
          │                     │
          │                     ├─< syllabi
          │                     └─< reviews (RESTRICT)
          │                     └─< course_section_views
```

「A ─< B」は A:B = 1:多、「>─」は多:1（結合先）を表す。

## コアドメイン（科目・シラバス・レビュー）

### `subjects` — 科目マスタ

| 列 | 型 | NULL | 説明 |
|---|---|---|---|
| `id` | BigInteger PK | - | |
| `name` | Text | NOT NULL | 科目名。`@validates`で半角ローマ数字(I/II等)を全角(Ⅰ/Ⅱ)に自動正規化 |
| `reading` | Text | NULL可 | よみがな（pykakasiで自動生成。LINE bot一覧のあいうえお順分割に使用） |
| `faculty` | Text | NULL可 | 学部名のみ（複合文字列ではない） |
| `department` | Text | NOT NULL, デフォルト`""` | 学科名（学科の区別が無い学部・教養科目は空文字。NULL不可はUNIQUE制約でNULL同士を区別しないPostgresの挙動を避けるため） |
| `classification` | Text | NULL可 | 分類名（`display_orders(kind='classification')`の表示順で管理） |
| `category` | Text | NULL可 | 大分類（教養/専門等） |
| `sort_order` | Integer | NOT NULL, デフォルト0 | |
| `term_type` | Text | NULL可 | 開講区分（前期/後期/通年/各クォーター等）。「集中」は別次元（曜日時限側）なので原則ここには入らない |
| `credits` | Numeric(3,1) | NULL可 | 単位数 |

- UNIQUE制約: `(name, faculty, department)` — 学部（学科）をまたいだ同名科目（卒業研究等）を区別する。
- Index: `(faculty, classification)` — 教養教育院の分類絞り込み向け。`(classification)` — 管理画面のGROUP BY・完全一致検索向け。
- `name`/`faculty`単独のindexは意図的に作らない（上記複合indexの先頭列プレフィックスで代替できるため、冗長indexとして2026-07-21に削除済み）。

### `instructors` — 教員マスタ

| 列 | 型 | NULL | 説明 |
|---|---|---|---|
| `id` | BigInteger PK | - | |
| `name` | Text | NOT NULL, UNIQUE, index | `@validates`で全角/半角スペースを自動除去して正規化 |
| `sort_order` | Integer | NOT NULL, デフォルト0 | |

### `course_sections` — 科目×教員のセクション

| 列 | 型 | NULL | 説明 |
|---|---|---|---|
| `id` | BigInteger PK | - | |
| `subject_id` | BigInteger FK→subjects.id (CASCADE) | NOT NULL | |
| `instructor_id` | BigInteger FK→instructors.id (CASCADE), index | NOT NULL | |

- UNIQUE制約: `(subject_id, instructor_id)`（先頭列のためsubject_id単体検索もカバーし、単独indexは張らない）

### `syllabi` — シラバス（年度別）

| 列 | 型 | NULL | 説明 |
|---|---|---|---|
| `id` | BigInteger PK | - | |
| `course_section_id` | BigInteger FK→course_sections.id (CASCADE), index | NOT NULL | |
| `year` | Integer | NOT NULL | |
| `academic_term` | Text | NOT NULL | 「第1クォーター」〜「第4クォーター」「後期」「集中」等の自由文字列 |
| `timetable_code` | Text | NULL可, index | 神戸大学シラバスサイトの時間割コード。シラバスURLはこの列+`department`から`core.config.make_syllabus_url()`で**毎回動的生成**する（URL列は持たない） |
| `created_at` | DateTime(tz) | NOT NULL | |

- UNIQUE制約: `(course_section_id, year, academic_term)`
- `syllabi.department`列は2026-07-18に廃止済み（`subjects.faculty`との94%重複が判明したため。学科粒度が必要な場合は`subjects.department`を参照）。
- `syllabi.target_grades`/`subject_category`列は2026-07-30の大規模リニューアル（My時間割機能全廃止）で廃止済み。

### `reviews` — 投稿レビュー

| 列 | 型 | NULL | 説明 |
|---|---|---|---|
| `id` | BigInteger PK | - | |
| `course_section_id` | BigInteger FK→course_sections.id (**RESTRICT**), index | NOT NULL | 科目・教員の削除時にレビューがあると削除自体がDBエラーになる（誤ってレビューを巻き添え削除しない設計） |
| `content` | Text | NULL可 | コメント（最大500文字、アプリ側で切り詰め） |
| `rating` | Integer | NULL可 | 5段階評価 |
| `ease_rating` | Text | NULL可 | 楽単度（SS/S/A/B/C） |
| `grading_method` | Text | NULL可 | 評価方法（最大500文字） |
| `submitter_name` | Text | NULL可 | |
| `nickname` | Text | NULL可 | 表示用ニックネーム（最大30文字） |
| `student_id` | Text | NULL可 | |
| `academic_year` | Integer | NULL可 | 受講年度 |
| `selected_instructor` | Text | NULL可 | |
| `is_approved` | Boolean | NOT NULL, デフォルトfalse | 管理画面で承認するまで非公開 |
| `created_at` | DateTime(tz) | NOT NULL | |

> **投稿されたレビューは、ユーザーから絶対に消去しない**（CLAUDE.mdのデータ保護ルール）。科目の削除・変更・マージ等でレビューを巻き添えにしないこと。

### `course_section_views` — 科目セクションの閲覧数

| 列 | 型 | NULL | 説明 |
|---|---|---|---|
| `course_section_id` | BigInteger PK, FK→course_sections.id (CASCADE) | - | |
| `view_count` | Integer | NOT NULL, デフォルト0 | |
| `last_viewed_at` | DateTime(tz) | NOT NULL | |

## 共通・運用系

### `display_orders` — 表示順マスタ（汎用）

| 列 | 型 | NULL | 説明 |
|---|---|---|---|
| `id` | Integer PK | - | |
| `kind` | String(50) | NOT NULL | `classification`（分類）/ `faculty`（学部）等を区別 |
| `name` | String(100) | NOT NULL | |
| `sort_order` | Integer | NOT NULL, デフォルト0 | |
| `parent_group` | String(100) | NULL可 | 分類の親グループ等 |
| `faculty` | String(100) | NOT NULL, デフォルト`""` | |

- UNIQUE制約: `(kind, name, faculty)`

### `user_profiles` — LINEユーザーのプロフィール

| 列 | 型 | NULL | 説明 |
|---|---|---|---|
| `line_user_id` | String(64) PK | - | |
| `name` | String(100) | NOT NULL | |
| `student_id` | String(20) | NOT NULL | |
| `faculty` | Text | NULL可 | |
| `grade` | Integer | NULL可 | |
| `department` | Text | NULL可 | |
| `updated_at` | DateTime(tz) | NULL可 | |
| `created_at` | DateTime(tz) | NOT NULL | |

友だち追加時の会員登録で氏名・学籍番号・学部・学年・学科すべての入力が必須（`core.config.is_profile_complete()`で判定）。`share_token_version`列は2026-07-30の大規模リニューアル（My時間割機能全廃止）で廃止済み。

### `message_logs` — LINEメッセージ送受信ログ

| 列 | 型 | NULL | 説明 |
|---|---|---|---|
| `id` | Integer PK | - | |
| `user_id` | String(64), index | NOT NULL | |
| `direction` | String(8) | NOT NULL | |
| `message` | Text | NOT NULL | |
| `created_at` | DateTime(tz) | NOT NULL | |

### `user_activity` — LINEアクション統計

| 列 | 型 | NULL | 説明 |
|---|---|---|---|
| `id` | Integer PK | - | |
| `user_id` | String(64) | NOT NULL | 単独indexは張らない（UNIQUE制約の先頭列で代替） |
| `action` | String(200) | NOT NULL | |
| `count` | Integer | NOT NULL, デフォルト1 | |
| `last_at` | DateTime(tz) | NOT NULL | |

- UNIQUE制約: `(user_id, action)`

### `error_logs` — サーバーエラーログ

| 列 | 型 | NULL | 説明 |
|---|---|---|---|
| `id` | Integer PK | - | |
| `user_id` | String(64) | NULL可 | |
| `action` | String(200) | NULL可 | |
| `error_type` | String(100) | NOT NULL | |
| `error_message` | Text | NOT NULL | |
| `traceback` | Text | NOT NULL | |
| `created_at` | DateTime(tz) | NOT NULL | |

### `push_subscriptions` — Web Push VAPID購読情報

| 列 | 型 | NULL | 説明 |
|---|---|---|---|
| `id` | Integer PK | - | |
| `endpoint` | Text | NOT NULL, UNIQUE | |
| `p256dh` | String(200) | NOT NULL | |
| `auth` | String(100) | NOT NULL | |
| `created_at` | DateTime(tz) | NOT NULL | |

### `richmenu_taps` — リッチメニュークリックログ

| 列 | 型 | NULL | 説明 |
|---|---|---|---|
| `id` | Integer PK | - | |
| `button` | String(50), index | NOT NULL | |
| `tapped_at` | DateTime(tz) | NOT NULL | |

## 外部キーの削除挙動（CASCADE / RESTRICT）まとめ

| 親テーブル | 子テーブル | 挙動 | 理由 |
|---|---|---|---|
| `subjects` | `course_sections` | CASCADE | 科目削除で紐づくセクションも削除 |
| `instructors` | `course_sections` | CASCADE | |
| `course_sections` | `syllabi` | CASCADE | |
| `course_sections` | `course_section_views` | CASCADE | |
| `course_sections` | `reviews` | **RESTRICT** | レビューを持つセクションは削除できない（データ保護ルール） |

## dev / 本番のDB同期対象

本番デプロイ時に `programing files/sync_db_to_prod.py` でdev→本番へ同期する4テーブル（自然キーでUPSERT、本番専用行は削除するが`reviews`/`syllabi`が紐づく行は`KEEP`保護）:

- `display_orders`
- `subjects`
- `instructors`
- `course_sections`

**絶対に同期しない**（ユーザーデータ・ログ・レビュー・利用履歴）: `reviews` / `message_logs` / `user_profiles` / `user_activity` / `error_logs` / `push_subscriptions` / `richmenu_taps` / `syllabi`

詳細な同期手順は [`DEPLOYMENT.md`](./DEPLOYMENT.md) を参照。

## 2026-07-30 大規模リニューアルで廃止したテーブル・列

My時間割・単位チェッカー（成績表PDF解析）・履修登録上限CAP制・必修科目自動登録の4機能を全廃止し、以下を`database.py`の`init_db()`で`DROP TABLE`/`DROP COLUMN`済み:

- テーブル: `user_syllabi` / `user_custom_courses` / `required_subjects` / `registration_caps` / `credit_requirements` / `subject_credit_categories` / `user_seiseki_raw` / `schedules`
- 列: `subjects.hide_from_timetable` / `subjects.senmon_group` / `user_profiles.share_token_version` / `syllabi.target_grades` / `syllabi.subject_category`

`reviews`テーブルおよびそこへ辿り着くFKチェーン（`course_sections`/`subjects`/`instructors`/`syllabi`）はこの削除の対象外で、レビューデータは一切失われていない。
