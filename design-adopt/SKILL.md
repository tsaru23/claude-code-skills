---
name: design-adopt
description: >-
  オープンソースのフロントエンドデザインをMCP・CLI・スキル経由で探索し、対象プロジェクトに最適なものを
  選定して適用する。「UIをオシャレにして」「デザインを良くして」「このサイトみたいなデザインにして」
  「コンポーネントを持ってきて」「shadcnで作って」「LPのデザインを作って」といった依頼で使用する。
  ただし、配色・トンマナの相談だけで実装を伴わない場合は `frontend-design` スキルを使う。
---

# design-adopt — OSSフロントエンドデザインの探索と適用

## 目的

対象プロジェクトのスタックを判定したうえで、shadcn/ui MCP・GitHub(`gh` CLI)・Claude Design・
Figma MCP・21st.dev Magic MCP・Lovable MCP といった情報源をコストの安い順に使い分けて候補を探し、
ライセンス・保守性・スタック適合を基準に最適なものを選び、既存のデザイントークンに合わせて調整して
適用する。

## Step 0 — 対象プロジェクトの判定（必ず最初に実行）

対象ディレクトリの `package.json`（無ければファイル構成）を読み、次の3系統に分類する。
分類結果をユーザーに1行で伝えてから次に進む。

- **A: React + Tailwind あり** → shadcn 系がそのまま入る
- **B: React あり / Tailwind なし** → Tailwind 導入の可否を**ユーザーに確認してから**進む
  - 導入する → 系統Aとして扱う
  - 導入しない → 系統Cとして扱う（shadcn は使わず、CSS Modules / 素のCSSで実装する）
- **C: 素のHTML/CSS、または非Reactフレームワーク** → shadcn は使えない。ルート2/3のみ

判定例（イメージ）:

| プロジェクト（例） | スタック | 系統 |
|---|---|---|
| `~/projects/saas-dashboard`（SaaS管理画面系） | Next 16 + React 19 + Tailwind v4 | A |
| `~/projects/internal-tool`, `~/projects/meeting-notes-app`（社内ツール系） | Next 16 + React 19（Tailwindなし） | B |
| `~/projects/habit-tracker`, `~/projects/study-log`（記録アプリ系） | Vite + React 19 | B |
| `~/projects/camera-app`（カメラ系ツール） | Vite（Reactなし） | C |
| `~/projects/landing-page`, `~/projects/docs-site`（LP系） | 素のHTML/CSS | C |

## Step 1 — ルートを選ぶ

サイトの性格で3つに分岐する。ルートによって**主に使う情報源が変わる**。

| ルート | 対象 | 主な情報源 |
|---|---|---|
| **1 コンポーネント駆動** | 管理画面・SaaS・フォーム/テーブル主体 | shadcn/ui MCP（+ GitHub でテンプレ探索） |
| **2 表現駆動** | LP・ポートフォリオ・ブランドサイト（モーションと余白で見せる） | `references/motion-stack.md` を**先に読む**。21st.dev の `search`（無料・無制限）+ GitHub |
| **3 参照駆動** | 既にFigmaに確定デザインがある | Figma MCP。ただし**月20回制限**（後述）を厳守 |

**ルート2では shadcn/ui MCP はほとんど役に立たない**（フォーム部品のカタログであってモーションの
語彙を持たないため）。ルート2だと判定したら、shadcn を探しに行かずに `references/motion-stack.md`
へ進むこと。

## Step 1.5 — 意匠を先に固める（ルート2、および既存デザインを大きく変える場合）

実装に入る前に `/design` スキル（Claude Design）で `.dc.html` アートボードを起こし、
**レイアウト・タイポスケール・色数をユーザーと合意してから** Step 2 に進む。
先にコードを書いてから見た目を議論すると手戻りが大きい。

例: `./design/main.dc.html`, `./design/works.dc.html`

ルート1で、既存のデザイントークンに沿って部品を足すだけの場合はこの Step を飛ばしてよい。

## Step 2 — 情報源の使い分け（コストの安い順に試す）

**この順序を守ることがトークンとクォータの節約になる。**

| 情報源 | 呼び出し方 | コスト | 役割 |
|---|---|---|---|
| shadcn/ui MCP | `get_project_registries` → `search_items_in_registries` → `view_items_in_registries` → `get_add_command_for_items` → `get_audit_checklist` | 無料・無制限 | **ルート1の第一候補**。実装コンポーネントの取得 |
| GitHub | `gh` CLI（認証済みのGitHubアカウントで使用・scope: repo/gist/read:org/workflow） | 無料 | OSSテンプレ/実装例の探索、**LICENSE確認**、star数・最終更新の確認 |
| Claude Design | `/design` スキル（`.dc.html` アートボード） | 無料 | Step 1.5 の意匠検討 |
| 21st.dev | 接続済み（Claude Desktop カスタムコネクタ） | `search` 等は**無料・無制限**／`get_component` のみ**1日2回** | **ルート2の探索源**。意匠のカタログ閲覧に使う |
| Magic UI MCP / daisyUI MCP | 未登録。必要になったら `claude mcp add -s user` で追加 | 無料・OSS | ルート2の補完（21st.dev で足りないとき） |
| Figma MCP | 接続済み（claude.aiコネクタ） | **月20回のみ** | 既存デザインの参照だけ。探索には絶対に使わない |
| Lovable MCP | 未登録 | クレジット消費（無料枠は1日5・月30程度） | 常用しない |

詳細は `references/sources.md` を参照。

### shadcn/ui MCP の注意書き

**必ず `get_project_registries` から始める。** 対象プロジェクトに `components.json` が無い、
またはレジストリが未設定だと後続のツールが失敗する。未設定なら先に
`npx shadcn@<版> init` を実行するかどうかをユーザーに確認すること。

**CLI は `@latest` で実行しない。** 導入時点の版を `npm view shadcn version` で調べ、
`npx shadcn@<版>` の形で固定する（CLI 自体も依存パッケージであり、実行のたびに最新版を
取りに行かせない）。`get_add_command_for_items` や 21st.dev の `installCommand` が
`@latest` 付きのコマンドを返しても、固定した版に書き換えてから実行する。
この方針と後述の取り込み前後の確認は、`webapp-blueprint/references/component-registry.md` の
「導入の手順」に揃えている。

### 21st.dev の注意書き

`get_usage` の実測値: `tier: free` / `freeSearchesPerDay: null`（無制限）/ `freeRetrievalsPerDay: 2`。

- **`search` `search_picker` および list/metadata 系はすべて無料・無制限**。遠慮なく探索に使う。
  ユーザーに候補を見せて選ばせたいときは `search_picker`（インラインのピッカーが出る）
- **メーターがかかるのは `get_component`（コード本体の取得）だけで1日2回**。
  候補を3つに絞り込んでから呼ぶこと。残量は `get_usage` で確認できる
- `get_theme` が返すテーマCSSは無料。ただしカタログは薄い（実測: 「dark theme」で1件）
- **配布形態は shadcn レジストリ**。`search` の結果に含まれる `installCommand` は
  `https://21st.dev/r/AUTHOR/NAME?api_key=$API_KEY_21ST` を `shadcn add` に渡す形で、
  CLI の版が `@latest` になっている。**そのまま実行せず**、版を固定し、Step 4 の
  「取り込み前後の確認」を通してから取り込む。CLI で取り込む場合は環境変数 `API_KEY_21ST` が
  必要で、キーの設定はユーザーに依頼する
- **URL にAPIキーが入る**ので、キーの値をコマンドライン・ログ・ドキュメントに出さない。
  コマンドには必ず `$API_KEY_21ST` のまま書き、`docs/design-sources.md` などに出典を記録するときは
  `?api_key=...` を除いた `https://21st.dev/r/AUTHOR/NAME` だけを書く
- 書き込み系（`submit_component` `edit_profile` `delete_*` など）は**ユーザーに明示的に
  頼まれない限り呼ばない**

### Figma の注意書き

ユーザーのプランは Starter tier / View シートで、`get_design_context` `get_screenshot`
`get_metadata` `get_variable_defs` などの読み取り系ツールが**合計で月20回**を共有する。
機能ブロックではなく回数制限。だから「とりあえずFigmaを見る」は禁止で、Figmaに確定デザインが
ある場合に限り、**呼ぶ前に何を取得するか決めてから1回で済ませる**こと。
出典: https://developers.figma.com/docs/figma-mcp-server/rate-limits-access/

### GitHub の注意書き

GitHub の MCP コネクタは OAuth 未認証のため使えない。`gh` CLI を使うこと。
以下は**実行検証済み**のコマンド。

探索（`--json license` でライセンスも同時に返るので、リポジトリごとに `/license` を叩かなくてよい）:

```bash
gh search repos "portfolio template" --stars=">1000" --sort stars --limit 5 --json fullName,stargazersCount,updatedAt,license,description
```

ファイル単体の取得（`--jq .content` は base64 のまま返るので使わない。raw ヘッダを使う）:

```bash
gh api repos/OWNER/REPO/contents/PATH -H "Accept: application/vnd.github.raw"
```

コツと落とし穴:

- **クエリは2語程度から始める。** 3語以上に絞ると `--stars` の閾値と噛み合って0件になりやすい
  （実測: `"portfolio nextjs tailwind" --stars=">300"` は0件、`"portfolio template" --stars=">1000"`
  は良質な候補が返る）
- star数の閾値でノイズを落とす。閾値なしだと0スターのリポジトリばかり返る
- リポジトリ全体を clone せず、必要なファイルだけ取ること（トークン節約）

## Step 3 — 選定基準（「最適なものを選ぶ」の中身）

候補を最大3つに絞り、次の軸で比較表を作ってユーザーに提示してから適用に進む。

1. **スタック適合** — Step 0 の系統に無改造で入るか
2. **ライセンス** — MIT / Apache-2.0 / ISC のみ採用可。**GPL系・独自ライセンス・ライセンス表記なしは
   不採用**。`gh search repos --json license` の結果で確認する
3. **依存の重さ** — 新規に増える依存パッケージ数。既存の依存で済むものを優先
4. **保守性** — star数と最終更新日（1年以上更新なしは減点）
5. **デザイントークン整合** — 既存の配色・角丸・フォントスケールを壊さないか

軸2で実際に落ちる例は多い。実測した検索結果の上位3件のうち、`chetanverma16/react-portfolio-template`
は**ライセンス表記なし**、`rammcodes/Dopefolio` は **GPL-3.0** で、いずれも不採用になった。
star数だけで選ばないこと。

## Step 4 — 適用

- 系統Aの場合: `get_add_command_for_items` で得たコマンドを、CLI の版を `npx shadcn@<版>` に
  書き換えたうえで、下の「取り込み前後の確認」に沿って実行する。最後に `get_audit_checklist` を
  必ず走らせる
- GitHub由来のコードを取り込む場合: ブランチ名ではなく**コミット SHA で固定**した URL
  （`gh api repos/OWNER/REPO/commits/BRANCH --jq .sha` で調べる）から取得する。
  **ライセンス表記を保持**し、ファイル冒頭に出典URL（SHA入り）をコメントで残す

### 取り込み前後の確認（shadcn 互換レジストリ全般）

レジストリから取り込むと、ソースがプロジェクトに書き込まれ、項目に書かれた npm パッケージも
インストールされる。shadcn/ui・21st.dev・GitHub 上のレジストリのいずれでも、次の順で進める。

1. **版を固定する。** `npm view shadcn version` で CLI の版を調べ、`npx shadcn@<版>` で実行する。
   Git 上のレジストリ JSON を使うときは、URL のブランチ名部分をコミット SHA に置き換える
2. **取り込む前に JSON を読む。** レジストリ項目の JSON を取得して、次を確かめる
   - `dependencies` / `devDependencies`: 増える npm パッケージが妥当か。版指定がなければ
     実行時点の最新版が入る点に注意する（依存の審査は `webapp-blueprint/references/tech-stack.md`）
   - `files`: ソースに外部への通信・`eval`・`dangerouslySetInnerHTML` など、用途から見て
     不要な処理がないか
   - `registryDependencies`: さらに別の URL から取り込む項目がないか。あれば、その項目にも
     同じ確認をする
3. **取り込んで差分を見る。** 作業ブランチで add を実行し、`git diff` と
   ロックファイル（`package-lock.json` / `pnpm-lock.yaml` など）の差分をすべて確認してから
   コミットする
4. **記録する。** Step 5 の項目に、SHA（Git から取り込んだ場合）・CLI の版・取得日を加える

shadcn/ui 公式や Git 上の JSON は、`curl -s <URL>` でそのまま取得してよい。

21st.dev の JSON は URL に APIキーが入るため、キーをコマンドライン引数・シェル履歴・ログに
出さない形で取得する。`--data-urlencode "api_key@-"` は値を標準入力から読むので、
キーがプロセスの引数にも現れない。取得結果はファイルに保存して読む。

```bash
printf '%s' "$API_KEY_21ST" | curl -sfG "https://21st.dev/r/AUTHOR/NAME" --data-urlencode "api_key@-" -o /tmp/21st-NAME.json
```

- `curl -v` や `set -x` は URL（キー入り）を出力するので使わない
- 読んで問題がなければ、確認したそのファイルを `npx shadcn@<版> add /tmp/21st-NAME.json` で
  取り込むと、確認した内容と取り込む内容が一致する。CLI の版がローカルファイルを受け付けない
  場合は、`npx shadcn@<版> add "https://21st.dev/r/AUTHOR/NAME?api_key=$API_KEY_21ST"` の形で
  実行する（キーは環境変数のまま渡し、値を直接書かない）
- CLI のエラー出力に URL が含まれることがあるので、出力をそのままドキュメントや
  Issue に貼らない
- 既存のデザイントークン（CSS変数 / `tailwind.config` / `globals.css`）に合わせて色・角丸・
  タイポを**必ず上書き調整**する。素のまま貼るとテンプレート臭が出る
- 適用後の検証: `webapp-testing` スキルまたは `/run` で実際に起動してスクリーンショットを撮り、
  ユーザーに見せる

## Step 5 — 出典とライセンスの記録

対象プロジェクト直下に `docs/design-sources.md` を作成/追記する。記録する項目:

- 日付（取得日）
- 採用元（URL。Git 由来ならコミット SHA 入り。21st.dev は `?api_key=...` を除いた URL）
- コミット SHA（Git から取り込んだ場合は必須）
- 取り込みに使った CLI の版（`shadcn@<版>`）
- ライセンス
- 取り込んだファイル
- 変更点

## 禁止事項

- 特定サイトのロゴ・画像・Lottie/動画アセット・コピー文をそのまま複製しない
- 非公開ソースをリバースエンジニアリングして貼り付けない（minified バンドルの復元を含む）
- GPL系ライセンスのコードを、ライセンス条件を満たさないまま取り込まない
- Figma MCP を「探索目的」で呼ばない（月20回の枠を浪費するため）
- APIキー・トークンの発行や入力は行わない。必要な場合はユーザーに依頼する
