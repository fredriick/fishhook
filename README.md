# fishhook

Data ingestion + Swarm intelligence simulation + Polymarket execution pipeline.

## Architecture

```
┌─────────────────────────────────────────────────────────────┐
│                    Pipeline Orchestrator                     │
├──────────────┬──────────────────┬───────────────────────────┤
│  Data Layer  │  Simulation Layer │  Execution Layer          │
│              │                   │                           │
│  Scraper     │  Swarm Engine     │  Polymarket Client        │
│  Interceptor │  1000 Agents      │  Order Book               │
│  Dyn Values  │  Social Network   │  Trade Executor           │
│  Proxy Mgr   │  Consensus Track  │  Position Tracker         │
├──────────────┴──────────────────┴───────────────────────────┤
│                    Strategy Engine                           │
│            (Data -> Signal -> Simulation -> Trade)           │
├─────────────────────────────────────────────────────────────┤
│                    Dashboard Layer                           │
│              Web UI (8787)  |  Terminal TUI                  │
└─────────────────────────────────────────────────────────────┘
```

## Project Structure

```
fishhook/
├── __init__.py
├── cli.py                        # CLI entry point
├── orchestrator.py               # Pipeline coordinator
├── config/
│   ├── __init__.py
│   └── settings.py               # Pydantic config (env vars + YAML)
├── ingestion/                    # Data layer
│   ├── engine.py                 # Playwright scraping engine
│   ├── interceptor.py            # Network request capture
│   ├── dynamic_values.py         # CSRF/session/auth token extraction
│   └── proxy_manager.py          # IP rotation pool
├── swarm/                        # Simulation layer
│   ├── agent.py                  # Agent personality, memory, opinions
│   ├── social.py                 # Scale-free social network graph
│   ├── consensus.py              # Consensus tracking, regime detection
│   └── world.py                  # Simulation orchestrator
├── market/                       # Polymarket layer
│   ├── client.py                 # CLOB + Gamma API client
│   ├── models.py                 # Market, OrderBook, Position, TradeSignal
│   └── executor.py               # Order lifecycle, position tracking
├── strategy/
│   └── engine.py                 # Data -> swarm -> divergence -> trade signals
└── dashboard/                    # Visualization layer
    ├── server.py                 # HTTP server (aiohttp)
    ├── terminal.py               # Rich-based TUI dashboard
    └── static/
        └── index.html            # Web dashboard (Chart.js)
```

## Setup

```bash
# Create virtual environment
py -m venv .venv

# Activate (Windows)
.venv\Scripts\activate

# Activate (Linux/Mac)
source .venv/bin/activate

# Install dependencies
pip install -e .

# Install Playwright browsers
python -m playwright install chromium
```

## CLI Commands

### `fishhook simulate`
Run the swarm simulation only. No network or API required.

```bash
fishhook simulate --agents 1000 --rounds 50 --signal 0.3
```

| Flag | Default | Description |
|------|---------|-------------|
| `--agents`, `-a` | 1000 | Number of agents in the swarm |
| `--rounds`, `-r` | 50 | Max simulation rounds |
| `--signal`, `-s` | 0.0 | External signal to inject (-1.0 to 1.0) |

**Output**: JSON with consensus state, distribution, social network stats, convergence status.

### `fishhook scrape`
Scrape URLs using Playwright with request interception.

```bash
fishhook scrape https://example.com https://polymarket.com
```

Captures: page HTML, intercepted API requests, dynamic tokens (CSRF, session, auth), response bodies.

### `fishhook run`
Run the full pipeline once (fetch markets -> simulate -> generate signals -> execute).

```bash
fishhook run --testnet --markets 10 --category crypto
```

| Flag | Default | Description |
|------|---------|-------------|
| `--markets`, `-m` | 10 | Max markets to analyze |
| `--category` | all | Market category filter |
| `--testnet` | false | Testnet mode (no real trades) |
| `--paper` | false | Paper trade on real market prices (no orders) |

### `fishhook loop`
Run the pipeline continuously.

```bash
fishhook loop --testnet --interval 60 --markets 10
```

| Flag | Default | Description |
|------|---------|-------------|
| `--interval`, `-i` | 60 | Seconds between runs |
| `--markets`, `-m` | 10 | Max markets per run |
| `--category` | all | Market category filter |
| `--testnet` | false | Testnet mode (no real trades) |
| `--paper` | false | Paper trade on real market prices (no orders) |

### `fishhook backtest`
Replay resolved markets and measure whether swarm divergence predicted the correct direction.

```bash
fishhook backtest --markets 50 --agents 500 --rounds 30
```

| Flag | Default | Description |
|------|---------|-------------|
| `--markets`, `-m` | 50 | Number of resolved markets to test |
| `--agents`, `-a` | 500 | Agents per simulation |
| `--rounds`, `-r` | 30 | Rounds per simulation |
| `--min-volume` | 1000.0 | Min market volume filter |
| `--category` | all | Market category filter |
| `--sweep` | false | Run parameter sweep (agents x thresholds) |
| `--live` | false | Use live (unresolved) markets with price momentum instead of resolved outcomes |

Resolved mode tests each market's outcome against the swarm; when no pre-resolution
price snapshot is available (the CLOB history endpoint only serves a rolling ~1-2
week window) it falls back to the documented 50/50 open-price assumption, so the
`raw_accuracy` metrics isolate the swarm's directional predictive power rather than
trading the final, already-circular closing price.

### `fishhook status`
Print current pipeline state as JSON (includes `config_version` and the config
snapshot path so any run can be mapped back to the exact parameters that were active).

```bash
fishhook status
```

### `fishhook config`
Show the active config version (deterministic sha256 hash of the effective config
with secrets redacted) plus its full redacted snapshot.

```bash
fishhook config            # version + snapshot
fishhook config --path     # snapshot file path only
```

Every config version is persisted once to `data/config_snapshots/<version>.json`
(redacted), so backtests and past trading sessions can be reproduced against the
exact parameters that were live at the time.

### `fishhook dashboard`
Launch the web dashboard.

```bash
fishhook dashboard --port 8787 --host 127.0.0.1
```

Then open `http://127.0.0.1:8787` in your browser.

### `fishhook tui`
Launch the terminal dashboard (Rich TUI).

```bash
fishhook tui --refresh 2.0
```

## Web Dashboard

Open `http://127.0.0.1:8787` after running `fishhook dashboard`.

### Panels

| Panel | Description |
|-------|-------------|
| **Swarm Consensus** | Direction (bullish/bearish/neutral), mean opinion, agreement ratio, strength |
| **Opinion Distribution** | Bar showing agent counts across strong bear -> strong bull spectrum |
| **Consensus History** | Line chart tracking mean opinion and agreement over simulation runs |
| **Distribution Chart** | Bar chart of current agent opinion distribution |
| **Simulation Controls** | Run live simulations with configurable agents, rounds, signal |
| **Portfolio** | Positions, total value, P&L, winning/losing counts |
| **Recent Runs** | Table of pipeline run results (markets, signals, trades, time) |
| **Social Network** | Agent count, connections, groups, top influencers by centrality |
| **Event Log** | Real-time log of simulation results and API calls |

### API Endpoints

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/api/status` | GET | Full pipeline state (running, trades, strategy, cached data) |
| `/api/simulation` | GET | Current swarm consensus and signal |
| `/api/simulation/run` | GET | Trigger a simulation. Query params: `agents`, `rounds`, `signal` |
| `/api/trades` | GET | Trade history and portfolio summary |
| `/api/network` | GET | Social network stats, influencers, community sizes |
| `/api/history` | GET | History of simulation results |

**Example**:
```
GET http://127.0.0.1:8787/api/simulation/run?agents=500&rounds=30&signal=0.3
```

## Configuration

File: `config.yaml` (or pass `--config path/to/file.yaml`)

```yaml
log_level: INFO

scraper:
  headless: true
  timeout_ms: 30000
  max_concurrent_pages: 5
  user_agent_rotation: true
  intercept_requests: true
  capture_dynamic_values: true
  proxy:
    enabled: false
    rotation_interval_seconds: 300
    proxies:
      - "http://proxy1:8080"
      - "http://proxy2:8080"

swarm:
  num_agents: 1000
  max_rounds: 50
  consensus_threshold: 0.8        # Agreement ratio to consider consensus reached
  social_connection_probability: 0.01
  opinion_update_rate: 0.1
  noise_factor: 0.05
  personality:
    risk_tolerance: 0.5
    conformity_bias: 0.5
    information_weight: 0.5
    social_influence_susceptibility: 0.5
    memory_decay_rate: 0.05
    conviction_strength: 0.5

polymarket:
  api_base_url: "https://clob.polymarket.com"
  gamma_api_url: "https://gamma-api.polymarket.com"
  api_key: ""                      # Set via env: MCP_PARSE_POLYMARKET__API_KEY
  api_secret: ""
  passphrase: ""
  chain_id: 137
  max_position_size: 100.0
  min_edge_threshold: 0.05
  testnet: true                    # Set to false for live trading
  paper_trading: false             # Paper trade on real prices (no orders)

strategy:
  divergence_threshold: 0.1       # Min edge to generate a signal
  min_confidence: 0.6             # Min swarm confidence to trade
  simulation_weight: 0.6          # Weight of swarm signal
  data_weight: 0.4                # Weight of scraped data signal
  cooldown_seconds: 60            # Min time between signals
  max_trades_per_hour: 10
```

### Environment Variables

All config fields can be overridden via env vars with prefix `MCP_PARSE_`:

```bash
MCP_PARSE_POLYMARKET__API_KEY=your_key
MCP_PARSE_POLYMARKET__TESTNET=false
MCP_PARSE_SWARM__NUM_AGENTS=2000
```

## How Each Layer Works

### 1. Data Ingestion

**ScrapingEngine** launches a headless Chromium browser via Playwright. It intercepts every network request the page makes, capturing:
- Request URL, method, headers, POST body
- Response status, headers, body
- Dynamic tokens: CSRF, session IDs, auth tokens, cookies

**DynamicValueExtractor** uses regex patterns to find tokens in HTML, JSON responses, and headers. These tokens are stored and injected into replay requests automatically.

**ProxyManager** maintains a pool of proxies with health tracking. Proxies are rotated on a configurable interval. Failed proxies are banned after 5 consecutive failures.

### 2. Swarm Simulation

Each **Agent** has:
- **Personality**: risk tolerance, conformity bias, information weight, social susceptibility, memory decay, conviction strength (all 0-1, randomized per agent)
- **Memory**: stores observations with timestamps, decays over time
- **Opinion**: a float from -1.0 (strong bear) to +1.0 (strong bull)
- **Social connections**: links to other agents in the network

The **SocialNetwork** is a scale-free graph (preferential attachment). Agents with higher degree become hubs. Community detection (Louvain algorithm) groups agents into clusters.

Each simulation **round**:
1. External signal is injected (all agents observe it, weighted by their `information_weight`)
2. Each agent gets neighbor opinions from the social network
3. Agent updates opinion: weighted blend of social pull, external signal, memory recall, current conviction, plus noise
4. Consensus is computed across all agents

**ConsensusTracker** measures:
- `mean_opinion`: average of all agent opinions
- `agreement_ratio`: fraction of agents within 0.3 of the mean
- `polarization_index`: standard deviation of the opinion histogram
- `strength`: agreement * (1 - std) * mean confidence
- `regime_change`: detected when mean opinion shifts > 0.3 between windows

### 3. Polymarket Integration

**PolymarketClient** interfaces with two APIs:
- **Gamma API** (`gamma-api.polymarket.com`): market data, prices, outcomes (public, no auth)
- **CLOB API** (`clob.polymarket.com`): order book, trades, order placement (requires API key + HMAC signing)

**TradeExecutor** manages:
- Rate limiting (max trades per hour)
- Position sizing (max position size)
- Order lifecycle (place, cancel, track fills)
- Position tracking (avg price, unrealized P&L)

### 4. Strategy Engine

For each market:
1. Compute `market_signal` from scraped data (sentiment, volume trends, social signals)
2. Run swarm simulation with the market signal
3. Get `swarm_signal` (mean opinion, confidence, direction)
4. Combine signals: `combined = swarm * simulation_weight + data * data_weight`
5. Compute `edge`: difference between fair price (derived from combined signal) and market price
6. Generate `TradeSignal` if edge > threshold and confidence > minimum

### 5. Dashboard

**Web Dashboard** (`fishhook dashboard`): aiohttp server serving a single-page app with Chart.js charts. Auto-refreshes every 5 seconds. Supports triggering live simulations from the UI.

**Terminal Dashboard** (`fishhook tui`): Rich-based live-updating terminal UI with split panes showing swarm consensus, opinion distribution, runs, and portfolio.

## Example Workflows

### Test the swarm only
```bash
fishhook simulate --agents 1000 --rounds 50 --signal 0.3
```

### Visualize in browser
```bash
fishhook dashboard
# Open http://127.0.0.1:8787
# Click "Run Simulation" to see live charts
```

### Run pipeline in testnet mode
```bash
fishhook run --testnet --markets 5
```

### Continuous loop with dashboard
Terminal 1:
```bash
fishhook dashboard
```
Terminal 2:
```bash
fishhook loop --testnet --interval 60 --markets 5
```
Then watch results update live at `http://127.0.0.1:8787`.

## Dependencies

| Package | Purpose |
|---------|---------|
| playwright | Browser automation for scraping |
| httpx | Async HTTP client for API calls |
| networkx | Social network graph |
| pydantic | Config validation and models |
| pydantic-settings | Env var config loading |
| pyyaml | YAML config files |
| rich | Terminal dashboard |
| numpy | Numerical computation for swarm |
| aiohttp | Web dashboard server |
