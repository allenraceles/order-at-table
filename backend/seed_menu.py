"""Idempotently copy shared/menu.json into the configured Supabase project."""

from __future__ import annotations

import json
from pathlib import Path

from psycopg.types.json import Jsonb

from backend.app.main import database

ROOT = Path(__file__).resolve().parents[1]
MENU = json.loads((ROOT / "shared" / "menu.json").read_text())


def category_id(label: str) -> str:
    return label.lower().replace(" & ", "-").replace(" ", "-")


def main():
    expected = {item["id"]: item["price"] for section in MENU["menu"] for item in section["items"]}
    with database() as connection:
        for category_position, section in enumerate(MENU["menu"]):
            category = category_id(section["category"])
            connection.execute(
                """INSERT INTO public.order_at_table_menu_categories (id, label, display_order)
                   VALUES (%s, %s, %s)
                   ON CONFLICT (id) DO UPDATE SET label = EXCLUDED.label,
                     display_order = EXCLUDED.display_order""",
                (category, section["category"], category_position),
            )
            for item_position, item in enumerate(section["items"]):
                connection.execute(
                    """INSERT INTO public.order_at_table_menu_items
                       (id, category_id, name, description, price, image_url,
                        tag, options, display_order)
                       VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                       ON CONFLICT (id) DO UPDATE SET
                         category_id = EXCLUDED.category_id,
                         name = EXCLUDED.name,
                         description = EXCLUDED.description,
                         price = EXCLUDED.price,
                         image_url = EXCLUDED.image_url,
                         tag = EXCLUDED.tag,
                         options = EXCLUDED.options,
                         display_order = EXCLUDED.display_order""",
                    (item["id"], category, item["name"], item["desc"], item["price"],
                     item["image"], item.get("tag"), Jsonb(item.get("options", [])),
                     item_position),
                )
        rows = connection.execute(
            "SELECT id, price FROM public.order_at_table_menu_items WHERE id = ANY(%s)",
            (list(expected),),
        ).fetchall()
        actual = {row["id"]: row["price"] for row in rows}
        if actual != expected:
            raise RuntimeError("Menu verification failed; import was rolled back")
    print(f"Menu import complete: {len(expected)} source items verified in Supabase")


if __name__ == "__main__":
    main()
