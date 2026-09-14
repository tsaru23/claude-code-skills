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
   shadcn CLI 互換の公開レジストリ（例: 個別 OSS プロジェクトが配布する `registry.json` 準拠のコンポーネント集）。採用する場合はライセンスとメンテナンス状況を必ず確認する。

4. **自作**
   上記のいずれでも要件を満たせない場合のみ、自前実装の対象として設計書に切り出す。

## blueprint-components 収録コンポーネント一覧

| コンポーネント | 用途 | 設計書の対応セクション | 導入コマンド |
|---|---|---|---|
| consent-manager | 同意状態（necessary/functional/analytics/marketing）の管理・永続化 | プライバシー・Cookie同意 | `npx shadcn@latest add https://raw.githubusercontent.com/tsaru23/blueprint-components/main/public/r/consent-manager.json` |
| cookie-consent-banner | 同意バナー UI（同意/拒否/カテゴリ別設定） | プライバシー・Cookie同意 | `npx shadcn@latest add https://raw.githubusercontent.com/tsaru23/blueprint-components/main/public/r/cookie-consent-banner.json` |
| consent-gate | 同意カテゴリに応じた条件付き描画 | プライバシー・Cookie同意 / 計測タグの読み込み制御 | `npx shadcn@latest add https://raw.githubusercontent.com/tsaru23/blueprint-components/main/public/r/consent-gate.json` |
| analytics-provider | GA4 / Plausible 計測アダプタ、Consent Mode v2 連動 | 計測・アクセス解析 | `npx shadcn@latest add https://raw.githubusercontent.com/tsaru23/blueprint-components/main/public/r/analytics-provider.json` |
| three-scene | React Three Fiber の Canvas 基盤 | 3D表現 | `npx shadcn@latest add https://raw.githubusercontent.com/tsaru23/blueprint-components/main/public/r/three-scene.json` |
| gltf-model-viewer | glTF/GLB モデルビューア | 3D表現 | `npx shadcn@latest add https://raw.githubusercontent.com/tsaru23/blueprint-components/main/public/r/gltf-model-viewer.json` |
| secure-form | zod + react-hook-form、honeypot、二重送信防止 | フォーム・セキュリティ対策 | `npx shadcn@latest add https://raw.githubusercontent.com/tsaru23/blueprint-components/main/public/r/secure-form.json` |
| seo-head | メタ/OGP/JSON-LD 出力 | SEO | `npx shadcn@latest add https://raw.githubusercontent.com/tsaru23/blueprint-components/main/public/r/seo-head.json` |
| theme-provider | light/dark/system テーマ切り替え | 基盤UI | `npx shadcn@latest add https://raw.githubusercontent.com/tsaru23/blueprint-components/main/public/r/theme-provider.json` |
| error-boundary | エラー境界とフォールバックUI | 基盤UI・信頼性 | `npx shadcn@latest add https://raw.githubusercontent.com/tsaru23/blueprint-components/main/public/r/error-boundary.json` |

## 設計書への転記形式

設計書の「## 14. 利用する既製コンポーネント」セクションは以下の4列で構成される。blueprint-components から採用したコンポーネントは、この形式でそのまま転記する。

```markdown
## 14. 利用する既製コンポーネント

| コンポーネント名 | 取得元 | 導入コマンド | 用途 |
| --- | --- | --- | --- |
| cookie-consent-banner | blueprint-components（tsaru23, 取得日: 2026-09-14） | `npx shadcn@latest add https://raw.githubusercontent.com/tsaru23/blueprint-components/main/public/r/cookie-consent-banner.json` | Cookie同意バナーの表示・カテゴリ別同意管理 |
```

## 導入前の確認事項

既製コンポーネントを採用する前に、以下を確認する。

- **ライセンス**: MIT など商用利用・改変が可能なライセンスか。制約のあるライセンス（GPL系など）の場合は法務確認を挟む。
- **依存の重さ**: npm 依存パッケージの数とバンドルサイズへの影響（例: `three` 系は3D表現が不要なページには含めない）。
- **Tailwind のバージョン整合**: 対象プロジェクトの Tailwind CSS バージョンとコンポーネントが前提とするバージョンが一致しているか（blueprint-components は Tailwind CSS v4 を前提とする）。
- **改変前提であること**: コピー＆ペースト方式のコンポーネントは「そのまま使う」ものではなく「プロジェクトに合わせて改変する」ことを前提に設計されている。命名・スタイル・挙動をプロジェクトの規約に合わせて調整する。
- **同意管理・セキュリティ系コンポーネントの限界**: `consent-manager` 系や `secure-form` などは実装の出発点であり、法的要件（GDPR、改正個人情報保護法等）の充足を保証しない。公開前に法務・専門家の確認を挟む。

## 出典の記録運用

採用したコンポーネントは、設計書またはADRに以下を記録する。

- 出典（レジストリ名・リポジトリ / パッケージ名）
- バージョンまたはコミットハッシュ（可能な場合）
- 取得日（YYYY-MM-DD）

これにより、後から挙動が変わった場合や脆弱性が報告された場合に、どの時点のコードを取り込んだかを追跡できるようにする。
