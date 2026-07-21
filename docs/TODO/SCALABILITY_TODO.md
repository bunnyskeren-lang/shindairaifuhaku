# 10000人同時利用に向けたスケーラビリティ改善の残課題

2026-07-21、「履修登録開始等、特定時刻への一斉アクセス」を想定したボトルネック解析
（レスポンス速度・DB・キャッシュ）を実施。Render現プランはFree/Starter（1CPU未満〜1CPU、単一インスタンス）。
関連: `Procfile`、`core/liff_auth.py`、`database.py`、`core/cache.py`、`core/rate_limit.py`、`main.py`のlifespan。
メモリ: `project_scalability_10k_users_20260721`

## 未解決・要フォローアップ

1. **Renderプランのアップグレード（インフラ判断・コスト発生のためユーザー実施待ち）**
   現状は単一インスタンス・1CPU未満のため、コード側をどれだけ改善してもハード上限がある。
   履修登録開始前後だけ一時的にStandard以上へ引き上げる案、または常時複数CPU化する案がある。
   `Procfile`は`WEB_CONCURRENCY`環境変数でワーカー数を制御できるよう既に対応済み
   （未設定なら従来通り1ワーカーで挙動不変）なので、プラン変更後は環境変数を設定するだけでよい。

2. **複数ワーカー化時のキャッシュ／レート制限／バックグラウンドタスク重複実行対策**
   `WEB_CONCURRENCY`を2以上にする場合、以下が未対応のまま残る（実際にワーカー数を増やす
   タイミングで対応要否を再検討すること）:
   - `core/cache.py`のインメモリキャッシュ・`core/rate_limit.py`のレート制限バケットは
     ワーカーごとに独立する（プロセス間で共有されない）。キャッシュ自体は各ワーカーが
     個別にTTL管理するだけで不整合は起きないが、レート制限の実質上限がワーカー数倍に緩む。
   - `main.py`の`lifespan`内バックグラウンドタスク（`self_ping()`・`backup_loop()`・
     `log_cleanup_loop()`・`prewarm_caches()`）は各ワーカープロセスで個別に起動される。
     `init_db()`は冪等（`IF NOT EXISTS`等）なので同時実行自体は安全だが、`backup_loop()`は
     ワーカー数分バックアップが重複生成されうる。

3. **Supabase pooler接続上限の実測確認**
   `database.py`の`DB_POOL_SIZE`/`DB_POOL_MAX_OVERFLOW`は環境変数で調整可能な設計だが、
   dev/本番それぞれのSupabaseプランでの実際のpooler接続上限は未確認。ワーカー数を増やす際は
   「ワーカー数 × pool_size」がこの上限を超えないよう必ず確認・再計算すること。

4. **本番未反映**
   `Procfile`と`core/liff_auth.py`の変更は本番（shindairaifuhaku.onrender.com）へは未反映。
   デフォルト値では挙動が変わらない変更のため緊急性は低いが、次回本番デプロイ時に含めること。

## 完了事項

- `Procfile`: `--workers ${WEB_CONCURRENCY:-1}`に変更（既定1、挙動不変）
- `core/liff_auth.py`: LINE ID Token検証用httpxクライアントの接続プール上限を
  `max_connections=100`（デフォルト）→`500`に拡大（一斉アクセス時のLINE検証API呼び出しが
  ここで頭打ちになりキューイングされる経路を解消）
- `CLAUDE.md`/`AGENTS.md`: アーキテクチャ概要に「プロセス構成（uvicornワーカー数）」
  セクションを新設し、上記の未解決事項を注意点として明記
- dev push済み（commit 326565e）
