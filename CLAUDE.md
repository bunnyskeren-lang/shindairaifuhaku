# デプロイルール

- **本番環境（shindairaifuhaku.onrender.com）へのデプロイは、ユーザーから明示的な指示がない限り絶対に行わないこと**
- dev環境（shindairaifuhaku-1.onrender.com）のみ自由に操作してよい
- `git push` の push先が `origin main` または `origin shindairaifuhaku` の場合は必ず確認を取ること

## setup_richmenu.py の実行ルール

- **必ず `--env` 引数を指定して実行すること**
  - dev:  `python setup_richmenu.py --env dev`   → `programing files/.env.dev` を使用
  - 本番: `python setup_richmenu.py --env prod`  → `programing files/.env` を使用（確認プロンプトあり）
- `--env prod` は**ユーザーから明示的に「本番のリッチメニューを更新して」と言われた場合のみ**実行すること
- `--env dev` はユーザーの許可のもとで自由に実行してよい

## .env ファイル構成

| ファイル | 環境 |
|---|---|
| `programing files/.env.dev` | **dev** ボット用トークン |
| `programing files/.env` | **本番** ボット用トークン |
