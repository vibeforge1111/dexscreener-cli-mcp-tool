param(
    [ValidateSet("all", "alpha", "watch", "topnew")]
    [string]$Only = "all",
    [ValidateSet("auto", "compact", "full")]
    [string]$TableMode = "compact",
    [switch]$NoPause
)

$ErrorActionPreference = "Stop"

$repoRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
Push-Location $repoRoot
try {
    $env:PYTHONIOENCODING = "utf-8"
    [Console]::OutputEncoding = [System.Text.UTF8Encoding]::new()
    chcp 65001 | Out-Null
    if ($TableMode -eq "auto") {
        Remove-Item Env:DS_TABLE_MODE -ErrorAction SilentlyContinue
    } else {
        $env:DS_TABLE_MODE = $TableMode
    }

    function Wait-IfNeeded([string]$message) {
        if (-not $NoPause) {
            Read-Host $message | Out-Null
        }
    }

    function Run-Step([string]$title, [scriptblock]$step) {
        Write-Host ""
        Write-Host "=== $title ===" -ForegroundColor Cyan
        & $step
    }

    if ($Only -eq "all" -or $Only -eq "alpha") {
        Run-Step "Alpha Drops (Relaxed Showcase Profile)" {
            uv run ds alpha-drops `
                --chains base,solana `
                --limit 10 `
                --max-age-hours 24 `
                --min-liquidity-usd 15000 `
                --min-volume-h24-usd 20000 `
                --min-txns-h1 10 `
                --min-breakout-readiness 25 `
                --max-vol-liq-ratio 120
        }
        Wait-IfNeeded "Press Enter to continue to alpha-drops-watch"
    }

    if ($Only -eq "all" -or $Only -eq "watch") {
        Run-Step "Alpha Drops Watch (One-Cycle Demo)" {
            uv run ds alpha-drops-watch `
                --chains base,solana `
                --limit 10 `
                --max-age-hours 24 `
                --interval 4 `
                --min-liquidity-usd 15000 `
                --min-volume-h24-usd 20000 `
                --min-txns-h1 10 `
                --min-breakout-readiness 25 `
                --max-vol-liq-ratio 120 `
                --cycles 1 `
                --no-screen `
                --no-alerts
        }
        Wait-IfNeeded "Press Enter to continue to top-new"
    }

    if ($Only -eq "all" -or $Only -eq "topnew") {
        Run-Step "Top New Coins (Base, 7d)" {
            uv run ds top-new `
                --chain base `
                --days 7 `
                --limit 10 `
                --min-liquidity-usd 15000 `
                --min-volume-h24-usd 1000 `
                --min-txns-h24 20
        }
    }
}
finally {
    Pop-Location
}
