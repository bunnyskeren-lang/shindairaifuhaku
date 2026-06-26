# 返信ルール

**必ず日本語で返信すること。**

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

- ローカル作業ブランチは `dev`
- devへのデプロイは必ず `git push origin dev:shindairaifuhaku-dev`
- 本番へのデプロイは必ず `git push origin dev:shindairaifuhaku-prod`（明示的な指示がある時のみ）

## setup_richmenu.py の実行ルール

- **必ず `--env` 引数を指定して実行すること**
  - dev:  `python setup_richmenu.py --env dev`   → `programing files/.env.dev` を使用
  - 本番: `python setup_richmenu.py --env prod`  → `programing files/.env` を使用（確認プロンプトあり）
- `--env prod` は**ユーザーから明示的に「本番のリッチメニューを更新して」と言われた場合のみ**実行すること
- `--env dev` はユーザーの許可のもとで自由に実行してよい

## SQLAlchemy async セッションのルール

**同一 `AsyncSession` オブジェクトで `asyncio.gather` を使った並行クエリは禁止。**

```python
# NG: 同一 session で並行実行 → InvalidRequestError / Internal Server Error
results = await asyncio.gather(session.execute(q1), session.execute(q2))

# OK: 順次実行
r1 = (await session.execute(q1)).all()
r2 = (await session.execute(q2)).all()
```

複数クエリを並行したい場合は、クエリごとに別の `async with AsyncSessionLocal() as session:` ブロックを使うこと。

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

同期対象（この3テーブルのみ）：
- `classification_orders`
- `courses`
- `course_instructors`

絶対に同期しないテーブル（ユーザーデータ・ログ・レビュー・利用履歴）：
- `pending_reviews`
- `message_logs`
- `user_profiles`
- `user_activity`
- `error_logs`
- `push_subscriptions`
- `richmenu_taps`

同期方法（Python スクリプトで dev → prod にコピー）：
1. dev DBから3テーブルのデータを取得
2. prod DBの3テーブルをTRUNCATEしてINSERT
3. レビュー等への外部キー制約に注意（`course_instructors` → `courses` の順で削除、逆順でINSERT）

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

現状の `courses.syllabus_url` は科目単位で1本のURLを持つ設計だが、将来的には `course_instructors` テーブルや担当教員情報と合わせて「この先生のこの科目のシラバス」として管理することを検討すること。

神戸大学シラバスサイトのURLは **時間割コード** から一意に決まる。

```
https://kym22-web.ofc.kobe-u.ac.jp/kobe_syllabus/2026/{path}/data/2026_{code}.html
```

| コードの2文字目 | 学部 | path |
|---|---|---|
| `U` | 教養科目（教養教育院） | `20` |
| `B` | 経営学部 | `06` |

例: `3U020` → `/20/` → `2026_3U020.html` / `3B379` → `/06/` → `2026_3B379.html`

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

## 経営学部 科目ナンバリングコード（2026年度）

経営学部のナンバリングコードは `B1BB___` の形式（1-2桁=B1, 3-4桁=BB, 5-7桁=分類コード）。

5-7桁の値と科目分類の対応：

| 5-7桁 | 分類 |
|-------|------|
| `100` | 第1群科目 |
| `101` | 第1群科目（初年次セミナー） |
| `202` | 第2群科目 |
| `300` | 第3群科目（①〜⑥を除く一般） |
| `103` | 第3群科目（⑤会計プロフェッショナル育成プログラム / ⑥経営データ科学特別学修プログラム） |
| `203` | グローバル科目群 / 他学部生向け専門科目 |
| `303` | グローバル科目群 / 他学部生向け専門科目 |
| `204` | 経営学部専門科目（他学部生向け区分） |
| `400` | 研究指導 |
| `403` | 卒業論文 / 上級科目 |

- 高度教養科目はナンバリングコードから判定不可（シラバス上は「高度教養」と記載されるが、コードに一対一対応なし）
- `syllabus_courses` テーブルの `numbering_code` カラムにコードが格納されている
