import { verifyNowPaymentsSignature } from "../lib/verify.js";
import { buildDeliveryCommand } from "../lib/products.js";

// POST /api/ipn — сюда стучится NOWPayments после смены статуса оплаты.
// Статусы идут по цепочке: waiting -> confirming -> confirmed -> finished
// (или partially_paid / failed / expired / refunded).
// Выдаём привилегию только на finished — это гарантирует, что платёж окончательно принят.
export async function handleIpn(request, env) {
  const raw = await request.text();
  let body;
  try {
    body = JSON.parse(raw);
  } catch {
    return new Response("bad json", { status: 400 });
  }

  const signature = request.headers.get("x-nowpayments-sig");
  const ok = await verifyNowPaymentsSignature(body, signature, env.NOWPAYMENTS_IPN_SECRET);
  if (!ok) {
    return new Response("invalid signature", { status: 401 });
  }

  const { order_id, payment_id, payment_status } = body;
  if (!order_id) {
    return new Response("no order_id", { status: 400 });
  }

  // идемпотентность: одно и то же IPN-уведомление может прийти повторно
  const alreadyProcessed = await env.DB.prepare(
    `SELECT np_payment_id FROM processed_ipn WHERE np_payment_id = ? AND payment_status = ?`
  )
    .bind(String(payment_id), payment_status)
    .first();

  if (alreadyProcessed) {
    return new Response("ok (duplicate)", { status: 200 });
  }

  await env.DB.prepare(
    `INSERT OR REPLACE INTO processed_ipn (np_payment_id, payment_status, received_at) VALUES (?, ?, ?)`
  )
    .bind(String(payment_id), payment_status, Date.now())
    .run();

  const order = await env.DB.prepare(`SELECT * FROM orders WHERE id = ?`).bind(order_id).first();
  if (!order) {
    return new Response("unknown order", { status: 404 });
  }

  if (payment_status === "finished" || payment_status === "confirmed") {
    if (order.status === "paid" || order.status === "delivered") {
      return new Response("ok (already paid)", { status: 200 });
    }

    // свободный донат без привилегии — сразу delivered, в очередь на плагин не идёт
    const newStatus = order.product === "custom" ? "delivered" : "paid";
    await env.DB.prepare(
      `UPDATE orders SET status = ?, np_payment_id = ?, paid_at = ? WHERE id = ?`
    )
      .bind(newStatus, String(payment_id), Date.now(), order_id)
      .run();

    if (order.product !== "custom") {
      const command = buildDeliveryCommand(order.product, order.months, order.player_nick);
      await env.DB.prepare(
        `INSERT INTO delivery_queue (order_id, command, status, created_at) VALUES (?, ?, 'pending', ?)`
      )
        .bind(order_id, command, Date.now())
        .run();
    }
  } else if (["failed", "expired", "refunded"].includes(payment_status)) {
    await env.DB.prepare(`UPDATE orders SET status = ? WHERE id = ?`).bind(payment_status, order_id).run();
  }
  // waiting / confirming / partially_paid — просто игнорируем, ждём следующий IPN

  return new Response("ok", { status: 200 });
}
