# BUGS.md — shindairaifuhaku バグ一覧

最終更新: 2026-06-26

---

## [重要度: 高] `admin_reviews_cleanup` — course_names が空のとき全未承認レビューが消える

- **ファイル**: main.py:2207-2215
- **問題**: `course_names = (await session.execute(select(Course.name))).scalars().all()` が空リスト `[]` を返した場合（DBに科目がゼロ件のとき）、SQLAlchemy は `PendingReview.course_name.not_in([])` を `true()` に変換する。その結果、`is_approved == False` の全レビューが削除される。通常はあり得ないが、DB移行時や誤操作で科目テーブルが空になると全未承認レビューが失われる。
- **修正方針**: Python リストの代わりにサブクエリ `~PendingReview.course_name.in_(select(Course.name))` を使うか、`if not course_names: return` ガードを先頭に追加する。
- **状態**: 未対応

---

## [重要度: 高] `/submit` — `line_user_id` が `_LINE_USER_ID_RE` で検証されない

- **ファイル**: main.py:1926
- **問題**: フォームの `line_user_id` フィールドを受け取った後、`uid = line_user_id.strip()` とするだけで `_LINE_USER_ID_RE` による形式検証をしない。攻撃者が任意の文字列（または他の実在ユーザーのLINE ID）を `line_user_id` に指定して POST すると、そのユーザーとして `UserProfile` が作成されたり、レビューが紐付けられたりする。他のエンドポイントでは `_LINE_USER_ID_RE` を定義してある（main.py:81）のに本ルートでは未使用。
- **修正方針**: `uid` が非空かつ `_LINE_USER_ID_RE.match(uid)` に失敗する場合は `_form_error("LINE ユーザー ID の形式が不正です")` を返す。
- **状態**: 未対応

---

## [重要度: 高] `autofill_profile` — 非ユニークな `student_id` で `scalar_one_or_none()` が例外

- **ファイル**: main.py:1873-1875
- **問題**: `models.py` の `UserProfile.student_id` に UNIQUE 制約がない（`String(20)`, nullable=False のみ）。同じ学籍番号で複数の `UserProfile` が存在する場合、`select(UserProfile.line_user_id).where(UserProfile.student_id == sid)` に対する `scalar_one_or_none()` が `MultipleResultsFound` 例外を発生させ、500 エラーになる。
- **修正方針**: `scalar_one_or_none()` を `scalars().first()` に変更するか、`UserProfile.student_id` に `unique=True` を付与し、DB マイグレーションで既存重複を解消する。
- **状態**: 未対応

---

## [重要度: 高] `/api/parse_seiseki` — `X-Line-User-Id` ヘッダーが未検証で他ユーザーの成績データを上書き可能

- **ファイル**: main.py:3283-3293
- **問題**: `uid = request.headers.get("X-Line-User-Id", "").strip()` でヘッダー値をそのまま使用する。HTTPヘッダーはクライアントが自由に設定できるため、第三者が `X-Line-User-Id: U<他人のID>` ヘッダーを付けてPDFをアップロードすると、その他人の `UserSeisekiRaw` レコードを上書きできる。認証なしエンドポイントなので誰でも実行可能。
- **修正方針**: `uid` を受け取ったら `_LINE_USER_ID_RE.match(uid)` で検証する。理想的には LIFF アクセストークンでサーバー側検証を行う。
- **状態**: 未対応

---

## [重要度: 高] `/api/seiseki/save_raw` — 認証なし・uid 検証なしで任意ユーザーの成績データを上書き可能

- **ファイル**: main.py:3315-3330
- **問題**: エンドポイントに認証がなく、`uid = body.get("uid", "").strip()` の値を `_LINE_USER_ID_RE` で検証しない。任意のクライアントが `{"uid": "U任意のID", "raw": {...}}` を POST することで、あらゆるユーザーの `UserSeisekiRaw` を書き換え・破壊できる。
- **修正方針**: `_LINE_USER_ID_RE` での uid 検証を追加する。さらに LIFF トークン検証を導入し、送信ユーザーが uid と一致することを確認する。
- **状態**: 未対応

---

## [重要度: 高] `/submit` — UserProfile flush 失敗が無言でスルーされ、レビューがユーザー紐付けなしで登録される

- **ファイル**: main.py:1934-1961
- **問題**: `session.flush()` が例外を発生させた場合、`except Exception: await session.rollback()` が実行されるが、その後コードはそのまま続行して `PendingReview` を `session.commit()` する。`UserProfile` 作成失敗がユーザーに通知されず、LINE ユーザーとレビューが紐付かない不整合な状態でレビューが登録される。失敗の原因（例: DBエラー）も握りつぶされる。
- **修正方針**: `except` ブロックで `return _form_error("プロフィールの保存に失敗しました")` を返すか、UserProfile と PendingReview を同じトランザクションに含めてどちらか失敗したら全体をロールバックする。
- **状態**: 未対応

---

## [重要度: 中] `_invalidate_review_cache` — `_course_list_cache` をクリアしないため課程リストの「レビューあり」表示が最大1時間遅延

- **ファイル**: main.py:236-246
- **問題**: レビューが承認/却下されると `_invalidate_review_cache()` が呼ばれるが、この関数は `_course_flex_cache` と `_ranking_cache` はクリアするものの `_course_list_cache` をクリアしない。科目一覧バブルはレビューの有無でボタン色（青=あり/グレー=なし）を変えているため、承認直後から最大 `_COURSE_LIST_TTL`（3600秒）間、古い色のまま表示され続ける。
- **修正方針**: `_invalidate_review_cache()` 内に `global _course_list_cache` と `_course_list_cache = {}` を追加する。
- **状態**: 未対応

---

## [重要度: 中] `/api/timetable/register` および DELETE — 認証なし、任意ユーザーの時間割を操作可能

- **ファイル**: main.py:3061-3079, 3082-3096
- **問題**: `api_timetable_register`（POST）と `api_timetable_unregister`（DELETE）は認証なし。`user_id` はリクエストボディ（POST）またはクエリパラメータ（DELETE）から受け取り、`_LINE_USER_ID_RE` 検証もない。任意の外部クライアントが任意ユーザーIDを指定して他人の時間割科目を登録・削除できる。
- **修正方針**: `_LINE_USER_ID_RE` での user_id 検証を追加する。理想的には LIFF アクセストークンでサーバー側検証を行い、user_id が一致するかを確認する。
- **状態**: 未対応

---

## [重要度: 中] `/api/timetable/profile` POST — 認証なし、grade が範囲未検証

- **ファイル**: main.py:2957-2976
- **問題**: `api_timetable_profile_set` は認証なし。任意の呼び出し元が任意ユーザーの学部・学年プロフィールを書き換えられる。また `grade = int(grade)` は値域チェックなし（負数、9999 など異常値を許容する）。
- **修正方針**: user_id を `_LINE_USER_ID_RE` で検証する。`grade` は `1 <= grade <= 6` などの範囲チェックを追加する。
- **状態**: 未対応

---

## [重要度: 中] `admin_push_subscribe` — JSONキー欠損で KeyError → 500

- **ファイル**: main.py:1995-2009
- **問題**: `data["endpoint"]`、`data["keys"]["p256dh"]`、`data["keys"]["auth"]` を try/except なしで直接アクセスする。リクエストボディのキーが欠損または `"keys"` が dict でない場合、`KeyError` が発生してグローバル例外ハンドラーが 500 を返す。管理者向けエンドポイントとはいえ、意味のある 400 エラーを返すべき。
- **修正方針**: `data.get(...)` 使用または try/except で `HTTPException(status_code=400, detail="invalid subscription payload")` を返す。
- **状態**: 未対応

---

## [重要度: 中] `api_course` — 最新50件取得後に instructor/year でソート・20件に絞るため古いレビューが欠落

- **ファイル**: main.py:2707-2712, 2750-2753
- **問題**: `_reviews()` クエリは `created_at DESC` でソートして最新50件を取得する。その後アプリ側で `selected_instructor / academic_year DESC` でソートし上位20件を表示する。レビューが50件超の科目では、instructor/academic_year 基準で上位に来るべき古いレビューが「最新50件」に含まれないため除外される。
- **修正方針**: DB クエリを `ORDER BY selected_instructor NULLS LAST, academic_year DESC` に変更するか、アプリ側ソートが正確に機能するよう LIMIT を外す（件数が多い科目での負荷増大に注意）。
- **状態**: 未対応

---

## [重要度: 中] `FollowEvent` — 友だち追加イベントがログ・アクティビティに記録されない

- **ファイル**: main.py:1561-1571
- **問題**: `PostbackEvent` と `MessageEvent` では `_save_log_bg` が呼ばれて `message_logs` と `user_activity` に記録されるが、`FollowEvent`（友だち追加）ではログが一切記録されない。友だち追加数の統計やユーザー初回接触の把握ができない。
- **修正方針**: `FollowEvent` ハンドラー内のウェルカムメッセージ返信後に `asyncio.create_task(_save_log_bg(event.source.user_id, "in", "[follow]"))` を追加する。
- **状態**: 未対応

---

## [重要度: 中] `autofill_profile` — 学籍番号を知るだけで他者の氏名が取得できる（情報漏洩）

- **ファイル**: main.py:1865-1882
- **問題**: `uid` が既存 `UserProfile` の `line_user_id` と一致しない場合でも、`student_id` が `PendingReview.student_id` に存在すれば `submitter_name`（氏名）を返す。また `taken`（同一 student_id で登録済みの別ユーザー）が存在するとき、新規 UserProfile 作成はスキップするが `{"found": True, "name": row}` を返すので、実質的に他人の氏名が呼び出し元に渡る。
- **修正方針**: `taken is not None and taken != uid` のとき `{"found": False}` を返す。また氏名を返す前に `uid` の LINE ユーザー検証を強化する。
- **状態**: 未対応

---

## [重要度: 低] `lifespan` — `init_db` 失敗後もキャッシュ初期化タスクが生成される

- **ファイル**: main.py:319-337
- **問題**: `init_db()` が失敗した場合、例外はキャッチされ `engine.dispose()` が呼ばれるが、その後の `asyncio.create_task(_reload_senmon_cache())` と `asyncio.create_task(_prewarm_caches())` は try/except の外側にあるため無条件に実行される。これらのタスクは DB アクセスを試みて失敗し、エラーが握りつぶされる。アプリは起動を続けるが、すべての DB 依存機能が壊れた状態になる。
- **修正方針**: `init_db()` が成功した場合のみキャッシュ初期化タスクを生成するよう、try ブロック内（または成功フラグを用いた条件分岐）に移動する。
- **状態**: 未対応

---

## [重要度: 低] `/api/seiseki/credits` — `uid` が `_LINE_USER_ID_RE` で検証されない

- **ファイル**: main.py:3305-3312
- **問題**: `uid` をクエリパラメータから受け取り、検証なしに `session.get(UserSeisekiRaw, uid)` に渡す。任意の文字列で DB を探索できる（データ変更はないが、ユーザー ID の存在確認に悪用される可能性がある）。`_LINE_USER_ID_RE` が定義されているのに本エンドポイントでは未使用。
- **修正方針**: `uid` が空または `_LINE_USER_ID_RE.match(uid)` に失敗する場合は `return {}` を返す。
- **状態**: 未対応

---

## [重要度: 低] `/api/timetable/slots/{day}/{period}` — `day` パスパラメータに入力検証なし

- **ファイル**: main.py:2979-3018
- **問題**: `day` パスパラメータが有効な曜日（"月","火","水","木","金","土","日" など）であることを検証しない。SQLAlchemy がパラメータ化するため SQL インジェクションのリスクはないが、無効な値に対して空リストを無言で返すため、クライアントのバグを検出しにくい。
- **修正方針**: 許可リストを定義し、`day` がリスト外の場合は `HTTPException(status_code=400)` を返す。
- **状態**: 未対応

---

## [重要度: 低] `admin_reviews_cleanup` — Python リストを NOT IN に展開するため大量科目でパフォーマンス低下

- **ファイル**: main.py:2207-2215
- **問題**: `course_names`（数百件の文字列）をすべて `NOT IN (値1, 値2, ...)` のリテラルとして展開する。PostgreSQL の SQL 解析コストが高く、プランキャッシュも効かない。高重要度の Bug #1（空リスト問題）と同一箇所。
- **修正方針**: Bug #1 の修正と合わせてサブクエリ `~PendingReview.course_name.in_(select(Course.name))` に変更する。
- **状態**: 未対応

---

## [重要度: 低] `/api/reclassify_seiseki` — `raw` データ構造の型検証なし

- **ファイル**: main.py:3296-3302
- **問題**: `body.get("raw")` の値を直接 `_classify_seiseki_raw(raw)` に渡す。`raw` が `dict` 以外の型（`str`, `list`, `int` など）の場合や、`gaigo_courses` キーが `list` でない場合に `AttributeError` / `TypeError` が発生し 500 エラーになる。
- **修正方針**: `isinstance(raw, dict)` チェックを追加し、不正な型の場合は `HTTPException(400)` を返す。
- **状態**: 未対応

---

## [重要度: 低] `handle_course_list` — 絞り込み結果が空のときのメッセージに `classification` が含まれない

- **ファイル**: main.py:1046-1048
- **問題**: `rows` が空のとき `f"まだ{label}科目が登録されていません"` と返すが、`label` は `category` のみ（"教養の" or ""）で `classification` の情報を含まない。例えば `classification="外国語"` でフィルターした結果が空の場合、「まだ教養の科目が登録されていません」と表示され、どの分類を検索したのかわからない。
- **修正方針**: `label = f"{classification}の" if classification else (f"{category}の" if category else "")` と変更する。
- **状態**: 未対応
