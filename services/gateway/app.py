import logging
import os
import random
import time
from collections import deque
from threading import Lock
from uuid import uuid4
from concurrent.futures import ThreadPoolExecutor, as_completed
import datetime

import requests
from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import HTMLResponse, JSONResponse
from opentelemetry import metrics, trace
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
from opentelemetry.instrumentation.requests import RequestsInstrumentor

from otel_setup import configure_otel


service_name = os.getenv("SERVICE_NAME", "gateway")
configure_otel(service_name)

RequestsInstrumentor().instrument()
app = FastAPI(title="Gateway Service")
FastAPIInstrumentor.instrument_app(app)

logger = logging.getLogger(service_name)
tracer = trace.get_tracer(service_name)
meter = metrics.get_meter(service_name)

checkout_counter = meter.create_counter(
    "demo_checkout_requests", description="Total checkout requests"
)
checkout_failure_counter = meter.create_counter(
    "demo_checkout_failures", description="Total checkout failed requests"
)
checkout_duration = meter.create_histogram(
    "demo_checkout_duration_ms",
    description="Checkout duration in milliseconds",
    unit="ms",
)

orders_url = os.getenv("ORDERS_URL", "http://orders:8000")
inventory_url = os.getenv("INVENTORY_URL", "http://inventory:8000")

# Observability backend URLs
prometheus_url = os.getenv("PROMETHEUS_URL", "http://prometheus:9090")
tempo_url = os.getenv("TEMPO_URL", "http://tempo:3200")
loki_url = os.getenv("LOKI_URL", "http://loki:3100")

runtime_lock = Lock()
runtime_events = deque(maxlen=5000)
runtime_total_requests = 0
runtime_total_errors = 0


def record_runtime_metric(latency_ms: float, is_error: bool) -> None:
    global runtime_total_requests, runtime_total_errors
    with runtime_lock:
        runtime_total_requests += 1
        if is_error:
            runtime_total_errors += 1
        runtime_events.append(
            {
                "ts": time.time(),
                "latency_ms": float(latency_ms),
                "is_error": bool(is_error),
            }
        )


def percentile(values: list[float], p: float) -> float:
    if not values:
        return 0.0
    sorted_values = sorted(values)
    idx = max(
        0, min(len(sorted_values) - 1, int(round((p / 100) * (len(sorted_values) - 1))))
    )
    return float(sorted_values[idx])


def fetch_runtime_metrics(window_seconds: int = 90) -> dict:
    now = time.time()
    cutoff = now - window_seconds

    with runtime_lock:
        recent = [event for event in runtime_events if event["ts"] >= cutoff]
        total_requests = runtime_total_requests

    sample_count = len(recent)
    error_count = sum(1 for event in recent if event["is_error"])
    latencies = [event["latency_ms"] for event in recent if event["latency_ms"] > 0]

    return {
        "source": "runtime",
        "window_seconds": window_seconds,
        "sample_count": sample_count,
        "total_requests": total_requests,
        "error_rate": (error_count / sample_count) if sample_count > 0 else 0.0,
        "checkout_rate": (sample_count / window_seconds) if window_seconds > 0 else 0.0,
        "checkout_latency_p50": percentile(latencies, 50),
        "checkout_latency_p95": percentile(latencies, 95),
    }


def reset_runtime_metrics() -> None:
    global runtime_total_requests, runtime_total_errors
    with runtime_lock:
        runtime_total_requests = 0
        runtime_total_errors = 0
        runtime_events.clear()


def fetch_trace_data(trace_id: str) -> dict:
    """Fetch trace data from Tempo API with retry logic"""
    try:
        response = requests.get(f"{tempo_url}/api/traces/{trace_id}", timeout=5)
        if response.status_code == 200:
            return response.json()
        return {"error": "Trace not found", "status_code": response.status_code}
    except Exception as e:
        logger.error(f"Failed to fetch trace: {e}")
        return {"error": str(e)}


def fetch_metrics_data() -> dict:
    """Fetch key metrics from Prometheus"""
    metrics_result = {}
    queries = {
        "checkout_rate": "rate(demo_checkout_requests_total[1m])",
        "checkout_latency_p95": "histogram_quantile(0.95, sum by (le) (rate(demo_checkout_duration_ms_bucket[5m])))",
        "checkout_latency_p50": "histogram_quantile(0.50, sum by (le) (rate(demo_checkout_duration_ms_bucket[5m])))",
        "error_rate": 'rate(demo_checkout_requests_total{status="error"}[1m])',
        "total_requests": "sum(demo_checkout_requests_total)",
    }

    try:
        for metric_name, query in queries.items():
            response = requests.get(
                f"{prometheus_url}/api/v1/query", params={"query": query}, timeout=3
            )
            if response.status_code == 200:
                data = response.json()
                metrics_result[metric_name] = data.get("data", {}).get("result", [])
    except Exception as e:
        logger.error(f"Failed to fetch metrics: {e}")

    return metrics_result


def fetch_logs_data(
    trace_id: str = None, request_id: str = None, lookback_minutes: int = 5
) -> dict:
    """Fetch logs from Loki API"""
    try:
        # Build LogQL query
        if request_id:
            query = f'{{service_name=~"gateway|orders|inventory"}} |~ "{request_id}"'
        elif trace_id:
            query = f'{{service_name=~"gateway|orders|inventory"}} |~ "{trace_id}"'
        else:
            query = '{service_name=~"gateway|orders|inventory"}'

        # Calculate time range
        end_time = datetime.datetime.now()
        start_time = end_time - datetime.timedelta(minutes=lookback_minutes)

        response = requests.get(
            f"{loki_url}/loki/api/v1/query_range",
            params={
                "query": query,
                "start": int(start_time.timestamp() * 1e9),  # nanoseconds
                "end": int(end_time.timestamp() * 1e9),
                "limit": 100,
            },
            timeout=5,
        )

        if response.status_code == 200:
            return response.json()
        return {"error": "Logs not found", "status_code": response.status_code}
    except Exception as e:
        logger.error(f"Failed to fetch logs: {e}")
        return {"error": str(e)}


@app.get("/", response_class=HTMLResponse)
def home() -> str:
    return """
    <!doctype html>
    <html lang=\"en\">
      <head>
        <meta charset=\"utf-8\" />
        <title>Observability Demo</title>
        <style>
          :root {
            --bg: #0b1020;
            --card: #111a33;
            --card-soft: #152042;
            --text: #e8ecf8;
            --muted: #9fb0d7;
            --accent: #5b8cff;
            --accent-2: #36d1dc;
            --danger: #ff6b6b;
            --border: #22315f;
          }
          body {
            font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif;
            margin: 24px;
            color: var(--text);
            background:
              radial-gradient(circle at 15% 10%, rgba(91, 140, 255, 0.18), transparent 40%),
              radial-gradient(circle at 85% 5%, rgba(54, 209, 220, 0.12), transparent 35%),
              var(--bg);
          }
          .layout {
            max-width: 1400px;
            margin: 0 auto;
            display: grid;
            gap: 16px;
          }
          .hero {
            background: linear-gradient(140deg, #16234a, #121b38);
            border: 1px solid var(--border);
            border-radius: 14px;
            padding: 18px;
            box-shadow: 0 8px 24px rgba(0, 0, 0, 0.25);
          }
          .hero h2 { margin: 0 0 8px 0; font-size: 24px; }
          .hero p { margin: 0; color: var(--muted); }
          .controls {
            background: var(--card);
            border: 1px solid var(--border);
            border-radius: 14px;
            padding: 14px;
          }
          .controls-grid {
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(240px, 1fr));
            gap: 10px;
            margin-bottom: 10px;
          }
          button {
            border: 1px solid #3451a2;
            background: linear-gradient(140deg, #22356d, #1a2a59);
            color: #eef3ff;
            border-radius: 10px;
            padding: 10px 12px;
            font-weight: 600;
            cursor: pointer;
          }
          button:hover { filter: brightness(1.08); }
          .btn-stop {
            border-color: #7b2b2b;
            background: linear-gradient(140deg, #5a1f1f, #3c1616);
            color: #ffd8d8;
          }
          pre {
            background: var(--card-soft);
            border: 1px solid var(--border);
            color: #d8e3ff;
            padding: 12px;
            border-radius: 10px;
            min-height: 88px;
            max-height: 220px;
            overflow: auto;
          }
          .obs-panel {
            background: var(--card);
            border: 1px solid var(--border);
            border-radius: 14px;
            padding: 14px;
          }
          .obs-panel h3 { margin: 0 0 10px 0; color: #dbe6ff; }
          .obs-frame {
            width: 100%;
            height: 850px;
            border: 1px solid var(--border);
            border-radius: 10px;
            background: #fff;
          }
        </style>
      </head>
      <body>
        <div class=\"layout\">
          <div class=\"hero\">
            <h2>Microservice Observability Demo</h2>
            <p>Gateway gọi Orders + Inventory, telemetry đi qua OpenTelemetry và hiển thị trực tiếp tại dashboard bên dưới.</p>
          </div>

          <div class=\"controls\">
            <div class=\"controls-grid\">
              <button onclick=\"scenarioSuccess()\">Kịch bản 1: Checkout thành công</button>
              <button onclick=\"scenarioConflict()\">Kịch bản 2: Lỗi hết hàng (409)</button>
              <button onclick=\"scenarioRandomError()\">Kịch bản 3: Lỗi ngẫu nhiên (500)</button>
              <button onclick=\"scenarioLoadBurst()\">Kịch bản 4: Tải hỗn hợp 20 giây</button>
              <button onclick="scenarioLatencyStress()">Kịch bản 5: Latency stress 60 giây</button>
              <button class=\"btn-stop\" onclick=\"stopScenario()\">Dừng kịch bản đang chạy</button>
            </div>
            <pre id=\"out\">Sẵn sàng...</pre>
          </div>

          <div class=\"obs-panel\">
            <h3>Observability Dashboard</h3>
            <iframe id=\"obsFrame\" class=\"obs-frame\" title=\"Observability Dashboard\"></iframe>
          </div>
        </div>
        <script>
          const outEl = document.getElementById('out');
          const obsFrame = document.getElementById('obsFrame');
          let isScenarioRunning = false;
          let stopRequested = false;
          let activeController = null;

          function sleep(ms) {
            return new Promise(resolve => setTimeout(resolve, ms));
          }

          function percentile(values, p) {
            if (!values || values.length === 0) return 0;
            const sorted = [...values].sort((a, b) => a - b);
            const idx = Math.ceil((p / 100) * sorted.length) - 1;
            return sorted[Math.max(0, Math.min(idx, sorted.length - 1))];
          }

          function openObservability(data) {
            if (data && data.trace_id) {
              const obsUrl = `/observability/${data.trace_id}?request_id=${data.request_id || ''}`;
              obsFrame.src = obsUrl;
            }
          }

          async function resetRuntimeMetrics() {
            try {
              const response = await fetch('/api/runtime-metrics/reset', { method: 'POST' });
              return response.ok;
            } catch (error) {
              return false;
            }
          }

          function toErrorRatePct(errors, total) {
            if (!total) return 0;
            return Number(((errors / total) * 100).toFixed(2));
          }

          async function callCheckout(qty, options = {}) {
            const updateDashboard = options.updateDashboard !== false;
            activeController = new AbortController();

            try {
              const response = await fetch(
                `/api/checkout?item_id=item-1&qty=${qty}`,
                { signal: activeController.signal }
              );
              const data = await response.json();
              outEl.textContent = JSON.stringify(data, null, 2);
              if (updateDashboard) {
                openObservability(data);
              }
              return { ok: response.ok, status: response.status, data, aborted: false };
            } catch (error) {
              if (error.name === 'AbortError') {
                outEl.textContent = 'Đã dừng kịch bản đang chạy.';
                return { ok: false, status: 0, data: null, aborted: true };
              }
              throw error;
            } finally {
              activeController = null;
            }
          }

          async function runScenario(name, runner) {
            if (isScenarioRunning) {
              outEl.textContent = 'Đang có kịch bản chạy. Bấm "Dừng kịch bản đang chạy" trước.';
              return;
            }

            isScenarioRunning = true;
            stopRequested = false;

            const resetOk = await resetRuntimeMetrics();
            if (!resetOk) {
              outEl.textContent = `${name}: không thể reset runtime metrics trước khi chạy.`;
              isScenarioRunning = false;
              return;
            }

            try {
              await runner();
            } catch (error) {
              outEl.textContent = `${name} gặp lỗi: ${error}`;
            } finally {
              isScenarioRunning = false;
            }
          }

          async function stopScenario() {
            stopRequested = true;
            if (activeController) {
              activeController.abort();
            }

            const resetOk = await resetRuntimeMetrics();
            if (!resetOk) {
              outEl.textContent = 'Đã dừng kịch bản, nhưng reset chỉ số thất bại.';
              return;
            }

            if (!isScenarioRunning) {
              outEl.textContent = 'Không có kịch bản nào đang chạy. Đã reset chỉ số runtime.';
            } else {
              outEl.textContent = 'Đã dừng kịch bản và reset chỉ số runtime.';
            }
          }

          async function scenarioSuccess() {
            await runScenario('Kịch bản 1', async () => {
              outEl.textContent = 'Đang chạy kịch bản 1 (success)...';
              if (stopRequested) return;
              const result = await callCheckout(1);
              if (result.aborted) return;

              const total = 1;
              const errors = result.ok ? 0 : 1;
              outEl.textContent = JSON.stringify({
                scenario: 'success-single-checkout',
                stopped: false,
                total_requests: total,
                success_requests: total - errors,
                error_requests: errors,
                error_rate_pct: toErrorRatePct(errors, total),
                last_status_code: result.status
              }, null, 2);
            });
          }

          async function scenarioConflict() {
            await runScenario('Kịch bản 2', async () => {
              outEl.textContent = 'Đang chạy kịch bản 2 (conflict 409)...';
              if (stopRequested) return;
              const result = await callCheckout(9999);
              if (result.aborted) return;

              const total = 1;
              const errors = result.ok ? 0 : 1;
              outEl.textContent = JSON.stringify({
                scenario: 'forced-conflict',
                stopped: false,
                total_requests: total,
                success_requests: total - errors,
                error_requests: errors,
                error_rate_pct: toErrorRatePct(errors, total),
                last_status_code: result.status
              }, null, 2);
            });
          }

          async function scenarioRandomError() {
            await runScenario('Kịch bản 3', async () => {
              outEl.textContent = 'Đang chạy kịch bản 3 (thử tối đa 10 lần để bắt lỗi ngẫu nhiên)...';
              let total = 0;
              let errors = 0;

              for (let i = 1; i <= 10; i++) {
                if (stopRequested) {
                  outEl.textContent = JSON.stringify({
                    scenario: 'random-error-search',
                    stopped: true,
                    total_requests: total,
                    success_requests: total - errors,
                    error_requests: errors,
                    error_rate_pct: toErrorRatePct(errors, total)
                  }, null, 2);
                  return;
                }

                const result = await callCheckout(1);
                if (result.aborted) return;
                total += 1;
                if (!result.ok) {
                  errors += 1;
                }

                if (!result.ok && result.status !== 409) {
                  outEl.textContent = JSON.stringify({
                    scenario: 'random-error-search',
                    stopped: false,
                    total_requests: total,
                    success_requests: total - errors,
                    error_requests: errors,
                    error_rate_pct: toErrorRatePct(errors, total),
                    error_captured_at_attempt: i,
                    last_status_code: result.status,
                    last_error: result.data?.detail || null
                  }, null, 2);
                  return;
                }
              }

              outEl.textContent = JSON.stringify({
                scenario: 'random-error-search',
                stopped: false,
                total_requests: total,
                success_requests: total - errors,
                error_requests: errors,
                error_rate_pct: toErrorRatePct(errors, total),
                note: 'Chưa gặp lỗi ngẫu nhiên sau 10 lần. Bấm lại để thử tiếp.'
              }, null, 2);
            });
          }

          async function scenarioLoadBurst() {
            await runScenario('Kịch bản 4', async () => {
              outEl.textContent = 'Đang chạy kịch bản 4 (tải hỗn hợp 20 giây)...';

              const started = Date.now();
              let total = 0;
              let success = 0;
              let errors = 0;

              while (Date.now() - started < 20000) {
                if (stopRequested) {
                  outEl.textContent = JSON.stringify({
                    scenario: 'load-burst-20s',
                    stopped: true,
                    total_requests: total,
                    success_requests: success,
                    error_requests: errors,
                    error_rate_pct: toErrorRatePct(errors, total)
                  }, null, 2);
                  return;
                }

                total += 1;
                const shouldForceConflict = Math.random() < 0.2;
                const qty = shouldForceConflict ? 9999 : (Math.floor(Math.random() * 3) + 1);
                const result = await callCheckout(qty, { updateDashboard: total === 1 });
                if (result.aborted) return;

                if (result.ok) success += 1;
                else errors += 1;

                await sleep(330);
              }

              outEl.textContent = JSON.stringify({
                scenario: 'load-burst-20s',
                stopped: false,
                total_requests: total,
                success_requests: success,
                error_requests: errors,
                error_rate_pct: toErrorRatePct(errors, total)
              }, null, 2);
            });
          }

          async function scenarioLatencyStress() {
            await runScenario('Kịch bản 5', async () => {
              outEl.textContent = 'Đang chạy kịch bản 5 (latency stress dao động mạnh 60 giây)...';

              const started = Date.now();
              const durationMs = 60000;
              let total = 0;
              let success = 0;
              let errors = 0;
              const latencySamples = [];
              let minLatency = Number.POSITIVE_INFINITY;
              let maxLatency = 0;

              const getPhaseInterval = (elapsedMs) => {
                const phase = Math.floor(elapsedMs / 10000) % 3;
                if (phase === 0) return 160;  // burst
                if (phase === 1) return 520;  // cool down
                return 280;                   // medium
              };

              const getPhaseQty = (elapsedMs) => {
                const phase = Math.floor(elapsedMs / 10000) % 3;
                if (phase === 0) return Math.random() < 0.30 ? 3 : 2;
                if (phase === 1) return 1;
                return Math.random() < 0.35 ? 2 : 1;
              };

              while (Date.now() - started < durationMs) {
                const elapsed = Date.now() - started;

                if (stopRequested) {
                  outEl.textContent = JSON.stringify({
                    scenario: 'latency-stress-60s-oscillating',
                    stopped: true,
                    total_requests: total,
                    success_requests: success,
                    error_requests: errors,
                    error_rate_pct: toErrorRatePct(errors, total),
                    min_latency_ms: Number.isFinite(minLatency) ? Math.round(minLatency) : 0,
                    max_latency_ms: Math.round(maxLatency),
                    p50_latency_ms: Math.round(percentile(latencySamples, 50)),
                    p95_latency_ms: Math.round(percentile(latencySamples, 95))
                  }, null, 2);
                  return;
                }

                total += 1;
                const qty = getPhaseQty(elapsed);
                const result = await callCheckout(qty, { updateDashboard: total === 1 });
                if (result.aborted) return;

                const latency = Number(result.data?.latency_ms);
                if (Number.isFinite(latency) && latency > 0) {
                  latencySamples.push(latency);
                  minLatency = Math.min(minLatency, latency);
                  maxLatency = Math.max(maxLatency, latency);
                }

                if (result.ok) success += 1;
                else errors += 1;

                await sleep(getPhaseInterval(elapsed));
              }

              outEl.textContent = JSON.stringify({
                scenario: 'latency-stress-60s-oscillating',
                stopped: false,
                total_requests: total,
                success_requests: success,
                error_requests: errors,
                error_rate_pct: toErrorRatePct(errors, total),
                min_latency_ms: Number.isFinite(minLatency) ? Math.round(minLatency) : 0,
                max_latency_ms: Math.round(maxLatency),
                p50_latency_ms: Math.round(percentile(latencySamples, 50)),
                p95_latency_ms: Math.round(percentile(latencySamples, 95))
              }, null, 2);
            });
          }
        </script>
      </body>
    </html>
    """


@app.get("/health")
def health() -> dict:
    return {"status": "ok", "service": service_name}


@app.get("/observability/{trace_id}", response_class=HTMLResponse)
def observability_page(trace_id: str, request_id: str = Query(default=None)):
    """Render the integrated observability page for a specific trace"""
    request_id_param = request_id if request_id else ""
    return f"""
    <!doctype html>
    <html lang="en">
      <head>
        <meta charset="utf-8" />
        <title>Observability Dashboard - {trace_id[:16]}</title>
        <script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.0/dist/chart.umd.min.js"></script>
        <style>
          * {{ box-sizing: border-box; margin: 0; padding: 0; }}
          body {{
            font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif;
            background: #f5f7fa;
            padding: 20px;
          }}
          .container {{ max-width: 1400px; margin: 0 auto; }}
          .header {{
            background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
            color: white;
            padding: 24px;
            border-radius: 12px;
            margin-bottom: 24px;
            box-shadow: 0 4px 6px rgba(0,0,0,0.1);
          }}
          .header h1 {{ font-size: 28px; margin-bottom: 8px; }}
          .header .meta {{ opacity: 0.9; font-size: 14px; }}
          .header .meta span {{
            margin-right: 20px;
            background: rgba(255,255,255,0.2);
            padding: 4px 12px;
            border-radius: 4px;
            display: inline-block;
            margin-top: 8px;
          }}
          .grid {{
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(450px, 1fr));
            gap: 20px;
            margin-bottom: 20px;
          }}
          .card {{
            background: white;
            border-radius: 8px;
            padding: 20px;
            box-shadow: 0 2px 4px rgba(0,0,0,0.08);
          }}
          .card h2 {{
            font-size: 18px;
            margin-bottom: 16px;
            color: #2d3748;
            border-bottom: 2px solid #e2e8f0;
            padding-bottom: 8px;
          }}
          .chart-container {{
            position: relative;
            height: 250px;
            margin-bottom: 16px;
          }}
          .trace-flow {{
            display: flex;
            align-items: center;
            justify-content: space-around;
            padding: 20px 0;
            min-height: 200px;
            flex-wrap: wrap;
          }}
          .service-box {{
            background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
            color: white;
            padding: 20px;
            border-radius: 8px;
            text-align: center;
            min-width: 120px;
            box-shadow: 0 4px 6px rgba(102, 126, 234, 0.3);
            margin: 10px;
          }}
          .service-box .name {{ font-weight: bold; margin-bottom: 8px; font-size: 16px; }}
          .service-box .duration {{ font-size: 12px; opacity: 0.9; }}
          .arrow {{
            color: #a0aec0;
            font-size: 24px;
            font-weight: bold;
            margin: 0 10px;
          }}
          .logs-table {{
            width: 100%;
            border-collapse: collapse;
            font-size: 13px;
          }}
          .logs-container {{
            max-height: 400px;
            overflow-y: auto;
            display: block;
          }}
          .logs-table thead {{
            position: sticky;
            top: 0;
            background: white;
            z-index: 10;
          }}
          .logs-table th {{
            background: #f7fafc;
            padding: 10px;
            text-align: left;
            font-weight: 600;
            border-bottom: 2px solid #e2e8f0;
          }}
          .logs-table td {{
            padding: 8px 10px;
            border-bottom: 1px solid #e2e8f0;
          }}
          .level-info {{ color: #3182ce; }}
          .level-warning {{ color: #dd6b20; }}
          .level-error {{ color: #e53e3e; }}
          .status {{
            padding: 4px 8px;
            border-radius: 4px;
            font-size: 12px;
            font-weight: 600;
          }}
          .status.live {{
            background: #c6f6d5;
            color: #22543d;
            animation: pulse 2s infinite;
          }}
          @keyframes pulse {{
            0%, 100% {{ opacity: 1; }}
            50% {{ opacity: 0.7; }}
          }}
          .metric-grid {{
            display: grid;
            grid-template-columns: repeat(2, 1fr);
            gap: 12px;
            margin-bottom: 20px;
          }}
          .metric-card {{
            background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
            color: white;
            padding: 16px;
            border-radius: 8px;
            text-align: center;
          }}
          .metric-card .value {{ font-size: 32px; font-weight: bold; }}
          .metric-card .label {{ font-size: 12px; opacity: 0.9; margin-top: 4px; }}
          .error {{
            background: #fed7d7;
            color: #742a2a;
            padding: 12px;
            border-radius: 6px;
            margin: 12px 0;
          }}
          .loading {{
            text-align: center;
            padding: 40px;
            color: #718096;
          }}
        </style>
      </head>
      <body>
        <div class="container">
          <div class="header">
            <h1>🔍 Observability Dashboard</h1>
            <div class="meta">
              <span><strong>Trace ID:</strong> {trace_id[:16]}...</span>
              {f'<span><strong>Request ID:</strong> {request_id_param}</span>' if request_id_param else ''}
              <span class="status live">● LIVE UPDATING</span>
            </div>
          </div>

          <!-- Metrics Section -->
          <div class="card">
            <h2>📊 Metrics Overview</h2>
            <div class="metric-grid" id="metricCards">
              <div class="metric-card">
                <div class="value" id="totalRequests">--</div>
                <div class="label">Total Requests</div>
              </div>
              <div class="metric-card">
                <div class="value" id="errorRate">--</div>
                <div class="label">Error Rate</div>
              </div>
              <div class="metric-card">
                <div class="value" id="p95Latency">--</div>
                <div class="label">P95 Latency (ms)</div>
              </div>
              <div class="metric-card">
                <div class="value" id="requestRate">--</div>
                <div class="label">Request Rate (req/s)</div>
              </div>
            </div>
            <div class="grid">
              <div class="chart-container">
                <canvas id="latencyChart"></canvas>
              </div>
              <div class="chart-container">
                <canvas id="rateChart"></canvas>
              </div>
            </div>
          </div>

          <!-- Trace Flow Section -->
          <div class="card">
            <h2>🔗 Distributed Trace Flow</h2>
            <div id="traceFlow" class="trace-flow">
              <div class="loading">Loading trace data...</div>
            </div>
          </div>

          <!-- Logs Section -->
          <div class="card">
            <h2>📝 Related Logs</h2>
            <div class="logs-container">
              <table class="logs-table" id="logsTable">
                <thead>
                  <tr>
                    <th style="width: 150px">Timestamp</th>
                    <th style="width: 80px">Level</th>
                    <th style="width: 100px">Service</th>
                    <th>Message</th>
                  </tr>
                </thead>
                <tbody id="logsBody">
                  <tr><td colspan="4" class="loading">Loading logs...</td></tr>
                </tbody>
              </table>
            </div>
          </div>
        </div>

        <script>
          const TRACE_ID = "{trace_id}";
          const REQUEST_ID = "{request_id_param}";
          let latencyChart, rateChart;
          let baselineTotalRequests = null;

          // Initialize charts
          function initCharts() {{
            const latencyCtx = document.getElementById('latencyChart').getContext('2d');
            latencyChart = new Chart(latencyCtx, {{
              type: 'line',
              data: {{
                labels: [],
                datasets: [{{
                  label: 'P95 Latency (ms)',
                  data: [],
                  borderColor: '#667eea',
                  backgroundColor: 'rgba(102, 126, 234, 0.1)',
                  tension: 0.4,
                  fill: true
                }}, {{
                  label: 'P50 Latency (ms)',
                  data: [],
                  borderColor: '#48bb78',
                  backgroundColor: 'rgba(72, 187, 120, 0.1)',
                  tension: 0.4,
                  fill: true
                }}]
              }},
              options: {{
                responsive: true,
                maintainAspectRatio: false,
                plugins: {{
                  legend: {{ display: true, position: 'top' }},
                  title: {{ display: true, text: 'Latency Over Time' }}
                }},
                scales: {{
                  y: {{ beginAtZero: true }}
                }}
              }}
            }});

            const rateCtx = document.getElementById('rateChart').getContext('2d');
            rateChart = new Chart(rateCtx, {{
              type: 'line',
              data: {{
                labels: [],
                datasets: [{{
                  label: 'Request Rate (req/s)',
                  data: [],
                  borderColor: '#ed8936',
                  backgroundColor: 'rgba(237, 137, 54, 0.1)',
                  tension: 0.4,
                  fill: true
                }}]
              }},
              options: {{
                responsive: true,
                maintainAspectRatio: false,
                plugins: {{
                  legend: {{ display: true, position: 'top' }},
                  title: {{ display: true, text: 'Request Rate Over Time' }}
                }},
                scales: {{
                  y: {{ beginAtZero: true }}
                }}
              }}
            }});
          }}

          // Fetch and update observability data
          async function fetchData() {{
            try {{
              const url = `/api/observability-data/${{TRACE_ID}}${{REQUEST_ID ? '?request_id=' + REQUEST_ID : ''}}`;
              const response = await fetch(url);
              const data = await response.json();

              updateMetrics(data.runtime_metrics || data.metrics || {{}});
              updateTrace(data.trace || {{}});
              updateLogs(data.logs || {{}});
            }} catch (error) {{
              console.error('Failed to fetch observability data:', error);
            }}
          }}

          // Update metrics cards and charts
          function updateMetrics(metrics) {{
            // Update metric cards - Parse many value shapes and always return a finite number
            const parseMetric = (value) => {{
              let raw = value;

              if (raw == null) return 0;

              if (Array.isArray(raw)) {{
                raw = raw[raw.length - 1];
              }}

              if (raw && typeof raw === 'object') {{
                if (Array.isArray(raw.value)) {{
                  raw = raw.value[raw.value.length - 1];
                }} else if ('value' in raw) {{
                  raw = raw.value;
                }}
              }}

              const num = typeof raw === 'number' ? raw : parseFloat(String(raw));
              return Number.isFinite(num) ? num : 0;
            }};

            const toFixedSafe = (value, digits = 2) => {{
              const num = Number(value);
              return Number.isFinite(num) ? num.toFixed(digits) : (0).toFixed(digits);
            }};

            const isRuntimeMetrics = metrics.source === 'runtime';

            const totalReq = isRuntimeMetrics
              ? parseMetric(metrics.total_requests)
              : parseMetric(metrics.total_requests?.[0]?.value?.[1]);

            const errRate = isRuntimeMetrics
              ? parseMetric(metrics.error_rate)
              : parseMetric(metrics.error_rate?.[0]?.value?.[1]);

            const p95 = isRuntimeMetrics
              ? parseMetric(metrics.checkout_latency_p95)
              : parseMetric(metrics.checkout_latency_p95?.[0]?.value?.[1]);

            const reqRate = isRuntimeMetrics
              ? parseMetric(metrics.checkout_rate)
              : parseMetric(metrics.checkout_rate?.[0]?.value?.[1]);

            if (baselineTotalRequests === null || totalReq < baselineTotalRequests) {{
              baselineTotalRequests = totalReq;
            }}

            const sessionTotalReq = Math.max(0, totalReq - baselineTotalRequests);

            document.getElementById('totalRequests').textContent = Math.round(sessionTotalReq);
            document.getElementById('errorRate').textContent = toFixedSafe(errRate * 100, 2) + '%';
            document.getElementById('p95Latency').textContent = toFixedSafe(p95, 2);
            document.getElementById('requestRate').textContent = toFixedSafe(reqRate, 2);

            // Update charts
            const now = new Date().toLocaleTimeString();
            const maxDataPoints = 20;

            // Latency chart
            if (latencyChart.data.labels.length >= maxDataPoints) {{
              latencyChart.data.labels.shift();
              latencyChart.data.datasets[0].data.shift();
              latencyChart.data.datasets[1].data.shift();
            }}
            latencyChart.data.labels.push(now);
            latencyChart.data.datasets[0].data.push(Number(toFixedSafe(p95, 2)));
            const p50 = isRuntimeMetrics
              ? parseMetric(metrics.checkout_latency_p50)
              : parseMetric(metrics.checkout_latency_p50?.[0]?.value?.[1]);
            latencyChart.data.datasets[1].data.push(Number(toFixedSafe(p50, 2)));
            latencyChart.update('none');

            // Rate chart
            if (rateChart.data.labels.length >= maxDataPoints) {{
              rateChart.data.labels.shift();
              rateChart.data.datasets[0].data.shift();
            }}
            rateChart.data.labels.push(now);
            rateChart.data.datasets[0].data.push(reqRate);
            rateChart.update('none');
          }}

          // Update trace flow visualization
          function updateTrace(trace) {{
            const flowDiv = document.getElementById('traceFlow');

            if (trace.error) {{
              flowDiv.innerHTML = `<div class="error">Error loading trace: ${{trace.error}}</div>`;
              return;
            }}

            // Parse Tempo trace response
            const batches = trace.batches || trace.resourceSpans || [];
            if (batches.length === 0) {{
              flowDiv.innerHTML = '<div class="loading">No trace data available yet. Trace may still be processing...</div>';
              return;
            }}

            // Extract spans and build service flow
            const spans = [];
            batches.forEach(batch => {{
              const resource = batch.resource || {{}};
              const serviceName = resource.attributes?.find(a => a.key === 'service.name')?.value?.stringValue || 'unknown';

              if (batch.scopeSpans) {{
                batch.scopeSpans.forEach(scopeSpan => {{
                  if (scopeSpan.spans) {{
                    scopeSpan.spans.forEach(span => {{
                      spans.push({{
                        name: span.name,
                        service: serviceName,
                        duration: (span.endTimeUnixNano - span.startTimeUnixNano) / 1000000, // Convert to ms
                        startTime: span.startTimeUnixNano
                      }});
                    }});
                  }}
                }});
              }}
            }});

            // Sort by start time
            spans.sort((a, b) => a.startTime - b.startTime);

            // Build flow HTML
            let flowHTML = '';
            spans.forEach((span, idx) => {{
              if (idx > 0) flowHTML += '<div class="arrow">→</div>';
              flowHTML += `
                <div class="service-box">
                  <div class="name">${{span.service}}</div>
                  <div>${{span.name}}</div>
                  <div class="duration">${{span.duration.toFixed(2)}} ms</div>
                </div>
              `;
            }});

            if (flowHTML) {{
              flowDiv.innerHTML = flowHTML;
            }} else {{
              flowDiv.innerHTML = '<div class="loading">Trace data format not recognized</div>';
            }}
          }}

          // Update logs table
          function updateLogs(logs) {{
            const tbody = document.getElementById('logsBody');

            if (logs.error) {{
              tbody.innerHTML = `<tr><td colspan="4" class="error">Error loading logs: ${{logs.error}}</td></tr>`;
              return;
            }}

            const results = logs.data?.result || [];
            if (results.length === 0) {{
              tbody.innerHTML = '<tr><td colspan="4" class="loading">No logs found for this trace</td></tr>';
              return;
            }}

            // Parse and display logs
            let logsHTML = '';
            results.forEach(result => {{
              const stream = result.stream || {{}};
              const values = result.values || [];

              values.forEach(([timestamp, message]) => {{
                const date = new Date(parseInt(timestamp) / 1000000);
                const level = stream.level || 'info';
                const service = stream.service_name || 'unknown';

                logsHTML += `
                  <tr>
                    <td>${{date.toLocaleTimeString()}}</td>
                    <td class="level-${{level}}">${{level.toUpperCase()}}</td>
                    <td>${{service}}</td>
                    <td>${{message}}</td>
                  </tr>
                `;
              }});
            }});

            tbody.innerHTML = logsHTML || '<tr><td colspan="4">No log entries</td></tr>';
          }}

          // Initialize and start polling
          initCharts();
          fetchData();
          setInterval(fetchData, 3000); // Refresh every 3 seconds
        </script>
      </body>
    </html>
    """


@app.get("/api/checkout")
def checkout(item_id: str = Query(default="item-1"), qty: int = Query(default=1)):
    started = time.perf_counter()
    request_id = str(uuid4())

    with tracer.start_as_current_span("checkout_flow") as span:
        span.set_attribute("demo.request_id", request_id)
        span.set_attribute("demo.item_id", item_id)
        span.set_attribute("demo.qty", qty)

        logger.info(
            "checkout_started",
            extra={"request_id": request_id, "item_id": item_id, "qty": qty},
        )

        try:
            order_res = requests.post(
                f"{orders_url}/create",
                json={"item_id": item_id, "qty": qty, "request_id": request_id},
                timeout=3,
            )
            order_res.raise_for_status()

            inventory_res = requests.post(
                f"{inventory_url}/reserve",
                json={"item_id": item_id, "qty": qty, "request_id": request_id},
                timeout=3,
            )
            inventory_res.raise_for_status()

            latency_ms = (time.perf_counter() - started) * 1000
            checkout_counter.add(1, {"route": "/api/checkout", "status": "ok"})
            checkout_duration.record(latency_ms, {"route": "/api/checkout"})
            record_runtime_metric(latency_ms, is_error=False)

            trace_id = format(span.get_span_context().trace_id, "032x")
            result = {
                "status": "ok",
                "request_id": request_id,
                "trace_id": trace_id,
                "order": order_res.json(),
                "inventory": inventory_res.json(),
                "latency_ms": round(latency_ms, 2),
            }
            logger.info(
                "checkout_succeeded",
                extra={"request_id": request_id, "latency_ms": latency_ms},
            )
            return JSONResponse(result)

        except requests.exceptions.HTTPError as exc:
            checkout_counter.add(1, {"route": "/api/checkout", "status": "error"})
            checkout_failure_counter.add(1, {"route": "/api/checkout"})
            latency_ms = (time.perf_counter() - started) * 1000
            checkout_duration.record(latency_ms, {"route": "/api/checkout"})
            record_runtime_metric(latency_ms, is_error=True)
            trace_id = format(span.get_span_context().trace_id, "032x")

            response = exc.response
            status_code = response.status_code if response is not None else 502

            detail = str(exc)
            if response is not None:
                try:
                    payload = response.json()
                    detail = payload.get("detail") or detail
                except ValueError:
                    if response.text:
                        detail = response.text

            if status_code == 409:
                logger.warning(
                    "checkout_conflict",
                    extra={"request_id": request_id, "item_id": item_id, "qty": qty},
                )
            else:
                logger.exception(
                    "checkout_failed_http", extra={"request_id": request_id}
                )

            return JSONResponse(
                status_code=status_code,
                content={
                    "status": "error",
                    "request_id": request_id,
                    "trace_id": trace_id,
                    "detail": f"Checkout failed: {detail}",
                    "latency_ms": round(latency_ms, 2),
                },
            )

        except requests.exceptions.RequestException as exc:
            checkout_counter.add(1, {"route": "/api/checkout", "status": "error"})
            checkout_failure_counter.add(1, {"route": "/api/checkout"})
            latency_ms = (time.perf_counter() - started) * 1000
            checkout_duration.record(latency_ms, {"route": "/api/checkout"})
            record_runtime_metric(latency_ms, is_error=True)
            trace_id = format(span.get_span_context().trace_id, "032x")
            logger.exception("checkout_failed", extra={"request_id": request_id})
            return JSONResponse(
                status_code=502,
                content={
                    "status": "error",
                    "request_id": request_id,
                    "trace_id": trace_id,
                    "detail": f"Checkout failed: {str(exc)}",
                    "latency_ms": round(latency_ms, 2),
                },
            )

        except Exception as exc:
            checkout_counter.add(1, {"route": "/api/checkout", "status": "error"})
            checkout_failure_counter.add(1, {"route": "/api/checkout"})
            latency_ms = (time.perf_counter() - started) * 1000
            checkout_duration.record(latency_ms, {"route": "/api/checkout"})
            record_runtime_metric(latency_ms, is_error=True)
            trace_id = format(span.get_span_context().trace_id, "032x")
            logger.exception(
                "checkout_failed_unexpected", extra={"request_id": request_id}
            )
            return JSONResponse(
                status_code=500,
                content={
                    "status": "error",
                    "request_id": request_id,
                    "trace_id": trace_id,
                    "detail": f"Checkout failed: {str(exc)}",
                    "latency_ms": round(latency_ms, 2),
                },
            )


@app.get("/api/chaos")
def chaos() -> dict:
    delay = random.uniform(0.2, 1.5)
    time.sleep(delay)
    if random.random() < 0.35:
        logger.warning("chaos_triggered", extra={"delay": delay})
        raise HTTPException(status_code=503, detail="Random chaos failure")
    logger.info("chaos_ok", extra={"delay": delay})
    return {"status": "ok", "delay": delay}


@app.post("/api/runtime-metrics/reset")
def reset_runtime_metrics_api() -> dict:
    reset_runtime_metrics()
    return {"status": "ok", "message": "runtime metrics reset"}


@app.get("/api/observability-data/{trace_id}")
def get_observability_data(trace_id: str, request_id: str = Query(default=None)):
    """
    Aggregate observability data from all backends
    Returns metrics, traces, and logs for the observability page
    """
    with tracer.start_as_current_span("fetch_observability_data"):
        # Fetch data from all backends in parallel using threads
        results = {
            "trace_id": trace_id,
            "request_id": request_id,
            "timestamp": time.time(),
        }

        with ThreadPoolExecutor(max_workers=4) as executor:
            futures = {
                executor.submit(fetch_trace_data, trace_id): "trace",
                executor.submit(fetch_metrics_data): "metrics",
                executor.submit(fetch_logs_data, trace_id, request_id): "logs",
                executor.submit(fetch_runtime_metrics): "runtime_metrics",
            }

            for future in as_completed(futures):
                key = futures[future]
                try:
                    results[key] = future.result()
                except Exception as e:
                    logger.error(f"Error fetching {key}: {e}")
                    results[key] = {"error": str(e)}

        return JSONResponse(results)
