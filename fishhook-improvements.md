# fishhook: Improvement Roadmap

A prioritized list of updates across every layer of the pipeline. No code — just what to build and why.

> ## Status
>
> Most of this roadmap is now implemented. Remaining work: **Nansen structured source**,
> **config versioning**, and the **agent heterogeneity expansion** (information-access variance,
> staggered update frequency, memory decay).
>
> | Shipped | Remaining |
> |---|---|
> | Backtester — resolved-outcome mode, category breakdown | Nansen connector |
> | Circuit breaker incl. `halt` / `resume` | Config versioning |
> | Kelly criterion position sizer | Agent heterogeneity expansion |
> | Signal deduplicator (incl. implied-price signal dedup) | — |
> | Source credibility scorer | — |
> | Paper trading mode (real mainnet prices, no orders) | — |
> | Bayesian belief updating | — |
> | Portfolio heat limits | — |
> | Slippage model | — |
> | Learned weights per market category | — |
> | Structured logging with correlation IDs | — |
> | Alerting (circuit breaker, drawdown, auth, anomalies) | — |
> | Signal staleness TTL | — |
> | Polarization detection | — |
> | P&L edge attribution | — |
> | Market category tagging | — |
> | Structured API sources (Dune queries + CLOB depth) | — |

---

## 1. Data Layer (`fishhook/ingestion/`)

### Source Credibility Scorer
> **Status: Implemented** — `fishhook/ingestion/credibility.py` weights signals per source and updates scores based on how often a source preceded correct outcomes.
Weight incoming signals by the reliability of their origin. A Reuters article and a random blog post should not carry equal influence into the swarm. Assign credibility scores per domain, update them over time based on how often that source's signals preceded correct market outcomes.

### Signal Deduplicator / Canonicalizer
> **Status: Implemented** — `fishhook/ingestion/deduplicator.py`. The `implied_probability` price signal is deduplicated on every market, even without a scraped payload.
The same news event will appear across dozens of sources within minutes. Without deduplication, your swarm processes the same signal 8–10 times independently, artificially inflating confidence. Normalize signals to canonical events before they enter the simulation.

### Structured API Sources
> **Status: Partial** — **Dune** queries and **Polymarket CLOB order-book depth** are wired into the strategy engine via `SignalSourceManager`; **Nansen** remains to be added.
Supplement Playwright scraping with direct API integrations for higher-reliability data:
- **Dune Analytics** — on-chain activity, wallet flows, protocol metrics
- **Nansen** — smart money wallet signals
- **Polymarket CLOB order book depth** — real-time liquidity and order flow imbalances as a signal in their own right, not just for execution

### Signal Staleness TTL
> **Status: Implemented** — `StrategyConfig.signal_ttl_seconds`; stale signals are skipped before simulation.
Every signal should carry a timestamp and a time-to-live. A signal older than its TTL should not enter a new simulation run. Markets move fast; stale signals are worse than no signals.

---

## 2. Swarm Layer (`fishhook/swarm/`)

### Bayesian Belief Updating
> **Status: Implemented** — agents adjust a `_bayesian_receptiveness` factor up/down based on whether their prior was right, weighting incoming signals accordingly.
Agents currently have static personalities and priors. Add Bayesian updating so agents shift their beliefs proportionally to incoming signal strength and source credibility. An agent that has been "wrong" repeatedly should become more receptive to contradicting signals. This makes the swarm adaptive rather than just noisy.

### Backtester
> **Status: Implemented** — `fishhook backtest` replays resolved markets, records best-effort pre-resolution entry prices from the CLOB, and reports raw (threshold-free) directional accuracy plus per-category breakdowns. `--live` opts into the momentum-only mode.
This is the most important addition to the entire project. Replay historical Polymarket markets — with their resolved outcomes — against your simulated consensus. Measure:
- How often swarm divergence predicted the correct direction
- Average edge at different divergence thresholds
- Which market categories (politics, sports, crypto) the swarm performs best on

Without a backtester, you have no idea if your system has genuine edge or if you're just lucky. All parameter tuning (`divergence_threshold`, `min_confidence`, `simulation_weight`) should flow from backtesting results, not intuition.

### Agent Heterogeneity Expansion
> **Status: Partial — agents vary by personality only.** Information-access variance, staggered update frequency, and memory decay are still to be built.
Current agents vary by personality. Also vary them by:
- **Information access** — some agents see all signals, some see only one source category
- **Update frequency** — some agents are slow to change their views, some flip quickly
- **Memory decay** — how far back agents remember previous signals

This creates a more realistic simulation of how real crowds form opinions.

### Polarization Detection
> **Status: Implemented** — the consensus pipeline tracks polarization (conviction-weighted split) as an explicit market signal.
The `ConsensusTracker` detects consensus. It should also detect when the swarm is deeply split — high polarization is itself a signal. A 50/50 split with high conviction on both sides is very different from a 50/50 split with low conviction. The former suggests genuine uncertainty; the latter suggests noise. Trade accordingly.

---

## 3. Strategy Layer (`fishhook/strategy/`)

### Kelly Criterion Position Sizer
> **Status: Implemented** — fractional-Kelly sizing scales position size with estimated edge and win probability.
Flat position sizing ignores your confidence level. Replace it with fractional Kelly sizing: position size scales with your estimated edge and win probability. Use quarter-Kelly or half-Kelly (never full Kelly) to account for model uncertainty. This single change has more impact on long-run returns than almost any signal improvement.

### Portfolio Heat Limits
> **Status: Implemented** — `fishhook/strategy/portfolio_heat.py` blocks new entries that would push correlated theme exposure past the configured cap.
Before entering a trade, check the correlation of the new position against all open positions. If your portfolio is already heavily exposed to a theme (e.g. US political markets, crypto price markets), block new correlated entries even if the individual signal is strong. Set a maximum portfolio heat — total correlated exposure as a percentage of bankroll.

### Learned Weight Parameters
> **Status: Implemented** — `fishhook/strategy/adaptive_weights.py` learns per-category weight adjustments from backtest outcomes instead of relying on static guesses.
`simulation_weight: 0.6` and `data_weight: 0.4` are currently static guesses. These should be outputs of the backtester, updated periodically as market conditions change. Consider separate weights per market category — the swarm may be a stronger signal for political markets than crypto markets.

### Signal Attribution
> **Status: Implemented** — every trade records which signals triggered it, the consensus, and the edge computation (`fishhook/market/attribution.py`).
When a trade is generated, record exactly which signals triggered it, what the swarm consensus was, and what confidence/edge score was computed. This is essential for post-hoc analysis: did the signal actually predict anything, or did you just get lucky on execution timing?

---

## 4. Execution Layer (`fishhook/market/`)

### Circuit Breaker
> **Status: Implemented** — drawdown/loss/error-rate rules plus manual `fishhook halt` / `fishhook resume` (`fishhook/market/circuit_breaker.py`).
Non-negotiable for any live bot. Define hard rules:
- If drawdown exceeds X% within Y hours, halt all new positions
- If consecutive losses exceed N, halt and alert
- If API error rate spikes, halt and alert

The circuit breaker should be the first thing checked before any order is placed. It should also be triggerable manually via CLI (`fishhook halt`).

### Paper Trading Mode
> **Status: Implemented** — `polymarket.paper_trading` and the `--paper` flag on `run`/`loop` consume real mainnet prices without submitting orders; testnet still forces paper mode.
The existing `--testnet` flag uses testnet pricing, which often diverges from mainnet. Add a paper trading mode that consumes real mainnet prices and order books but does not submit orders. This gives more realistic signal validation than testnet before committing real capital.

### Slippage Model
> **Status: Implemented** — order-book-derived slippage is estimated before entry and subtracted from the edge; post-slippage edge below threshold skips the trade.
The current executor places orders without modeling slippage. For thin Polymarket markets, entering a large position moves the price against you. Before placing an order, estimate expected slippage from the order book depth and factor it into the edge calculation. If post-slippage edge falls below threshold, skip the trade.

### P&L Tracker with Edge Attribution
> **Status: Implemented** — realized vs model edge and slippage cost are tracked separately per trade and aggregated.
Track not just profit/loss but:
- **Realized edge** — actual returns vs market baseline
- **Model edge** — what the model predicted vs what happened
- **Slippage cost** — how much execution cost eroded theoretical edge

Separating these tells you whether underperformance is a signal problem, a sizing problem, or an execution problem.

---

## 5. Infrastructure & Operations

### Structured Logging with Correlation IDs
> **Status: Implemented** — a correlation ID threads through ingestion → simulation → strategy → execution so any run can be reconstructed.
Every pipeline run should generate a correlation ID that follows a signal from ingestion → simulation → strategy → execution. When a trade goes wrong, you should be able to reconstruct the exact state of every component at the moment of decision.

### Alerting
> **Status: Implemented** — Telegram/webhook alerts for circuit-breaker trips, drawdown thresholds, auth failures, and anomalous consensus.
Add alerting (Telegram bot, email, or webhook) for:
- Circuit breaker triggered
- Drawdown thresholds hit
- API auth failures
- Simulation runs producing anomalous results (e.g. consensus > 0.99, which usually means a bug)

### Config Versioning
> **Status: Open** — the active config is not yet versioned or tied to a trading session ID.
Track which config was active for each trading session. Parameter changes should be versioned so backtesting can reproduce the exact configuration used during any historical period.

### Market Category Tagging
> **Status: Implemented** — markets are tagged with a category (from the market's own category or tag list) and surfaced through the backtester's per-category breakdowns.
Tag each Polymarket market with a category (politics, crypto, sports, science, etc.) before simulation. This enables category-level performance analysis and per-category parameter tuning.

---

## Priority Order

| Priority | Update | Layer | Reason |
|---|---|---|---|
| 1 | Backtester | Swarm | Validates everything else |
| 2 | Circuit breaker | Execution | Protects capital |
| 3 | Kelly sizer | Strategy | Biggest return impact |
| 4 | Signal deduplicator | Ingestion | Fixes swarm inflation |
| 5 | Source credibility scorer | Ingestion | Improves signal quality |
| 6 | Paper trading mode | Execution | Safer validation path |
| 7 | Bayesian belief updating | Swarm | More realistic agents |
| 8 | Portfolio heat limits | Strategy | Prevents correlated ruin |
| 9 | Slippage model | Execution | Closes edge leakage |
| 10 | Learned weight parameters | Strategy | Replaces guesswork |
| 11 | Structured logging | Infra | Enables debugging |
| 12 | Alerting | Infra | Operational safety net |
| 13 | Signal staleness TTL | Ingestion | Prevents stale signals |
| 14 | Polarization detection | Swarm | Richer market signal |
| 15 | P&L edge attribution | Execution | Diagnoses underperformance |
