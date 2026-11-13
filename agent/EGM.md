# Agent (`kube-sentinel/agent`)

## Overview
`kube-sentinel/agent` implements Kube-Sentinel's autonomous, read-only AI Site Reliability Engineer (SRE) investigation pipeline built on LangGraph and Google Gemini (see [`README.md`](./README.md)). Given a free-text incident description or firing Alertmanager alerts, it connects to the local MCP server over stdio using strictly read-only cluster/observability tools, executes a bounded tool-calling investigation loop (`investigate` ⇄ `tools`), and synthesizes a schema-validated `Report` (`write_report`) containing root cause, transcript-backed evidence, and a single allowlisted remediation proposal saved with `pending_approval` status for human review.

## Interface
- **CLI Entry Points**:
  - `python -m agent [alert ...]` — runs a single CLI investigation using `sys.argv[1:]` joined as the alert prompt or falling back to `DEFAULT_ALERT` [`__main__.py:main`], [`__main__.py:DEFAULT_ALERT`].
  - `python -m agent.watch [--once]` — polls Alertmanager for firing alerts in `NAMESPACE` and triggers grouped investigations continuously, or checks once and exits when `--once` is passed [`watch.py:watch`], [`watch.py:--once`].
- **End-to-End Orchestration ([`run.py`](./run.py))**:
  - `run_investigation(alert: str, on_tool: Callable[[str, dict], None] | None = None, source: str = "cli", save: bool = True, on_switch=None) -> dict` — validates API credentials, opens read-only MCP tools, runs the LangGraph pipeline, attaches report metadata (`id`, `created_at`, `alert`, `source`, `model`, `status`), optionally persists JSON to `REPORTS_DIR`, and returns the result dictionary [`run.py:run_investigation`].
  - `to_json(result: dict) -> dict` — serializes an investigation result dictionary (dumping `Report` and injecting computed `suggested_fix.command`) for JSON storage [`run.py:to_json`].
  - `check_api_key() -> None` — verifies `GOOGLE_API_KEY` or `GEMINI_API_KEY` is set in the environment [`run.py:check_api_key`].
  - `ROOT: Path` and `REPORTS_DIR: Path` — repository root (`kube-sentinel/`) and report output directory [`run.py:ROOT`], [`run.py:REPORTS_DIR`].
- **LangGraph Investigation Engine ([`graph.py`](./graph.py))**:
  - `State` (`TypedDict`) — graph state schema tracking `alert`, `messages` (`add_messages` reducer), `tool_calls`, `trace`, and `report` [`graph.py:State`].
  - `build_graph(tools: list, pool: ModelPool, on_tool: Callable[[str, dict], None] | None = None)` — compiles the `StateGraph(State)` state machine (`START -> investigate -> tools|write_report -> END`) [`graph.py:build_graph`].
  - `investigate(alert: str, tools: list, on_tool: Callable[[str, dict], None] | None = None, pool: ModelPool | None = None) -> dict` — executes one graph run and returns `{"report", "trace", "tool_calls", "model_requests", "models", "seconds"}` [`graph.py:investigate`].
  - `MAX_TOOL_CALLS: int` — maximum tool invocations per investigation (default `20`) [`graph.py:MAX_TOOL_CALLS`].
- **Incident Report Schema ([`report.py`](./report.py))**:
  - `Category` — `Literal["scaled-to-zero", "misconfiguration", "oom-killed", "image-pull-error", "bad-probe", "crash-loop", "resource-exhaustion", "network", "dependency-failure", "unknown"]` [`report.py:Category`].
  - `SuggestedFix(BaseModel)` — Pydantic model (`action`, `deployment`, `replicas`, `reason`) with computed `.command -> str` rendering the `kubectl` command or human-intervention notice [`report.py:SuggestedFix`], [`report.py:SuggestedFix.command`].
  - `Report(BaseModel)` — structured incident report (`summary`, `root_cause`, `component`, `category`, `evidence`, `user_impact`, `suggested_fix`, `confidence`) with `to_text() -> str` formatter [`report.py:Report`], [`report.py:Report.to_text`].
- **Gemini Model Failover Pool ([`models.py`](./models.py))**:
  - `ModelPool(names: list[str] | None = None, on_switch=None)` — manages ordered `ChatGoogleGenerativeAI` clients with `.current -> str` and `ainvoke(messages: list, tools: list | None = None, schema: Any = None)` failover execution [`models.py:ModelPool`], [`models.py:ModelPool.current`], [`models.py:ModelPool.ainvoke`].
  - `model_names() -> list[str]` and `DEFAULT_MODELS: list[str]` — resolves configured Gemini model order (defaulting to `["gemini-3.5-flash", "gemini-3.5-flash-lite", "gemini-3.1-flash-lite", "gemini-3.7-flash"]`) [`models.py:model_names`], [`models.py:DEFAULT_MODELS`].
- **MCP Tool Adapter ([`mcp_tools.py`](./mcp_tools.py))**:
  - `mcp_tools(read_only: bool = True) -> AsyncIterator[list[StructuredTool]]` — async context manager spawning `python -m mcp_server` over stdio and yielding LangChain `StructuredTool` wrappers [`mcp_tools.py:mcp_tools`].
  - `MAX_TOOL_OUTPUT: int` — per-tool output character limit (`6000`) [`mcp_tools.py:MAX_TOOL_OUTPUT`].
- **Alertmanager Watcher Helpers ([`watch.py`](./watch.py))**:
  - `shop_alerts() -> list[dict]` — fetches active Alertmanager alerts filtered to `NAMESPACE` [`watch.py:shop_alerts`].
  - `alert_text(alerts: list[dict]) -> str` — formats active alerts (ordered with `"critical"` severity first) into an investigation prompt [`watch.py:alert_text`].
  - `investigate_now(alerts: list[dict]) -> None` and `log(msg: str) -> None` — runs an `"alertmanager"`-sourced investigation batch and logs timestamped progress [`watch.py:investigate_now`], [`watch.py:log`].
  - `POLL_SECONDS: int`, `SETTLE_SECONDS: int`, `NAMESPACE: str` — watcher timing and target namespace constants [`watch.py:POLL_SECONDS`], [`watch.py:SETTLE_SECONDS`], [`watch.py:NAMESPACE`].
- **System Prompts ([`prompts.py`](./prompts.py))**:
  - `INVESTIGATE_PROMPT: str`, `OUT_OF_BUDGET: str`, `REPORT_PROMPT: str` — SRE investigation rules, budget-exhaustion directive, and transcript-only report synthesis instructions [`prompts.py:INVESTIGATE_PROMPT`], [`prompts.py:OUT_OF_BUDGET`], [`prompts.py:REPORT_PROMPT`].
- **Environment Variables**:
  - `GOOGLE_API_KEY` / `GEMINI_API_KEY` (required API credentials) [`run.py:check_api_key`]; `GEMINI_MODELS` / `GEMINI_MODEL` (comma-separated model list) [`models.py:model_names`]; `KUBE_SENTINEL_MAX_TOOL_CALLS` (default `"20"`) [`graph.py:MAX_TOOL_CALLS`]; `KUBE_SENTINEL_REPORTS_DIR` (default `kube-sentinel/reports`) [`run.py:REPORTS_DIR`]; `KUBE_SENTINEL_POLL_SECONDS` (default `"20"`), `KUBE_SENTINEL_SETTLE_SECONDS` (default `"45"`), `KUBE_SENTINEL_NAMESPACE` (default `"default"`) [`watch.py:POLL_SECONDS`], [`watch.py:SETTLE_SECONDS`], [`watch.py:NAMESPACE`].
- **Subdirectories**: None (leaf component package).

## Invariants
- **Strict Read-Only Cluster Access**: `run_investigation` always invokes `mcp_tools(read_only=True)`, and `mcp_tools` filters MCP server tools strictly to those with `t.annotations.read_only_hint` set, guaranteeing the LLM is never given write or remediation tools [`run.py:run_investigation`], [`mcp_tools.py:mcp_tools`].
- **Allowlisted Remediation & Human Approval Gate**: `SuggestedFix.action` is schema-constrained to `Literal["scale_deployment", "rollout_undo", "rollout_restart", "none"]`, and `run_investigation` stamps every generated report with `status = "pending_approval"` rather than executing fixes automatically [`report.py:SuggestedFix`], [`run.py:run_investigation`].
- **Bounded Tool Budget & Graph Recursion**: When `state["tool_calls"] >= MAX_TOOL_CALLS`, the `investigate` node appends `OUT_OF_BUDGET` and invokes `pool.ainvoke` without `tools` bound while `next_step` routes unconditionally to `write_report`; total LangGraph steps are bounded by `recursion_limit = 2 * MAX_TOOL_CALLS + 10` [`graph.py:build_graph`], [`graph.py:investigate`], [`prompts.py:OUT_OF_BUDGET`].
- **Tool-Transcript Flattening Before Structured Output**: `write_report` transforms prior `AIMessage.tool_calls` and `tool` messages into plain-text `AIMessage`/`HumanMessage` entries via `_flatten` before requesting `schema=Report`, preventing structured-output model failures on tool-call history [`graph.py:build_graph`], [`graph.py:_flatten`].
- **Tool Error Containment & Output Truncation**: `ToolNode` runs with `handle_tool_errors=True`, `_to_langchain` strips `None` arguments, and `_result_text` prefixes MCP errors with `"TOOL ERROR: "` and truncates payloads exceeding `MAX_TOOL_OUTPUT` (`6000` chars) so noisy logs cannot overflow context [`graph.py:build_graph`], [`mcp_tools.py:_to_langchain`], [`mcp_tools.py:_result_text`], [`mcp_tools.py:MAX_TOOL_OUTPUT`].
- **Deterministic Model Pool Failover**: `ModelPool` configures every `ChatGoogleGenerativeAI` client with `temperature=0, max_retries=1, timeout=90` and only fails over to the next model on `GoogleRateLimitError`, `GoogleModelNotFoundError`, or `GoogleAPIError` with HTTP code in `_SKIP_CODES` (`{429, 404, 500, 503, 504}`); non-skippable exceptions raise immediately, and exhausting all models raises `RuntimeError` [`models.py:ModelPool.__init__`], [`models.py:ModelPool.ainvoke`], [`models.py:_should_skip`], [`models.py:_SKIP_CODES`].
- **API Key Pre-Flight Check**: Both `run_investigation` and `watch` call `check_api_key()` upfront and terminate via `SystemExit` if neither `GOOGLE_API_KEY` nor `GEMINI_API_KEY` is present [`run.py:check_api_key`], [`run.py:run_investigation`], [`watch.py:watch`].
- **Alert Deduplication & Settle Coalescing**: In continuous mode, `watch()` tracks `seen` alert `fingerprint` values, sleeps `SETTLE_SECONDS` when unseen fingerprints arrive to batch correlated alerts into one investigation, and resets `seen` only when zero firing alerts remain in `NAMESPACE`; polling errors in `shop_alerts()` are caught and logged without stopping the watcher loop [`watch.py:watch`].

## Side Effects
- **Environment & Warning Mutation on Import**: Importing `agent.run` loads `kube-sentinel/.env` into `os.environ` via `load_dotenv(ROOT / ".env")` and registers global `warnings.filterwarnings` rules suppressing Gemini sampling and unawaited-coroutine warnings [`run.py:ROOT`].
- **MCP Server Subprocess Execution**: `mcp_tools` spawns `sys.executable -m mcp_server` as a child process over stdio JSON-RPC, which transitively performs read-only network requests to the Kubernetes API, Prometheus, Loki, and Alertmanager [`mcp_tools.py:mcp_tools`].
- **Outbound Gemini API Calls**: `ModelPool.ainvoke` sends HTTPS requests to Google's Generative Language API via `ChatGoogleGenerativeAI`, mutates pool counters (`index`, `used`, `requests`), and invokes `on_switch` callbacks on failover [`models.py:ModelPool.ainvoke`].
- **Direct Alertmanager Polling**: `shop_alerts()` calls `mcp_server.observability.firing_alerts()`, issuing HTTP/HTTPS GET requests to Alertmanager (either via `ALERTMANAGER_URL` or through the Kubernetes API service proxy) [`watch.py:shop_alerts`].
- **Report File Persistence**: When `save=True`, `run_investigation` creates `REPORTS_DIR` (`mkdir(exist_ok=True)`) and writes UTF-8 JSON report files named `{YYYYMMDD-HHMMSS-<6hex>}.json` to disk [`run.py:run_investigation`].
- **Console / Callback I/O**: `__main__.main` and `watch` write live tool traces, model switch alerts, timestamps, and formatted incident reports to `stdout` [`__main__.py:main`], [`watch.py:log`], [`watch.py:investigate_now`].

## Verification
- Run: `<test-command>`
