"""Create the public menu and private order schema in RestauPro."""

from pathlib import Path

from backend.app.main import database


def main():
    schema = Path(__file__).with_name("schema.sql").read_text()
    with database() as connection:
        connection.execute(schema)
        count = connection.execute(
            """SELECT count(*) AS count FROM information_schema.tables
               WHERE (table_schema = 'public' AND table_name IN
                      ('order_at_table_menu_categories', 'order_at_table_menu_items'))
                  OR (table_schema = 'order_at_table' AND table_name IN
                      ('orders', 'order_lines'))"""
        ).fetchone()["count"]
        if count != 4:
            raise RuntimeError("Schema verification failed; setup was rolled back")
    print("RestauPro schema ready: four tables verified")


if __name__ == "__main__":
    main()
