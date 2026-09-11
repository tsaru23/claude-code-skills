# 情報源の詳細（design-adopt Step 2 の詳細版）

各情報源について「接続状態 / 起動コマンドまたはツール名 / 料金と制限（出典URL付き）/
向いている用途 / 向いていない用途」をまとめる。

## shadcn/ui MCP

- **接続状態**: user スコープに登録済み（`npx shadcn@latest mcp`）
- **起動コマンドまたはツール名**: ツール7個
  - `get_project_registries`
  - `list_items_in_registries`
  - `search_items_in_registries`
  - `view_items_in_registries`
  - `get_item_examples_from_registries`
  - `get_add_command_for_items`
  - `get_audit_checklist`
- **料金と制限**: 完全無料・OSS。出典: https://ui.shadcn.com/docs/mcp
- **向いている用途**: 系統A（React + Tailwind）のコンポーネント駆動サイト。フォーム・テーブル・
  ダイアログなど実装済みコンポーネントの取得。第一候補として常に最初に試す
- **向いていない用途**: 系統C（非React）。表現駆動（モーション主体）のLP/ブランドサイト

## GitHub（`gh` CLI）

- **接続状態**: MCP コネクタは OAuth 未認証のため使用不可。`gh` CLI（v2.92.0）を
  認証済みのGitHubアカウントで使用（scope: repo/gist/read:org/workflow）
- **起動コマンドまたはツール名**: Bash 経由で `gh search repos` / `gh api` を実行。
  以下は実行検証済みの形。

  ```bash
  # 探索。--json license でライセンスも同時に返るので /license を別途叩かなくてよい
  gh search repos "portfolio template" --stars=">1000" --sort stars --limit 5 --json fullName,stargazersCount,updatedAt,license,description

  # ファイル単体の取得。--jq .content は base64 のまま返るので raw ヘッダを使う
  gh api repos/OWNER/REPO/contents/PATH -H "Accept: application/vnd.github.raw"
  ```
- **料金と制限**: 無料
- **向いている用途**: OSSテンプレート/実装例の探索、LICENSE確認、star数・最終更新日の確認。
  リポジトリ全体を clone せず、必要なファイルだけを取得する（トークン節約）
- **落とし穴**: クエリを3語以上に絞ると `--stars` の閾値と噛み合って0件になりやすい
  （実測: `"portfolio nextjs tailwind" --stars=">300"` は0件）。2語程度から始め、
  star数の閾値でノイズを落とす。閾値なしだと0スターのリポジトリばかり返る
- **向いていない用途**: リアルタイムの意匠検討（それは Claude Design の担当）

## Claude Design（`/design` スキル）

- **接続状態**: 利用可能
- **起動コマンドまたはツール名**: `/design` スキル。`.dc.html` アートボードを Artifact として
  公開し、ブラウザ上で直接編集できる
- **料金と制限**: 無料
- **向いている用途**: 実装前の意匠検討。例: `./design/main.dc.html`,
  `./design/works.dc.html`
- **向いていない用途**: 実装済みコンポーネントをそのままコードに落とすこと（意匠固めの後、
  改めて実装が必要）

## Figma MCP

- **接続状態**: claude.ai コネクタ経由で接続済み
- **起動コマンドまたはツール名**: `get_design_context` `get_screenshot` `get_metadata`
  `get_variable_defs` など。`whoami` `create_new_file` `add_code_connect_map` は無制限
- **料金と制限**: Starter tier / View シート → 読み取り系ツール合計で**月20回**。
  デスクトップ版サーバは Dev/Full シート必須のため使用不可。
  出典: https://developers.figma.com/docs/figma-mcp-server/rate-limits-access/
- **向いている用途**: ルート3（参照駆動）。既にFigmaに確定デザインがある場合のみ、
  何を取得するか決めてから1回で済ませて呼ぶ
- **向いていない用途**: 探索目的での利用は絶対に禁止（月20回の枠を浪費するため）

## 21st.dev

- **接続状態**: Claude Desktop の**カスタムコネクタ**（リモートMCP）として接続済み。
  APIキーは接続URLに含まれているため、環境変数の設定なしでMCPツールが使える
- **起動コマンドまたはツール名**: 主なツールは `search` / `search_picker`（カタログ検索）、
  `get_component`（コード取得・**課金対象**）、`get_theme`（テーマCSS・無料）、
  `get_inspiration`、`search_logo`、`list_team_components`、`get_usage`（残量確認）
- **料金と制限**: `get_usage` の実測値
  ```json
  {"tier":"free","freeSearchesPerDay":null,"freeRetrievalsPerDay":2,"freeRetrievalsRemaining":2}
  ```
  - **検索・メタデータ・list 系はすべて無料かつ無制限**（`freeSearchesPerDay: null`）
  - **メーターがかかるのは `get_component` だけで1日2回**
  - `get_theme` のテーマCSSは無料
  - 有料プラン: https://21st.dev/pricing
- **配布形態**: shadcn レジストリ。`search` 結果の `installCommand` は
  `npx shadcn@latest add "https://21st.dev/r/AUTHOR/NAME?api_key=$API_KEY_21ST"` の形。
  このコマンドをそのまま叩くなら環境変数 `API_KEY_21ST` が別途必要（MCP経由の取得には不要）
- **向いている用途**: **ルート2（表現駆動）の探索**。検索が無料無制限なので、
  意匠のカタログを広く見るのに最適。候補を絞り切ってから `get_component` を呼ぶ
- **向いていない用途**: 1日3件以上のコード取得。書き込み系ツール
  （`submit_component` `edit_profile` `delete_*` など）はユーザーに頼まれない限り呼ばない
- **注意**: 以前ローカルに `claude mcp add -s user 21st-magic`（stdio版）を登録したが、
  このカスタムコネクタと**役割が重複**する。stdio版はAPIキー未設定のまま接続失敗し続けるので、
  `claude mcp remove -s user 21st-magic` で削除してよい

## Lovable MCP

- **接続状態**: 未登録
- **起動コマンドまたはツール名**: `https://mcp.lovable.dev`
- **料金と制限**: 全プランで接続可だが `create_project` と `send_message` はクレジット消費。
  無料枠は1日5・月30クレジット程度。
  出典: https://docs.lovable.dev/integrations/lovable-mcp-server と
  https://docs.lovable.dev/introduction/credits-and-usage
- **向いている用途**: 該当なし（常用非推奨）
- **向いていない用途**: 常用。クレジットをすぐに消費するため design-adopt のフローには組み込まない

## Magic UI MCP / daisyUI MCP（未登録・ルート2で必要になったら追加）

- **接続状態**: 未登録。追加する場合は `claude mcp add -s user` で user スコープに入れる
- **料金と制限**: どちらも公式MCP・OSSで無料
  - Magic UI MCP: https://magicui.design/docs/mcp
    （コアコンポーネントはOSSで無料。Magic UI Pro は別売のテンプレート集で、
    Pro専用コンポーネントは有料の可能性がある）
  - daisyUI MCP: https://daisyui.com/docs/mcp/
- **向いている用途**: ルート2（表現駆動）。shadcn/ui が持たないモーション・装飾系の語彙を補う
- **向いていない用途**: ルート1のフォーム・テーブル（shadcn/ui のほうが揃っている）
