# UI/UX Specification (Terminal)

## Design Principles
1. Fast visual parsing over decorative output.
2. Consistent color semantics:
   - Green: positive momentum
   - Red: negative momentum
   - Cyan/Blue: structure/meta
   - Magenta: signals/tags
3. Keep primary table visible as the anchor.
4. Show secondary summaries in compact side/bottom panels.

## Primary Views
1. `hot`:
   - Static ranked table.
2. `watch`:
   - Full-screen board with refresh status.
3. `inspect`:
   - Detail panel + risk/proxy panel.
4. `search`:
   - Lightweight table for quick lookup.

## Board Layout (Watch / Enhanced Hot)
1. Top:
   - Product title and UTC timestamp.
2. Middle:
   - Hot Runner leaderboard.
3. Bottom:
   - Chain heat panel.
   - Market structure/risk panel.
   - Footer status (refresh interval, filters, API health hints).

## Leaderboard Columns
1. Rank
2. Chain
3. Token + score
4. Price
5. 1h change
6. 24h volume
7. 1h txns
8. Liquidity
9. Market cap
10. Boost count/amount
11. Age
12. Signal tags

## Readability Rules
1. Use compact number formats (`K/M/B`).
2. Truncate long token names safely.
3. Avoid non-ASCII assumptions for JSON output.
4. Ensure content remains legible at 100-column terminals.

## Interaction Model
1. Non-interactive commands are script-first.
2. Watch mode is Ctrl+C driven.
3. Tasks system commands should be explicit and single-purpose:
   - create, list, show, update-status, run.

## Error UX
1. Fail with a clear one-line reason.
2. Suggest next command when possible.
3. Keep stack traces hidden by default for user commands.

## Accessibility Notes
1. Color should not be the only signal:
   - include +/- symbols
   - include textual tags
2. JSON mode should preserve full numeric values for downstream tooling.

## Terminal Style Guide (MANDATORY)

All visual code lives in `ui.py`. Only `cli.py` imports from it. Never
hardcode hex colors or Unicode in cli.py - use the constants below.

### Color Palette (ui.py constants)

| Constant        | Hex       | Usage                                    |
|-----------------|-----------|------------------------------------------|
| `C_BORDER`      | `#3a3d4a` | Table and panel borders                  |
| `C_BORDER_DIM`  | `#2a2d3a` | Subtle/secondary borders                 |
| `C_ROW_ALT`     | `#1e2029` | Alternating row background (the dark grey)|
| `C_TITLE`       | `#e5e7eb` | Off-white for titles                     |
| `C_LABEL`       | `#6b7280` | Medium grey for labels                   |
| `C_DIM`         | `#4b5563` | Dark grey for dim/secondary text         |
| `C_TEXT`        | `#d1d5db` | Light grey primary text                  |
| `C_GREEN`       | `#4ade80` | Positive momentum                        |
| `C_GREEN_BRIGHT`| `#22c55e` | Strong positive                          |
| `C_RED`         | `#f87171` | Negative momentum                        |
| `C_RED_BRIGHT`  | `#ef4444` | Strong negative                          |
| `C_GOLD`        | `#fbbf24` | Token symbols, highlights                |
| `C_AMBER`       | `#f59e0b` | Deeper amber                             |
| `C_BLUE`        | `#60a5fa` | Chain accent, links                      |
| `C_CYAN`        | `#67e8f9` | Freshness indicators                     |
| `C_PURPLE`      | `#a78bfa` | Signal tags                              |
| `C_WHITE`       | `#f9fafb` | Near-white emphasis                      |

### Table Construction Rules

Every Rich `Table()` must follow this pattern:

```python
table = Table(
    title=title,
    box=box.SIMPLE_HEAVY,           # always SIMPLE_HEAVY for data tables
    header_style=f"bold {C_TEXT}",   # always C_TEXT for headers
    row_styles=["", f"on {C_ROW_ALT}"],  # alternating black / dark grey
    border_style=C_BORDER,          # always C_BORDER
    title_style="",                 # no extra title styling
)
```

**Critical rules:**
- Row alternation is `["", f"on {C_ROW_ALT}"]` - black then dark grey. Never
  change C_ROW_ALT or hardcode a different value.
- Never pass `style=` to `table.add_row()` in one-shot scans. Per-row style
  overrides the alternating `row_styles` and gives every row the same
  background. Only use per-row `style=` in live/watch modes for
  change-highlighting, and return `None` when there is no change to highlight.
- Panels use `box.HEAVY` with `border_style=C_BORDER`.
- Status footers use `render_status_footer()` from ui.py.

### Visual Helpers (ui.py functions)

| Helper              | Purpose                                          |
|---------------------|--------------------------------------------------|
| `_rank_badge(i)`    | Diamond-styled rank (gold/silver/bronze top 3)   |
| `_momentum_text(v)` | Arrow-prefixed percentage (green up / red down)  |
| `_age_badge(hours)` | Freshness-tiered age with dot for <1h tokens     |
| `_vol_heat(vol)`    | Volume with intensity-based color tiers          |
| `_chain_text(id)`   | Dot-prefixed chain abbreviation (SOL, BASE, etc) |
| `_score_gauge(s)`   | Visual bar with fill/empty chars + colored score |
| `_flow_meter(b,s)`  | Block chars green/red based on buy/sell dominance|
| `_holders_gauge(n)` | Mini tier bar + holder count                     |
| `_signal_badge(t)`  | Dot-prefixed signal tags                         |
| `_safe_text(s)`     | Wraps Unicode for terminal encoding safety       |
| `_compact_level()`  | Returns 0/1/2 based on terminal width            |
| `fmt_usd(v)`        | Compact dollar formatting (K/M/B)                |
| `fmt_price(v)`      | Price formatting with appropriate decimals       |
| `fmt_holders(n)`    | Holder count formatting (K/M)                    |

### Unicode Constants

| Constant     | Char | Escape     |
|-------------|------|------------|
| `DIAMOND`    | ◆    | `\u25c6`   |
| `DOT`        | ●    | `\u25cf`   |
| `ARROW_UP`   | ▲    | `\u25b2`   |
| `ARROW_DOWN` | ▼    | `\u25bc`   |
| `BAR_FILL`   | █    | `\u2588`   |
| `BAR_EMPTY`  | ░    | `\u2591`   |

Always wrap raw Unicode in `_safe_text()`.

### Anti-Patterns (DO NOT)

1. **DO NOT** hardcode hex colors in cli.py - import constants from ui.py.
2. **DO NOT** create local helper functions that duplicate ui.py helpers
   (e.g. local `_pct_text()` when `_momentum_text()` exists).
3. **DO NOT** use `box.ROUNDED` - all borders are `box.HEAVY` (panels)
   or `box.SIMPLE_HEAVY` (tables).
4. **DO NOT** override row background with `style=` on `add_row()` in
   one-shot mode. The table's `row_styles` handles alternation.
5. **DO NOT** use `show_edge=True` on data tables.
6. **DO NOT** bypass `_safe_text()` for Unicode characters.

## UI Acceptance Criteria
1. Hot table renders cleanly without wrapped corruption in standard terminal widths.
2. Watch mode updates without flicker artifacts and no duplicate scroll spam.
3. Inspect view clearly separates facts and inferred distribution proxies.
