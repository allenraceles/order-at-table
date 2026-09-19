# Mesa & Co. workspace

This pnpm monorepo contains three frontends and one Python API:

| App | Location | Local URL | Data |
| --- | --- | --- | --- |
| Customer ordering | `apps/order-at-table` | `http://localhost:5173` | Supabase menu and FastAPI orders |
| Front of house | `apps/front-of-house` | `http://localhost:5174` | Live orders through FastAPI |
| Kitchen display | `apps/kitchen-display` | `http://localhost:5175` | Paid tickets through FastAPI |
| Order API | `backend` | `http://localhost:8000` | RestauPro Supabase Postgres |

The front-of-house and kitchen apps are the sources of the existing [Front-of-house dashboard](https://mesa-foh-dashboard.raseresu.chatgpt.site) and [Kitchen Display](https://mesa-kitchen-view.raseresu.chatgpt.site). All three apps now use the same RestauPro orders through FastAPI. The original standalone customer prototype is saved in `prototype/index.html`.

## Set up Supabase

1. Copy `.env.example` to `.env`. In the restaupro project's **Connect** dialog, copy a Postgres connection string (Transaction pooler for IPv4) into `SUPABASE_DB_URL`. URL-encode special characters in the password, including `@`. Keep `.env` local; it is ignored by Git. Never put this connection string in a `VITE_` variable or browser code. The backend checks that the connection belongs to RestauPro before using it. Set `VITE_SUPABASE_PUBLISHABLE_KEY` to the project's **publishable** key from Project Settings → API Keys. Put a server-side secret key (`sb_secret_...`, or the legacy `service_role` key) in `SUPABASE_SERVICE_ROLE_KEY`; it is used only by FastAPI to upload images to Storage. Never give that key a `VITE_` prefix. Set `ORDER_STAFF_KEY` to a long random secret; the local `.env` already has one generated for this checkout.
2. Install the Python packages, create the public read-only menu tables and private `order_at_table` order tables, then import the ten menu items on a new database:

```bash
python3 -m venv .venv
.venv/bin/pip install -r backend/requirements.txt
.venv/bin/python -m backend.setup_supabase
.venv/bin/python -m backend.seed_menu
```

The import updates matching menu IDs and prices. Do not rerun it after editing menu data in Supabase unless you intend to restore values from `shared/menu.json`. The four categories and ten menu items were imported into RestauPro. There was no local SQLite order database in this checkout, so there were no existing orders to copy.

## Run locally

Start FastAPI:

```bash
.venv/bin/uvicorn backend.app.main:app --reload --port 8000
```

In separate terminals, start the three frontends:

```bash
pnpm install
pnpm dev:order
pnpm dev:foh
pnpm dev:kitchen
```

Enter the `ORDER_STAFF_KEY` value from your local `.env` in each staff screen. The key stays in that browser tab's session storage and is sent only to FastAPI. In Front of house, open **Tables**, choose a table, and toggle it **In service** to allow ordering. Copy its ordering link for the guest. The `?table=` number selects the actual dining table; an order number such as `#1234` identifies one purchase for payment and tracking. After payment, a guest can start another order while the table remains in service; each submission creates a separate order for payment and the kitchen queue. Toggle the table **Out of service** when the guest leaves. Starting a new service gives the table a new service ID, so a previous guest's saved order no longer appears in the ordering app. Front of house and Kitchen Display refresh every five seconds. A customer order starts as `awaiting_payment`; Front of house confirms counter payment, releasing it as `new`; Kitchen starts preparation and marks it `ready`; either staff screen can mark it `complete`. The customer status panel refreshes every five seconds too. Staff reads and changes require the key, and the private order tables remain inaccessible to browser Supabase Data API roles.

The FOH **Manage** view uses the same staff key. Staff can edit restaurant name, location, address, phone, hours, kitchen status, preparation time message, header, subheader, brand mark, logo, and accent color; add or edit menu categories and items; and add, resize, or deactivate tables. Logo and menu uploads accept JPEG, PNG, and WebP pictures, resize them in the browser, convert them to WebP, and enforce a 1 MB server and bucket limit. Replacing a saved managed image removes the previous object from Storage. Hiding a category or item removes it from customer ordering after refresh. A table in service cannot be deactivated. Keep the staff key limited to people authorized to edit the menu and venue settings.

Run `pnpm build` to build all three apps, or `pnpm build:order`, `pnpm build:foh`, or `pnpm build:kitchen` for one. `pnpm dev` remains an alias for the customer app. The customer menu loads live from Supabase's Data API using read-only row-level policies. Its Vite server proxies `/api` to FastAPI for orders. Check `http://localhost:8000/api/health` for the database connection. The API exposes `GET /api/menu`, `POST /api/orders`, and `GET /api/orders/{number}`. FastAPI validates prices and options against Supabase before saving orders.

## PayMongo checkout

The customer checkout offers PayMongo hosted checkout for GCash and QR Ph when it is configured, plus counter payment for discounts and special billing. Online orders stay in `awaiting_payment` until a signed `checkout_session.payment.paid` webhook confirms the exact order amount; only then do they reach the kitchen. The PayMongo return page may briefly show payment pending while the webhook arrives. Maya is not offered because its availability for this checkout API and account has not been verified.

1. Apply the updated `backend/schema.sql` to RestauPro by running `.venv/bin/python -m backend.setup_supabase` after the database connection works. This adds private PayMongo checkout columns and keeps existing orders.
2. Put your **test** secret API key in `PAYMONGO_SECRET_KEY` in local `.env` and Render. Keep it server only. Set `ORDER_CUSTOMER_URL` to the public customer site origin on Render; locally use `http://localhost:5173`. Never put the secret in a `VITE_` variable.
3. In PayMongo Developers → Webhooks, create a **test mode** endpoint at `https://YOUR_API_HOST/api/paymongo/webhook`, subscribed to `checkout_session.payment.paid`. Copy that endpoint's signing secret into `PAYMONGO_WEBHOOK_SECRET` in local `.env` and Render. Use a separate live webhook and live key when you are ready for real payments.
4. Run a test order and complete a PayMongo test payment. Confirm the order changes from `awaiting_payment` to `new` in Front of house and appears in Kitchen. A redirect alone does not confirm payment.

The root Sites configuration points to the customer app's static build; each other app has a `.openai/hosting.json` identifying its existing Site. The three hosted Sites are static and do not host FastAPI.

## Put the API online with Render

The repository includes `render.yaml` and a Dockerfile for a Render web service. Its Blueprint uses Render's free plan for initial testing. Free services can sleep when idle, so the first request after idle can take longer; choose a paid plan in Render if this will be used for live restaurant service.

1. Push this repository to a GitHub, GitLab, or Bitbucket repository you control. Keep `.env` out of Git.
2. In Render, choose **New → Blueprint**, connect that repository, and select its root `render.yaml`. Render will build `backend/Dockerfile` and ask for `SUPABASE_DB_URL`, `SUPABASE_SERVICE_ROLE_KEY`, `ORDER_STAFF_KEY`, `PAYMONGO_SECRET_KEY`, and `PAYMONGO_WEBHOOK_SECRET`. Copy those exact values from your local `.env` into Render's secret fields. The Blueprint already lists the three Site origins for CORS.
3. The API is deployed at `https://mesa-order-api.onrender.com`; open its `/api/health` endpoint to confirm `{"ok":true}`.
4. The published frontend configuration is in `.env.production`: all three apps build with this API origin and RestauPro's browser-safe Supabase values. Rebuild and republish the customer, Front of house, and Kitchen Sites after changes to this file. Vite embeds these values at build time.
5. Open each published Site and verify that a new customer order appears in Front of house, payment confirmation releases it to Kitchen, and Kitchen status changes appear on the customer's order page.

Keep `ORDER_STAFF_KEY` private and share it only with trusted staff who need the FOH or Kitchen screens. Do not put `SUPABASE_DB_URL` or `ORDER_STAFF_KEY` in a `VITE_` variable. The published sites cannot use live orders until the API is deployed and their builds include its URL.
