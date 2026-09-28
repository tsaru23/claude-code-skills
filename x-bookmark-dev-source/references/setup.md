# セットアップ手順

`scripts/xbm.py` は公式 X API v2 だけを使う CLI です。スクレイピングや Cookie の利用は一切行いません。自分のブックマーク取得は 1 件 $0.001、投稿者本人のリプライ検索は保存した投稿あたり最大 $0.05 の従量課金です。

このスキルのスクリプトは **skills フォルダの中では動かしません**。`data/` を git リポジトリにするため、任意の作業用フォルダ（例: `~/x-bookmarks/`）に `scripts/` の中身をコピーしてから使います。

## 0. 作業フォルダを用意する

```
mkdir ~/x-bookmarks
cp <このスキル>/scripts/xbm.py ~/x-bookmarks/
cp <このスキル>/scripts/run_sync.ps1 ~/x-bookmarks/
```

（テストも動かしたい場合は `scripts/tests/test_xbm.py` も `~/x-bookmarks/tests/` にコピーします）

## 1. X Developer Console でアプリを作る（人間の作業）

ここから先はブラウザでの操作・支払いが伴うため、必ずユーザー本人が行います。エージェントは案内するだけで、Client ID の入力や認可操作を代行しません。

1. [X Developer Portal](https://developer.x.com/) でアプリを作成する
2. アプリの **User authentication settings** で OAuth 2.0 を有効化する
   - App type: **Native App**（public client、client secret なし）
   - Callback URI / Redirect URL: `http://127.0.0.1:8765/callback`
   - Website URL: 任意の自分のURL（無ければポートフォリオサイトなどで可）
3. **Keys and tokens** タブから **OAuth 2.0 Client ID** を控える
4. X Developer Portal のダッシュボードで従量課金用のクレジットを少額購入し、コンソールで支出上限（budget cap）を設定する。金額の目安は下記「費用の目安」を参照
5. 続けて「投稿者本人のリプライ検索」機能を使う場合は `tweets/search/all` へのアクセスレベルも確認する（無くても `search/recent` に自動フォールバックする）

## 2. 初回認証（ブラウザでの認可はユーザーが行う）

```
python xbm.py auth
```

`%USERPROFILE%\.x-bookmarks\config.json` が無い場合、対話的に Client ID の入力を求められます。実行するとブラウザが開くので、X 側の認証画面でユーザー本人が許可操作を行ってください。認証完了後、`users/me` を1回だけ呼び出して user id を保存します（以降は呼びません）。

トークン・設定は `%USERPROFILE%\.x-bookmarks\`（`config.json`, `token.json`）に保存されます。データは含まれないため、このフォルダは git 管理しません。

## 3. 初回はフル取得

```
python xbm.py sync --full
```

既知IDでは止まらず、`next_token` が尽きるまで（API上限800件）取得します。件数分の課金（800件なら約 $0.8）が発生します。

## 4. 以降は差分同期のみでよい

```
python xbm.py sync
```

保存済みIDに当たったページで停止します。`--page-size` を指定しない場合、1ページ目は1件・2ページ目以降は20件を取得するため、新規ブックマークが無い日の空振りコストは $0.001 で済みます。

## 5. タスクスケジューラへの登録（毎日の自動同期）

`run_sync.ps1` は差分同期を実行し、`data/` が git リポジトリなら同期後に commit・push まで行います。スリープ解除、失敗時は10分おき3回まで再試行する設定です。

PowerShell（管理者権限は不要、ユーザーのタスクとして登録する場合の例）:

```powershell
$action = New-ScheduledTaskAction -Execute "pwsh.exe" `
  -Argument '-NoProfile -NonInteractive -WindowStyle Hidden -ExecutionPolicy Bypass -File "<path>\run_sync.ps1"'
$trigger = New-ScheduledTaskTrigger -Daily -At 4:00
$settings = New-ScheduledTaskSettingsSet `
  -StartWhenAvailable -WakeToRun -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
  -ExecutionTimeLimit (New-TimeSpan -Minutes 30) -MultipleInstances IgnoreNew `
  -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 10)
Register-ScheduledTask -TaskName "x-bookmarks-sync" -Action $action -Trigger $trigger -Settings $settings
```

`<path>` は手順0でコピーした作業フォルダの絶対パスに置き換えてください。

## 6. データ用のプライベートリポジトリを作る（人間の作業）

`data/` 配下には**他人が書いた投稿の本文**が保存されます。著作権および X 開発者規約上、公開は不可です。**必ずプライベートリポジトリ**として作成してください。

```
cd <作業フォルダ>
mkdir data
cp <このスキル>/templates/data-CLAUDE.md data/CLAUDE.md
cp <このスキル>/templates/data-gitignore data/.gitignore
git -C data init
gh repo create <your-account>/x-bookmarks-data --private --source data --remote origin
```

無人実行の `run_sync.ps1` が認証プロンプトで止まらないよう、push は `gh` の資格情報を使うように設定します。

```
git -C data config --local credential.https://github.com.helper ''
git -C data config --local --add credential.https://github.com.helper '!gh auth git-credential'
```

`data/CLAUDE.md` は「開発ソースとして使う」モードでこのリポジトリを Claude に読ませたときの、安全ルールを書いたテンプレートです。`data/.gitignore` は `skipped.jsonl` / `pending.jsonl` / `*.log` / `backup-*/` を push 対象から除外します（開発ネタとして参照しない投稿や除外記録は残さない方針のため）。

## 費用の目安

- 初回 `sync --full`（800件取得した場合）: 約 $0.8（フィルタで除外されても、取得自体には課金される）
- 以降の `sync`: 新規に取得した件数 × $0.001（新着が無い日は $0.001 のみ）
- 開発関連フィルタの第2段階（キーワード/ドメインに未マッチの投稿）は `claude -p --model haiku` を呼び出す。Claude Code にサブスクリプションでログインしていればその利用枠で動き、追加料金はかからない。API キーでログインしている場合だけ API の従量課金になる
- 投稿者本人のリプライ検索は、**保存が決まった投稿1件につき**検索API呼び出し1回、返ってきた件数 × $0.005 が加算される（除外・保留された投稿には発生しない。目安として保存1件あたり最大 $0.05 程度）

より詳しい仕組み（差分取得のページサイズ調整、フィルタの2段階判定、リプライ取得のフォールバック挙動など）は、コピー元プロジェクトの `README.md` にも記載があります。
