# claude-code-skills

Claude Code で使っているスキルのうち、自分で作った4つをまとめたリポジトリです。スキルは、`SKILL.md` に「どんな依頼で動くか」と「どう進めるか」を書いたフォルダのことで、依頼の内容が `description` の条件に合うと、Claude Code がその手順を読み込んで動きます（Agent Skills）。導入の手順と、動かないときの確認点は末尾にまとめています。

| スキル | 何をするか | 追加で必要なもの |
|---|---|---|
| [model-orchestrator](#model-orchestrator) | 上位モデルに設計とレビューを任せ、実装は難しさに応じて安いモデルへ振り分ける | なし |
| [indie-marketing](#indie-marketing) | 個人開発のプロダクトを広めるための計画を立て、テストしながら回す | 投稿文づくりに別スキル `sns-post-writer`（未収録） |
| [design-adopt](#design-adopt) | オープンソースのデザインを探して、プロジェクトに合うものを取り込む | shadcn/ui の MCP、GitHub CLI（`gh`）、Figma の MCP など |
| [session-title-refresh](#session-title-refresh) | セッション名を、何をしているか分かる名前に付け直す | セッションの一覧取得と改名ができるツール |

## model-orchestrator

複数のタスクをまとめて実装するときに使います。上位のモデルは設計・担当の割り当て・レビューだけを受け持ち、コードを書く作業は Opus / Sonnet / Haiku のサブエージェントに難しさで振り分けます。レビューで不合格になった成果物は、指摘をそのまま添えて一段上のモデルにやり直させます。

高いモデルのトークンは、判断にだけ使う。これが狙いです。「タスクを振り分けて」「コスパよく実装して」のような依頼で動きます。

## indie-marketing

作ったアプリやツールが広まらないときの相談役です。まずプロダクトが検証前なのか拡大期なのかを見極め、有料広告と無料の手段（SNS 運用、共有機能、SEO / LLMO、ASO）から合うものを選びます。そのうえで数値目標つきの計画書を作り、テストの記録を残しながら進めます。

`references/playbook.md` は、ShinCode 氏の YouTube 動画を要約・再構成したものです。出典はファイルの冒頭にあります。

## design-adopt

UI の見た目を良くしたいときに、ゼロから作らず、既存のオープンソースのデザインを探して取り込みます。探す順番は shadcn/ui の MCP、GitHub、Figma の MCP、21st.dev で、手間の少ない情報源から当たります。採用するのは MIT / Apache / ISC ライセンスのものだけで、出典を記録するところまでが手順に入っています。

## session-title-refresh

セッション一覧に、無題のままのものや中身と合わない名前が並んでいるときに使います。名前を「分類タグ＋状態＋内容」の形に付け直し、すでに内容と合っている名前には触りません。手で1回だけ実行しても、定期実行に組み込んでもかまいません。

## 導入方法

使いたいスキルのフォルダを、そのまま `~/.claude/skills/` の下にコピーします。

```
~/.claude/skills/model-orchestrator/
~/.claude/skills/indie-marketing/
~/.claude/skills/design-adopt/
~/.claude/skills/session-title-refresh/
```

コピーのあとに設定を足す必要はありません。依頼の内容が各スキルの `description` に合えば、自動で読み込まれます。

## 動かないときに確認すること

- **indie-marketing**: 投稿文を作る段階で、このリポジトリに入っていない `sns-post-writer` を呼びます。その段階だけは、別の方法で投稿文を用意してください
- **design-adopt**: shadcn/ui の MCP、GitHub CLI（`gh`）、Figma の MCP、21st.dev を使う手順があり、つながっていないツールの段階は動きません
- **session-title-refresh**: セッションの一覧取得と名前の変更ができるツール（Claude のデスクトップアプリなどが提供するもの）が要ります。Claude Code だけの環境では動かないことがあります

うまく動かないときや改善の提案は、Issues に書いてください。

## ライセンス

MIT License です。全文は [LICENSE](LICENSE) にあります。
