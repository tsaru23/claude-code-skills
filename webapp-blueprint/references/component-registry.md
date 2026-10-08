# 既製コンポーネントの調達ガイド

Phase 8（実装への引き渡し）で読む。設計書で決めた要件に対して、ゼロから実装コードを書く前に既製コンポーネントを当たるための調達先と手順をまとめる。

## 原則: ゼロから書く前に、まず既製コンポーネントを当たる

設計書に列挙された要件（同意管理、3D表示、フォームのセキュリティ対策、SEO、テーマ切り替え、エラー処理など）は、多くの場合すでに実装パターンが確立している。自前実装は「既製コンポーネントでは要件を満たせない」と判断できた箇所に限定する。

## 調達先の優先順位

1. **blueprint-components**（このスキルと対になる自前レジストリ）
   `https://github.com/tsaru23/blueprint-components`
   下記の10コンポーネントを収録する。webapp-blueprint スキルが対象とする要件（プライバシー・計測・3D・フォーム・SEO・基盤UI）を優先的にカバーしているため、最初に確認する。

2. **shadcn/ui 本体**
   `https://ui.shadcn.com`
   ボタン・ダイアログ・フォームパーツ・ナビゲーションなど、汎用的な UI プリミティブはこちらを優先する。blueprint-components はこれらを前提として組み立てられている。

3. **その他の OSS レジストリ**
   実在とライセンスを確認済みの調達先に限る。

   | 調達先 | 配布形態 | ライセンス | 取り込み可否 | 備考 |
   |---|---|---|---|---|
   | [Magic UI](https://magicui.design)（`magicuidesign/magicui`） | コピー＆ペースト方式 | MIT | 可（ライセンス表記を残す） | star 約22,400、更新は活発。別売の Pro テンプレートは対象外 |
   | [Obsidian UI](https://www.obsidianui.dev) | コピー＆ペースト方式 | サイト上の表記は MIT（GitHub 上の実体は未確認） | 実体の LICENSE を確認してから | 取り込み前に GitHub リポジトリの LICENSE を確認する |
   | [Aceternity UI](https://ui.aceternity.com) | コピー＆ペースト方式 | 無料コンポーネントは利用可だが、ライセンス条項でソースの再配布を明確に禁止 | 不可 | 自分の案件で使う分には問題ない |
   | [21st.dev](https://21st.dev) | shadcn 互換レジストリ | 投稿者ごとに異なる | 個別に確認してから | 案件での利用も個別に確認してから |

   「案件で使えるか」と「自分のレジストリに取り込んで再配布できるか」は別の話である。Aceternity UI がその例で、案件で使うのは問題ないが、ソースを自分のコンポーネント集として配り直すことは規約で禁じられている。名前だけを見聞きした調達先は、実在とライセンスを確認できたものだけを候補にし、確認できなかったものは候補に入れない。

   調達先を探す手順そのものはここでは扱わない。手元にあれば探索を担当する別スキル（`design-adopt` など）に任せる。

4. **自作**
   上記のいずれでも要件を満たせない場合のみ、自前実装の対象として設計書に切り出す。

## blueprint-components 収録コンポーネント一覧

導入コマンドの `<SHA>` は、ブランチ名（`main`）ではなく、導入時点のコミット SHA（40桁）に置き換える。
ブランチ名で取得すると、同じコマンドでも実行するたびに中身が変わりうる。取得元のリポジトリが
改ざんされた場合も、その内容がそのまま取り込まれる。SHA の調べ方と導入の手順は、表の下の
「導入の手順」に従う。

| コンポーネント | 用途 | 設計書の対応セクション | 導入コマンド |
|---|---|---|---|
| consent-manager | 同意状態（necessary/functional/analytics/marketing）の管理・永続化 | プライバシー・Cookie同意 | `npx shadcn@<版> add https://raw.githubusercontent.com/tsaru23/blueprint-components/<SHA>/public/r/consent-manager.json` |
| cookie-consent-banner | 同意バナー UI（同意/拒否/カテゴリ別設定） | プライバシー・Cookie同意 | `npx shadcn@<版> add https://raw.githubusercontent.com/tsaru23/blueprint-components/<SHA>/public/r/cookie-consent-banner.json` |
| consent-gate | 同意カテゴリに応じた条件付き描画 | プライバシー・Cookie同意 / 計測タグの読み込み制御 | `npx shadcn@<版> add https://raw.githubusercontent.com/tsaru23/blueprint-components/<SHA>/public/r/consent-gate.json` |
| analytics-provider | GA4 / Plausible 計測アダプタ、Consent Mode v2 連動 | 計測・アクセス解析 | `npx shadcn@<版> add https://raw.githubusercontent.com/tsaru23/blueprint-components/<SHA>/public/r/analytics-provider.json` |
| three-scene | React Three Fiber の Canvas 基盤 | 3D表現 | `npx shadcn@<版> add https://raw.githubusercontent.com/tsaru23/blueprint-components/<SHA>/public/r/three-scene.json` |
| gltf-model-viewer | glTF/GLB モデルビューア | 3D表現 | `npx shadcn@<版> add https://raw.githubusercontent.com/tsaru23/blueprint-components/<SHA>/public/r/gltf-model-viewer.json` |
| secure-form | zod + react-hook-form、honeypot、二重送信防止 | フォーム・セキュリティ対策 | `npx shadcn@<版> add https://raw.githubusercontent.com/tsaru23/blueprint-components/<SHA>/public/r/secure-form.json` |
| seo-head | メタ/OGP/JSON-LD 出力 | SEO | `npx shadcn@<版> add https://raw.githubusercontent.com/tsaru23/blueprint-components/<SHA>/public/r/seo-head.json` |
| theme-provider | light/dark/system テーマ切り替え | 基盤UI | `npx shadcn@<版> add https://raw.githubusercontent.com/tsaru23/blueprint-components/<SHA>/public/r/theme-provider.json` |
| error-boundary | エラー境界とフォールバックUI | 基盤UI・信頼性 | `npx shadcn@<版> add https://raw.githubusercontent.com/tsaru23/blueprint-components/<SHA>/public/r/error-boundary.json` |

## 導入の手順

shadcn 形式のレジストリから取り込むと、コンポーネントのソースがプロジェクトに書き込まれ、
レジストリ項目に書かれた npm パッケージもインストールされる。そのため、取り込む前に中身を
読み、取り込んだあとに差分を見る。blueprint-components 以外の shadcn 互換レジストリ
（21st.dev 等）から取り込む場合も同じ手順に従う。

1. **SHA を調べる。** 取り込みたい時点のコミット SHA を調べる。
   ```bash
   git ls-remote https://github.com/tsaru23/blueprint-components refs/heads/main
   ```
   出力の先頭40桁が SHA である。リリースのタグがあれば、タグが指すコミットの SHA を使ってよい。
2. **CLI の版を固定する。** `npx shadcn@latest` ではなく、導入時点の版を `npx shadcn@<版>` で
   指定する（`npm view shadcn version` で調べる）。CLI 自体も依存パッケージであり、
   実行するたびに最新版を取りに行く形にしない。
3. **取り込む前に中身を読む。** SHA を入れた URL の JSON を取得し、次を確かめる。
   ```bash
   curl -s https://raw.githubusercontent.com/tsaru23/blueprint-components/<SHA>/public/r/secure-form.json
   ```
   - `dependencies` / `devDependencies` に書かれた npm パッケージが、`references/tech-stack.md` の
     「依存を採用する前の審査」を通るか。版の指定がない場合は実行時点の最新版が入るため、
     公開直後の版を避けるパッケージマネージャの待機期間の設定（`references/security-baseline.md`）を
     有効にしてから取り込む
   - `files` のソースに、外部への通信・`eval`・`dangerouslySetInnerHTML` など、用途から
     考えて不要な処理が含まれていないか
   - `registryDependencies` で、さらに別の URL から取り込む項目がないか（あれば、その項目にも同じ手順を適用する）。
     `utils` のように URL ではなく名前だけの項目は、shadcn/ui 本体のレジストリから取り込まれる
4. **取り込んで差分を見る。** 作業ブランチで導入コマンドを実行し、`git diff` と
   ロックファイルの変更をすべて確認してからコミットする。
5. **検査を通す。** 依存の脆弱性監査・シークレット検出・静的解析（`references/quality-gates.md` の
   「セキュリティ検査」）を実行し、High 以上の指摘がないことを確かめる。
6. **記録する。** SHA・CLI の版・取得日を、下の形式で設計書に残す。

## 設計書への転記形式

設計書の「## 15. 利用する既製コンポーネント」セクションは以下の4列で構成される。blueprint-components から採用したコンポーネントは、この形式でそのまま転記する。取得元には、必ずコミット SHA を書く。

```markdown
## 15. 利用する既製コンポーネント

| コンポーネント名 | 取得元 | 導入コマンド | 用途 |
| --- | --- | --- | --- |
| cookie-consent-banner | blueprint-components（tsaru23, コミット: <40桁のSHA>, 取得日: 2026-09-14） | `npx shadcn@<版> add https://raw.githubusercontent.com/tsaru23/blueprint-components/<40桁のSHA>/public/r/cookie-consent-banner.json` | Cookie同意バナーの表示・カテゴリ別同意管理 |
```

## 導入前の確認事項

既製コンポーネントを採用する前に、以下を確認する。

- **ライセンス**: MIT など商用利用・改変が可能なライセンスか。制約のあるライセンス（GPL系など）の場合は法務確認を挟む。
- **再配布の可否**: 案件で使えるかどうかとは別に、自分のレジストリ等に取り込んで再配布できるかを個別に確認する。
- **依存の重さ**: npm 依存パッケージの数とバンドルサイズへの影響（例: `three` 系は3D表現が不要なページには含めない）。
- **セキュリティ**: 取り込むソースと、それが連れてくる npm パッケージを「導入の手順」に沿って確かめる。コピー＆ペースト方式のコンポーネントは、取り込んだ時点で自分のコードになる。取り込み元で脆弱性が直っても自動では反映されないため、出典を記録し、取り込み元の更新を定期的に確かめる。
- **Tailwind のバージョン整合**: 対象プロジェクトの Tailwind CSS バージョンとコンポーネントが前提とするバージョンが一致しているか（blueprint-components は Tailwind CSS v4 を前提とする）。
- **改変前提であること**: コピー＆ペースト方式のコンポーネントは「そのまま使う」ものではなく「プロジェクトに合わせて改変する」ことを前提に設計されている。命名・スタイル・挙動をプロジェクトの規約に合わせて調整する。
- **同意管理・セキュリティ系コンポーネントの限界**: `consent-manager` 系や `secure-form` などは実装の出発点であり、法的要件（GDPR、改正個人情報保護法等）の充足を保証しない。公開前に法務・専門家の確認を挟む。

## 出典の記録運用

採用したコンポーネントは、設計書またはADRに以下を記録する。

- 出典（レジストリ名・リポジトリ / パッケージ名）
- コミット SHA（Git から取り込んだ場合。必須）またはパッケージのバージョン
- 取り込みに使った CLI の版
- 取得日（YYYY-MM-DD）

これにより、後から挙動が変わった場合や脆弱性が報告された場合に、どの時点のコードを取り込んだかを追跡できるようにする。取り込み元に脆弱性の修正が入ったら、記録した SHA との差分を見て、自分のコードに反映するかを判断する。
