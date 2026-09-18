-- Menu tables are readable through Supabase's public Data API. Orders stay private.
CREATE SCHEMA IF NOT EXISTS order_at_table;
REVOKE ALL ON SCHEMA order_at_table FROM PUBLIC, anon, authenticated;

-- Preserve existing RestauPro menu edits when upgrading the original private schema.
DO $$
BEGIN
  IF to_regclass('order_at_table.menu_categories') IS NOT NULL THEN
    ALTER TABLE order_at_table.menu_categories RENAME TO order_at_table_menu_categories;
    ALTER TABLE order_at_table.order_at_table_menu_categories SET SCHEMA public;
  END IF;
  IF to_regclass('order_at_table.menu_items') IS NOT NULL THEN
    ALTER TABLE order_at_table.menu_items RENAME TO order_at_table_menu_items;
    ALTER TABLE order_at_table.order_at_table_menu_items SET SCHEMA public;
  END IF;
END $$;

CREATE TABLE IF NOT EXISTS public.order_at_table_menu_categories (
  id text PRIMARY KEY,
  label text NOT NULL,
  display_order integer NOT NULL,
  is_active boolean NOT NULL DEFAULT true
);

CREATE TABLE IF NOT EXISTS public.order_at_table_menu_items (
  id text PRIMARY KEY,
  category_id text NOT NULL REFERENCES public.order_at_table_menu_categories(id),
  name text NOT NULL,
  description text NOT NULL,
  price integer NOT NULL CHECK (price >= 0),
  image_url text NOT NULL,
  tag text,
  options jsonb NOT NULL DEFAULT '[]'::jsonb CHECK (jsonb_typeof(options) = 'array'),
  display_order integer NOT NULL,
  is_available boolean NOT NULL DEFAULT true
);

CREATE TABLE IF NOT EXISTS order_at_table.orders (
  id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  number text NOT NULL UNIQUE,
  table_number integer NOT NULL CHECK (table_number > 0),
  payment_method text NOT NULL CHECK (payment_method IN ('counter', 'paymongo')),
  status text NOT NULL CHECK (status IN ('awaiting_payment', 'new', 'preparing', 'ready', 'complete', 'cancelled')),
  total integer NOT NULL CHECK (total >= 0),
  created_at timestamptz NOT NULL DEFAULT now(),
  payment_confirmed_at timestamptz,
  preparing_at timestamptz,
  ready_at timestamptz,
  completed_at timestamptz
);

CREATE TABLE IF NOT EXISTS order_at_table.dining_tables (
  number integer PRIMARY KEY CHECK (number > 0),
  seats integer NOT NULL CHECK (seats > 0),
  is_active boolean NOT NULL DEFAULT true,
  current_service_id uuid,
  service_started_at timestamptz
);
ALTER TABLE order_at_table.dining_tables ADD COLUMN IF NOT EXISTS is_active boolean NOT NULL DEFAULT true;

CREATE TABLE IF NOT EXISTS order_at_table.restaurant_settings (
  id integer PRIMARY KEY CHECK (id = 1),
  name text NOT NULL,
  location text NOT NULL,
  address text NOT NULL DEFAULT '',
  phone text NOT NULL DEFAULT '',
  hours text NOT NULL,
  header text NOT NULL,
  subheader text NOT NULL,
  brand_mark text NOT NULL,
  accent_color text NOT NULL CHECK (accent_color ~ '^#[0-9A-Fa-f]{6}$')
);
INSERT INTO order_at_table.restaurant_settings
  (id, name, location, address, phone, hours, header, subheader, brand_mark, accent_color)
VALUES (1, 'Mesa & Co.', 'Greenbelt 5 · Makati', '', '', 'Open until 10:00 PM',
        'Take your time. We’ll bring it.',
        'Order from your table whenever you’re ready. Prices already include VAT. Need a special discount or billing request? You can pay at the counter.',
        'M', '#dce85d')
ON CONFLICT (id) DO NOTHING;

INSERT INTO order_at_table.dining_tables (number, seats) VALUES
  (2, 2), (4, 4), (6, 4), (8, 2), (9, 4), (11, 2),
  (12, 4), (14, 6), (15, 2), (16, 4), (18, 4), (20, 6)
ON CONFLICT (number) DO NOTHING;

ALTER TABLE order_at_table.orders
  ADD COLUMN IF NOT EXISTS payment_confirmed_at timestamptz,
  ADD COLUMN IF NOT EXISTS preparing_at timestamptz,
  ADD COLUMN IF NOT EXISTS ready_at timestamptz,
  ADD COLUMN IF NOT EXISTS completed_at timestamptz;
ALTER TABLE order_at_table.orders ADD COLUMN IF NOT EXISTS table_service_id uuid;
ALTER TABLE order_at_table.orders
  ADD COLUMN IF NOT EXISTS paymongo_checkout_id text,
  ADD COLUMN IF NOT EXISTS paymongo_checkout_url text;
ALTER TABLE order_at_table.orders DROP CONSTRAINT IF EXISTS orders_payment_method_check;
ALTER TABLE order_at_table.orders ADD CONSTRAINT orders_payment_method_check
  CHECK (payment_method IN ('counter', 'paymongo'));
CREATE UNIQUE INDEX IF NOT EXISTS order_at_table_orders_paymongo_checkout_id_idx
  ON order_at_table.orders(paymongo_checkout_id) WHERE paymongo_checkout_id IS NOT NULL;
ALTER TABLE order_at_table.orders DROP CONSTRAINT IF EXISTS orders_status_check;
ALTER TABLE order_at_table.orders ADD CONSTRAINT orders_status_check
  CHECK (status IN ('awaiting_payment', 'new', 'preparing', 'ready', 'complete', 'cancelled'));

CREATE TABLE IF NOT EXISTS order_at_table.order_lines (
  id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  order_id bigint NOT NULL REFERENCES order_at_table.orders(id) ON DELETE CASCADE,
  item_id text NOT NULL REFERENCES public.order_at_table_menu_items(id),
  name text NOT NULL,
  option text,
  quantity integer NOT NULL CHECK (quantity BETWEEN 1 AND 10),
  unit_price integer NOT NULL CHECK (unit_price >= 0)
);

CREATE INDEX IF NOT EXISTS order_at_table_order_lines_order_id_idx
  ON order_at_table.order_lines(order_id);
CREATE INDEX IF NOT EXISTS order_at_table_orders_table_service_id_idx
  ON order_at_table.orders(table_service_id, id DESC);

ALTER TABLE public.order_at_table_menu_categories ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.order_at_table_menu_items ENABLE ROW LEVEL SECURITY;
ALTER TABLE order_at_table.orders ENABLE ROW LEVEL SECURITY;
ALTER TABLE order_at_table.order_lines ENABLE ROW LEVEL SECURITY;
ALTER TABLE order_at_table.dining_tables ENABLE ROW LEVEL SECURITY;
ALTER TABLE order_at_table.restaurant_settings ENABLE ROW LEVEL SECURITY;

REVOKE ALL ON ALL TABLES IN SCHEMA order_at_table FROM anon, authenticated;
REVOKE ALL ON ALL SEQUENCES IN SCHEMA order_at_table FROM anon, authenticated;

REVOKE ALL ON public.order_at_table_menu_categories, public.order_at_table_menu_items FROM PUBLIC, anon, authenticated;
GRANT SELECT ON public.order_at_table_menu_categories, public.order_at_table_menu_items TO anon, authenticated;

DROP POLICY IF EXISTS order_at_table_active_categories ON public.order_at_table_menu_categories;
CREATE POLICY order_at_table_active_categories ON public.order_at_table_menu_categories
  FOR SELECT TO anon, authenticated USING (is_active = true);
DROP POLICY IF EXISTS order_at_table_available_items ON public.order_at_table_menu_items;
CREATE POLICY order_at_table_available_items ON public.order_at_table_menu_items
  FOR SELECT TO anon, authenticated USING (is_available = true);
