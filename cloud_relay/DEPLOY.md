# nms-location-relay — Deployment

Cloudflare D1 (free tier). No paid plan needed.
Two secrets: one for agents writing in, one for the plant server reading out.

---

## Prerequisites

- Cloudflare account (free tier is fine)
- Node ≥ 18 installed on this machine
- `npx wrangler login` authenticated

```bash
cd /home/nmsserver/nms-location-relay
npm install
npx wrangler login        # opens browser — approve, then return here
```

---

## Step 1 — Create the D1 database

```bash
npx wrangler d1 create nms-location-relay
```

Copy the `database_id` from the output, then open `wrangler.toml` and replace
`REPLACE_WITH_D1_DATABASE_ID` with it.

---

## Step 2 — Apply the schema

```bash
npx wrangler d1 execute nms-location-relay --file=schema.sql
```

Confirm with:

```bash
npx wrangler d1 execute nms-location-relay --command="SELECT name FROM sqlite_master WHERE type='table'"
```

Expected: `location_samples`.

---

## Step 3 — Set secrets

Choose two long random strings (e.g. `openssl rand -hex 32`).

```bash
npx wrangler secret put INGEST_TOKEN    # agents use this to write samples
npx wrangler secret put BACKEND_TOKEN   # plant server uses this to drain + ack
```

Keep both values — you'll need them in Steps 5 and 6.

---

## Step 4 — Deploy

```bash
npx wrangler deploy
```

Note the Worker URL printed at the end:
`https://nms-location-relay.<your-subdomain>.workers.dev`

Smoke-test:

```bash
curl -X POST https://nms-location-relay.<your-subdomain>.workers.dev/v1/location/drain \
  -H "Authorization: Bearer <BACKEND_TOKEN>" \
  -H "Content-Type: application/json" \
  -d '{"max": 1}'
# → {"messages":[],"backlog":0}
```

---

## Step 5 — Wire up the plant server (this machine)

Add to the container's environment (edit `.env` or pass via `docker run -e`):

```
RELAY_URL=https://nms-location-relay.<your-subdomain>.workers.dev
RELAY_BACKEND_TOKEN=<the BACKEND_TOKEN you chose above>
```

Then restart the container:

```bash
echo "V1V2V3@S1S2" | sudo -S docker restart nms_app
```

The scheduler's `drain_location_relay()` job activates automatically — it was
already deployed and is a no-op until these two env vars are set.

---

## Step 6 — Wire up the agent (D:\nms_agents\.env)

```
RELAY_URL=https://nms-location-relay.<your-subdomain>.workers.dev
INGEST_TOKEN=<the INGEST_TOKEN you chose above>
```

The agent's `_relay_location_samples()` is already implemented (Part B).
It only fires when the plant LAN is unreachable and RELAY_URL is set — silent
on-network.

---

## Ongoing operations

**Check the backlog** (how many samples are sitting undelivered):

```bash
curl -X POST https://nms-location-relay.<your-subdomain>.workers.dev/v1/location/drain \
  -H "Authorization: Bearer <BACKEND_TOKEN>" \
  -H "Content-Type: application/json" \
  -d '{"max": 0}'
# backlog field shows the count
```

**Rotate a token** (if compromised):

```bash
npx wrangler secret put INGEST_TOKEN    # or BACKEND_TOKEN
```

Then update the matching env var on the agent / container side and restart both.

**View D1 rows directly**:

```bash
npx wrangler d1 execute nms-location-relay \
  --command="SELECT device_id, COUNT(*) as n FROM location_samples WHERE acked_at IS NULL GROUP BY device_id"
```

---

## Note on the other session's Worker

The other Claude session (nms_agents repo) built a different Worker in
`D:\nms_agents\cloud_relay\` using Cloudflare Queues + HMAC device keys.
That version requires the Workers **Paid** plan ($5/mo) and per-device key
provisioning steps. This version (D1 + bearer tokens) is the one that matches
the backend poller already running in this container — use this one.
