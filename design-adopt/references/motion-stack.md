# motion-stack — 表現駆動サイトをOSSだけで再構成するレシピ

## 方針

特定サイトのコードを複製するのではない。公開されている**表現要素を分解し、自分のアセットと
コピーで再構成する**。デザインの「型」（スムーススクロール、テキスト分割アニメ、極端に絞った
色数）は著作権の対象ではないが、ロゴ・アセット・コピー・コードそのものは対象である。

## buttermax.net のスタック分解（実測済み）

| 実測した検出物 | 正体 | OSSでの代替 |
|---|---|---|
| `window.gsapVersions === ["3.12.1"]`、`SplitText` | GSAP + SplitText プラグイン | **GSAP 3.13 以降は全プラグインが無料**。SplitText もそのまま使える |
| `LottieMesh` / `LottieShader` / `LottieTexture` | Lottie を WebGL テクスチャとして描画する自作実装 | `lottie-web` (MIT) + `ogl` または `three` (MIT) |
| `window.Scroll` | 独自スムーススクロール実装 | **Lenis** (MIT) |
| `__myFont_2f1432` フォント | `next/font` による自己ホスティング | 同じく `next/font/local` |
| Next.js Pages Router + CSS Modules、body背景 `rgb(16,16,16)` | — | 同構成で再現可能 |
| ページ全体が Lottie ローダーで開始 | イントロアニメーション | `lottie-web` + GSAP タイムライン |

この質感は希少ライブラリではなく、構成の選び方とモーションの作り込みの density から出ている。
再現の難所はライブラリ調達ではなく、タイミング設計とアセット（Lottie/フォント）の質である。

## 系統別の導入手順

- **系統A/B（React）**: `npm i lenis gsap` + `lottie-web`。GSAP は `ScrollTrigger` と
  `SplitText` を登録する
- **系統C（素のHTML）**: CDN ではなくローカルに置く方針、または Claude Design で `.dc.html`
  として意匠を固めてから静的HTMLに落とす
