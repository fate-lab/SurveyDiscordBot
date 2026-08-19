// Эндпоинт для плагина на майнкрафт-сервере.
// GET  /api/queue           — забрать порцию невыполненных команд
// POST /api/queue           — { ids: [1,2,3] } отметить их выполненными
//
// Авторизация: заголовок X-Server-Secret должен совпадать с env.SERVER_SECRET.

function checkAuth(request, env) {
  const secret = request.headers.get("x-server-secret");
  return secret && env.SERVER_SECRET && secret === env.SERVER_SECRET;
}

export async function handleQueueGet(request, env) {
  if (!checkAuth(request, env)) {
    return json({ error: "unauthorized" }, 401);
  }

  const { results } = await env.DB.prepare(
    `SELECT dq.id, dq.command, o.player_nick
     FROM delivery_queue dq
     JOIN orders o ON o.id = dq.order_id
     WHERE dq.status = 'pending'
     ORDER BY dq.id ASC
     LIMIT 20`
  ).all();

  return json({ commands: results });
}

export async function handleQueueAck(request, env) {
  if (!checkAuth(request, env)) {
    return json({ error: "unauthorized" }, 401);
  }

  let body;
  try {
    body = await request.json();
  } catch {
    return json({ error: "bad_json" }, 400);
  }

  const ids = Array.isArray(body.ids) ? body.ids.filter((n) => Number.isInteger(n)) : [];
  if (ids.length === 0) {
    return json({ error: "no_ids" }, 400);
  }

  const now = Date.now();
  const placeholders = ids.map(() => "?").join(",");
  await env.DB.prepare(
    `UPDATE delivery_queue SET status = 'done', done_at = ? WHERE id IN (${placeholders})`
  )
    .bind(now, ...ids)
    .run();

  await env.DB.prepare(
    `UPDATE orders SET status = 'delivered'
     WHERE id IN (SELECT order_id FROM delivery_queue WHERE id IN (${placeholders}))`
  )
    .bind(...ids)
    .run();

  return json({ ok: true, updated: ids.length });
}

function json(obj, status = 200) {
  return new Response(JSON.stringify(obj), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}
