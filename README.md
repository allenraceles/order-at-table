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

1. Copy `.env.example` to `.env`. In the restaupro project's **Connect** dialog, copy a Postgres connection string (Transaction pooler for IPv4) into `SUPABASE_DB_URL`. URL-encode special characters in the password, including `@`. Keep `.env` local; it is ignored by Git. Never put this connection string in a `VITE_` variable or browser code. The backend checks that the connection belongs to RestauPro before using it. Set `VITE_SUPABASE_PUBLISHABLE_KEY` to the project's **publishable** key from Project Settings → API Keys. The publishable key is safe for the browser; never use a secret or service-role key there. Set `ORDER_STAFF_KEY` to a long random secret; the local `.env` already has one generated for this checkout.
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

Enter the `ORDER_STAFF_KEY` value from your local `.env` in each staff screen. The key stays in that browser tab's session storage and is sent only to FastAPI. In Front of house, open **Tables**, choose a table, and copy its ordering link for the guest. Staff may **Mark table in service** before the first order; if the table is available, its first customer order starts the service automatically. The `?table=` number selects the actual dining table; an order number such as `#1234` identifies one purchase for payment and tracking. After all of a table's orders are served, **End table service** makes it available again. Starting a new service gives the table a new service ID, so a previous guest's saved order no longer appears in the ordering app. Front of house and Kitchen Display refresh every five seconds. A customer order starts as `awaiting_payment`; Front of house confirms counter payment, releasing it as `new`; Kitchen starts preparation and marks it `ready`; either staff screen can mark it `complete`. The customer status panel refreshes every five seconds too. Staff reads and changes require the key, and the private order tables remain inaccessible to browser Supabase Data API roles.

Run `pnpm build` to build all three apps, or `pnpm build:order`, `pnpm build:foh`, or `pnpm build:kitchen` for one. `pnpm dev` remains an alias for the customer app. The customer menu loads live from Supabase's Data API using read-only row-level policies. Its Vite server proxies `/api` to FastAPI for orders. Check `http://localhost:8000/api/health` for the database connection. The API exposes `GET /api/menu`, `POST /api/orders`, and `GET /api/orders/{number}`. FastAPI validates prices and options against Supabase before saving orders.

Only counter payment can be submitted. GCash, Maya, and QR Ph remain unavailable until a payment provider is integrated. The root Sites configuration points to the customer app's static build; each other app has a `.openai/hosting.json` identifying its existing Site. The three hosted Sites are static and do not host FastAPI.

## Put the API online with Render

The repository includes `render.yaml` and a Dockerfile for a Render web service. Its Blueprint uses Render's free plan for initial testing. Free services can sleep when idle, so the first request after idle can take longer; choose a paid plan in Render if this will be used for live restaurant service.

1. Push this repository to a GitHub, GitLab, or Bitbucket repository you control. Keep `.env` out of Git.
2. In Render, choose **New → Blueprint**, connect that repository, and select its root `render.yaml`. Render will build `backend/Dockerfile` and ask for `SUPABASE_DB_URL` and `ORDER_STAFF_KEY`. Copy those exact values from your local `.env` into Render's secret fields. The Blueprint already lists the three Site origins for CORS.
3. The API is deployed at `https://mesa-order-api.onrender.com`; open its `/api/health` endpoint to confirm `{"ok":true}`.
4. The published frontend configuration is in `.env.production`: all three apps build with this API origin and RestauPro's browser-safe Supabase values. Rebuild and republish the customer, Front of house, and Kitchen Sites after changes to this file. Vite embeds these values at build time.
5. Open each published Site and verify that a new customer order appears in Front of house, payment confirmation releases it to Kitchen, and Kitchen status changes appear on the customer's order page.

Keep `ORDER_STAFF_KEY` private and share it only with trusted staff who need the FOH or Kitchen screens. Do not put `SUPABASE_DB_URL` or `ORDER_STAFF_KEY` in a `VITE_` variable. The published sites cannot use live orders until the API is deployed and their builds include its URL.
