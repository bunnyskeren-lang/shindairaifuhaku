# マイ時間割 友達共有機能の残課題

2026-07-15実装。LINEの友達（Bot登録ユーザー）同士でマイ時間割を共有できる機能。
関連: `core/security.py`（`make_share_token`/`verify_share_token`）、
`routers/timetable_api.py`（`/api/timetable/share_token`・`/api/timetable/shared`・`/api/timetable/share_revoke`）、
`templates/liff/timetable.html`（共有モーダル・閲覧専用モード）。
メモリ: `project_timetable_friend_share`、`project_line_account_unverified`

## 未解決・要フォローアップ

1. **shareTargetPicker（LINE友達選択ピッカー）がLINE公式アカウント未認証のため使えない**
   dev botのLINE公式アカウントが「未認証アカウント」のため、`liff.shareTargetPicker()`は
   常に`FORBIDDEN: shareTargetPicker is not allowed in this LIFF app`で失敗し、
   共有ボタンは実質クリップボードコピーにフォールバックする形でしか動作しない。
   コード側は`try { shareTargetPicker } catch { クリップボードコピー }`の構造を維持しているため、
   → LINE Official Account Managerでアカウント認証（事業者情報の申請）を行えば、
   コード変更なしで自動的に友達選択ピッカーが使えるようになる見込み。認証申請は未着手。
   本番bot（shindairaifuhaku）の認証状態も未確認。

2. **未登録ユーザーが会員登録完了後、共有リンクへ自動的に戻る導線が無い**
   `?share=<token>`で開いた未登録ユーザーは`/register?uid=...`へリダイレクトされるが、
   登録完了後は自分の時間割LIFFに着地し、元の共有リンクには戻らない（友達の時間割は
   もう一度リンクを開き直してもらう必要がある）。実害は小さいが、UXとしては改善余地あり。

3. **閲覧側（友達側）の実機での最終確認が未完了**
   オーナー側の共有ボタン・モーダル・エラー表示は実機で確認済みだが、実際に別の登録済み
   LINEユーザーが共有リンクを開いて閲覧専用モード（オーナー名表示・セルタップでの
   読み取り専用表示・編集UI非表示）が正しく機能するかは未検証。

4. **本番未反映**
   ユーザーから明示的な指示があるまで本番（shindairaifuhaku.onrender.com）へはデプロイしない方針。
   本番反映時は`UserProfile.share_token_version`列のマイグレーション（`database.py`の
   `init_db()`内、`ALTER TABLE user_profiles ADD COLUMN IF NOT EXISTS share_token_version...`）が
   自動実行されることを確認すること。

## 完了事項

- `core/security.py`: `make_share_token(line_user_id, version)`/`verify_share_token(token)`で
  世代番号付きHMAC署名トークンを発行・検証（有効期限なし、`share_token_version`不一致で失効）
- `routers/timetable_api.py`: `_load_timetable_courses()`共通化、
  `GET /api/timetable/share_token`・`GET /api/timetable/shared`・`POST /api/timetable/share_revoke`追加
- `models.py`/`database.py`: `UserProfile.share_token_version`列追加（既定0、共有停止でインクリメント）
- `templates/liff/timetable.html`: 共有モーダル（友達に送る／共有を停止する、リンク流出注意書き・
  停止時の説明を明記）、`?share=`閲覧専用モード（`document.body.classList.add('view-only')` +
  CSS `.edit-only`で編集UI一括非表示、`renderReadOnlyCourseList()`で読み取り専用表示）、
  無効リンクを開いた際のわかりやすいエラー表示（`err.friendly`フラグ経由）
- dev push済み（commit 2818b74 → 0de4aa7 → 0b99408 → 0441818 → 0307c2f → 715dc1d）
