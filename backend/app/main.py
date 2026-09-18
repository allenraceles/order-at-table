"""Mesa & Co. menu and ordering API backed by Supabase Postgres."""

from __future__ import annotations

import os
import secrets
import uuid
from pathlib import Path
from typing import Literal
from urllib.parse import urlsplit

import psycopg
from dotenv import load_dotenv
from fastapi import Depends, FastAPI, Header, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from psycopg.rows import dict_row
from pydantic import BaseModel, Field

load_dotenv(Path(__file__).resolve().parents[2] / ".env")
RESTAUPRO_PROJECT_REF = "ftdzbagcesvujvrbaqdl"


class OrderLineInput(BaseModel):
    item_id: str
    option: str | None = None
    quantity: int = Field(ge=1, le=10)


class OrderInput(BaseModel):
    table_number: int = Field(ge=1)
    table_service_id: uuid.UUID
    payment_method: Literal["counter"]
    items: list[OrderLineInput] = Field(min_length=1, max_length=20)


class OrderStatusInput(BaseModel):
    status: Literal["preparing", "ready", "complete"]


def require_staff(x_staff_key: str | None = Header(default=None)) -> None:
    configured = os.environ.get("ORDER_STAFF_KEY", "")
    if not configured or configured == "REPLACE_WITH_A_LONG_RANDOM_SECRET":
        raise HTTPException(status_code=503, detail="Staff access is not configured")
    if not x_staff_key or not secrets.compare_digest(x_staff_key, configured):
        raise HTTPException(status_code=401, detail="Invalid staff access key")


def database():
    url = os.environ.get("SUPABASE_DB_URL")
    if not url:
        raise HTTPException(status_code=503, detail="Supabase database is not configured")
    parsed = urlsplit(url)
    correct_project = (parsed.username or "").endswith(f".{RESTAUPRO_PROJECT_REF}") or (
        parsed.hostname or ""
    ).startswith(f"db.{RESTAUPRO_PROJECT_REF}.")
    if not correct_project:
        raise HTTPException(status_code=503, detail="Database URL is not for the RestauPro project")
    try:
        return psycopg.connect(url, sslmode="require", prepare_threshold=None, row_factory=dict_row)
    except psycopg.OperationalError:
        raise HTTPException(status_code=503, detail="Supabase database is unavailable") from None


def read_order(connection: psycopg.Connection, number: str) -> dict | None:
    order = connection.execute(
        """SELECT number, table_number, table_service_id, payment_method, status, total, created_at,
                  payment_confirmed_at, preparing_at, ready_at, completed_at
           FROM order_at_table.orders WHERE number = %s""",
        (number,),
    ).fetchone()
    if order is None:
        return None
    order["table_service_id"] = str(order["table_service_id"]) if order["table_service_id"] else None
    order["items"] = connection.execute(
        """SELECT item_id, name, option, quantity, unit_price
           FROM order_at_table.order_lines WHERE order_id = (
             SELECT id FROM order_at_table.orders WHERE number = %s
           ) ORDER BY id""",
        (number,),
    ).fetchall()
    serialize_timestamps(order)
    return order


def serialize_timestamps(order: dict) -> None:
    for field in ("created_at", "payment_confirmed_at", "preparing_at", "ready_at", "completed_at"):
        if order.get(field) is not None:
            order[field] = order[field].isoformat()


def list_orders(connection: psycopg.Connection) -> list[dict]:
    orders = connection.execute(
        """SELECT id, number, table_number, table_service_id, payment_method, status, total, created_at,
                  payment_confirmed_at, preparing_at, ready_at, completed_at
           FROM order_at_table.orders ORDER BY id DESC LIMIT 200"""
    ).fetchall()
    if not orders:
        return []
    ids = [order["id"] for order in orders]
    lines = connection.execute(
        """SELECT order_id, item_id, name, option, quantity, unit_price
           FROM order_at_table.order_lines WHERE order_id = ANY(%s) ORDER BY id""",
        (ids,),
    ).fetchall()
    by_order = {order_id: [] for order_id in ids}
    for line in lines:
        by_order[line.pop("order_id")].append(line)
    for order in orders:
        order["table_service_id"] = str(order["table_service_id"]) if order["table_service_id"] else None
        order["items"] = by_order[order.pop("id")]
        serialize_timestamps(order)
    return orders


app = FastAPI(title="Mesa & Co. Order API", version="1.0.0")
allowed_origins = [f"http://{host}:{port}" for host in ("localhost", "127.0.0.1") for port in (5173, 5174, 5175)]
allowed_origins.extend(origin.strip() for origin in os.environ.get("ORDER_ALLOWED_ORIGINS", "").split(",") if origin.strip())
app.add_middleware(
    CORSMiddleware,
    allow_origins=allowed_origins,
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
)


@app.get("/api/health")
def health():
    with database() as connection:
        connection.execute("SELECT 1")
    return {"ok": True}


@app.get("/api/menu")
def get_menu():
    with database() as connection:
        categories = connection.execute(
            """SELECT id, label FROM public.order_at_table_menu_categories
               WHERE is_active = true ORDER BY display_order, id"""
        ).fetchall()
        items = connection.execute(
            """SELECT id, category_id, name, description, price, image_url, tag, options
               FROM public.order_at_table_menu_items WHERE is_available = true
               ORDER BY display_order, id"""
        ).fetchall()
    sections = [{"category": category["label"], "items": []} for category in categories]
    by_category = {category["id"]: section for category, section in zip(categories, sections)}
    for item in items:
        section = by_category.get(item["category_id"])
        if section is not None:
            section["items"].append({
                "id": item["id"], "name": item["name"], "desc": item["description"],
                "price": item["price"], "image": item["image_url"],
                "tag": item["tag"], "options": item["options"],
            })
    return {"categories": ["All", *(category["label"] for category in categories)], "menu": sections}


def serialize_table(table: dict) -> dict:
    return {
        "number": table["number"],
        "seats": table["seats"],
        "current_service_id": str(table["current_service_id"]) if table["current_service_id"] else None,
        "service_started_at": table["service_started_at"].isoformat() if table["service_started_at"] else None,
    }


@app.get("/api/tables/{number}/service")
def get_table_service(number: int):
    with database() as connection:
        table = connection.execute(
            "SELECT number, seats, current_service_id, service_started_at FROM order_at_table.dining_tables WHERE number = %s",
            (number,),
        ).fetchone()
    if table is None:
        raise HTTPException(status_code=404, detail="Unknown table number")
    return serialize_table(table)


@app.post("/api/orders", status_code=201)
def create_order(payload: OrderInput):
    with database() as connection:
        table = connection.execute(
            "SELECT current_service_id FROM order_at_table.dining_tables WHERE number = %s FOR UPDATE",
            (payload.table_number,),
        ).fetchone()
        if table is None:
            raise HTTPException(status_code=404, detail="Unknown table number")
        if table["current_service_id"] != payload.table_service_id:
            raise HTTPException(status_code=409, detail="This table service has ended. Ask staff to start a new service.")
        lines = []
        for line in payload.items:
            item = connection.execute(
                """SELECT id, name, price, options FROM public.order_at_table_menu_items
                   WHERE id = %s AND is_available = true""",
                (line.item_id,),
            ).fetchone()
            if item is None:
                raise HTTPException(status_code=422, detail=f"Unknown menu item: {line.item_id}")
            if line.option is not None and line.option not in item["options"]:
                raise HTTPException(status_code=422, detail=f"Invalid option for {item['name']}")
            extra = int(line.option.split("+₱", 1)[1]) if line.option and "+₱" in line.option else 0
            lines.append({
                "item_id": item["id"], "name": item["name"], "option": line.option,
                "quantity": line.quantity, "unit_price": item["price"] + extra,
            })
        total = sum(line["quantity"] * line["unit_price"] for line in lines)
        for _ in range(10):
            number = str(secrets.randbelow(9000) + 1000)
            inserted = connection.execute(
                """INSERT INTO order_at_table.orders
                   (number, table_number, table_service_id, payment_method, status, total)
                   VALUES (%s, %s, %s, %s, 'awaiting_payment', %s)
                   ON CONFLICT (number) DO NOTHING RETURNING id""",
                (number, payload.table_number, payload.table_service_id, payload.payment_method, total),
            ).fetchone()
            if inserted is not None:
                break
        else:
            raise HTTPException(status_code=503, detail="Could not assign an order number. Please retry.")
        for line in lines:
            connection.execute(
                """INSERT INTO order_at_table.order_lines
                   (order_id, item_id, name, option, quantity, unit_price)
                   VALUES (%s, %s, %s, %s, %s, %s)""",
                (inserted["id"], line["item_id"], line["name"], line["option"],
                 line["quantity"], line["unit_price"]),
            )
        return read_order(connection, number)


@app.get("/api/orders/{number}")
def get_order(number: str):
    with database() as connection:
        order = read_order(connection, number)
    if order is None:
        raise HTTPException(status_code=404, detail="Order not found")
    return order


@app.get("/api/staff/orders", dependencies=[Depends(require_staff)])
def get_staff_orders():
    with database() as connection:
        return {"orders": list_orders(connection)}


@app.get("/api/staff/tables", dependencies=[Depends(require_staff)])
def get_staff_tables():
    with database() as connection:
        rows = connection.execute(
            "SELECT number, seats, current_service_id, service_started_at FROM order_at_table.dining_tables ORDER BY number"
        ).fetchall()
    return {"tables": [serialize_table(row) for row in rows]}


@app.post("/api/staff/tables/{number}/start-service", dependencies=[Depends(require_staff)])
def start_table_service(number: int):
    with database() as connection:
        table = connection.execute(
            """UPDATE order_at_table.dining_tables
               SET current_service_id = %s, service_started_at = now()
               WHERE number = %s AND current_service_id IS NULL
               RETURNING number, seats, current_service_id, service_started_at""",
            (uuid.uuid4(), number),
        ).fetchone()
        if table is None:
            existing = connection.execute("SELECT number FROM order_at_table.dining_tables WHERE number = %s", (number,)).fetchone()
            raise HTTPException(status_code=404 if existing is None else 409,
                                detail="Unknown table number" if existing is None else "Table is already in service")
    return serialize_table(table)


@app.post("/api/staff/tables/{number}/end-service", dependencies=[Depends(require_staff)])
def end_table_service(number: int):
    with database() as connection:
        table = connection.execute(
            "SELECT number, seats, current_service_id, service_started_at FROM order_at_table.dining_tables WHERE number = %s FOR UPDATE",
            (number,),
        ).fetchone()
        if table is None:
            raise HTTPException(status_code=404, detail="Unknown table number")
        if table["current_service_id"] is None:
            raise HTTPException(status_code=409, detail="Table is not in service")
        pending = connection.execute(
            """SELECT 1 FROM order_at_table.orders
               WHERE table_service_id = %s AND status NOT IN ('complete', 'cancelled') LIMIT 1""",
            (table["current_service_id"],),
        ).fetchone()
        if pending:
            raise HTTPException(status_code=409, detail="Finish all orders before ending table service")
        updated = connection.execute(
            """UPDATE order_at_table.dining_tables SET current_service_id = NULL, service_started_at = NULL
               WHERE number = %s RETURNING number, seats, current_service_id, service_started_at""",
            (number,),
        ).fetchone()
    return serialize_table(updated)


@app.post("/api/staff/orders/{number}/confirm-payment", dependencies=[Depends(require_staff)])
def confirm_counter_payment(number: str):
    with database() as connection:
        updated = connection.execute(
            """UPDATE order_at_table.orders
               SET status = 'new', payment_confirmed_at = now()
               WHERE number = %s AND status = 'awaiting_payment' RETURNING number""",
            (number,),
        ).fetchone()
        if updated is None:
            existing = read_order(connection, number)
            raise HTTPException(status_code=404 if existing is None else 409,
                                detail="Order not found" if existing is None else "Order is no longer awaiting payment")
        return read_order(connection, number)


@app.post("/api/staff/orders/{number}/status", dependencies=[Depends(require_staff)])
def update_order_status(number: str, payload: OrderStatusInput):
    transitions = {
        "preparing": ("new", "preparing_at"),
        "ready": ("preparing", "ready_at"),
        "complete": ("ready", "completed_at"),
    }
    current_status, timestamp_field = transitions[payload.status]
    with database() as connection:
        updated = connection.execute(
            f"""UPDATE order_at_table.orders
                SET status = %s, {timestamp_field} = now()
                WHERE number = %s AND status = %s RETURNING number""",
            (payload.status, number, current_status),
        ).fetchone()
        if updated is None:
            existing = read_order(connection, number)
            raise HTTPException(status_code=404 if existing is None else 409,
                                detail="Order not found" if existing is None else "Order status has changed; refresh the queue")
        return read_order(connection, number)
