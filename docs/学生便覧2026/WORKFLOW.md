# 学生便覧→DB反映 作業手順書（ファイル整理＋単位チェッカー実装フロー）

作成日: 2026-07-17
目的: 学生便覧から必要データだけを抽出した「学部名.md」を作り、現行DBと照合して不足科目・分類を追加し、
学部ごとの卒業要件（分類別必要単位数）を単位チェッカー（マイ時間割LIFF）で可視化するまでの標準手順と、
そのために生成された関連ファイルの整理方針をまとめる。

便覧の読み方そのもの（PDF抽出・別表の凡例・データ品質の罠など）は [BINRAN_READING_GUIDE.md](BINRAN_READING_GUIDE.md) が正典。
本書は「ファイルをどこに置き、どのスクリプトをどの順で動かすか」のパイプライン手順書。

---

## A. 関連ファイルの現状マップ（2026-07-17時点）

### programing files/（運用スクリプト。**削除・移動禁止**）

| ファイル | 役割 | 状態 |
|---|---|---|
| `seed_nogaku_credit_requirements.py` | 農学部の credit_requirements（6コース×12区分=72件）+ CAP（54単位）投入 | dev投入済み・本番未実行 |
| `seed_nogaku_subject_categories.py` | 農学部の科目↔コース紐付け（subject_credit_categories 518件）投入。`--dry-run` あり | dev投入済み・本番未実行 |
| `update_nogaku_credit_notes.py` | 農学部の各区分noteに「対象科目：〜」一覧を追記（seed_nogaku_subject_categories実行後に走らせる） | dev実行済み・本番未実行 |
| `seed_bungaku_credit_requirements.py` | 文学部の credit_requirements + CAP（54単位）+ required_subjects（基盤系4科目）投入 | dev投入済み・本番未実行 |

これらは**本番デプロイ時に `--env prod` で再実行する必要がある**ため削除禁止
（DB同期スクリプト `sync_db_to_prod.py` は credit_requirements 等を同期するが、seedスクリプトは
再現手順・冪等な再投入手段として保持する）。

### docs/（ドキュメント・作業ログ）

| ファイル | 役割 | 整理方針 |
|---|---|---|
| `docs/学生便覧2026/{学部}/` | 学部ごとの便覧PDF原本 + 抽出結果「学部名.md」 | 現行の正しい構成。維持 |
| `docs/学生便覧2026/IMPLEMENTATION_CANDIDATES.md` | 全学部の実装候補一覧（管理画面 /admin/binran_discrepancies が読む） | 移動・改名禁止（.gitignore/管理画面が参照） |
| `docs/学生便覧2026/BINRAN_READING_GUIDE.md` | 便覧読み込みルールブック | 維持 |
| `docs/TODO/NOGAKU_CREDIT_CHECKER_TODO.md` | 農学部の残課題（未マッチ31件・生産環境工学コース内訳未確定 等） | → `docs/学生便覧2026/nogaku/` へ移動（学部固有のため） |
| `docs/TODO/TIMETABLE_SHARE_TODO.md` | 時間割友達共有のTODO（本作業と無関係・機能自体は完成済み） | 内容確認のうえ削除可 |
| `docs/search_unmatched.txt` / `search_unmatched2.txt` | 農学部の便覧科目名をDBへ部分一致検索した作業ログ（使い捨て） | 削除可（結論はNOGAKU_CREDIT_CHECKER_TODO.mdの未マッチ31件リストに反映済み） |

---

## B. ファイル整理整頓の手順

以下を上から順に実行する（すべてリポジトリルートで実行）。

```powershell
# 1. 農学部TODOを学部フォルダへ移動（git管理下なので git mv）
git mv "docs/TODO/NOGAKU_CREDIT_CHECKER_TODO.md" "docs/学生便覧2026/nogaku/NOGAKU_CREDIT_CHECKER_TODO.md"

# 2. 時間割共有TODOは完成済み機能のメモ。中身を一読して不要なら削除
#    （残したい場合はそのままでよい。TODOフォルダが空になったら削除）
git rm "docs/TODO/TIMETABLE_SHARE_TODO.md"

# 3. 使い捨ての検索ログを削除（git管理下に入っていれば git rm、未追跡なら Remove-Item）
git rm "docs/search_unmatched.txt" "docs/search_unmatched2.txt"

# 4. フォルダ名の統一: shisutemujoho → sysinfo（他学部はローマ字省略形、CLAUDE.mdの記載も sysinfo）
git mv "docs/学生便覧2026/shisutemujoho" "docs/学生便覧2026/sysinfo"

# 5. 医学部PDFのリネームが未コミットのまま（旧 binran_2026_igaku.pdf 削除 + 新 binran_2026_igaku1_iryososei.pdf 追加）
git add "docs/学生便覧2026/igaku/"

# 6. まとめてコミット
git commit -m "学生便覧2026関連ファイルを整理。農学部TODOを学部フォルダへ移動、使い捨て検索ログ削除、sysinfoフォルダ名統一"
git push origin dev
git push origin dev:shindairaifuhaku-dev
```

注意事項:
- **`IMPLEMENTATION_CANDIDATES.md` は移動・改名しない**（管理画面と .gitignore ルールが絶対パスで参照）。
- **`programing files/` のseed系スクリプト4本は動かさない**（上表参照）。
- CLAUDE.md には「docs/ は .gitignore 対象」と書かれているが、現在の `.gitignore` に docs の行は無く
  docs 配下は全て git 管理下にある（医学部PDFがgit statusに出るのはそのため）。実態に合わせて
  CLAUDE.md / AGENTS.md の該当記述を更新するか、意図的でなければ .gitignore を復旧するか要判断。

---

## C. 標準フロー: 1学部を「便覧→DB→可視化」まで処理する手順

1学部あたりの作業を10ステップに分ける。

**現在地（2026-07-17）: Step 0〜3（便覧PDF配置・学部名.md抽出・DB照合・まとめ）は調査済み10学部すべて完了済み。**
確定データは各 `学部名.md` と `IMPLEMENTATION_CANDIDATES.md` に集約されている。
これからの作業は **Step 4以降（DB投入〜可視化）を未投入学部について回すこと**（下記D参照）。
Step 0〜3 の手順は、今後新しい学部（医学部医学科・経済学部）を調査するときのために残している。

### Step 0. 準備
- `docs/学生便覧2026/BINRAN_READING_GUIDE.md` を必ず読む（PDF入手経路・凡例・罠・チェックリスト）。
- `git log --oneline -20` と `git diff` で並行セッションの変更が無いか確認する。

### Step 1. 便覧PDFの入手と配置
- 便覧PDFを入手し `docs/学生便覧2026/{学部ローマ字}/binran_2026_{学部}.pdf` として配置。
  学部フォルダが無ければ既存の命名（bungaku / hougaku / igaku / kaiyo / keiei / kokusai_ningen /
  kougaku / nogaku / rigaku / sysinfo）に倣って新規作成する。

### Step 2. 「学部名.md」の生成（必要データの抽出）
- 既存の `nogaku/nogaku.md` の章立てをテンプレートにする:
  `0.メタ情報 / 1.履修登録上限(CAP) / 2.単位要件(credit_requirements用) / 3.必修科目(required_subjects用) / 4.特殊分岐・注意事項 / 5.DB突き合わせ結果 / 6.クロスチェック結果`
- 抽出はGrep等の機械的絞り込みを優先し、PDF全文をLLMに読ませない（トークン節約の恒久ルール）。

### Step 3. DBとの照合（科目の不足洗い出し）
- 学部名.md の科目一覧を dev DB の `subjects` と突合する。突合時の注意:
  - 共通専門基礎科目（線形代数・微分積分等）は `faculty='教養教育院'` 側にある。学科分類だけ見ると欠落と誤判定する。
  - 全角/半角・ローマ数字（Ⅰ vs I）・空白の表記ゆれを正規化してから比較する。
  - 突合結果は学部名.md の「5. DB突き合わせ結果」に記録する（一時テキストファイルを作らない。
    作った場合は結論を.mdに転記して削除する）。

### Step 4. 不足科目のDB投入
- **シラバスデータはClaudeが自力取得できない**。不足科目はシラバスサイトの該当ページを
  手動コピペして `data/syllabus_{学部}_{曜日等}.txt` として用意する。
- 投入: `cd "programing files"` して
  `python -X utf8 import_syllabus.py --env dev --also-courses --faculty {学部名} <データファイル>`
  （単位数・開講年次のスクレイピングは自動で走る。新しい学部コードなら FACULTY_PATH を
  `import_syllabus.py` / `fetch_syllabus_info.py` / `core/config.py` / `templates/liff/timetable.html` の4箇所に追加）。

### Step 5. 単位要件・CAPの投入スクリプト作成
- `seed_bungaku_credit_requirements.py`（学科分岐なし）または `seed_nogaku_credit_requirements.py`
  （コース分岐あり）をテンプレートに `seed_{学部}_credit_requirements.py` を作成。
- 投入対象: `credit_requirements`（category_id は `{学部or コース接頭辞}_{区分}` 形式）、
  `registration_caps`（CAP、departmentがNULLなら学部共通値）、確定できる場合のみ `required_subjects`。
- **required_subjects は grade（学年）が完全一致でしか発動しない。学年が便覧から確定できない科目は投入禁止**。
- 冪等に書く（既存行はUPDATE、無ければINSERT）。`--env dev` で実行し件数を確認。

### Step 6. 科目↔区分の紐付け（対象学部のみ）
- 便覧の別表に「この区分にはこの科目群」の指定がある学部は、`seed_nogaku_subject_categories.py` を
  テンプレートに `seed_{学部}_subject_categories.py` を作成し `subject_credit_categories` へ投入。
- 必ず `--dry-run` で未マッチ科目を確認 → 表記ゆれは `NAME_ALIASES` に追加して再実行。
  それでも未マッチのものは学部名.md「5. DB突き合わせ結果」に残課題として記録。
- 紐付け後、`update_nogaku_credit_notes.py` 相当で各区分の note に「対象科目：〜」一覧を追記
  （単位チェッカーUIの注記に表示される）。

### Step 7. フロントエンドへの学部追加
- `templates/liff/timetable.html` の `_CREDIT_CHECKER_DEPARTMENTS` に学部・学科（コース）名を追加。
  **これを忘れるとDBにデータがあっても単位チェッカーUI自体が表示されない**（農学部で実際に発生）。

### Step 8. 実装候補一覧の更新と齟齬確認
- `IMPLEMENTATION_CANDIDATES.md` に学部セクションを追記・更新。
- dev の `/admin/binran_discrepancies` で便覧×DBの齟齬一覧を確認し、ステータスを更新。

### Step 9. dev検証
- dev LIFF（shindairaifuhaku-1.onrender.com）のマイ時間割で、該当学部プロフィールに切り替えて
  単位チェッカーの区分・必要単位数・進捗バー・note注記の表示を目視確認。
- 成績表PDF連携がある学部は `/api/parse_seiseki` の区分ラベル対応（`core/seiseki.py`）も確認。

### Step 10. コミットとデプロイ
```powershell
git add <変更ファイル>
git commit -m "{学部名}の単位チェッカー・CAP・必修科目データを投入。学生便覧2026別表第N準拠"
git push origin dev
git push origin dev:shindairaifuhaku-dev   # 両方必須（片方だけでは未反映）
```
- **本番反映はユーザーの明示指示があるときのみ**: `git push origin dev:shindairaifuhaku-prod` →
  `sync_db_to_prod.py` → 各seedスクリプト `--env prod` 実行、の順。

---

## D. 現在の進捗と次のアクション（2026-07-17時点・dev DB実測値）

### フェーズ1: 便覧調査（抽出・DB照合・まとめ）— **10学部すべて完了**
経営 / 文 / 農 / 工（5学科+共通）/ 海洋政策 / システム情報 / 国際人間（4学科）/ 法 / 理（5学科）/
医（医療創成工学科+保健学科4専攻）— 確定値は `IMPLEMENTATION_CANDIDATES.md` と各学部フォルダの `学部名.md` 参照。

### フェーズ2: dev DBへの投入 — **4学部のみ完了**（credit_requirements実測）

| 学部 | credit_requirements | CAP | required_subjects | 科目紐付け |
|---|---|---|---|---|
| 経営学部 | 19件 ✓ | 49単位 ✓ | − | 群判定は自動化済み |
| 文学部 | 17件 ✓ | 54単位 ✓ | 4件 ✓ | − |
| 農学部 | 72件 ✓ | 54単位 ✓ | grade未確定で見送り | 518件 ✓（未マッチ31件） |
| 工学部 | 53件 ✓ | 54単位 ✓ | 機械5件のみ ✓ | − |
| 海洋政策科学部 | **未投入** | 未投入 | − | − |
| システム情報学部 | **未投入** | 未投入 | − | − |
| 国際人間科学部（4学科） | **未投入** | 未投入 | − | − |
| 法学部（3コース分岐） | **未投入** | 未投入 | − | − |
| 理学部（5学科） | **未投入** | 未投入 | − | − |
| 医学部（保健4専攻+医療創成） | **未投入** | 未投入 | − | − |

### 次のアクション（優先度順）

1. **B章のファイル整理を実行してコミット**（5分で終わる）。
2. **未投入6学部のDB投入**（本作業のメイン）。1学部ずつ:
   `IMPLEMENTATION_CANDIDATES.md` の確定値から `seed_{学部}_credit_requirements.py` を作成
   （雛形: 学科・コース分岐あり→`seed_nogaku_credit_requirements.py` / なし→`seed_bungaku_credit_requirements.py`）
   → `--env dev` 実行 → Step 7（`_CREDIT_CHECKER_DEPARTMENTS` 追加）→ Step 9（dev目視）→ Step 10（コミット）。
   科目↔区分の指定がある学部は Step 6（subject_categories 紐付け+note追記）も行う。
3. **農学部の未マッチ31件**（`NOGAKU_CREDIT_CHECKER_TODO.md` 参照）: 前期科目・開講停止・表記ゆれの
   切り分け → シラバスサイトで時間割コード特定 → `import_syllabus.py` 補完 or `NAME_ALIASES` 追加。
4. **農学部・工学部の required_subjects**: 抽出・DB突合は完了しているが学年（grade）が便覧から
   確定できず投入見送り中。gradeは完全一致でしか発動しないため、確定するまで投入禁止。
5. **農学部 生産環境工学コースの専門科目内訳**: 「別に定める指定科目」の一覧が便覧に無く合計値のみ登録。
6. **新規調査（Step 0〜3から）**: 医学部医学科（便覧未入手）・経済学部（便覧未調査。科目データ216件は投入済み）。
7. **本番反映**: 上記すべて dev のみ。本番デプロイ時は Step 10 の本番手順 + 全seedスクリプトの
   `--env prod` 実行 + `sync_db_to_prod.py` を忘れないこと。
