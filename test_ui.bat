@echo off
chcp 65001 >nul
set DS_TABLE_MODE=full

echo ============================================
echo   DEX SCANNER UI TEST SUITE
echo ============================================
echo.

echo [1/5] HOT SCAN - Solana Top 10
echo -------------------------------------------
python -c "from dexscreener_cli.cli import app; app(['hot', '--chains', 'solana', '--limit', '10'])"
echo.
echo Press any key for next test...
pause >nul

echo [2/5] MULTI-CHAIN SCAN
echo -------------------------------------------
python -c "from dexscreener_cli.cli import app; app(['hot', '--chains', 'solana,base,ethereum', '--limit', '15'])"
echo.
echo Press any key for next test...
pause >nul

echo [3/5] SEARCH - pepe
echo -------------------------------------------
python -c "from dexscreener_cli.cli import app; app(['search', 'pepe'])"
echo.
echo Press any key for next test...
pause >nul

echo [4/5] TOP NEW RUNNERS - Solana
echo -------------------------------------------
python -c "from dexscreener_cli.cli import app; app(['top-new', '--chain', 'solana', '--limit', '8'])"
echo.
echo Press any key for next test...
pause >nul

echo [5/5] DISCOVERY MODE - Loose filters
echo -------------------------------------------
python -c "from dexscreener_cli.cli import app; app(['hot', '--chains', 'solana', '--limit', '15', '--min-liquidity-usd', '10000', '--min-volume-h24-usd', '10000', '--min-txns-h1', '5'])"
echo.
echo ============================================
echo   ALL TESTS COMPLETE
echo ============================================
echo.
echo To run LIVE WATCH mode, run:
echo   python -c "from dexscreener_cli.cli import app; app(['watch', '--chains', 'solana', '--limit', '12', '--interval', '10'])"
pause
