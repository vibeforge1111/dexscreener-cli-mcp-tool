# Next Build Tasks

Status key: `pending` | `in_progress` | `done`

## 1) Signal Quality - Early Runner Detection
- Status: `done`
- Scope:
  - Add volatility compression metric.
  - Add breakout readiness score.
  - Add boost velocity signal.
- Acceptance:
  - New runner board shows readiness/compression fields.
  - Scanner tags include at least one early-signal tag when criteria match.

## 2) Signal Quality - Relative Strength
- Status: `done`
- Scope:
  - Compute chain baseline for 1h momentum and flow.
  - Add per-token relative strength vs chain baseline.
- Acceptance:
  - New runner board shows RS field.
  - Rank/sort can prioritize RS.

## 3) Risk Control - Momentum Half-Life and Decay Filter
- Status: `done`
- Scope:
  - Track per-token momentum history during watch loops.
  - Estimate momentum half-life.
  - Filter fast-decay tokens with configurable thresholds.
- Acceptance:
  - CLI supports enabling/disabling decay filter.
  - Filter reason visible in tags/board hints.

## 4) Watch UX - Keyboard Interactions
- Status: `done`
- Scope:
  - Chain switching hotkeys (`1-9`).
  - Sort mode toggle (`s`).
  - Copy selected/top token + pair (`c`).
- Acceptance:
  - Footer shows active hotkeys and selected modes.
  - At least one interactive session demonstrates chain switch and sort mode updates.

## 5) Docs and Validation
- Status: `done`
- Scope:
  - README updates with new options/hotkeys.
  - Live command verification for `new-runners` and `new-runners-watch`.
- Acceptance:
  - Commands run successfully in current environment.
  - Output includes new analytics columns/panels.
