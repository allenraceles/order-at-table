"""Mesa & Co. menu and ordering API backed by Supabase Postgres."""

from __future__ import annotations

import os
import base64
import hashlib
import hmac
import json
import re
import secrets
import time
import uuid
from pathlib import Path
from typing import Literal
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urlsplit
from urllib.request import Request as UrlRequest, urlopen

import psycopg
from dotenv import load_dotenv
from fastapi import Depends, FastAPI, Header, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb
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
    payment_method: Literal["counter", "paymongo"]
    items: list[OrderLineInput] = Field(min_length=1, max_length=20)


class CheckoutInput(BaseModel):
    table_service_id: uuid.UUID


class OrderStatusInput(BaseModel):
    status: Literal["preparing", "ready", "complete"]


class RestaurantSettingsInput(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    location: str = Field(min_length=1, max_length=120)
    address: str = Field(max_length=240)
    phone: str = Field(max_length=50)
    hours: str = Field(min_length=1, max_length=100)
    header: str = Field(min_length=1, max_length=160)
    subheader: str = Field(min_length=1, max_length=500)
    brand_mark: str = Field(min_length=1, max_length=3)
    accent_color: str = Field(pattern=r"^#[0-9A-Fa-f]{6}$")


class CategoryInput(BaseModel):
    label: str = Field(min_length=1, max_length=80)
    display_order: int = Field(ge=0, le=10000)
    is_active: bool = True


class MenuItemInput(BaseModel):
    category_id: str
    name: str = Field(min_length=1, max_length=120)
    description: str = Field(max_length=500)
    price: int = Field(ge=0, le=1000000)
    image_url: str = Field(max_length=1000)
    tag: str | None = Field(default=None, max_length=60)
    options: list[str] = Field(default_factory=list, max_length=20)
    display_order: int = Field(ge=0, le=10000)
    is_available: bool = True


class TableInput(BaseModel):
    number: int = Field(ge=1, le=9999)
    seats: int = Field(ge=1, le=100)


class TableUpdateInput(BaseModel):
    seats: int = Field(ge=1, le=100)
    is_active: bool


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
    allow_methods=["GET", "POST", "PUT"],
    allow_headers=["*"],
)


def paymongo_secret() -> str:
    key = os.environ.get("PAYMONGO_SECRET_KEY", "")
    if not key.startswith(("sk_test_", "sk_live_")) or key.endswith("REPLACE_ME"):
        raise HTTPException(status_code=503, detail="PayMongo is not configured")
    return key


def create_paymongo_session(number: str, table_number: int, lines: list[dict]) -> tuple[str, str]:
    customer_url = os.environ.get("ORDER_CUSTOMER_URL", "http://localhost:5173").rstrip("/")
    parsed = urlsplit(customer_url)
    if parsed.scheme not in ("http", "https") or not parsed.netloc or parsed.path:
        raise HTTPException(status_code=503, detail="Customer return URL is not configured")
    return_url = f"{customer_url}/?{urlencode({'table': table_number, 'payment': 'return'})}"
    attributes = {
        "line_items": [{"name": line["name"] + (f" ({line['option']})" if line["option"] else ""),
                        "amount": line["unit_price"] * 100, "currency": "PHP", "quantity": line["quantity"]}
                       for line in lines],
        "payment_method_types": ["gcash", "qrph"],
        "success_url": return_url,
        "cancel_url": return_url,
        "reference_number": number,
        "description": f"Mesa & Co. order #{number} · table {table_number}",
    }
    authorization = base64.b64encode(f"{paymongo_secret()}:".encode()).decode()
    request = UrlRequest(
        "https://api.paymongo.com/v2/checkout_sessions",
        data=json.dumps({"data": {"attributes": attributes}}).encode(),
        headers={"Authorization": f"Basic {authorization}", "Content-Type": "application/json",
                 "Idempotency-Key": f"mesa-order-{number}"},
        method="POST",
    )
    try:
        with urlopen(request, timeout=15) as response:
            session = json.load(response)["data"]
        checkout_id = session["id"]
        checkout_url = session["attributes"]["checkout_url"]
        if not checkout_id.startswith("cs_") or urlsplit(checkout_url).hostname != "checkout.paymongo.com":
            raise ValueError("Unexpected PayMongo checkout response")
        return checkout_id, checkout_url
    except (HTTPError, URLError, KeyError, ValueError, TimeoutError):
        raise HTTPException(status_code=502, detail="Could not start PayMongo checkout. Please try again.") from None


def verify_paymongo_signature(body: bytes, header: str, secret: str, live: bool) -> bool:
    parts = dict(part.strip().split("=", 1) for part in header.split(",") if "=" in part)
    timestamp = parts.get("t", "")
    signature = parts.get("li" if live else "te", "")
    if not timestamp.isdigit() or abs(time.time() - int(timestamp)) > 300 or not signature:
        return False
    digest = hmac.new(secret.encode(), timestamp.encode() + b"." + body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(digest, signature)


@app.get("/api/health")
def health():
    with database() as connection:
        connection.execute("SELECT 1")
    return {"ok": True}


@app.get("/api/payments/config")
def payment_config():
    key = os.environ.get("PAYMONGO_SECRET_KEY", "")
    webhook = os.environ.get("PAYMONGO_WEBHOOK_SECRET", "")
    return {"paymongo_enabled": key.startswith(("sk_test_", "sk_live_")) and
            not key.endswith("REPLACE_ME") and bool(webhook) and not webhook.endswith("REPLACE_ME")}


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


def read_restaurant(connection: psycopg.Connection) -> dict:
    return connection.execute(
        """SELECT name, location, address, phone, hours, header, subheader, brand_mark, accent_color
           FROM order_at_table.restaurant_settings WHERE id = 1"""
    ).fetchone()


@app.get("/api/restaurant")
def get_restaurant():
    with database() as connection:
        return read_restaurant(connection)


@app.get("/api/staff/admin/restaurant", dependencies=[Depends(require_staff)])
def get_admin_restaurant():
    return get_restaurant()


@app.put("/api/staff/admin/restaurant", dependencies=[Depends(require_staff)])
def update_restaurant(payload: RestaurantSettingsInput):
    with database() as connection:
        return connection.execute(
            """UPDATE order_at_table.restaurant_settings SET
               name=%s, location=%s, address=%s, phone=%s, hours=%s, header=%s,
               subheader=%s, brand_mark=%s, accent_color=%s WHERE id=1
               RETURNING name, location, address, phone, hours, header, subheader, brand_mark, accent_color""",
            (payload.name.strip(), payload.location.strip(), payload.address.strip(), payload.phone.strip(),
             payload.hours.strip(), payload.header.strip(), payload.subheader.strip(), payload.brand_mark.strip(),
             payload.accent_color),
        ).fetchone()


@app.get("/api/staff/admin/menu", dependencies=[Depends(require_staff)])
def get_admin_menu():
    with database() as connection:
        categories = connection.execute(
            """SELECT id, label, display_order, is_active FROM public.order_at_table_menu_categories
               ORDER BY display_order, id"""
        ).fetchall()
        items = connection.execute(
            """SELECT id, category_id, name, description, price, image_url, tag, options,
                      display_order, is_available FROM public.order_at_table_menu_items
               ORDER BY display_order, id"""
        ).fetchall()
    return {"categories": categories, "items": items}


@app.post("/api/staff/admin/categories", dependencies=[Depends(require_staff)], status_code=201)
def create_category(payload: CategoryInput):
    with database() as connection:
        return connection.execute(
            """INSERT INTO public.order_at_table_menu_categories (id, label, display_order, is_active)
               VALUES (%s, %s, %s, %s) RETURNING id, label, display_order, is_active""",
            (f"category_{uuid.uuid4().hex[:12]}", payload.label.strip(), payload.display_order, payload.is_active),
        ).fetchone()


@app.put("/api/staff/admin/categories/{category_id}", dependencies=[Depends(require_staff)])
def update_category(category_id: str, payload: CategoryInput):
    with database() as connection:
        row = connection.execute(
            """UPDATE public.order_at_table_menu_categories SET label=%s, display_order=%s, is_active=%s
               WHERE id=%s RETURNING id, label, display_order, is_active""",
            (payload.label.strip(), payload.display_order, payload.is_active, category_id),
        ).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="Category not found")
    return row


def save_menu_item(payload: MenuItemInput, item_id: str | None = None):
    if any(not option.strip() or len(option) > 100 for option in payload.options):
        raise HTTPException(status_code=422, detail="Options must be 1–100 characters each")
    for option in payload.options:
        if "+₱" in option:
            match = re.search(r"\+₱(\d+)$", option)
            if not match or int(match.group(1)) > 1000000:
                raise HTTPException(status_code=422, detail="Option prices must end with +₱ and a number up to 1000000")
    if payload.image_url and not (payload.image_url.startswith("https://") or payload.image_url.startswith("http://")):
        raise HTTPException(status_code=422, detail="Image URL must start with http:// or https://")
    with database() as connection:
        if connection.execute("SELECT 1 FROM public.order_at_table_menu_categories WHERE id=%s", (payload.category_id,)).fetchone() is None:
            raise HTTPException(status_code=422, detail="Category not found")
        values = (payload.category_id, payload.name.strip(), payload.description.strip(), payload.price,
                  payload.image_url.strip(), payload.tag.strip() if payload.tag else None,
                  Jsonb([option.strip() for option in payload.options]), payload.display_order, payload.is_available)
        if item_id is None:
            row = connection.execute(
                """INSERT INTO public.order_at_table_menu_items
                   (id, category_id, name, description, price, image_url, tag, options, display_order, is_available)
                   VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                   RETURNING id, category_id, name, description, price, image_url, tag, options, display_order, is_available""",
                (f"item_{uuid.uuid4().hex[:12]}", *values),
            ).fetchone()
        else:
            row = connection.execute(
                """UPDATE public.order_at_table_menu_items SET category_id=%s, name=%s, description=%s,
                   price=%s, image_url=%s, tag=%s, options=%s, display_order=%s, is_available=%s WHERE id=%s
                   RETURNING id, category_id, name, description, price, image_url, tag, options, display_order, is_available""",
                (*values, item_id),
            ).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="Menu item not found")
    return row


@app.post("/api/staff/admin/items", dependencies=[Depends(require_staff)], status_code=201)
def create_menu_item(payload: MenuItemInput):
    return save_menu_item(payload)


@app.put("/api/staff/admin/items/{item_id}", dependencies=[Depends(require_staff)])
def update_menu_item(item_id: str, payload: MenuItemInput):
    return save_menu_item(payload, item_id)


@app.post("/api/staff/admin/tables", dependencies=[Depends(require_staff)], status_code=201)
def create_table(payload: TableInput):
    with database() as connection:
        existing = connection.execute("SELECT number, is_active FROM order_at_table.dining_tables WHERE number=%s", (payload.number,)).fetchone()
        if existing:
            raise HTTPException(status_code=409, detail="Table number already exists. Reactivate it instead.")
        row = connection.execute(
            """INSERT INTO order_at_table.dining_tables (number, seats) VALUES (%s,%s)
               RETURNING number, seats, is_active""", (payload.number, payload.seats),
        ).fetchone()
    return row


@app.get("/api/staff/admin/tables", dependencies=[Depends(require_staff)])
def get_admin_tables():
    with database() as connection:
        rows = connection.execute(
            """SELECT number, seats, is_active, current_service_id FROM order_at_table.dining_tables
               ORDER BY number"""
        ).fetchall()
    return {"tables": [{**row, "current_service_id": str(row["current_service_id"]) if row["current_service_id"] else None}
                       for row in rows]}


@app.put("/api/staff/admin/tables/{number}", dependencies=[Depends(require_staff)])
def update_table(number: int, payload: TableUpdateInput):
    with database() as connection:
        row = connection.execute(
            """UPDATE order_at_table.dining_tables SET seats=%s, is_active=%s
               WHERE number=%s AND (%s OR current_service_id IS NULL)
               RETURNING number, seats, is_active""",
            (payload.seats, payload.is_active, number, payload.is_active),
        ).fetchone()
        if row is None:
            raise HTTPException(status_code=409, detail="Table not found or currently in service")
    return row


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
            "SELECT number, seats, current_service_id, service_started_at FROM order_at_table.dining_tables WHERE number = %s AND is_active = true",
            (number,),
        ).fetchone()
    if table is None:
        raise HTTPException(status_code=404, detail="Unknown table number")
    return serialize_table(table)


@app.post("/api/orders", status_code=201)
def create_order(payload: OrderInput):
    with database() as connection:
        table = connection.execute(
            "SELECT current_service_id FROM order_at_table.dining_tables WHERE number = %s AND is_active = true FOR UPDATE",
            (payload.table_number,),
        ).fetchone()
        if table is None:
            raise HTTPException(status_code=404, detail="Unknown table number")
        if table["current_service_id"] != payload.table_service_id:
            raise HTTPException(status_code=409, detail="This table service has ended. Ask staff to start a new service.")
        lines = []
        for line in payload.items:
            item = connection.execute(
                """SELECT i.id, i.name, i.price, i.options FROM public.order_at_table_menu_items i
                   JOIN public.order_at_table_menu_categories c ON c.id = i.category_id
                   WHERE i.id = %s AND i.is_available = true AND c.is_active = true""",
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


@app.post("/api/orders/{number}/checkout")
def start_checkout(number: str, payload: CheckoutInput):
    with database() as connection:
        row = connection.execute(
            """SELECT id, table_number, table_service_id, payment_method, status,
                      paymongo_checkout_id, paymongo_checkout_url
               FROM order_at_table.orders WHERE number = %s FOR UPDATE""",
            (number,),
        ).fetchone()
        if row is None:
            raise HTTPException(status_code=404, detail="Order not found")
        if row["table_service_id"] != payload.table_service_id or row["payment_method"] != "paymongo":
            raise HTTPException(status_code=403, detail="Checkout is not available for this order")
        if row["status"] != "awaiting_payment":
            raise HTTPException(status_code=409, detail="This order is no longer awaiting payment")
        if row["paymongo_checkout_url"]:
            return {"checkout_url": row["paymongo_checkout_url"]}
        lines = connection.execute(
            "SELECT name, option, quantity, unit_price FROM order_at_table.order_lines WHERE order_id = %s ORDER BY id",
            (row["id"],),
        ).fetchall()
        checkout_id, checkout_url = create_paymongo_session(number, row["table_number"], lines)
        connection.execute(
            """UPDATE order_at_table.orders SET paymongo_checkout_id = %s, paymongo_checkout_url = %s
               WHERE id = %s""",
            (checkout_id, checkout_url, row["id"]),
        )
    return {"checkout_url": checkout_url}


@app.post("/api/paymongo/webhook")
async def paymongo_webhook(request: Request):
    secret = os.environ.get("PAYMONGO_WEBHOOK_SECRET", "")
    if not secret or secret.endswith("REPLACE_ME"):
        raise HTTPException(status_code=503, detail="PayMongo webhook is not configured")
    body = await request.body()
    expected_live = paymongo_secret().startswith("sk_live_")
    if not verify_paymongo_signature(body, request.headers.get("Paymongo-Signature", ""), secret, expected_live):
        raise HTTPException(status_code=401, detail="Invalid PayMongo signature")
    try:
        event = json.loads(body)
        data = event["data"]
        # PayMongo's event resource wraps checkout details in data.attributes.
        # The hosted checkout documentation also shows an unwrapped payload.
        details = data["attributes"] if data.get("type") == "event" else data
        live = details["livemode"]
        session = details["data"]
        if not isinstance(live, bool):
            raise ValueError()
    except (ValueError, KeyError, TypeError):
        raise HTTPException(status_code=400, detail="Invalid PayMongo event") from None
    if live != expected_live:
        raise HTTPException(status_code=400, detail="PayMongo mode mismatch")
    if details.get("type") != "checkout_session.payment.paid":
        return {"ok": True}
    attributes = session.get("attributes", {})
    payments = attributes.get("payments", [])
    number = attributes.get("reference_number")
    if not session.get("id") or not number or not any(
        payment.get("attributes", {}).get("status") == "paid" and
        payment.get("attributes", {}).get("currency") == "PHP"
        for payment in payments
    ):
        raise HTTPException(status_code=400, detail="Invalid paid checkout")
    with database() as connection:
        row = connection.execute(
            """SELECT total, status FROM order_at_table.orders
               WHERE number = %s AND payment_method = 'paymongo' AND paymongo_checkout_id = %s FOR UPDATE""",
            (number, session["id"]),
        ).fetchone()
        if row is None:
            raise HTTPException(status_code=409, detail="Checkout order was not found")
        if not any(payment.get("attributes", {}).get("status") == "paid" and
                   payment.get("attributes", {}).get("currency") == "PHP" and
                   payment.get("attributes", {}).get("amount") == row["total"] * 100
                   for payment in payments):
            raise HTTPException(status_code=409, detail="Checkout amount does not match order")
        if row["status"] == "awaiting_payment":
            connection.execute(
                """UPDATE order_at_table.orders SET status = 'new', payment_confirmed_at = now()
                   WHERE number = %s""",
                (number,),
            )
    return {"ok": True}


@app.get("/api/staff/orders", dependencies=[Depends(require_staff)])
def get_staff_orders():
    with database() as connection:
        return {"orders": list_orders(connection)}


@app.get("/api/staff/tables", dependencies=[Depends(require_staff)])
def get_staff_tables():
    with database() as connection:
        rows = connection.execute(
            "SELECT number, seats, current_service_id, service_started_at FROM order_at_table.dining_tables WHERE is_active = true ORDER BY number"
        ).fetchall()
    return {"tables": [serialize_table(row) for row in rows]}


@app.post("/api/staff/tables/{number}/start-service", dependencies=[Depends(require_staff)])
def start_table_service(number: int):
    with database() as connection:
        table = connection.execute(
            """UPDATE order_at_table.dining_tables
               SET current_service_id = %s, service_started_at = now()
               WHERE number = %s AND is_active = true AND current_service_id IS NULL
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
               WHERE number = %s AND payment_method = 'counter' AND status = 'awaiting_payment' RETURNING number""",
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
