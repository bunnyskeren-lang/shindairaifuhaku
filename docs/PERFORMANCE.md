# LINE Bot 応答速度の改善記録

**対象日**: 2026-06-28  
**対象ブランチ**: dev

---

## 改善前の問題点

webhookが届くたびに以下の無駄なコストが発生していた。

1. **`AsyncApiClient` を毎回新規作成**  
   `async with AsyncApiClient(configuration) as api_client:` を `_process_events` 内で毎回実行していたため、LINE APIサーバーへのTCP接続確立が毎webhook発生し、+50〜150ms の遅延が生じていた。

2. **ログ削除クリーンアップが返信をブロック**  
   2%の確率でDBのログ削除クエリを `await` していたため、その分だけ返信が遅れていた。

3. **カテゴリ一覧表示のたびにDBクエリが走る**  
   `handle_course_list` 内でシラバスURLを毎回DBから取得していた（`_course_list_cache` のミス時）。

4. **プリウォームが順次実行**  
   起動時のキャッシュ温めが8つのDB接続を順番に実行していたため、完了まで時間がかかっていた。

5. **科目FlexMessageが遅延ビルド**  
   起動後の初回科目検索時にFlexMessageを組み立てる処理が走っていた。

---

## 実施した改善

### 1. LINE APIクライアントの永続化

**変更箇所**: `lifespan()` / `_process_events()`

```python
# 起動時に1度だけ生成
_line_api_client = AsyncApiClient(configuration)
_line_api = AsyncMessagingApi(_line_api_client)

# シャットダウン時にクローズ
await _line_api_client.close()
```

`_process_events` 内の `async with AsyncApiClient(...) as api_client:` を廃止し、永続化したクライアントを使い回す。HTTP keep-alive接続が維持されるため、`reply_message` のたびにかかっていた接続確立コストがゼロになる。

### 2. ログ削除のバックグラウンド化

**変更箇所**: `_process_events()`

```python
# 変更前: awaitで返信をブロック
if random.random() < 0.02:
    await session.execute(delete(MessageLog)...)

# 変更後: バックグラウンドタスク
if random.random() < 0.02:
    asyncio.create_task(_cleanup_old_logs())
```

### 3. シラバスURLの全件キャッシュ

**変更箇所**: `_get_syllabus_urls_cached()` を新設 / `handle_course_list()`

```python
async def _get_syllabus_urls_cached() -> dict[int, str]:
    # CourseSection.syllabus_url を全件キャッシュ（TTL 3600秒）
```

`handle_course_list` でカテゴリごとに発生していたシラバスURL取得DBクエリを廃止。全件を一括キャッシュし、dict参照に置き換えた。

### 4. プリウォームの並列化 + 高速化

**変更箇所**: `_prewarm_caches()`

```python
# 変更前: 順次（2秒待機後に逐次実行）
await asyncio.sleep(2)
for fn in [...]:
    await fn()

# 変更後: 並列（0.5秒待機後に一括実行）
await asyncio.sleep(0.5)
await asyncio.gather(
    _get_cls_order_map(),
    _get_cls_parent_map(),
    _get_cls_set(),
    _get_courses_cached(),
    _get_reviewed_cached(),
    _get_all_instructors_cached(),
    _get_all_review_stats_cached(),
    _get_syllabus_urls_cached(),
)
```

### 5. 全科目FlexMessageの事前ビルド

**変更箇所**: `_prewarm_caches()`

```python
_, all_courses = await _get_courses_cached()
for course in all_courses:
    await get_course_flex(course, "")
```

起動直後から全科目の `_course_flex_cache` が埋まるため、科目検索が dict ルックアップのみで完結する。

---

## 改善後の遅延内訳（概算）

| ステップ | 改善前 | 改善後 |
|---|---|---|
| キャッシュから応答を組み立て | 数ms〜数十ms | 数ms |
| カテゴリ一覧（初回） | DBクエリ +100ms | キャッシュのみ |
| LINE APIへのHTTP呼び出し | 接続確立 +50〜150ms + API 100〜200ms | API 100〜200ms のみ |
| LINEが端末に配信 | 50〜100ms | 50〜100ms（変わらず） |

---

## これ以上速くする場合の選択肢

コードレベルの最適化はほぼ限界。残り 100〜200ms は LINE プラットフォームとのネットワーク往復コスト。

### Renderのリージョン変更（最も効果的）

Renderのデプロイリージョンが **US（Oregon等）** の場合、LINE日本サーバーへの往復に +100〜150ms かかる。**Singapore リージョン**に変更すると大幅に改善できる可能性がある。

変更方法: Renderダッシュボード → サービスを削除して再作成（リージョン選択は作成時のみ）

### Push APIへの切り替え

Reply API（reply_token方式）から Push API に切り替えることも技術的には可能だが、以下の理由で推奨しない：
- LINEの有料プランが必要
- 遅延自体は変わらない（LINE API呼び出しは同じ）
- コード変更コストが大きい
