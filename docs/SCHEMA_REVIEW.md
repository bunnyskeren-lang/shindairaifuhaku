# スキーマレビュー（調査のみ・変更なし）

調査日: 2026-07-15（2026-06-27版の全面書き直し）／テーブル一覧セクションは2026-07-18追記
対象: 現行スキーマ（`models.py`、`database.py` の `init_db()`、各 `routers/`・`core/`・`line_bot/` のクエリパターン）

> **注意**: このドキュメントは基本的に調査・記録のみ。ALTER TABLE 等の実施は行っていない。
> 2026-06-27時点の旧スキーマ（`courses`/`course_instructors`/`pending_reviews`/`syllabus_courses`等）は
> 2026-07-06のスキーマ移行で完全に廃止済み。旧内容は git 履歴（このファイルの過去版）を参照。
> **例外**: `course_sections.syllabus_url` は2026-07-18に本ドキュメントの指摘（P2「年度またぎURL変更を表現できない」）を
> 踏まえて実際に廃止し、`syllabi.timetable_code`/`department`からの動的生成に変更済み（詳細は該当セクション参照）。

---

## テーブル一覧（カラム説明・親子関係）

出典: `models.py`（2026-07-18時点、全21テーブル）。以下、コアドメイン→単位/CAP系→ユーザー系→ログ/運用系の順に記載する。

### コアドメイン（科目・シラバス・レビュー）

#### subjects（科目マスタ）— 親: なし（ルート）
| カラム | 型 | 説明 |
|---|---|---|
| id | BigInteger PK | |
| name | Text, index | 科目名。`normalize_subject_name()`でローマ数字表記(Ⅰ/Ⅱ等)を自動統一 |
| reading | Text, nullable | よみがな（pykakasi自動生成、LINE bot一覧の五十音分割に使用） |
| faculty | Text, nullable, index | 学部名。教養科目は`教養教育院`、共通専門基礎科目は`NULL`のケースあり |
| classification | Text, nullable | 分類名（`display_orders`のkind='classification'の`name`と対応、LINE bot一覧の分類分け用） |
| category | Text, nullable | 「教養」/「専門」の大分類 |
| senmon_group | Text, nullable | 経営学部等の専門科目群（第1群〜等）判定結果 |
| sort_order | Integer, default 0 | 表示順 |
| term_type | Text, nullable | 開講期区分 |
| credits | Numeric(3,1), nullable | 単位数 |
| hide_from_timetable | Boolean, default false | 時間割の科目選択一覧から非表示にするフラグ |

UNIQUE(name, faculty)。子テーブル: `course_sections`（CASCADE）、`subject_credit_categories`（CASCADE）、`required_subjects`（CASCADE）。

#### instructors（教員マスタ）— 親: なし（ルート）
| カラム | 型 | 説明 |
|---|---|---|
| id | BigInteger PK | |
| name | Text, UNIQUE, index | 教員名。`normalize_instructor_name()`で空白除去等を自動正規化 |
| sort_order | Integer, default 0 | 表示順 |

子テーブル: `course_sections`（CASCADE）。

#### course_sections（科目×教員のセクション）— 親: subjects, instructors
| カラム | 型 | 説明 |
|---|---|---|
| id | BigInteger PK | |
| subject_id | BigInteger FK→subjects.id (CASCADE), index | |
| instructor_id | BigInteger FK→instructors.id (CASCADE), index | |

UNIQUE(subject_id, instructor_id)。子テーブル: `syllabi`（CASCADE）、`reviews`（**RESTRICT**）、`course_section_views`（CASCADE）。
シラバスURLは2026-07-18に本テーブルの列（`syllabus_url`）から`syllabi`側の動的生成に移行済み（後述）。

#### syllabi（時間割マスタ・年度別シラバス）— 親: course_sections
| カラム | 型 | 説明 |
|---|---|---|
| id | BigInteger PK | |
| course_section_id | BigInteger FK→course_sections.id (CASCADE), index | |
| year | Integer | 年度 |
| academic_term | Text | 開講期（旧`quarter`から2026-07頃リネーム） |
| timetable_code | Text, nullable, index | 時間割コード。シラバスURLは列を持たず、`core.config.make_syllabus_url(timetable_code, department)`で毎回動的生成する |
| target_grades | Text, nullable | 開講年次（対象学年） |
| subject_category | Text, nullable | 科目分類（シラバスページ由来） |
| department | Text, nullable | 所属学科（シラバスページの所属列）。シラバスURL生成の第2引数にも使う |
| created_at | DateTime(tz) | |

UNIQUE(course_section_id, year, academic_term)。子テーブル: `schedules`（CASCADE）、`user_syllabi`（CASCADE）。

#### schedules（曜日・時限）— 親: syllabi
| カラム | 型 | 説明 |
|---|---|---|
| id | BigInteger PK | |
| syllabus_id | BigInteger FK→syllabi.id (CASCADE), index | |
| day_of_week | Text | 月/火/水/木/金/土/日/集（集中講義） |
| period | Integer, nullable | 時限（1〜6想定、集中講義はNULL） |
| created_at | DateTime(tz) | |

UNIQUE(syllabus_id, day_of_week, period)。子テーブル: なし。

#### reviews（投稿レビュー）— 親: course_sections
| カラム | 型 | 説明 |
|---|---|---|
| id | BigInteger PK | |
| course_section_id | BigInteger FK→course_sections.id (**RESTRICT**), index | 科目削除時もレビューがあれば削除自体をDBが拒否 |
| content | Text, nullable | 感想本文 |
| rating | Integer, nullable | 評価（1〜5想定） |
| ease_rating | Text, nullable | 楽単度（SS/S/A/B/C想定） |
| grading_method | Text, nullable | 評価方法 |
| submitter_name | Text, nullable | 投稿者氏名（管理画面のみ表示） |
| nickname | Text, nullable | 表示用ニックネーム |
| student_id | Text, nullable | 学籍番号 |
| academic_year | Integer, nullable | 受講年度 |
| selected_instructor | Text, nullable | 投稿時に選択した教員名（表記ゆれ吸収用） |
| is_approved | Boolean, default false | 管理画面での承認フラグ。承認済みのみLIFF等で公開 |
| created_at | DateTime(tz) | |

**絶対にユーザーから消去しないテーブル**（CLAUDE.md参照）。子テーブル: なし。

#### course_section_views（科目セクション閲覧数）— 親: course_sections
| カラム | 型 | 説明 |
|---|---|---|
| course_section_id | BigInteger PK, FK→course_sections.id (CASCADE) | |
| view_count | Integer, default 0 | 閲覧数 |
| last_viewed_at | DateTime(tz) | |

子テーブル: なし。

### 単位チェッカー・CAP系

#### credit_requirements（単位要件定義）— 親: なし（ルート）
| カラム | 型 | 説明 |
|---|---|---|
| category_id | String(50) PK | 単位区分ID |
| label | String(100) | 表示名 |
| group_name | String(50) | グループ名 |
| sort_order | Integer | 表示順 |
| required_credits | Integer | 必要単位数 |
| note | Text, nullable | 注記（対象科目の内訳等） |
| faculty | String(100), default '経営学部' | 学部名 |
| department | Text, nullable | 学科名 |
| combined_of | JSONB, nullable | 合算対象の`category_id`配列（他行の`category_id`をFKなしで参照） |
| max_credits | Integer, nullable | 算入上限単位数 |

子テーブル: `subject_credit_categories`（`category_id`へのFK、ondelete未指定＝RESTRICT相当）。

#### subject_credit_categories（科目↔単位カテゴリ紐付け）— 親: subjects, credit_requirements
| カラム | 型 | 説明 |
|---|---|---|
| id | BigInteger PK | |
| subject_id | BigInteger FK→subjects.id (CASCADE), index | |
| category_id | String(50) FK→credit_requirements.category_id, index | ondelete未指定（RESTRICT相当） |
| credits | Numeric(3,1), default 2.0 | この区分としてカウントする単位数 |

UNIQUE(subject_id, category_id)。子テーブル: なし。

#### registration_caps（履修登録上限単位数・CAP制）— 親: なし（ルート）
| カラム | 型 | 説明 |
|---|---|---|
| id | BigInteger PK | |
| faculty | Text, index | |
| department | Text, nullable | NULLならその学部の学科共通値 |
| year | Integer | 学年 |
| max_credits | Integer | 上限単位数 |
| created_at | DateTime(tz) | |

UNIQUE(faculty, department, year)。子テーブル: なし。

#### required_subjects（学部・学科・学年別 必修科目マスタ）— 親: subjects
| カラム | 型 | 説明 |
|---|---|---|
| id | BigInteger PK | |
| faculty | Text, index | |
| department | Text | |
| grade | Integer | 対象学年 |
| subject_id | BigInteger FK→subjects.id (CASCADE), index | |
| student_id_parity | String(4), nullable | 学籍番号末尾の偶奇クラス分け（NULLなら全員対象） |
| note | Text, nullable | |
| created_at | DateTime(tz) | |

UNIQUE(faculty, department, grade, subject_id)。時間割登録時に`user_syllabi`へ自動登録する対象を管理。子テーブル: なし。

### ユーザー系

#### user_profiles（LINEユーザープロフィール）— 親: なし（ルート、line_user_idが実質的な軸）
| カラム | 型 | 説明 |
|---|---|---|
| line_user_id | String(64) PK | |
| name | String(100) | 氏名 |
| student_id | String(20), UNIQUE | 学籍番号 |
| faculty | Text, nullable | |
| grade | Integer, nullable | |
| department | Text, nullable | |
| share_token_version | Integer, default 0 | マイ時間割共有リンクの世代番号。「共有を停止する」でインクリメントし旧リンクを無効化 |
| created_at | DateTime(tz) | |
| updated_at | DateTime(tz), nullable | トリガーで自動更新 |

子テーブル: なし（`line_user_id`はFKで参照されていない。`user_syllabi`等との紐付けはアプリ層のみ、詳細は「横断的な問題点」参照）。

#### user_syllabi（ユーザーの時間割登録）— 親: syllabi
| カラム | 型 | 説明 |
|---|---|---|
| id | BigInteger PK | |
| line_user_id | String(64), index | `user_profiles`へのFKなし |
| syllabus_id | BigInteger FK→syllabi.id (CASCADE), index | |
| classroom | Text, nullable | 教室（ユーザー入力） |
| created_at | DateTime(tz) | |

UNIQUE(line_user_id, syllabus_id)。子テーブル: なし。

#### user_custom_courses（手動追加科目）— 親: なし（シラバスマスタ外の個人科目）
| カラム | 型 | 説明 |
|---|---|---|
| id | BigInteger PK | |
| line_user_id | String(64), index | `user_profiles`へのFKなし |
| name | Text | 科目名 |
| instructor | Text, nullable | |
| classification | Text, nullable | `credit_requirements.category_id`と一致させる想定（FKなし） |
| credits | Integer, default 2 | |
| year | Integer | |
| day_of_week | Text | |
| period | Integer | |
| created_at | DateTime(tz) | |

子テーブル: なし。

#### user_seiseki_raw（成績表PDF解析結果）— 親: なし（line_user_idが軸）
| カラム | 型 | 説明 |
|---|---|---|
| line_user_id | String(64) PK | `user_profiles`へのFKなし |
| raw_json | JSONB | 解析済み成績データ |
| gpa | Float, nullable | |
| updated_at | DateTime(tz) | トリガーで自動更新 |

子テーブル: なし。

### 共通・運用系

#### display_orders（表示順マスタ・汎用）— 親: なし
| カラム | 型 | 説明 |
|---|---|---|
| id | Integer PK | |
| kind | String(50) | `classification`/`faculty`/`credit_requirement_group`のいずれか（CHECK制約なし・アプリ側規約） |
| name | String(100) | 対象名 |
| sort_order | Integer, default 0 | |
| parent_group | String(100), nullable | 同テーブルの`name`を自己参照する想定（FKなし） |
| faculty | String(100), default '' | kind='credit_requirement_group'の場合の学部区別に使用 |

UNIQUE(kind, name, faculty)。子テーブル: なし。

#### message_logs / error_logs / user_activity / richmenu_taps / push_subscriptions（ログ・運用系）— いずれも親なし
| テーブル | 主なカラム | 説明 |
|---|---|---|
| message_logs | id PK, user_id(index), direction(CHECK: in/out), message, created_at(index) | LINEメッセージ送受信ログ |
| error_logs | id PK, user_id, action, error_type, error_message, traceback, created_at(index) | サーバーエラーログ |
| user_activity | id PK, user_id(index), action, count, last_at | UNIQUE(user_id, action)。LINEアクション統計 |
| richmenu_taps | id PK, button(index), tapped_at | リッチメニュークリックログ |
| push_subscriptions | id PK, endpoint(UNIQUE), p256dh, auth, created_at | Web Push購読情報。`line_user_id`列は一度追加後DROP済み（全員一斉配信のみのため紐付け無し） |

いずれも子テーブルなし。

### 親子関係サマリ（FK/ON DELETE一覧）

```
subjects ──CASCADE──> course_sections ──CASCADE──> syllabi ──CASCADE──> schedules
subjects ──CASCADE──> course_sections ──CASCADE──> syllabi ──CASCADE──> user_syllabi
subjects ──CASCADE──> course_sections ──RESTRICT──> reviews
subjects ──CASCADE──> course_sections ──CASCADE──> course_section_views
instructors ──CASCADE──> course_sections
subjects ──CASCADE──> subject_credit_categories <──RESTRICT(未指定)── credit_requirements
subjects ──CASCADE──> required_subjects
```

上記以外（`user_profiles`↔`user_syllabi`/`user_custom_courses`/`user_seiseki_raw`/`message_logs`等、`display_orders.parent_group`の自己参照、`credit_requirements.combined_of`の他行参照、`user_custom_courses.classification`↔`credit_requirements.category_id`）はいずれもFK制約を持たない“論理的な”親子関係であり、アプリ側の規約でのみ整合性が保たれている。詳細は下記「横断的な問題点」参照。

---

## subjects

- **現状**: id, name (Text, index), reading, faculty (Text, index, nullable), classification, category, senmon_group, sort_order, term_type, credits (Numeric(3,1)), hide_from_timetable (Boolean); UNIQUE(name, faculty)
- **問題点**:
  1. `(name, faculty)` の複合UNIQUEだが `faculty` は nullable。PostgreSQLの仕様上 NULL 同士は重複と判定されないため、`faculty=NULL` の科目（共通専門基礎科目など）は同名科目が UNIQUE 制約をすり抜けて増殖しうる。`programing files/sync_db_to_prod.py:82-84` のコメントで既知の欠陥として明記されており、実際に dev→prod 同期のたびに重複INSERTが起こりうる。
  2. `classification` / `category` / `term_type` / `senmon_group` は全て自由入力の `Text` で CHECK 制約なし。有効値集合（例: `category` は「教養」/「専門」）は `line_bot/handler.py` 等アプリ層にのみ存在し、DBはタイポや表記ゆれを一切防げない。
  3. `credits` は `Numeric(3,1)` だが範囲チェックなし（負の値や極端な値もDB上は許容）。
  4. `hide_from_timetable` は `database.py:245` で `ALTER TABLE ... ADD COLUMN IF NOT EXISTS` により後付け。`models.py` の宣言的定義と実際のDBスキーマの整合性が `init_db()` の実行順・累積ALTER文に依存しており、宣言的スキーマからは実態が読み取れない。
- **改善案**:
  - `faculty` を `NOT NULL DEFAULT ''` にして UNIQUE を確実に機能させる（既存の `faculty IS NULL` 行を空文字に寄せる移行が必要）
  - `classification`/`category`/`senmon_group` に CHECK 制約または参照テーブルを検討（`category` は「教養」/「専門」の2値のみなので特に着手しやすい）
- **リスク**:
  - `faculty` の NULL→空文字統一は、`faculty IS NULL` を前提にしたクエリ（`routers/`各所）の洗い出しが必要。
  - CHECK 制約追加は既存データに違反値がないか事前確認必須。

---

## instructors

- **現状**: id, name (Text, UNIQUE, index), sort_order
- **問題点**:
  1. `name` はUNIQUEだが、`routers/admin/courses.py` の教員追加処理は `normalize_instructor_name()` で空白除去のみ正規化しており、異体字・全角/半角以外の表記ゆれ（旧字体など）は正規化されない。同一教員が複数レコードに分裂する余地が残る（2026-07-13に既存重複523グループ・580件を手動統合した実績あり、根本原因は未解消）。
- **改善案**:
  - 正規化ロジックの拡張は保留中の課題として認識しておく（頻度が低いため優先度は低い）
- **リスク**: 低い。運用でのマージ対応が現実的。

---

## course_sections

- **現状**: id, subject_id (FK→subjects CASCADE), instructor_id (FK→instructors CASCADE); UNIQUE(subject_id, instructor_id)
- **2026-07-18対応済み**: 従来ここにあった `syllabus_url`（科目×教員につき1本だけ持つ非正規化カラム）は、実際には同じ `course_section` が複数年度の `syllabi` にまたがる設計（`syllabi.course_section_id` は多対1）のため、年度をまたぐURL変更を表現できず値が陳腐化する問題があった（旧・下記「改善案」参照）。`timetable_code`/`department` はいずれも既に `syllabi` 側に存在し100%導出可能な値だったため、列を廃止して `syllabi.timetable_code`+`department` から `core.config.make_syllabus_url()` で毎回動的生成する方式に変更した。バックフィル不要でそのままDROPできたため、データ移行リスクはゼロだった。副作用として、`syllabi` レコードを持たない `course_section`（時間割インポート前・レビュー投稿のみ由来のセクション）はURLを導出できなくなり、`fetch_syllabus_info.py` の単位数・群判定の自動取得はスキップされ手入力での補完が必要になった（既存データで単位数取得済みの科目には影響なし、影響するのは今後syllabiを持たないまま追加される科目のみ）。
- **問題点**:
  1. `subject_id`/`instructor_id` ともに `ON DELETE CASCADE` のため、科目や教員を削除すると `course_sections` 経由で `syllabi`→`schedules` まで芋づる式にCASCADE削除される。一方 `reviews.course_section_id` は `ON DELETE RESTRICT`（後述）なので、レビューが1件でも紐づいていれば科目削除自体がDBレベルでブロックされる。削除経路が増えるたびにアプリ側で同じ「レビュー存在チェック」を個別実装する必要がある。
- **改善案**: 特になし（上記の主要な問題点は解消済み）。
- **リスク**: 低い。

---

## syllabi

- **現状**: id, course_section_id (FK→course_sections CASCADE), year, academic_term, timetable_code (index), target_grades, subject_category, department; UNIQUE(course_section_id, year, academic_term)
- **問題点**:
  1. `academic_term` は自由 `Text` でCHECK制約なし。有効値の定義が `routers/timetable_api.py` の `_TERM_ORDER`（CASE式によるソート順定義）と `core/required_subjects.py` の `_TERM_TO_QUARTERS` 辞書の**2箇所**に重複してハードコードされている。新しい開講区分表記を追加する際に片方だけ更新すると、時間割の並び順表示と必修科目自動登録の学期重複判定がズレる。
  2. `academic_term` は2026-07頃に `quarter` からリネームされた経緯があり（`database.py:227-236` の `RENAME COLUMN` 処理）、旧名の名残がコード内コメント等に残っていないか注意が必要。
- **改善案**:
  - `academic_term` の許容値を1箇所（例: `core/config.py`）に集約し、両ファイルがそこを参照するようリファクタリング
- **リスク**: リファクタリングのみで既存データへの影響はない。

---

## schedules

- **現状**: id, syllabus_id (FK→syllabi CASCADE), day_of_week, period; UNIQUE(syllabus_id, day_of_week, period); INDEX(day_of_week, period)
- **問題点**:
  1. `day_of_week` は自由 `Text` でCHECK制約なし。有効値 `{"月","火","水","木","金","土","日","集"}` は `routers/timetable_api.py` にアプリ内定数として存在するのみ。`programing files/` 配下のインポートスクリプト経由で表記ゆれ（例: 半角/全角、英語表記）が混入する余地がある。
  2. `(day_of_week, period)` の複合インデックスは2026-07-14に追加済み（マイ時間割のコマタップ用）だが、`syllabus_id` を含まない設計のため、特定科目のスケジュール検索は別途 `syllabus_id` の単体インデックス（FK自動生成分）を使う想定。用途が分かれている点はコメント（`database.py:405-409`）で説明されており妥当。
- **改善案**: `day_of_week` に `CHECK (day_of_week IN ('月','火','水','木','金','土','日','集'))` を追加。
- **リスク**: 既存データに想定外の表記があれば事前確認が必要。

---

## reviews

- **現状**: id, course_section_id (FK→course_sections **RESTRICT**), content, rating, ease_rating, grading_method, submitter_name, nickname, student_id, academic_year, selected_instructor, is_approved (Boolean), created_at
- **問題点**:
  1. `rating`（想定1〜5）・`ease_rating`（想定 SS/S/A/B/C、`routers/liff_api.py` でのみ検証）にCHECK制約がない。`message_logs.direction` には `database.py:187-192` でCHECK制約が付与されているのに、同様に値集合が固定されている `reviews` には一切ない、という一貫性の欠如がある。
  2. `course_section_id` の `ON DELETE RESTRICT` はCLAUDE.mdの「レビューを巻き添え削除しない」方針をDBレベルで保証する良い設計だが、そのぶんアプリ側の教員削除・科目削除・第三外国語一括削除・末尾数字マージ等、`routers/admin/courses.py` の複数の削除系エンドポイントそれぞれで「レビューが紐づいていればスキップ/中止する」という同じ事前チェックロジックが個別に実装されている。DB制約があるので最悪ケースでもデータは守られるが、チェック漏れがあるとRESTRICT違反の500エラーとしてユーザーに露出する（データ破損はしないが、UXとしては不親切）。
- **改善案**:
  - `rating` に `CHECK (rating BETWEEN 1 AND 5)`、`ease_rating` に `CHECK (ease_rating IN ('SS','S','A','B','C'))` を追加
  - 削除系エンドポイントの「レビュー存在チェック」を共通ヘルパー関数に一本化し、コピペ実装を解消
- **リスク**:
  - CHECK制約は既存データに違反値がないか `SELECT DISTINCT` で事前確認が必要。
  - ヘルパー関数化はリファクタリングであり既存データへの影響はない。

---

## course_section_views

- **現状**: course_section_id (PK, FK→course_sections CASCADE), view_count, last_viewed_at
- **問題点**: 特になし。FK+CASCADEで科目削除時の孤立行リスクは解消済み。旧スキーマの `course_views`（`course_name` 非正規化カラムを持ち科目改名時にズレる問題）は移行時に解消されている。
- **改善案**: なし。
- **リスク**: なし。

---

## display_orders

- **現状**: id, kind (String), name, sort_order, parent_group, faculty; UNIQUE(kind, name, faculty)
- **問題点**:
  1. `kind` はCHECK制約のない自由文字列で、`classification`/`faculty`/`credit_requirement_group` の3種を1テーブルに混在させる汎用設計になっている。`routers/admin/courses.py` の分類の親グループ設定処理は `kind='classification'` という値の存在チェックをせずに行を作成でき、タイポで無効な `kind` が紛れ込んでもDBは検知しない。
  2. `parent_group` は同テーブルの `name` を自己参照する想定のカラムだが、FK制約もCHECKもない。存在しない親グループ名を登録できる。
- **改善案**:
  - `kind` に `CHECK (kind IN ('classification','faculty','credit_requirement_group'))` を追加
  - 新しい `kind` を追加する際は、この一覧とアプリ側の分岐（`core/cache.py`等）を同時に更新するルールを明文化
- **リスク**: CHECK追加は既存データの `kind` 値を事前に洗い出せば低リスク。

---

## credit_requirements

- **現状**: category_id (PK, String), label, group_name, sort_order, required_credits, note, faculty, department, combined_of (JSONB配列), max_credits
- **問題点**:
  1. `combined_of` は他行の `category_id` を文字列で保持するJSONB配列だが、FKでも検証でもない。`routers/admin/credit_requirements.py` の複数の削除処理は「`subject_credit_categories` からの参照有無」のみをチェックしており、**他の `credit_requirements` 行の `combined_of` 配列に自分の `category_id` が含まれているか**は一切確認していない。例えば `senmon_55`（`database.py:351-352` が参照する `senmon2`/`senmon3`/`global`）のいずれかを管理画面から削除すると、`combined_of` に存在しない `category_id` が残ったまま合算計算が実行される。
  2. `combined_of`/`max_credits` を使った合算・上限ロジックの実体は Python バックエンド（`core/seiseki.py`）ではなく `templates/liff/timetable.html` のフロントエンドJSにあり、卒業要件計算の実装場所がスキーマのドキュメントだけからは追えない。
  3. `department` はFACULTY_DEPARTMENTS等のマスタ参照がなく自由 `Text`。工学部・農学部の管理画面ルートは `KOUBU_DEPARTMENTS`/`NOGAKU_DEPARTMENTS` という辞書を独自にハードコードしており、`core/config.py` の学部学科マスタとは別の「第三のマスタ」が事実上存在している。
- **改善案**:
  - 削除処理に「他行の `combined_of` からの参照チェック」を追加
  - `department` の妥当性検証をアプリ層で共通マスタ経由に統一
- **リスク**: 削除処理の追加チェックはロジック変更のみで既存データへの影響なし。

---

## subject_credit_categories

- **現状**: id, subject_id (FK→subjects CASCADE), category_id (FK→credit_requirements.category_id、ondelete未指定=RESTRICT相当); UNIQUE(subject_id, category_id)
- **問題点**: `category_id` のFKに `ondelete` が明示されていないため、`credit_requirements` の行を削除しようとすると参照があれば自動的にRESTRICTでブロックされる（これ自体は安全側で問題ではない）。ただし前述の通り `combined_of` からの参照は保護されない非対称性がある。
- **改善案**: なし（FK自体は妥当）。
- **リスク**: なし。

---

## registration_caps

- **現状**: id, faculty (index), department (nullable), year, max_credits; UNIQUE(faculty, department, year)
- **問題点**: `faculty`/`department` はFKなし自由 `Text`。`routers/admin/registration_caps.py` は `FACULTY_DEPARTMENTS` マスタとの突合なしにフォーム値をそのまま保存でき、学部名をタイポしたCAP行が有効な学部と紐づかず静かに無視される（エラーにならないため気づきにくい）。
- **改善案**: 保存時に `core/config.py` の学部・学科マスタと照合するバリデーションを追加。
- **リスク**: 低い。バリデーション追加のみ。

---

## required_subjects

- **現状**: id, faculty (index), department, grade, subject_id (FK→subjects CASCADE), student_id_parity, note; UNIQUE(faculty, department, grade, subject_id)
- **問題点**:
  1. `core/required_subjects.py` の自動登録処理は `CourseSection`→`Syllabus` をJOINして当該年度の `Syllabus` が存在しなければ黙って `continue` する。必修科目マスタに登録されているのに該当年度のシラバスが未インポートだと、学生に何の通知もなく自動登録がスキップされる。
  2. `required_subjects` は `programing files/sync_db_to_prod.py` の同期対象5テーブル（`display_orders`/`subjects`/`instructors`/`course_sections`/`subject_credit_categories`）に**含まれていない**。dev で必修科目マスタを整備しても本番には反映されず、`subject_id` の値自体も dev/prod で一致する保証がないため、手動同期に完全依存している。
- **改善案**:
  - 自動登録スキップ時にエラーログ（`error_logs`）へ記録し、サイレント失敗を可視化する
  - `required_subjects` を dev→prod 同期の対象に含めるか、少なくとも同期漏れリスクをCLAUDE.mdに明記する
- **リスク**: ログ追加は低リスク。同期対象拡大はスクリプト側の `subject_id` 変換ロジック追加が必要。

---

## user_syllabi

- **現状**: id, line_user_id (index), syllabus_id (FK→syllabi CASCADE), classroom, created_at; UNIQUE(line_user_id, syllabus_id)
- **問題点**: `line_user_id` は `user_profiles.line_user_id` へのFKがない単なる `String(64)`。現状 `UserProfile` を削除するAPI/管理画面が存在しないため顕在化していないが、将来の退会機能実装時にこのテーブルが孤児データ化するリスクが設計上放置されている。
- **改善案**: 退会機能を実装する際は、削除処理内で `user_syllabi`/`user_custom_courses`/`user_seiseki_raw`/`message_logs` 等 `line_user_id` を持つ全テーブルを明示的に削除する設計にする（FK CASCADEをここで導入するかは、`user_profiles` 自体が頻繁に削除されるテーブルではないため優先度は低い）。
- **リスク**: 低い（現状は退会機能自体が存在しないため）。

---

## user_custom_courses

- **現状**: id, line_user_id (index), name, instructor, classification, credits, year, day_of_week, period, created_at
- **問題点**:
  1. `user_syllabi` と同様に `line_user_id` にFKなし。
  2. `day_of_week`/`period` にCHECK制約なし。`routers/timetable_api.py` でのみ `_VALID_DAYS`・`1<=period<=6` を検証しており、DBを直接操作すれば不正値が入りうる。
  3. `classification` は `credit_requirements.category_id` と一致させる設計（`models.py` のクラスdocstringに明記）だが、FKではないため無効な `category_id` 文字列を保存しても単位チェッカーの集計から静かに漏れるだけで気づきにくい。
- **改善案**: `classification` に `credit_requirements.category_id` へのFK（nullable、ondelete SET NULL）を追加すると無効値の混入を防げる。
- **リスク**: 既存データに `credit_requirements` に存在しない `classification` 値がないか事前確認が必要。

---

## user_profiles

- **現状**: line_user_id (PK), name, student_id (UNIQUE), faculty, grade, department, share_token_version, created_at, updated_at (トリガーで自動更新)
- **問題点**: 特になし。`student_id` のUNIQUE制約・`updated_at` の自動更新トリガーは2026-07-06のスキーマ移行で整備済み。
- **改善案**: なし。
- **リスク**: なし。

---

## user_seiseki_raw

- **現状**: line_user_id (PK), raw_json (JSONB), gpa (Float, nullable), updated_at (トリガーで自動更新)
- **問題点**: `line_user_id` に `user_profiles` へのFKなし（前述の横断的問題と同じ）。それ以外は2026-06-27時点で指摘されていた `TEXT`→`JSONB` 変換・`onupdate` の非同期環境での不確実性への対応（DBトリガー化）はいずれも解消済み。
- **改善案**: なし（横断的なFK欠如の課題としてまとめて扱う）。
- **リスク**: なし。

---

## message_logs / error_logs / user_activity / richmenu_taps / push_subscriptions

- **現状**:
  - `message_logs`: id, user_id (index), direction (CHECK制約あり), message, created_at (index)
  - `error_logs`: id, user_id, action, error_type, error_message, traceback, created_at (index)
  - `user_activity`: id, user_id (index), action, count, last_at; UNIQUE(user_id, action)
  - `richmenu_taps`: id, button (index), tapped_at
  - `push_subscriptions`: id, endpoint (UNIQUE), p256dh, auth, created_at
- **問題点**: 2026-06-27版で指摘されていた「`created_at` にインデックスなし」「`direction` にCHECK制約なし」はいずれも `database.py` で解消済み（`ix_message_logs_created_at`、`ix_error_logs_created_at`、`chk_ml_direction`）。`push_subscriptions.line_user_id` は一度追加された後 `database.py:310` で `DROP COLUMN` されており、購読者とLINEユーザーの紐付けは意図的に持たない設計に確定している（全員一斉配信のみのため）。
- **改善案**: 特になし。ログ性テーブルとして現状の設計で妥当。
- **リスク**: なし。

---

## 横断的な問題点

### 1. `line_user_id` を軸にしたFK欠如
`user_syllabi`・`user_custom_courses`・`user_seiseki_raw`・`message_logs`・`user_activity` など多数のテーブルが `user_profiles.line_user_id` へのFKを持たない。現状は退会・ユーザー削除機能自体が存在しないため実害はないが、将来実装時に孤立データが構造的に発生しうる。

### 2. CHECK制約の適用が不均一
`message_logs.direction` にはCHECK制約があるのに、同様に値集合が固定されている `reviews.rating`/`reviews.ease_rating`、`schedules.day_of_week`、`syllabi.academic_term`、`display_orders.kind` にはない。整備の優先順位が場当たり的になっている。

### 3. 削除時の整合性チェックが局所的・分散的
`reviews` は `ON DELETE RESTRICT` でDBが守っているが、`credit_requirements.combined_of`（他行からのJSONB参照）はアプリ側でもDB側でも検証されていない非対称性がある。削除系エンドポイントごとに個別実装されたチェックロジックも共通化されていない。

### 4. `subjects.faculty` のNULL許容がUNIQUE制約を無効化
`(name, faculty)` の複合UNIQUEはPostgreSQLのNULL非等価性により `faculty IS NULL` の行では機能しない。既知の欠陥として `sync_db_to_prod.py` にコメントで明記されているが未対応。

### 5. 学部・学科マスタが複数箇所に分散
`core/config.py` の `FACULTY_DEPARTMENTS` に加え、`routers/admin/credit_requirements.py` に `KOUBU_DEPARTMENTS`/`NOGAKU_DEPARTMENTS` という独自辞書が存在し、`registration_caps`/`credit_requirements` の `faculty`/`department` はどちらのマスタとも突合されずに自由入力で保存される。

### 6. dev→prod同期対象の範囲が限定的
`sync_db_to_prod.py` の同期対象は5テーブル（`display_orders`/`subjects`/`instructors`/`course_sections`/`subject_credit_categories`）のみで、`credit_requirements`・`registration_caps`・`required_subjects` は対象外。これらをdevで整備しても本番へは手動反映が必要で、環境間drift（本ドキュメント作成時点で工学部の科目選択色分け・CAP機能・農学部単位チェッカー等、複数の項目が「dev反映済み・本番未反映」の状態にある）が常態化している。

### 7. キャッシュ無効化がDBコミットと非トランザクショナル
`core/cache.py` の各 `invalidate_*_cache()` はDB更新後に手動呼び出しする設計で、呼び出し忘れや例外発生時の未到達があると最大1時間（TTL）ステールデータが配信されるリスクが構造的に残る。

### 8. `programing files/models.py` とルート `models.py` の乖離
`normalize_instructor_name`/`normalize_subject_name` が両ファイルに独立定義され手動同期する運用になっている。また `programing files/models.py` には `ErrorLog`/`UserActivity`/`RichMenuTap`/`PushSubscription`/`CourseSectionView`/`UserSyllabus`/`UserCustomCourse` の定義がなく、スクリプト側からはこれらのテーブルを型安全に扱えない。

### 9. マイグレーションフレームワーク不在（旧版から継続）
スキーマ管理は引き続き `database.py` の `init_db()` 内の逐次 `ALTER TABLE IF NOT EXISTS` / `DO $$ ... EXCEPTION` ブロックで行っている。ファイルが400行超まで肥大化しており、変更の依存関係・実行順序が読みにくくなっている。Alembic等の導入は中長期的な課題として残る。

---

## 優先度別改善一覧

### P1（高優先度・データ整合性リスクあり）

| # | 対象 | 問題 | 改善方針 |
|---|---|---|---|
| 1 | `subjects` | `(name, faculty)` UNIQUEが `faculty IS NULL` 行で機能しない | `faculty` を `NOT NULL DEFAULT ''` に統一 |
| 2 | `credit_requirements` | `combined_of` からの参照が削除時にチェックされない | 削除処理に「他行の`combined_of`参照チェック」を追加 |
| 3 | `required_subjects` | dev→prod同期対象外で環境間drift、自動登録スキップがサイレント | エラーログ記録の追加、同期対象への追加検討 |
| 4 | `registration_caps` / `credit_requirements` | `faculty`/`department` がマスタ突合なしの自由入力 | 保存時に共通マスタ（`core/config.py`）と照合するバリデーション追加 |

### P2（中優先度・将来の機能拡張に影響）

| # | 対象 | 問題 | 改善方針 |
|---|---|---|---|
| 5 | `user_syllabi`/`user_custom_courses`/`user_seiseki_raw`等 | `line_user_id` にFKなし | 退会機能実装時に明示的な連鎖削除ロジックを整備 |
| 6 | `reviews` | `rating`/`ease_rating` にCHECK制約なし | CHECK制約追加（既存データ確認後） |
| 7 | `schedules` | `day_of_week` にCHECK制約なし | CHECK制約追加 |
| 8 | `display_orders` | `kind` にCHECK制約なし・`parent_group` 自己参照FKなし | CHECK追加、自己参照FK検討 |
| 9 | `user_custom_courses` | `classification` が `credit_requirements.category_id` と不整合を起こしても検知不可 | FK（nullable, SET NULL）追加を検討 |

### P3（低優先度・品質向上）

| # | 対象 | 問題 | 改善方針 |
|---|---|---|---|
| 10 | `reviews`削除チェック | 複数エンドポイントに同一ロジックがコピペ | 共通ヘルパー関数へ統一 |
| 11 | `syllabi.academic_term` | 許容値が2箇所（`timetable_api.py`/`required_subjects.py`）に重複ハードコード | 1箇所に集約 |
| 12 | `programing files/models.py` | ルート`models.py`とテーブル定義・正規化関数が乖離 | 定期的な同期、または共通モジュール化 |
| 13 | 全体 | マイグレーション管理が `init_db()` の逐次ALTERのみで肥大化 | Alembic導入を中長期的に検討 |
| 14 | `instructors` | 異体字等の表記ゆれが正規化されず重複が発生しうる | 優先度低・運用でのマージ対応を継続 |
