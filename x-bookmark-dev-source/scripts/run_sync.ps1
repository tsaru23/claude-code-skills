# タスクスケジューラから毎日呼ばれる差分同期。出力は data/sync.log に追記する
# data/ が git リポジトリなら、同期後に変更を commit して push する（参照用の非公開リポジトリ）
$ErrorActionPreference = 'Continue'
$root = $PSScriptRoot
# スケジューラ経由だと PATH にユーザー側の claude.exe が入らないことがあるため明示する
$env:Path = "$env:USERPROFILE\.local\bin;$env:Path"
$env:PYTHONIOENCODING = 'utf-8'
# python の UTF-8 出力を PowerShell 側でも UTF-8 として読む（ログの文字化け防止）
[Console]::OutputEncoding = [Text.UTF8Encoding]::new($false)
# スケジューラ経由ではシステム PATH の古い Python が先に来ることがあるため、3.10 以上を探して使う
$python = $env:XBM_PYTHON
if (-not $python) {
    $python = Get-Command python -All -ErrorAction SilentlyContinue |
        Where-Object { $_.Source -notmatch 'WindowsApps' } |
        Where-Object { & $_.Source -c 'import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)' 2>$null; $LASTEXITCODE -eq 0 } |
        Select-Object -First 1 -ExpandProperty Source
}
if (-not $python) { $python = 'python' }
$data = Join-Path $root 'data'
$log = Join-Path $data 'sync.log'
New-Item -ItemType Directory -Force $data | Out-Null
Set-Location $root

function Write-Log($lines) {
    $lines | Where-Object { "$_" -notmatch 'SEP-2352' } | ForEach-Object { "$_" } |
        Add-Content -Path $log -Encoding utf8
}

"=== $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss') ===" | Add-Content -Path $log -Encoding utf8
Write-Log (& $python xbm.py sync 2>&1)
$code = $LASTEXITCODE
if ($code -ne 0) { "exit=$code" | Add-Content -Path $log -Encoding utf8 }

if (Test-Path (Join-Path $data '.git')) {
    # 夜間の無人実行で認証画面を出して止まらないようにする（認証は gh の資格情報を使う）
    $env:GIT_TERMINAL_PROMPT = '0'
    $env:GCM_INTERACTIVE = 'never'
    Write-Log (git -C $data add -A 2>&1)
    git -C $data diff --cached --quiet
    if ($LASTEXITCODE -ne 0) {
        Write-Log (git -C $data commit -m "sync $(Get-Date -Format 'yyyy-MM-dd')" 2>&1)
    }
    # 前回 push に失敗した commit もここで送られる
    Write-Log (git -C $data push -q 2>&1)
    if ($LASTEXITCODE -ne 0) {
        "push failed" | Add-Content -Path $log -Encoding utf8
        if ($code -eq 0) { $code = 1 }
    }
}

# 失敗（起動直後の未接続など）をタスクスケジューラに伝え、10分後の再試行を効かせる
exit $code
