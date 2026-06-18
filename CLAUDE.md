# デプロイルール

- **本番環境（shindairaifuhaku.onrender.com）へのデプロイは、ユーザーから明示的な指示がない限り絶対に行わないこと**
- dev環境（shindairaifuhaku-1.onrender.com）のみ自由に操作してよい
- `git push` の push先が `origin main` または `origin shindairaifuhaku` の場合は必ず確認を取ること
- `setup_richmenu.py` など本番トークンを使うスクリプトの実行も、明示的な指示がない限り行わないこと
