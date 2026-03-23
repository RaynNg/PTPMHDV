# Microservice Observability Demo (OpenTelemetry)

Demo này tạo một hệ thống microservice đơn giản để quan sát:
- **Metrics** (Prometheus + Grafana)
- **Logs** (Loki + Grafana)
- **Tracing** (Tempo + Grafana)
- Dùng **OpenTelemetry** để instrument từ ứng dụng và chuyển qua **OpenTelemetry Collector**.

## Kiến trúc

- `gateway` (UI + API): gọi `orders` và `inventory`
- `orders`: tạo đơn hàng giả lập, có random failure
- `inventory`: trừ tồn kho giả lập, có random failure
- `otel-collector`: nhận telemetry OTLP từ services
- `prometheus`: scrape metrics từ collector
- `loki`: lưu logs
- `tempo`: lưu traces
- `grafana`: giao diện quan sát tập trung + dashboard mẫu

## Chạy nhanh

Yêu cầu: Docker Desktop đang chạy.

```bash
docker compose up --build -d
```

Mở các URL:
- App demo: http://localhost:8000
- Grafana: http://localhost:3000 (user/pass: `admin` / `admin`)
- Prometheus: http://localhost:9090

## Cách tạo traffic

1. Mở http://localhost:8000
2. Bấm `Bắn tải 3 req/s` để tạo traffic liên tục
3. Sau 10-30 giây, mở Grafana để quan sát

## Xem metrics, logs, tracing trên Grafana

### 1) Metrics (graph)
- Vào **Dashboards** → **Demo** → `Microservice Observability Overview`
- Có sẵn biểu đồ:
  - Throughput checkout
  - p95 latency checkout
  - Orders created rate
  - Inventory reserve failures

### 2) Logs
- Vào **Explore**
- Chọn datasource: **Loki**
- Query nhanh:
  ```
  {}
  ```
  hoặc lọc theo service:
  ```
  {service_name="gateway"}
  ```

### 3) Traces
- Vào **Explore**
- Chọn datasource: **Tempo**
- Chạy query TraceQL cơ bản:
  ```
  { name = "checkout_flow" }
  ```
- Mở trace để thấy span đi qua `gateway -> orders -> inventory`

## Dừng hệ thống

```bash
docker compose down
```

## Dọn dữ liệu và stop

```bash
docker compose down -v
```
