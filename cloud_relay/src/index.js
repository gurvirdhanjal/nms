/**
 * NMS Location Relay — Cloudflare Worker
 *
 * Three endpoints:
 *   POST /v1/location/enqueue  — agent  → relay  (INGEST_TOKEN)
 *   POST /v1/location/drain    — server → relay  (BACKEND_TOKEN)
 *   POST /v1/location/ack      — server → relay  (BACKEND_TOKEN)
 *
 * Storage: Cloudflare D1 (SQLite).  Works on the free Workers plan.
 *
 * Security model:
 *   - Strict field whitelist on enqueue — only lat/lng/accuracy/source/recorded_at
 *     can ever enter this relay.  Anything else (typed text, screenshots, etc.)
 *     is rejected at the schema-validation layer before it touches D1.
 *   - INGEST_TOKEN (agent→relay) and BACKEND_TOKEN (server→relay) are separate
 *     secrets so a compromised device key cannot read or drain the queue.
 *   - sample_uuid dedup: relay-delivered samples that arrive twice (visibility
 *     timeout expired before ack) are silently dropped by the UNIQUE constraint.
 */

// Only these keys are allowed in a sample object. Anything else → 400.
const ALLOWED_SAMPLE_KEYS = new Set([
  'sample_uuid', 'latitude', 'longitude', 'accuracy_meters', 'source', 'recorded_at',
]);

// ── helpers ────────────────────────────────────────────────────────────────────

function json(data, status = 200) {
  return new Response(JSON.stringify(data), {
    status,
    headers: { 'Content-Type': 'application/json' },
  });
}

function bearerToken(request) {
  const auth = request.headers.get('Authorization') || '';
  return auth.startsWith('Bearer ') ? auth.slice(7).trim() : '';
}

function validateSample(raw) {
  if (typeof raw !== 'object' || raw === null || Array.isArray(raw)) return null;

  // Strict whitelist — reject any unrecognised key.
  for (const k of Object.keys(raw)) {
    if (!ALLOWED_SAMPLE_KEYS.has(k)) return null;
  }

  const lat = parseFloat(raw.latitude);
  const lng = parseFloat(raw.longitude);
  if (!Number.isFinite(lat) || lat < -90  || lat > 90)  return null;
  if (!Number.isFinite(lng) || lng < -180 || lng > 180) return null;

  const acc = raw.accuracy_meters != null ? parseFloat(raw.accuracy_meters) : null;

  return {
    sample_uuid:     typeof raw.sample_uuid  === 'string' ? raw.sample_uuid.slice(0, 64)  : null,
    latitude:        lat,
    longitude:       lng,
    accuracy_meters: Number.isFinite(acc) && acc > 0 ? acc : null,
    source:          typeof raw.source       === 'string' ? raw.source.slice(0, 32)       : null,
    recorded_at:     typeof raw.recorded_at  === 'string' ? raw.recorded_at.slice(0, 32)  : null,
  };
}

// ── handlers ──────────────────────────────────────────────────────────────────

async function handleEnqueue(request, env) {
  if (bearerToken(request) !== env.INGEST_TOKEN) return json({ error: 'Unauthorized' }, 401);

  const contentLength = parseInt(request.headers.get('Content-Length') || '0');
  if (contentLength > 8192) return json({ error: 'Payload too large' }, 413);

  let rawText;
  try { rawText = await request.text(); }
  catch { return json({ error: 'Failed to read body' }, 400); }
  if (rawText.length > 8192) return json({ error: 'Payload too large' }, 413);

  let body;
  try { body = JSON.parse(rawText); }
  catch { return json({ error: 'Invalid JSON' }, 400); }

  const deviceId = typeof body.device_id === 'string' ? body.device_id.slice(0, 64).trim() : '';
  if (!deviceId) return json({ error: 'device_id required' }, 400);

  const rawSamples = Array.isArray(body.samples) ? body.samples.slice(0, 10) : [];
  if (!rawSamples.length) return json({ error: 'samples array required' }, 400);

  let accepted = 0;
  const duplicateUuids = [];

  for (const raw of rawSamples) {
    const s = validateSample(raw);
    if (!s) continue; // malformed sample — skip silently

    try {
      await env.DB.prepare(`
        INSERT INTO location_samples
          (device_id, sample_uuid, latitude, longitude, accuracy_meters, source, recorded_at)
        VALUES (?, ?, ?, ?, ?, ?, ?)
      `).bind(deviceId, s.sample_uuid, s.latitude, s.longitude, s.accuracy_meters, s.source, s.recorded_at)
        .run();
      accepted++;
    } catch (err) {
      // UNIQUE constraint on sample_uuid fires here for exact duplicates.
      if (s.sample_uuid && String(err).includes('UNIQUE')) {
        duplicateUuids.push(s.sample_uuid);
      }
      // Any other DB error: skip this sample, continue with the rest.
    }
  }

  return json({ accepted, duplicate_uuids: duplicateUuids }, 202);
}

async function handleDrain(request, env) {
  if (bearerToken(request) !== env.BACKEND_TOKEN) return json({ error: 'Unauthorized' }, 401);

  let body = {};
  try { body = await request.json(); } catch {}

  const max           = Math.min(Math.max(parseInt(body.max) || 50, 1), 200);
  const visibilityMs  = Math.max(parseInt(body.visibility_timeout_ms) || 60000, 5000);
  const leaseExpires  = new Date(Date.now() + visibilityMs)
    .toISOString().replace('T', ' ').slice(0, 19);

  // Fetch rows that are unacked and not currently leased by another drain call.
  const { results } = await env.DB.prepare(`
    SELECT id, device_id, sample_uuid, latitude, longitude, accuracy_meters, source, recorded_at
    FROM   location_samples
    WHERE  acked_at IS NULL
      AND  (lease_expires_at IS NULL OR lease_expires_at < datetime('now'))
    ORDER  BY id ASC
    LIMIT  ?
  `).bind(max).all();

  if (!results.length) return json({ messages: [], backlog: 0 });

  // Stamp the lease so concurrent drain calls don't double-deliver.
  const ids         = results.map(r => r.id);
  const placeholders = ids.map(() => '?').join(', ');
  await env.DB.prepare(
    `UPDATE location_samples SET lease_expires_at = ? WHERE id IN (${placeholders})`
  ).bind(leaseExpires, ...ids).run();

  // Rough backlog count (excludes currently-leased rows for simplicity).
  const backlogRow = await env.DB.prepare(
    `SELECT COUNT(*) AS cnt FROM location_samples WHERE acked_at IS NULL`
  ).first();
  const backlog = Math.max(0, (backlogRow?.cnt ?? 0) - ids.length);

  const messages = results.map(r => ({
    lease_id: String(r.id),
    attempts: 1,
    sample: {
      device_id: r.device_id,
      samples: [{
        sample_uuid:     r.sample_uuid,
        latitude:        r.latitude,
        longitude:       r.longitude,
        accuracy_meters: r.accuracy_meters,
        source:          r.source,
        recorded_at:     r.recorded_at,
      }],
    },
  }));

  return json({ messages, backlog });
}

async function handleAck(request, env) {
  if (bearerToken(request) !== env.BACKEND_TOKEN) return json({ error: 'Unauthorized' }, 401);

  let body = {};
  try { body = await request.json(); } catch {}

  const ackIds = (Array.isArray(body.acks) ? body.acks : [])
    .slice(0, 500)
    .map(id => parseInt(id))
    .filter(n => Number.isFinite(n) && n > 0);

  if (!ackIds.length) return json({ acked: 0 });

  const placeholders = ackIds.map(() => '?').join(', ');
  const result = await env.DB.prepare(
    `UPDATE location_samples SET acked_at = datetime('now')
     WHERE id IN (${placeholders}) AND acked_at IS NULL`
  ).bind(...ackIds).run();

  return json({ acked: result.meta?.changes ?? ackIds.length });
}

// Scheduled cleanup: delete acked rows older than 48 h (runs via Cron Trigger).
async function handleScheduled(env) {
  await env.DB.prepare(
    `DELETE FROM location_samples WHERE acked_at < datetime('now', '-48 hours')`
  ).run();
  // Also expire truly abandoned leases that were never acked after 24h.
  await env.DB.prepare(
    `DELETE FROM location_samples
     WHERE acked_at IS NULL AND created_at < datetime('now', '-24 hours')`
  ).run();
}

// ── entry point ───────────────────────────────────────────────────────────────

export default {
  async fetch(request, env) {
    const { pathname } = new URL(request.url);
    const method = request.method;

    if (method === 'POST' && pathname === '/v1/location/enqueue') return handleEnqueue(request, env);
    if (method === 'POST' && pathname === '/v1/location/drain')   return handleDrain(request, env);
    if (method === 'POST' && pathname === '/v1/location/ack')     return handleAck(request, env);

    return json({ error: 'Not found' }, 404);
  },

  async scheduled(_event, env) {
    await handleScheduled(env);
  },
};
