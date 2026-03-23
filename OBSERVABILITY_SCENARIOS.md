# Observability Scenarios (Demo Runbook)

Tài liệu này mô tả 5 kịch bản demo trong trang `http://localhost:8000/`, kèm ví dụ cách xác định lỗi nằm ở đâu bằng Metrics + Traces + Logs.

---

## Cách đọc nhanh để tìm lỗi

1. **Metrics**: nhìn dấu hiệu tổng quan (error rate, request rate, p95 latency).
2. **Trace**: xác định service/span nào chậm hoặc fail trước.
3. **Logs**: chốt nguyên nhân cụ thể theo `request_id`/`trace_id`.

> Quy tắc: Metrics báo "có vấn đề", Trace chỉ ra "ở đâu", Logs giải thích "vì sao".

---

## Kịch bản 1: Checkout thành công

### Mục tiêu

Xác nhận luồng chuẩn hoạt động ổn định (`gateway -> orders -> inventory`).

### Cách chạy

- Bấm nút **Kịch bản 1: Checkout thành công**.

### Kỳ vọng observability

- **Metrics**: total request tăng, error rate thấp/0.
- **Trace**: đủ span của cả 3 service, không có span lỗi.
- **Logs**: có `checkout_started`, `inventory_reserved`, `checkout_succeeded`.

### Ví dụ nhận ra lỗi ở đâu

- Nếu thỉnh thoảng fail dù là kịch bản success:
  - Trace cho thấy fail ở span gọi `orders/create`.
  - Logs `gateway` có `checkout_failed_http` với upstream orders.
  - Kết luận: lỗi nằm phía `orders` hoặc network giữa gateway-orders.

---

## Kịch bản 2: Lỗi hết hàng (409)

### Mục tiêu

Mô phỏng lỗi nghiệp vụ có kiểm soát (không đủ tồn kho).

### Cách chạy

- Bấm nút **Kịch bản 2: Lỗi hết hàng (409)** (gửi `qty` rất lớn).

### Kỳ vọng observability

- **Metrics**: error tăng.
- **Trace**: fail tại nhánh inventory reserve.
- **Logs**: `inventory_not_enough` + `checkout_conflict`.

### Ví dụ nhận ra lỗi ở đâu

- HTTP trả `409` với `Checkout failed: Not enough stock`.
- Trace cho thấy gateway gọi orders xong, fail ở inventory.
- Logs inventory có `requested > available`.
- Kết luận: đây là lỗi dữ liệu nghiệp vụ ở inventory (không phải lỗi hệ thống).

---

## Kịch bản 3: Lỗi ngẫu nhiên (500)

### Mục tiêu

Mô phỏng lỗi không ổn định để demo điều tra sự cố production-like.

### Cách chạy

- Bấm nút **Kịch bản 3: Lỗi ngẫu nhiên (500)** (thử nhiều lần cho đến khi bắt lỗi).

### Kỳ vọng observability

- **Metrics**: error rate tăng theo từng đợt.
- **Trace**: một số trace fail, một số trace pass.
- **Logs**: thấy các log failure như `inventory_random_failure` hoặc `checkout_failed_http`.

### Ví dụ nhận ra lỗi ở đâu

- Nếu chỉ một phần request lỗi:
  - Metrics cho thấy lỗi theo spike.
  - Trace của request lỗi dừng ở inventory.
  - Logs inventory có `random_failure`.
  - Kết luận: lỗi ngẫu nhiên nằm ở inventory, không phải toàn hệ thống gateway.

---

## Kịch bản 4: Tải hỗn hợp 20 giây

### Mục tiêu

Tạo traffic hỗn hợp success + conflict để thấy hệ thống dưới tải ngắn.

### Cách chạy

- Bấm nút **Kịch bản 4: Tải hỗn hợp 20 giây**.

### Kỳ vọng observability

- **Metrics**: request rate tăng rõ, error rate có dao động.
- **Trace**: nhiều trace liên tiếp, có trace thành công và trace lỗi.
- **Logs**: tăng volume log tương ứng.

### Ví dụ nhận ra lỗi ở đâu

- Nếu request rate cao nhưng p95 tăng mạnh:
  - Trace cho thấy span inventory dài bất thường.
  - Logs inventory có delay cao hơn bình thường.
  - Kết luận: bottleneck latency ở inventory khi có burst traffic.

---

## Kịch bản 5: Latency stress 60 giây

### Mục tiêu

Tạo tải ổn định đủ lâu để đọc **P50/P95** rõ ràng.

### Cách chạy

- Bấm nút **Kịch bản 5: Latency stress 60 giây**.

### Kỳ vọng observability

- **Metrics**: p50/p95 không còn bằng 0 (đủ mẫu trong cửa sổ thời gian).
- **Trace**: dễ thấy span chậm lặp lại theo mô hình tải.
- **Logs**: có lưu lượng ổn định, thuận tiện đối chiếu theo thời gian.
- **Output kịch bản**: có `p50_latency_ms`, `p95_latency_ms` local summary.

### Ví dụ nhận ra lỗi ở đâu

- Nếu p95 tăng mạnh còn p50 vẫn thấp:
  - Metrics cho thấy tail latency (đuôi chậm) tăng.
  - Trace các request chậm tập trung ở một span downstream.
  - Logs downstream có delay hoặc retry.
  - Kết luận: không phải mọi request đều chậm; có vấn đề ở nhóm request đuôi (tail).

---

## Checklist điều tra lỗi nhanh (thực chiến)

- Bước 1: Ghi lại `request_id`, `trace_id` từ response.
- Bước 2: Mở dashboard trace tương ứng để xác định span lỗi/chậm.
- Bước 3: Tìm logs theo `request_id` để chốt root cause.
- Bước 4: Đối chiếu metrics (error rate, p95, request rate) để đánh giá mức ảnh hưởng.

---

## Ghi chú khi số liệu chưa lên ngay

- Prometheus query dùng cửa sổ thời gian (`rate(...[1m])`), cần đủ mẫu mới hiện rõ.
- Sau khi vừa restart stack, số liệu có thể trễ vài chu kỳ scrape.
- Với demo latency, ưu tiên chạy kịch bản 5 để có dữ liệu ổn định hơn.
