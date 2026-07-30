# デプロイ方法

Render の Web Service にデプロイする（起動コマンドは `Procfile` を参照）。本番・dev で Render サービスと DB（Supabase プロジェクト）が完全に分かれており、コードは GitHub の別ブランチから各サービスにデプロイされる。

> AIエージェント（Claude Code等）向けの自動化ルール・確認要否は `CLAUDE.md` / `AGENTS.md` が正。本ドキュメントは人間の開発者向けの手順書として、その内容を整理したもの。

## 環境対応表

| 環境 | Renderサービス | URL | GitHubブランチ | Supabaseプロジェクト（リージョン） |
|---|---|---|---|---|
| **dev** | shindairaifuhaku-1 (shindairaifuhaku-dev) | https://shindairaifuhaku-1.onrender.com | `shindairaifuhaku-dev` | `ofsvkcptzngbsxtdbqzj`（aws-1-ap-northeast-1） |
| **本番** | shindairaifuhaku (shindairaifuhaku-prod) | https://shindairaifuhaku.onrender.com | `shindairaifuhaku-prod` | `sagubqrhjnzrtcvlmzqy`（aws-1-ap-northeast-2） |

ローカル作業ブランチは `dev`。

## dev へのデプロイ

dev環境への操作は確認不要で実行してよい運用（CLAUDE.md）。

```bash
git push origin dev                          # devブランチへの通常push
git push origin dev:shindairaifuhaku-dev      # dev環境（Render）へのデプロイ
```

コミット後は**両方**実行すること（片方だけでは本番同期・GitHub上のdevブランチ・Render dev環境のいずれかが古いままになる）。

## 本番へのデプロイ

**本番へのデプロイは、ユーザー（プロジェクトオーナー）から明示的な指示がない限り行わない。**

```bash
# 1. コードを本番ブランチにプッシュ
git push origin dev:shindairaifuhaku-prod

# 2. 本番リッチメニューを更新（programing files/ から実行すること）
cd "programing files" && python -X utf8 setup_richmenu.py --env prod
```

### DBの同期（本番デプロイ時は必ず実施）

コードのプッシュに加えて、必ず dev → 本番 のDB同期も行う。

```bash
cd "programing files"
python -X utf8 sync_db_to_prod.py
```

同期対象は以下の4テーブルのみ（UPSERT。本番のみに存在する行も削除するが、`course_sections`経由で`syllabi`/`reviews`が紐づく行は`KEEP(要確認)`表示に留めて削除しない）:

- `display_orders`
- `subjects`
- `instructors`
- `course_sections`

`sync_db_to_prod.py`実行後に`KEEP(要確認)`ログが出た場合、同名科目のfaculty違いによる重複などが原因のことが多い。手動で`reviews.course_section_id`の付け替え→旧行削除の対応が必要。

同期しては**いけない**テーブル（ユーザーデータ・ログ・レビュー・利用履歴）: `reviews` / `message_logs` / `user_profiles` / `user_activity` / `error_logs` / `push_subscriptions` / `richmenu_taps` / `syllabi`（シラバスデータ・`import_syllabus.py`で別途管理）。

DBスキーマとテーブルの詳細は [`DB_SCHEMA.md`](./DB_SCHEMA.md) を参照。

## リッチメニュー設定（`setup_richmenu.py`）

- **必ず `--env` 引数を指定して実行する**
  - dev: `python setup_richmenu.py --env dev` → `programing files/.env.dev` を使用（確認不要）
  - 本番: `python setup_richmenu.py --env prod` → `programing files/.env` を使用（確認プロンプトあり、明示的な指示がある場合のみ実行）

## 環境変数の追加ルール

**新しい環境変数を追加したときは、必ずコード変更と同時に以下を案内する：**

1. `.env.dev` または `.env`（`programing files/`配下、またはルート直下）に追加した変数名と値を明示する
2. **Render ダッシュボードへの登録も必ず行う**（ローカルの `.env` だけではRenderに反映されない）
3. dev用に追加した変数は dev サービス（`shindairaifuhaku-1`）へ、本番用は本番サービス（`shindairaifuhaku`）へ登録する

例：「Render dev の Environment に以下を追加してください：`KEY=value`」

環境変数の全項目は [`SETUP.md`](./SETUP.md) を参照。

## LIFF ID / REVIEW_FORM_URL の固定ルール（絶対に入れ替えない）

| 環境 | LIFF_ID | 科目詳細エンドポイント | REVIEW_FORM_URL |
|---|---|---|---|
| **本番** | `2010406205-emxo5rhE` | `https://shindairaifuhaku.onrender.com/liff/course` | `https://shindairaifuhaku.onrender.com` |
| **dev** | `2010433465-R8b5k1SZ` | `https://shindairaifuhaku-1.onrender.com/liff/course` | `https://shindairaifuhaku-1.onrender.com` |

本番の科目詳細ボタンは必ず本番LIFF ID・本番エンドポイントを使い、devはdevの値を使う。`LIFF_ID`はRenderの各サービス環境変数で管理する（本番はコードデフォルト値と一致）。

## DB自動バックアップ

Supabase Freeプランには自動バックアップが無いため、`BACKUP_ENABLED=true` の環境では本番アプリ自身が `core/backup.py` の `backup_loop()` で `BACKUP_INTERVAL_HOURS`（既定1時間）ごとに全テーブルをダンプし、Supabase Storageへアップロードする（`BACKUP_RETENTION_DAYS`、既定15日より古い世代は削除）。

ローカルPCへの取り込み:

```bash
cd "programing files"
python download_prod_backup.py --env dev   # または --env prod
```

差分のみダウンロードし `backups/{env}/` へ保存する。Windowsタスクスケジューラ等での定期実行を想定。

復元は「`init_db()`で空スキーマを作ってから、ダウンロードした`.sql.gz`を解凍してINSERT文を流し込む」想定（DDLは含まれない）。

## デプロイ後の確認

- Renderのdevへの反映には数分〜十数分かかる場合がある
- `/health` エンドポイントで起動確認
- Renderダッシュボードのログで `DB OK` / `DB ERROR` の出力を確認（`main.py`の`lifespan`が出力）
- 本番デプロイ後は必ず本番DBの状態（同期漏れ・データ不整合）を確認する。本番DBはローカル開発環境からNetwork Restrictionsにより直接到達できないため、Renderの管理画面またはRenderにデプロイされたアプリ自身（本番の`/admin/*`）経由で確認する
