# スキーマレビュー（調査のみ・変更なし）

調査日: 2026-07-15（2026-06-27版の全面書き直し）／テーブル一覧セクションは2026-07-30更新
対象: 現行スキーマ（`models.py`、`database.py` の `init_db()`、各 `routers/`・`core/`・`line_bot/` のクエリパターン）

> **注意**: このドキュメントは基本的に調査・記録のみ。ALTER TABLE 等の実施は行っていない。
> 2026-06-27時点の旧スキーマ（`courses`/`course_instructors`/`pending_reviews`/`syllabus_courses`等）は
> 2026-07-06のスキーマ移行で完全に廃止済み。旧内容は git 履歴（このファイルの過去版）を参照。
> **例外**: `course_sections.syllabus_url` は2026-07-18に本ドキュメントの指摘（旧P2「年度またぎURL変更を表現できない」）を
> 踏まえて実際に廃止し、`syllabi.timetable_code`/`department`からの動的生成に変更済み（詳細は該当セクション参照）。
> `subjects.faculty` のNOT NULL化（下記P1 #1）も2026-08-25に対応済み。
> **2026-07-30**: My時間割・単位チェッカー（成績表PDF解析）・履修登録上限CAP制・必修科目自動登録の
> 4機能を全廃止し、`credit_requirements`/`subject_credit_categories`/`registration_caps`/`required_subjects`/
> `user_syllabi`/`user_custom_courses`/`user_seiseki_raw`/`schedules`の8テーブルと
> `subjects.hide_from_timetable`/`subjects.senmon_group`/`user_profiles.share_token_version`/
> `syllabi.target_grades`/`syllabi.subject_category`の5列をDROP済み。以下はこの廃止後の現行スキーマ
> （全13テーブル）のみを対象とする。旧テーブルのレビュー内容は git 履歴（このファイルの過去版）を参照。

---

## テーブル一覧（カラム説明・親子関係）

出典: `models.py`（2026-07-30時点、全13テーブル）。以下、コアドメイン→共通・運用系の順に記載する。

### コアドメイン（科目・シラバス・レビュー）

#### subjects（科目マスタ）— 親: なし（ルート）
| カラム | 型 | 説明 |
|---|---|---|
| id | BigInteger PK | |
| name | Text | 科目名。`normalize_subject_name()`でローマ数字表記(Ⅰ/Ⅱ等)を自動統一 |
| reading | Text, nullable | よみがな（pykakasi自動生成、LINE bot一覧の五十音分割に使用） |
| faculty | Text, nullable | 学部名。教養科目は`教養教育院`、共通専門基礎科目は`NULL`のケースあり |
| department | Text, default '' | 学科名（工学部5学科・理学部5学科・医学部保健学科4専攻等）。無ければ空文字 |
| classification | Text, nullable | 分類名（`display_orders`のkind='classification'の`name`と対応、LINE bot一覧の分類分け用） |
| category | Text, nullable | 「教養」/「専門」の大分類 |
| sort_order | Integer, default 0 | 表示順 |
| term_type | Text, nullable | 開講期区分 |
| credits | Numeric(3,1), nullable | 単位数 |

UNIQUE(name, faculty, department)。INDEX(faculty, classification) / INDEX(classification)。子テーブル: `course_sections`（CASCADE）。

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
| subject_id | BigInteger FK→subjects.id (CASCADE) | |
| instructor_id | BigInteger FK→instructors.id (CASCADE), index | |

UNIQUE(subject_id, instructor_id)。子テーブル: `syllabi`（CASCADE）、`reviews`（**RESTRICT**）、`course_section_views`（CASCADE）。
シラバスURLは2026-07-18に本テーブルの列（`syllabus_url`）から`syllabi`側の動的生成に移行済み（後述）。

#### syllabi（年度別シラバス）— 親: course_sections
| カラム | 型 | 説明 |
|---|---|---|
| id | BigInteger PK | |
| course_section_id | BigInteger FK→course_sections.id (CASCADE), index | |
| year | Integer | 年度 |
| academic_term | Text | 開講期（旧`quarter`から2026-07頃リネーム） |
| timetable_code | Text, nullable, index | 時間割コード。シラバスURLは列を持たず、`core.config.make_syllabus_url(timetable_code, department)`で毎回動的生成する |
| created_at | DateTime(tz) | |

UNIQUE(course_section_id, year, academic_term)。子テーブル: なし（`schedules`/`user_syllabi`は2026-07-30に廃止済み）。

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
| status | Text, default 'pending' | 'pending'/'approved'/'rejected'。承認済みのみLIFF等で公開。却下は削除せずstatusで保持（2026-08-23、旧is_approved Booleanから移行） |
| created_at | DateTime(tz) | |

**絶対にユーザーから消去しないテーブル**（CLAUDE.md参照）。子テーブル: なし。

#### course_section_views（科目セクション閲覧数）— 親: course_sections
| カラム | 型 | 説明 |
|---|---|---|
| course_section_id | BigInteger PK, FK→course_sections.id (CASCADE) | |
| view_count | Integer, default 0 | 閲覧数 |
| last_viewed_at | DateTime(tz) | |

子テーブル: なし。

### 共通・運用系

#### user_profiles（LINEユーザープロフィール）— 親: なし（line_user_idが実質的な軸）
| カラム | 型 | 説明 |
|---|---|---|
| line_user_id | String(64) PK | |
| name | String(100) | 氏名 |
| student_id | String(20), UNIQUE | 学籍番号 |
| faculty | Text, nullable | |
| grade | Integer, nullable | |
| department | Text, nullable | |
| created_at | DateTime(tz) | |
| updated_at | DateTime(tz), nullable | トリガーで自動更新 |

子テーブル: なし（`line_user_id`はFKで参照されていない）。

#### display_orders（表示順マスタ・汎用）— 親: なし
| カラム | 型 | 説明 |
|---|---|---|
| id | Integer PK | |
| kind | String(50) | `classification`/`faculty`のいずれか（CHECK制約なし・アプリ側規約） |
| name | String(100) | 対象名 |
| sort_order | Integer, default 0 | |
| parent_group | String(100), nullable | 同テーブルの`name`を自己参照する想定（FKなし） |
| faculty | String(100), default '' | |

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
subjects ──CASCADE──> course_sections ──CASCADE──> syllabi
subjects ──CASCADE──> course_sections ──RESTRICT──> reviews
subjects ──CASCADE──> course_sections ──CASCADE──> course_section_views
instructors ──CASCADE──> course_sections
```

上記以外（`display_orders.parent_group`の自己参照等）はFK制約を持たない“論理的な”親子関係であり、アプリ側の規約でのみ整合性が保たれている。詳細は下記「横断的な問題点」参照。

---

## subjects

- **現状**: id, name (Text), reading, faculty (Text, nullable), department (Text, default ''), classification, category, sort_order, term_type, credits (Numeric(3,1)); UNIQUE(name, faculty, department)
- **問題点**:
  1. `(name, faculty, department)` の複合UNIQUEだが `faculty` は nullable。PostgreSQLの仕様上 NULL 同士は重複と判定されないため、`faculty=NULL` の科目（共通専門基礎科目など）は同名科目が UNIQUE 制約をすり抜けて増殖しうる。`programing files/sync_db_to_prod.py` のコメントで既知の欠陥として明記されており、実際に dev→prod 同期のたびに重複INSERTが起こりうる。
  2. `classification` / `category` / `term_type` は全て自由入力の `Text` で CHECK 制約なし。有効値集合（例: `category` は「教養」/「専門」）は `line_bot/handler.py` 等アプリ層にのみ存在し、DBはタイポや表記ゆれを一切防げない。
  3. `credits` は `Numeric(3,1)` だが範囲チェックなし（負の値や極端な値もDB上は許容）。
- **改善案**:
  - `faculty` を `NOT NULL DEFAULT ''` にして UNIQUE を確実に機能させる（既存の `faculty IS NULL` 行を空文字に寄せる移行が必要）
  - `classification`/`category` に CHECK 制約または参照テーブルを検討（`category` は「教養」/「専門」の2値のみなので特に着手しやすい）
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
- **2026-07-18対応済み**: 従来ここにあった `syllabus_url`（科目×教員につき1本だけ持つ非正規化カラム）は、実際には同じ `course_section` が複数年度の `syllabi` にまたがる設計（`syllabi.course_section_id` は多対1）のため、年度をまたぐURL変更を表現できず値が陳腐化する問題があった。`timetable_code`/`department` はいずれも既に `syllabi`/`subjects` 側に存在し100%導出可能な値だったため、列を廃止して `syllabi.timetable_code`+`department` から `core.config.make_syllabus_url()` で毎回動的生成する方式に変更した。バックフィル不要でそのままDROPできたため、データ移行リスクはゼロだった。副作用として、`syllabi` レコードを持たない `course_section`（時間割インポート前・レビュー投稿のみ由来のセクション）はURLを導出できなくなり、`fetch_syllabus_info.py` の単位数の自動取得はスキップされ手入力での補完が必要になった。
- **問題点**:
  1. `subject_id`/`instructor_id` ともに `ON DELETE CASCADE` のため、科目や教員を削除すると `course_sections` 経由で `syllabi` まで芋づる式にCASCADE削除される。一方 `reviews.course_section_id` は `ON DELETE RESTRICT`（後述）なので、レビューが1件でも紐づいていれば科目削除自体がDBレベルでブロックされる。削除経路が増えるたびにアプリ側で同じ「レビュー存在チェック」を個別実装する必要がある。
- **改善案**: 特になし（上記の主要な問題点は解消済み）。
- **リスク**: 低い。

---

## syllabi

- **現状**: id, course_section_id (FK→course_sections CASCADE), year, academic_term, timetable_code (index); UNIQUE(course_section_id, year, academic_term)
- **問題点**:
  1. `academic_term` は自由 `Text` でCHECK制約なし。有効値の定義がアプリ層に分散している。
  2. `academic_term` は2026-07頃に `quarter` からリネームされた経緯があり（`database.py` の `RENAME COLUMN` 処理）、旧名の名残がコード内コメント等に残っていないか注意が必要。
- **改善案**:
  - `academic_term` の許容値を1箇所（例: `core/config.py`）に集約する
- **リスク**: リファクタリングのみで既存データへの影響はない。

---

## reviews

- **現状**: id, course_section_id (FK→course_sections **RESTRICT**), content, rating, ease_rating, grading_method, submitter_name, nickname, student_id, academic_year, selected_instructor, status (Text: pending/approved/rejected), created_at
- **問題点**:
  1. `rating`（想定1〜5）・`ease_rating`（想定 SS/S/A/B/C、`routers/liff_api.py` でのみ検証）にCHECK制約がない。`message_logs.direction` には CHECK 制約が付与されているのに、同様に値集合が固定されている `reviews` には一切ない、という一貫性の欠如がある。
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
- **問題点**: 特になし。FK+CASCADEで科目削除時の孤立行リスクは解消済み。
- **改善案**: なし。
- **リスク**: なし。

---

## display_orders

- **現状**: id, kind (String), name, sort_order, parent_group, faculty; UNIQUE(kind, name, faculty)
- **問題点**:
  1. `kind` はCHECK制約のない自由文字列。`routers/admin/courses.py` の分類の親グループ設定処理は `kind='classification'` という値の存在チェックをせずに行を作成でき、タイポで無効な `kind` が紛れ込んでもDBは検知しない。
  2. `parent_group` は同テーブルの `name` を自己参照する想定のカラムだが、FK制約もCHECKもない。存在しない親グループ名を登録できる。
- **改善案**:
  - `kind` に `CHECK (kind IN ('classification','faculty'))` を追加
  - 新しい `kind` を追加する際は、この一覧とアプリ側の分岐（`core/cache.py`等）を同時に更新するルールを明文化
- **リスク**: CHECK追加は既存データの `kind` 値を事前に洗い出せば低リスク。

---

## user_profiles

- **現状**: line_user_id (PK), name, student_id (UNIQUE), faculty, grade, department, created_at, updated_at (トリガーで自動更新)
- **問題点**: 特になし。`student_id` のUNIQUE制約・`updated_at` の自動更新トリガーは2026-07-06のスキーマ移行で整備済み。
- **改善案**: なし。
- **リスク**: なし。

---

## message_logs / error_logs / user_activity / richmenu_taps / push_subscriptions

- **現状**:
  - `message_logs`: id, user_id (index), direction (CHECK制約あり), message, created_at (index)
  - `error_logs`: id, user_id, action, error_type, error_message, traceback, created_at (index)
  - `user_activity`: id, user_id (index), action, count, last_at; UNIQUE(user_id, action)
  - `richmenu_taps`: id, button (index), tapped_at
  - `push_subscriptions`: id, endpoint (UNIQUE), p256dh, auth, created_at
- **問題点**: 特になし。`push_subscriptions.line_user_id` は一度追加された後 `DROP COLUMN` されており、購読者とLINEユーザーの紐付けは意図的に持たない設計に確定している（全員一斉配信のみのため）。
- **改善案**: 特になし。ログ性テーブルとして現状の設計で妥当。
- **リスク**: なし。

---

## 横断的な問題点

### 1. CHECK制約の適用が不均一
`message_logs.direction` にはCHECK制約があるのに、同様に値集合が固定されている `syllabi.academic_term` にはない（`reviews.rating`/`reviews.ease_rating`/`display_orders.kind`は2026-08-25に追加済み）。整備の優先順位が場当たり的になっている。

### 2. `subjects.faculty` のNULL許容がUNIQUE制約を無効化（2026-08-25対応済み）
`(name, faculty, department)` の複合UNIQUEはPostgreSQLのNULL非等価性により `faculty IS NULL` の行では機能しない問題があったが、`database.py` `init_db()` で既存NULL行（共通専門基礎科目2件→`教養教育院`、他は空文字）を補完した上で `faculty` をNOT NULL化した。

### 3. キャッシュ無効化がDBコミットと非トランザクショナル
`core/cache.py` の各 `invalidate_*_cache()` はDB更新後に手動呼び出しする設計で、呼び出し忘れや例外発生時の未到達があると最大1時間（TTL）ステールデータが配信されるリスクが構造的に残る。

### 4. `programing files/models.py` とルート `models.py` の乖離（2026-08-25調査・部分対応済み）
`normalize_instructor_name`/`normalize_subject_name` が両ファイルに独立定義され手動同期する運用になっている。更新漏れを早期検知するため `tests/test_normalize_functions_sync.py` を新設し、2箇所の実装（関数本体+依存する変換テーブル）が完全一致していることをASTベースで機械的に検証するようにした（`programing files/models.py` は `database.py` のimportにDATABASE_URL等の環境変数を要求するため、実行はせずソースコード比較のみ行う）。
`ErrorLog`/`UserActivity`/`RichMenuTap`/`PushSubscription`/`CourseSectionView` の定義が無い点は、`programing files/` 配下の全スクリプトを調査した結果これらのテーブルを実際に触っている箇所が皆無だったため、現状は実害なしと判断した（CLAUDE.mdの通り、このファイルはスクリプトが触るテーブルのみを対象とする設計）。将来これらのテーブルをスクリプト側で扱う必要が生じた時点で追加すればよい。

### 5. マイグレーションフレームワーク不在（旧版から継続）
スキーマ管理は引き続き `database.py` の `init_db()` 内の逐次 `ALTER TABLE IF NOT EXISTS` / `DO $$ ... EXCEPTION` ブロックで行っている。変更の依存関係・実行順序が読みにくくなっている。Alembic等の導入は中長期的な課題として残る。

---

## 優先度別改善一覧

### P1（高優先度・データ整合性リスクあり）

| # | 対象 | 問題 | 改善方針 |
|---|---|---|---|
| 1 | `subjects` | ~~`(name, faculty, department)` UNIQUEが `faculty IS NULL` 行で機能しない~~ → 2026-08-25対応済み | `faculty` を `NOT NULL DEFAULT ''` に統一 |

### P2（中優先度・将来の機能拡張に影響）

| # | 対象 | 問題 | 改善方針 |
|---|---|---|---|
| 2 | `reviews` | ~~`rating`/`ease_rating` にCHECK制約なし~~ → 2026-08-25対応済み | CHECK制約追加（既存データ確認後） |
| 3 | `display_orders` | ~~`kind` にCHECK制約なし~~ → 2026-08-25対応済み・`parent_group` 自己参照FKなし | CHECK追加、自己参照FK検討 |

### P3（低優先度・品質向上）

| # | 対象 | 問題 | 改善方針 |
|---|---|---|---|
| 4 | `reviews`削除チェック | 複数エンドポイントに同一ロジックがコピペ | 共通ヘルパー関数へ統一 |
| 5 | `syllabi.academic_term` | 許容値がアプリ層に分散 | 1箇所に集約 |
| 6 | `programing files/models.py` | ルート`models.py`とテーブル定義・正規化関数が乖離 → 2026-08-25、正規化関数は`tests/test_normalize_functions_sync.py`で同期検証を追加。未使用テーブルの型定義欠如は実害なしと確認済み | 定期的な同期、または共通モジュール化 |
| 7 | 全体 | マイグレーション管理が `init_db()` の逐次ALTERのみで肥大化 | Alembic導入を中長期的に検討 |
| 8 | `instructors` | 異体字等の表記ゆれが正規化されず重複が発生しうる | 優先度低・運用でのマージ対応を継続 |
