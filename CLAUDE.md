# デプロイルール

- **本番環境（shindairaifuhaku.onrender.com）へのデプロイは、ユーザーから明示的な指示がない限り絶対に行わないこと**
- dev環境（shindairaifuhaku-1.onrender.com）のみ自由に操作してよい
- `git push` の push先が `origin main` または `origin shindairaifuhaku` の場合は必ず確認を取ること

## ブランチとRenderサービスの対応

| Renderサービス | GitHub ブランチ | コマンド |
|---|---|---|
| **dev** (shindairaifuhaku-1) | `shindairaifuhaku-dev` | `git push origin dev:shindairaifuhaku-dev` |
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
cd "programing files" && python -X utf8 setup_richmenu.py "../picture/ricchimenu.png"
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
