# リポジトリ全体レビュー(2026-07-21)で見送った項目

2026-07-21、Staff Engineer視点での全体レビュー（バグ/保守性/可読性/パフォーマンス/
セキュリティ/命名規則/技術的負債）を実施。同日午前に実施済みだった全体監査Top20
（メモリ: `project_repo_wide_audit_20260721`）を踏まえ、差分・未着手分を4並列調査した上で
P1中心に実装した。実装できた項目はdevブランチにpush・dev環境へデプロイ済み。
本項目は実装せず見送ったもの。

## 見送った項目

1. **管理者トークンのサーバー側失効機構（P2）**
   `core/security.py`の管理者トークンはサーバー側セッションストアを持たない純粋な
   HMAC署名方式のため、`routers/admin/auth.py`の`admin_logout`はCookie削除のみで
   トークン自体を失効させられない。漏洩・コピーされたトークンは「ログアウト」後も
   `ADMIN_TOKEN_TTL`（4時間）いっぱい有効なまま。
   対応にはセッションストア（DBテーブル or インメモリのブラックリスト）導入という
   設計変更が要るため、着手前に方針確認が必要と判断し見送った。

2. **`Subject`モデル名とコード全体の呼称`course`の不一致（P2）**
   DBモデルクラス名は`Subject`だが、ルート・関数名・変数名では一貫して「course」という
   呼称が使われている（`/api/course/{id}`、`get_course_flex()`等）。一方`CourseSection`
   （科目×教員）は`cs`/`course_section_id`と略される。初見の開発者が`Course`という
   モデルを探して見つけられない命名の食い違いだが、リポジトリ全体に及ぶ大規模リネームで
   リスクが高いため見送った。

3. **`handle_course_list`（`line_bot/handler.py`）の分割（P2）→ 2026-08-25対応済み**
   よみがな分割メニュー生成部分を`_build_alpha_split_menu()`に、バリアント統合・URL付与・
   FlexBubble組み立て部分を`_build_course_bubbles()`にそれぞれ抽出し、`handle_course_list`
   本体は305行→63行に縮小。ロジックは一切変更せず移動のみ（dev DBの実データで
   リファクタ前後のFlexMessage出力が全パターン完全一致することを検証済み）。

4. **`Subject.faculty` NOT NULL化（P2・DB migration）**
   `department`列はnullable=False+空文字プレースホルダ方式なのに対し、`faculty`列は
   nullable=Trueのまま非対称。今回、新規書き込み経路2箇所（`routers/admin/courses.py`・
   `programing files/import_syllabus.py`）はNULLでなく空文字を書くよう修正済みだが、
   既存データにNULL行がどれだけあるか・(name,faculty,department)のUNIQUE制約を
   NULL経由ですり抜けた重複行が無いかは、dev DBへ接続できないこの実行環境からは
   確認できない。次回dev DBにアクセス可能なセッションで、件数確認→バックフィル→
   `ALTER COLUMN faculty SET NOT NULL`の順で対応すること。

5. **`init_db()`のAlembic移行（P1・大規模DB migration）**
   `database.py`の`init_db()`（465行、`ALTER TABLE`38件・`DO $$`条件分岐11件等）が
   起動のたび手続き型SQLを逐次実行する設計。過去に一度、CHECK制約違反で`DO $$`ブロック
   全体がロールバックし後続マイグレーションが毎回無効化される障害が実際に起きている
   （コード内コメントに記録あり）。Alembicへの移行はステップ案自体は前回セッションで
   検討済み（①`alembic init`→②現行`init_db()`内容をベースラインリビジョンとして手動移植
   →③本番/dev両方に`alembic stamp`→④以後は新規リビジョンで管理）だが、本番/devの
   実スキーマ状態を確認しながら`stamp`のタイミングを見極める必要があり、dev DBに
   接続できないこの実行環境では安全に実施できないため見送った。

6. **FACULTY_PATH等5種の対応表をDBテーブル化（P2・DB migration）**
   `core/config.py`・`programing files/fetch_syllabus_info.py`の2箇所に手動複製されている
   （FACULTY_PATH/ENGINEERING_RANGES/MEDICINE_SUBLETTERS/MEDICINE_RANGES/
   DEPARTMENT_PATH_OVERRIDE、2026-07-30のMy時間割機能全廃止で`templates/liff/timetable.html`
   （JS版）が削除され3箇所→2箇所に減った）。今回は完全なDBテーブル化の代わりに、値が
   一致していることを検証する自動テスト（`tests/test_syllabus_path_sync.py`）を追加し
   更新漏れの早期検知だけ対応した。DBテーブル化自体（新テーブル設計＋参照箇所を
   API呼び出しに置き換え）は、キャッシュ無効化漏れ等の新たなバグを
   生むリスクとdev DBでの動作確認ができない制約から見送った。

## 次回セッションへの引き継ぎ

- 上記4・5・6はいずれも「dev DBに接続できればすぐ着手できる」項目。次回dev DBに
  アクセス可能なセッションでまとめて再検討すること。
- 上記1・2・3はDB接続に関係なく、方針確認や競合回避のタイミングを見て着手可能。
