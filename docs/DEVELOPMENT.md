# 開発手順

## 前提

環境構築がまだの場合は先に [`SETUP.md`](./SETUP.md) を参照。

## ローカルサーバー起動

```bash
uvicorn main:app --reload
```

`/health` で死活確認、`/callback` がLINE Webhookのエントリポイント。ローカルでLINE Webhookを受けたい場合は ngrok 等でトンネルを張り、LINE Developersコンソールの Webhook URL を切り替える。

## テスト

```bash
pip install -r requirements-dev.txt
pytest
```

- `pytest.ini` の `testpaths = tests`
- **実DB（Supabase/PostgreSQL）への接続は不要**。`tests/conftest.py` がテストごとに独立したSQLiteインメモリDBを作成し、`models.py` のテーブル定義をそのまま流用する（PostgreSQL固有の `BigInteger` はSQLite向けにスタブ変換される）
- テスト構成:
  - `test_imports.py` — 全モジュールのimportが通るか（構文・循環import等の早期検知）
  - `test_config.py` / `test_security.py` / `test_rate_limit.py` — 各coreモジュールの単体テスト
  - `test_syllabus_path_sync.py` — シラバスURL生成ロジックの整合性（`core/config.py`と`programing files/fetch_syllabus_info.py`、複数箇所の複製ロジックがズレていないか）
  - `test_e2e_review_submit.py` — レビュー投稿のエンドツーエンドテスト
  - `test_db_fixture_sanity.py` — SQLiteフィクスチャ自体の健全性確認

## Lint

```bash
ruff check .
```

`ruff.toml` で `E4`（import）/`E7`（文構造）/`E9`（構文エラー）/`F`（pyflakes）のみを対象にした軽量構成。`programing files/*.py` は起動時に`.env`読み込み等を先に実行してからimportする構成が意図的なため `E402`（import順）を除外している。

**SQLAlchemyの真偽値比較に注意**: `Model.col == True` / `== False` / `== None` はruffが `is True` 等への書き換えを提案するが、SQLAlchemyの式ビルダーではこれは**壊れる**（Python標準の`is`比較になりSQL式を生成できない）。`.is_(True)` / `.is_(False)` / `.is_(None)` を使うこと。

## 開発ワークフロー（CLAUDE.mdより抜粋・詳細版）

- 機能追加・バグ修正の単位で小さくコミットする。1コミットに複数の無関係な変更を混在させない
- コミットメッセージは「何を」「なぜ」変更したかが分かるように具体的に書く
- 新しいセッション/作業を再開する際は、まず `git log --oneline -20` と `git diff` で現在の状態を把握する
- `models.py` にモデルを追加・削除したら **必ず** `database.py` の `init_db()` 内のimportも同時に更新する
- `programing files/models.py` はルートの `models.py` とは別定義。scriptsが触るテーブルの列を追加・変更・削除したら両方を確認・更新する
- 同一 `AsyncSession` 内で `asyncio.gather` による並行クエリは行わない（`InterfaceError` の原因になる）。並行したい場合は各コルーチンが個別に `AsyncSessionLocal()` を開く
- 投稿済みレビュー（`reviews`テーブル）は明示的な指示がない限り削除しない。科目の削除・変更・マージ操作でレビューを巻き添えにしない
- 科目名の正規化・LINE bot一覧の表示ルールを触る場合は [`SUBJECT_NAME_RULES.md`](./SUBJECT_NAME_RULES.md) を参照し、変更したら同ドキュメントも更新する
- シラバスURL生成ロジック（`core/config.py`の`make_syllabus_url`/`_SYLLABUS_FACULTY_PATH`等）を変更する場合、`programing files/fetch_syllabus_info.py` の同一ロジック計2箇所を同時に更新する（`test_syllabus_path_sync.py`が整合性を検証）

## 新しい学部・大量科目データを追加するときの注意

- LINE bot科目一覧には1回の返信あたり上限がある（40バブル≒240科目）。`classification`が学部単位のまま分割されていない学部は、科目数が閾値（48件）を超えると自動でよみがな順の均等分割メニューが挟まる（`line_bot/handler.py`の`_ALPHA_SPLIT_THRESHOLD`）ため、明示的な分類分け作業は不要
- 分割ラベルはよみがな（`subjects.reading`）の先頭文字を使う。新規科目追加時に`reading`が空文字のまま残らないよう注意（`database.py`の`init_db()`起動時バックフィルが自動生成する設計。バックフィル条件を`IS NULL`だけに戻すと空文字のまま埋まらなくなるため変更しないこと）
- 新学部でシラバスURLのpathが未対応の場合は `core/config.py` の `_SYLLABUS_FACULTY_PATH`、`programing files/fetch_syllabus_info.py` の2箇所に追記する

## PRを作る場合

このリポジトリは基本的に `dev` ブランチで作業し、Renderのdev環境へ直接pushする運用（下記デプロイ節参照）。GitHub上でPRレビューを経由したい場合は通常のGitHubフローに従う。

## デプロイ

ローカルでの動作確認後のデプロイ手順は [`DEPLOYMENT.md`](./DEPLOYMENT.md) を参照。
