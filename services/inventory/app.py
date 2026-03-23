import logging
import os
import random
import time
from threading import Lock

from fastapi import FastAPI, HTTPException
from opentelemetry import metrics, trace
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
from pydantic import BaseModel

from otel_setup import configure_otel


service_name = os.getenv("SERVICE_NAME", "inventory")
configure_otel(service_name)

app = FastAPI(title="Inventory Service")
FastAPIInstrumentor.instrument_app(app)

logger = logging.getLogger(service_name)
tracer = trace.get_tracer(service_name)
meter = metrics.get_meter(service_name)

reserve_counter = meter.create_counter("demo_inventory_reserve", description="Inventory reserve requests")
reserve_fail_counter = meter.create_counter(
    "demo_inventory_reserve_fail", description="Inventory reserve failed requests"
)
stock_gauge = meter.create_up_down_counter("demo_inventory_stock", description="Current stock deltas")

lock = Lock()
stock = {
    "item-1": 100,
    "item-2": 80,
    "item-3": 120,
}


class ReserveRequest(BaseModel):
    item_id: str
    qty: int
    request_id: str


@app.get("/health")
def health() -> dict:
    return {"status": "ok", "service": service_name, "stock": stock}


@app.post("/reserve")
def reserve(payload: ReserveRequest) -> dict:
    with tracer.start_as_current_span("reserve_inventory") as span:
        span.set_attribute("demo.request_id", payload.request_id)
        span.set_attribute("demo.item_id", payload.item_id)
        span.set_attribute("demo.qty", payload.qty)

        delay_s = random.uniform(0.02, 0.15)
        time.sleep(delay_s)

        with lock:
            available = stock.get(payload.item_id, 0)
            reserve_counter.add(1, {"item_id": payload.item_id})

            if available < payload.qty:
                reserve_fail_counter.add(1, {"item_id": payload.item_id, "reason": "not_enough_stock"})
                logger.warning(
                    "inventory_not_enough",
                    extra={
                        "request_id": payload.request_id,
                        "item_id": payload.item_id,
                        "requested": payload.qty,
                        "available": available,
                    },
                )
                raise HTTPException(status_code=409, detail="Not enough stock")

            if random.random() < 0.08:
                reserve_fail_counter.add(1, {"item_id": payload.item_id, "reason": "random_failure"})
                logger.warning("inventory_random_failure", extra={"request_id": payload.request_id})
                raise HTTPException(status_code=500, detail="Inventory service random failure")

            stock[payload.item_id] = available - payload.qty
            stock_gauge.add(-payload.qty, {"item_id": payload.item_id})
            remaining = stock[payload.item_id]

        logger.info(
            "inventory_reserved",
            extra={
                "request_id": payload.request_id,
                "item_id": payload.item_id,
                "qty": payload.qty,
                "remaining": remaining,
                "delay_ms": int(delay_s * 1000),
            },
        )

        return {
            "reserved": True,
            "item_id": payload.item_id,
            "qty": payload.qty,
            "remaining": remaining,
            "processing_delay_ms": int(delay_s * 1000),
        }
