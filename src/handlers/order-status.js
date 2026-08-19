// GET /api/order-status?order_id=...
export async function handleOrderStatus(request, env) {
  const url = new URL(request.url);
  const orderId = url.searchParams.get("order_id");
  if (!orderId) {
    return json({ error: "no_order_id" }, 400);
  }

  const order = await env.DB.prepare(`SELECT status, product, months FROM orders WHERE id = ?`)
    .bind(orderId)
    .first();

  if (!order) {
    return json({ error: "not_found" }, 404);
  }

  return json({ status: order.status, product: order.product, months: order.months });
}

function json(obj, status = 200) {
  return new Response(JSON.stringify(obj), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}
