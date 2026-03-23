import logging
import os
import random
import time
from uuid import uuid4

from fastapi import FastAPI, HTTPException
from opentelemetry import metrics, trace
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
from pydantic import BaseModel

from otel_setup import configure_otel


service_name = os.getenv("SERVICE_NAME", "orders")
configure_otel(service_name)

app = FastAPI(title="Orders Service")
FastAPIInstrumentor.instrument_app(app)

logger = logging.getLogger(service_name)
tracer = trace.get_tracer(service_name)
meter = metrics.get_meter(service_name)

order_counter = meter.create_counter("demo_orders_created", description="Total orders created")
order_fail_counter = meter.create_counter("demo_orders_failed", description="Total failed orders")


class CreateOrderRequest(BaseModel):
    item_id: str
    qty: int
    request_id: str


@app.get("/health")
def health() -> dict:
    return {"status": "ok", "service": service_name}


@app.post("/create")
def create_order(payload: CreateOrderRequest) -> dict:
    with tracer.start_as_current_span("create_order") as span:
        span.set_attribute("demo.request_id", payload.request_id)
        span.set_attribute("demo.item_id", payload.item_id)
        span.set_attribute("demo.qty", payload.qty)

        delay_s = random.uniform(0.03, 0.25)
        time.sleep(delay_s)

        if random.random() < 0.1:
            order_fail_counter.add(1, {"item_id": payload.item_id})
            logger.warning("order_creation_failed", extra={"request_id": payload.request_id})
            raise HTTPException(status_code=500, detail="Order service random failure")

        order_id = f"ord-{uuid4().hex[:8]}"
        order_counter.add(1, {"item_id": payload.item_id})

        logger.info(
            "order_created",
            extra={
                "request_id": payload.request_id,
                "order_id": order_id,
                "item_id": payload.item_id,
                "qty": payload.qty,
                "delay_ms": int(delay_s * 1000),
            },
        )

        return {
            "order_id": order_id,
            "item_id": payload.item_id,
            "qty": payload.qty,
            "processing_delay_ms": int(delay_s * 1000),
        }
