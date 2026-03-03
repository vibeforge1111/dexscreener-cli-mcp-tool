# Implementation Plan

## Delivery Phases

## Phase 1: Product Docs (Completed)
1. PRD
2. System architecture
3. UI/UX specification

## Phase 2: Core Enhancements
1. Local preset storage:
   - Save named scan filter profiles.
   - Load profile into `hot/watch`.
2. Task system:
   - Persist scan tasks locally.
   - Run a task directly from CLI.
3. Enhanced visual board:
   - Add chain heat and flow summaries.

## Phase 3: MCP Expansion
1. Add MCP tools for:
   - list tasks
   - run task
   - save/list presets

## Phase 4: Validation
1. Compile checks.
2. CLI smoke checks:
   - `hot`, `watch`, `search`, `inspect`
   - `preset` commands
   - `task` commands
3. MCP function smoke checks.

## Task System Specification

## Entities
1. ScanPreset
   - `name`
   - `chains`
   - `limit`
   - `min_liquidity_usd`
   - `min_volume_h24_usd`
   - `min_txns_h1`
   - `min_price_change_h1`
   - `created_at`
   - `updated_at`
2. ScanTask
   - `id`
   - `name`
   - `preset` (optional)
   - `filters` (inline override, optional)
   - `status` (`todo`, `running`, `done`, `blocked`)
   - `notes`
   - `created_at`
   - `updated_at`
   - `last_run_at`

## Storage
1. File: `%USERPROFILE%/.dexscreener-cli/presets.json`
2. File: `%USERPROFILE%/.dexscreener-cli/tasks.json`

## CLI API (Planned)
1. `ds preset save`
2. `ds preset list`
3. `ds preset show`
4. `ds preset delete`
5. `ds task create`
6. `ds task list`
7. `ds task show`
8. `ds task status`
9. `ds task run`
10. `ds task delete`

## Done Criteria
1. User can save a preset and run hot scan via preset in one command.
2. User can create named tasks and execute scans from those tasks.
3. UI board displays chain heat and flow context panels.
4. New commands are documented in README.
