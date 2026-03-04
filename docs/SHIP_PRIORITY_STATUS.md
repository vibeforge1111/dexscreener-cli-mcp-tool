# Ship Priority Status

Date: March 4, 2026

## Top-10 Priority Systems
1. Adaptive rate-budget engine  
Status: `done`

2. API scope/compliance guardrails  
Status: `pending` (documentation/legal review needed before broad distribution)

3. Signal ingestion v2 (boosts + profiles + community + batched pairs)  
Status: `done`

4. Risk firewall for runner quality  
Status: `done` (risk score, risk flags, and ranking penalty implemented)

5. Chain-aware default profiles  
Status: `done` (`strict`, `balanced`, `discovery`)

6. Realtime alpha event engine with guardrails  
Status: `done` (cooldown + max alerts/hour + dedupe flow)

7. Explainable scoring output  
Status: `done` (component-level breakdown in JSON + MCP)

8. MCP surface hardening (tools + resources + prompts)  
Status: `done` (resources/prompts added; tools retained)

9. MCP auth for remote deployment  
Status: `pending` (kept local/stdin-first operational model)

10. Validation and observability  
Status: `in_progress` (runtime stats and command checks implemented; replay/backtest harness still pending)

## Immediate Next Build
1. Add replay dataset runner for deterministic signal regression tests.
2. Add optional auth/deployment profile for remote MCP hosting.
3. Add compliance checklist file linked to Dex API terms.
