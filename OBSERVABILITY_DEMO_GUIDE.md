# 🔍 Observability Dashboard - Hướng Dẫn Sử Dụng

## Tổng Quan

Hệ thống microservice observability demo với tích hợp dashboard real-time hiển thị **Metrics, Traces, và Logs** sử dụng OpenTelemetry.

## 🚀 Cách Sử Dụng

### Bước 1: Khởi Động Hệ Thống

```bash
# Start all services
docker-compose up -d

# Wait for all services to be healthy (30-60 seconds)
docker-compose ps

# All services should show "Up" status
```

### Bước 2: Generate Initial Traffic

**Quan trọng**: Để observability dashboard hiển thị đầy đủ data, cần generate traffic trước:

```bash
# Generate 20-30 requests để populate metrics
for i in {1..30}; do curl -s http://localhost:8000/api/checkout > /dev/null; sleep 0.3; done
```

**Lý do**:

- Prometheus scrapes metrics mỗi 15-30 giây
- Metrics cần ít nhất 1-2 scrape cycles để hiển thị charts
- Traces/logs cần vài giây để được ingested

### Bước 3: Sử Dụng Dashboard

1. **Truy cập main interface**:

   ```
   http://localhost:8000
   ```

2. **Trigger Single Checkout**:
   - Click button "Gọi 1 request checkout"
   - **Dashboard tab mới tự động mở** với URL: `/observability/{trace_id}`

3. **Xem Observability Data**:
   - **Metrics Section**: 4 metric cards + 2 live charts
   - **Trace Flow Section**: Visual service call chain
   - **Logs Section**: Filtered log table

4. **Generate Load Traffic**:
   - Click "Bắn tải 3 req/s" để continuous traffic
   - Watch dashboard auto-update every 3 seconds
   - Charts populate với rolling window (20 data points)

## ⏱️ Data Availability Timeline

**Quan trọng - Hiểu về observability timing**:

| Data Type          | Availability  | Explanation                                |
| ------------------ | ------------- | ------------------------------------------ |
| **API Response**   | Immediate     | Checkout response với trace_id, request_id |
| **Dashboard Page** | Immediate     | HTML page loads ngay lập tức               |
| **Traces**         | 2-10 seconds  | Batch export → Collector → Tempo ingestion |
| **Logs**           | 5-15 seconds  | Batch export → Collector → Loki indexing   |
| **Metrics**        | 15-60 seconds | Scrape interval + aggregation              |

### Tại Sao "Error Loading Trace"?

Khi mở dashboard ngay sau checkout, có thể thấy:

```
Error loading trace: Trace not found
```

**Đây là expected behavior** vì:

1. **Trace Batching**: OpenTelemetry BatchSpanProcessor batches traces mỗi 5 seconds
2. **Collector Processing**: Collector nhận và forward traces
3. **Tempo Ingestion**: Tempo writes traces vào storage (1-2s)
4. **Total Latency**: 2-10 seconds từ request → trace available

### Solution:

**Option 1: Wait và Refresh**

- Dashboard auto-polls every 3 seconds
- Sau 10-15 seconds, trace section sẽ hiển thị data
- Không cần manual refresh

**Option 2: Generate Traffic First** ⭐ Recommended

```bash
# Generate 30 requests với delays
for i in {1..30}; do
  curl -s http://localhost:8000/api/checkout > /dev/null
  sleep 0.5
done

# Wait 30 seconds cho data propagation
sleep 30

# Bây giờ trigger checkout và open dashboard
# All three pillars sẽ có data ngay lập tức
```

**Option 3: Use Load Generator**

1. Click "Bắn tải 3 req/s"
2. Wait 1 minute
3. Stop load: "Dừng tải"
4. Trigger single checkout để open dashboard
5. Dashboard hiển thị full data với historical context

## 📊 Dashboard Components chi tiết

### 1. Metrics Overview

**4 Metric Cards**:

- **Total Requests**: Cumulative checkout count
- **Error Rate**: Percentage of failed requests
- **P95 Latency**: 95th percentile response time (ms)
- **Request Rate**: Requests per second

**2 Live Charts**:

- **Latency Chart**: P95 và P50 latency over time (line chart)
- **Rate Chart**: Request rate trend (line chart)

**Characteristics**:

- Auto-updates every 3 seconds
- Rolling window: 20 data points maximum
- Smooth animations với Chart.js
- Shows system-wide aggregate metrics (không phải single request)

### 2. Distributed Trace Flow

**Visual Representation**:

```
┌──────────┐      ┌──────────┐      ┌───────────┐
│ Gateway  │  →   │  Orders  │  →   │ Inventory │
│ 150ms    │      │  80ms    │      │  45ms     │
└──────────┘      └──────────┘      └───────────┘
```

**Information Shown**:

- Service names (Gateway, Orders, Inventory)
- Operation names (checkout_flow, create_order, reserve_inventory)
- Duration (milliseconds) cho mỗi span
- Call sequence (left to right by start time)

**States**:

- **Loading**: "Loading trace data..." - Trace chưa ready
- **Error**: "Error loading trace" - 404 từ Tempo (trace not found)
- **Success**: Service boxes với durations

### 3. Related Logs

**Table Columns**:

- **Timestamp**: HH:MM:SS format
- **Level**: INFO (blue), WARNING (orange), ERROR (red)
- **Service**: gateway, orders, inventory
- **Message**: Log content

**Filtering**:

- Logs filtered by request_id or trace_id
- Only shows logs từ last 5 minutes
- Maximum 100 log entries
- Auto-scrollable table

**States**:

- **Loading**: "Loading logs..." - Initial state
- **No Data**: "No logs found" - LogQL query không match
- **Success**: Populated table với color-coded levels

## 🔧 Troubleshooting

### Issue 1: "Error loading trace: Trace not found"

**Symptoms**: Trace section shows error message

**Root Causes**:

1. ✅ **Normal timing delay** (2-10 seconds) - Most common
2. ❌ Traces not exported từ services
3. ❌ Collector not forwarding to Tempo
4. ❌ Tempo ingestion issues

**Diagnostics**:

```bash
# Check collector receiving traces
docker-compose logs otel-collector | grep -i "span\|trace"

# Check Tempo status
curl http://localhost:3200/status

# Check service logs for OpenTelemetry errors
docker-compose logs gateway | grep -i "otel\|export"
```

**Solutions**:

- **Wait**: Auto-refresh sẽ load trace sau vài giây
- **Restart services**: `docker-compose restart gateway orders inventory`
- **Check collector**: Verify OTLP endpoints reachable
- **Generate more traffic**: Build up trace data first

### Issue 2: Metrics Cards Show "--" or 0

**Symptoms**: Metric cards không hiển thị numbers

**Root Causes**:

1. ✅ Prometheus chưa scrape metrics
2. ✅ Insufficient traffic data
3. ❌ Collector not exposing metrics
4. ❌ Prometheus scrape failing

**Diagnostics**:

```bash
# Check Prometheus targets
curl http://localhost:9090/api/v1/targets

# Check collector metrics endpoint
curl http://localhost:8889/metrics | grep demo_checkout

# Check Prometheus có data
curl "http://localhost:9090/api/v1/query?query=demo_checkout_requests_total"
```

**Solutions**:

- **Wait 30-60 seconds**: Prometheus scrape interval
- **Generate traffic**: Need requests để create metrics
- **Restart Prometheus**: `docker-compose restart prometheus`
- **Check collector**: Ensure metrics được exposed

### Issue 3: No Logs in Table

**Symptoms**: Logs section shows "No logs found"

**Root Causes**:

1. ✅ Logs chưa được indexed trong Loki
2. ✅ Request_id/trace_id filter không match
3. ❌ Loki not receiving logs từ collector
4. ❌ LogQL query syntax error

**Diagnostics**:

```bash
# Check Loki health
curl http://localhost:3100/ready

# Test Loki query
curl -G http://localhost:3100/loki/api/v1/query_range \
  --data-urlencode 'query={service_name=~"gateway|orders|inventory"}' \
  --data-urlencode 'limit=10'

# Check collector logs export
docker-compose logs otel-collector | grep -i "loki\|log"
```

**Solutions**:

- **Wait 10-15 seconds**: Loki indexing latency
- **Generate traffic**: Need logs để display
- **Restart Loki**: `docker-compose restart loki`
- **Remove filters**: Test với broader LogQL query

### Issue 4: Inventory "409 Conflict" Errors

**Symptoms**: Checkout returns `{"detail":"Checkout failed: 409 Client Error: Conflict..."}`

**Root Cause**: ✅ Stock exhausted (normal behavior)

**Solution**:

```bash
# Reset stock by restarting inventory service
docker-compose restart inventory

# Verify stock reset
# Initial stock: item-1: 100, item-2: 80, item-3: 120
```

### Issue 5: Dashboard không Auto-Open

**Symptoms**: Click checkout nhưng tab mới không mở

**Root Causes**:

1. ❌ Browser blocked popup
2. ❌ JavaScript error trong console
3. ❌ Checkout failed (500 error)

**Solutions**:

- **Check browser console**: F12 → Console tab
- **Allow popups**: Browser settings → Allow popups từ localhost:8000
- **Verify checkout succeeds**: Check response JSON có `trace_id` field

## 📈 Best Practices cho Demo

### 1. Pre-Population Strategy

Trước khi demo dashboard:

```bash
# Step 1: Generate baseline traffic (30-60 seconds)
for i in {1..50}; do
  curl -s http://localhost:8000/api/checkout > /dev/null
  sleep 0.5
done

# Step 2: Wait for data propagation
sleep 30

# Step 3: Start load generator
# Navigate to http://localhost:8000
# Click "Bắn tải 3 req/s"

# Step 4: Wait 1 minute cho charts populate

# Step 5: Trigger single checkout
# Dashboard opens với full data available
```

### 2. Demo Sequence

**Flow 1 - Quick Demo** (5 minutes):

1. Open web interface
2. Start load generator (3 req/s)
3. Wait 1 minute
4. Click single checkout
5. New tab opens → point out 3 sections
6. Highlight auto-refresh (watch numbers update)

**Flow 2 - Detailed Demo** (15 minutes):

1. Pre-populate traffic (50 requests)
2. Open web interface
3. Explain architecture (3 microservices + observability stack)
4. Trigger checkout → dashboard opens
5. Explain each section:
   - Metrics: System-wide performance
   - Traces: Request journey
   - Logs: Debugging context
6. Demonstrate live updates
7. Show chaos engineering (`/api/chaos`)
8. Toggle load generator on/off
9. Compare Grafana vs integrated dashboard

### 3. Narrative Points

**Opening**:

- "Traditional observability: Nhiều tools riêng biệt (Grafana, Jaeger, Kibana)"
- "Integrated dashboard: Single view cho developers"

**Metrics**:

- "System health at a glance"
- "P95 latency shows worst-case user experience"
- "Error rate alerts về service degradation"

**Traces**:

- "End-to-end request visibility"
- "Identify bottlenecks (which service slowest?)"
- "Debug failures (where did request fail?)"

**Logs**:

- "Contextual debugging information"
- "Filtered by trace/request ID"
- "Correlation với metrics và traces"

**Live Updates**:

- "Real-time monitoring without manual refresh"
- "3-second polling interval"
- "Rolling window charts (last 20 data points)"

## 🏗️ Architecture Overview

```
┌─────────────────────────────────────────────────────────┐
│                    Web Interface                         │
│                 http://localhost:8000                    │
│  ┌──────────────┐                                       │
│  │   Button      │ ─── Click ──→ /api/checkout          │
│  └──────────────┘         ├─→ trace_id: xyz123          │
│                            └─→ Opens: /observability/xyz │
└─────────────────────────────────────────────────────────┘
                                    │
                                    ▼
┌─────────────────────────────────────────────────────────┐
│              Observability Dashboard Page                │
│         /observability/{trace_id}?request_id=...         │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐  │            │ │  Metrics (Charts) │  │ Traces (Flow)  │  │  Logs (Table)  │  │
│  └──────────────┘  └──────────────┘  └──────────────┘  │
│         │                  │                  │          │
│         └────────── Auto-Refresh (3s) ───────┘          │
│                          │JavaScript Fetch                │
│                          ▼                                │
│              /api/observability-data/{trace_id}           │
│                          │                                │
│          ┌───────────────┼───────────────┐               │
│          │               │               │               │
│          ▼               ▼               ▼               │
│    Prometheus        Tempo           Loki                │
│    (Metrics)       (Traces)         (Logs)               │
└─────────────────────────────────────────────────────────┘
                          ▲
                          │ OTLP Export
┌─────────────────────────┴────────────────────────────────┐
│                OpenTelemetry Collector                    │
│          Receives: Traces, Metrics, Logs                  │
│          Routes: Tempo, Prometheus, Loki                  │
└───────────────────────────────────────────────────────────┘
                          ▲
                          │ OTLP HTTP (port 4318)
          ┌───────────────┼───────────────┐
          │               │               │
          ▼               ▼               ▼
    ┌─────────┐     ┌─────────┐    ┌──────────┐
    │ Gateway │ ──→ │ Orders  │ ──→│Inventory │
    │ :8000   │     │ :8000   │    │  :8000   │
    └─────────┘     └─────────┘    └──────────┘
```

## 🎯 Key Features

### Backend (API Layer)

- **`fetch_trace_data()`**: Queries Tempo API cho distributed traces
- **`fetch_metrics_data()`**: Queries Prometheus với PromQL cho metrics
- **`fetch_logs_data()`**: Queries Loki với LogQL cho filtered logs
- **`/api/observability-data/{trace_id}`**: Aggregates all three pillars parallel

**Performance**:

- Parallel fetching với ThreadPoolExecutor
- Total response time: < 1 second
- Graceful degradation nếu backend fails

### Frontend (Dashboard Page)

- **Chart.js 4.4.0**: Interactive, animated charts
- **Auto-refresh**: 3-second polling interval
- **Rolling window**: Max 20 data points per chart
- **Responsive design**: Works trên multiple screen sizes
- **Modern UI**: Purple gradient theme, card layout

**State Management**:

- Loading states: "Loading..." placeholders
- Error states: Red error messages
- Success states: Populated data với smooth transitions

### Integration

- **Auto-open tab**: window.open() sau checkout success
- **URL parameters**: trace_id và request_id trong URL
- **Correlation**: Links between metrics ↔ traces ↔ logs

## 🔄 Data Flow Details

### Trace Export Pipeline

```
1. Gateway creates span (checkout_flow)
   └─> OpenTelemetry SDK: tracer.start_span()

2. Orders/Inventory create child spans
   └─> Distributed context propagation

3. Spans batched (5 second window)
   └─> BatchSpanProcessor buffer

4. Export to Collector via OTLP/HTTP
   └─> POST http://otel-collector:4318/v1/traces

5. Collector receives và forwards
   └─> OTLP/gRPC to Tempo via port 4317

6. Tempo ingests và indexes (1-2 seconds)
   └─> Storage backend: /tmp/tempo/blocks

7. Dashboard queries Tempo
   └─> GET /api/traces/{trace_id}
```

**Total Latency**: Request → Trace Available = **2-10 seconds**

### Metrics Export Pipeline

```
1. Services record metrics (counters, histograms)
   └─> OpenTelemetry Metrics SDK

2. Periodic export (5 second interval)
   └─> PeriodicExportingMetricReader

3. OTLP export to Collector
   └─> POST http://otel-collector:4318/v1/metrics

4. Collector exposes Prometheus endpoint
   └─> GET http://otel-collector:8889/metrics

5. Prometheus scrapes (15-30s interval)
   └─> Scrape target: otel-collector:8889

6. Dashboard queries Prometheus
   └─> GET /api/v1/query với PromQL
```

**Total Latency**: Metric Created → Available in Prometheus = **15-60 seconds**

### Logs Export Pipeline

```
1. Services generate logs (logger.info/warning/error)
   └─> Python logging với OpenTelemetry handler

2. Logs batched (default window)
   └─> BatchLogRecordProcessor

3. OTLP export to Collector
   └─> POST http://otel-collector:4318/v1/logs

4. Collector forwards to Loki
   └─> POST /loki/api/v1/push

5. Loki indexes logs (5-10 seconds)
   └─> Storage: /loki/chunks

6. Dashboard queries Loki
   └─> GET /loki/api/v1/query_range với LogQL
```

**Total Latency**: Log Generated → Available in Loki = **5-15 seconds**

## 💡 Tips & Tricks

### Tip 1: Use Grafana for Deep Dive

Observability dashboard good cho quick view, nhưng Grafana has more features:

```
http://localhost:3000 (admin/admin)
```

- Custom dashboard creation
- Long-term trend analysis
- Alerting rules
- Complex queries
- Multiple data sources side-by-side

### Tip 2: Monitor Resource Usage

```bash
# Check Docker resource usage
docker stats

# Check service health periodically
watch -n 5 'docker-compose ps'
```

### Tip 3: Clean Up Between Demos

```bash
# Reset all data và restart fresh
docker-compose down -v
docker-compose up -d

# Wait 30s for startup
sleep 30

# Pre-populate với baseline traffic
for i in {1..50}; do curl -s localhost:8000/api/checkout > /dev/null; sleep 0.3; done
```

### Tip 4: Customize Dashboard

Edit `/observability/{trace_id}` route trong `services/gateway/app.py`:

- Change color scheme (search `#667eea`, `#764ba2`)
- Modify chart types (line → bar, pie, etc.)
- Add more metrics (inventory stock levels, custom business metrics)
- Change polling interval (line 548: `setInterval(fetchData, 3000)`)

### Tip 5: Debug với Browser DevTools

Open observability dashboard → F12:

- **Console**: Check JavaScript errors
- **Network**: Monitor `/api/observability-data/{trace_id}` responses
- **Elements**: Inspect HTML structure
- **Performance**: Analyze page load times

## 📚 Additional Resources

### OpenTelemetry Documentation

- [OpenTelemetry Python SDK](https://opentelemetry.io/docs/languages/python/)
- [OTLP Specification](https://opentelemetry.io/docs/specs/otlp/)
- [Instrumentation Guide](https://opentelemetry.io/docs/concepts/instrumentation/)

### Observability Backends

- [Prometheus Querying](https://prometheus.io/docs/prometheus/latest/querying/basics/)
- [Grafana Tempo Tracing](https://grafana.com/docs/tempo/latest/)
- [Grafana Loki LogQL](https://grafana.com/docs/loki/latest/query/)

### Chart.js

- [Chart.js Documentation](https://www.chartjs.org/docs/latest/)
- [Line Chart Guide](https://www.chartjs.org/docs/latest/charts/line.html)
- [Performance Tips](https://www.chartjs.org/docs/latest/general/performance.html)

---

**Tóm tắt sử dụng nhanh**:

1. `docker-compose up -d` - Start hệ thống
2. Generate 30-50 requests trước
3. Wait 60 seconds cho data propagation
4. Click "Gọi 1 request checkout"
5. Dashboard tab mới mở với full data
6. Enjoy real-time observability! 🎉
