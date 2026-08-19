import {
  PRODUCTS,
  isValidProduct,
  isValidMonths,
  calculatePrice,
  isValidNick,
  isValidCustomAmount,
} from "../lib/products.js";

// Тариф:      { nick: "Notch", product: "vip", months: 3 }
// Свободный донат: { nick: "Notch", product: "custom", customAmount: 5 }
// -> { invoice_url: "https://nowpayments.io/payment/?iid=..." , order_id }
export async function handleCheckout(request, env) {
  let body;
  try {
    body = await request.json();
  } catch {
    return json({ error: "bad_json" }, 400);
  }

  const { nick, product, months, customAmount } = body || {};

  if (!isValidNick(nick)) {
    return json({ error: "invalid_nick", message: "Никнейм: 3-16 символов, латиница/цифры/подчёркивание" }, 400);
  }

  let amountUsd, months_;
  if (product === "custom") {
    if (!isValidCustomAmount(customAmount)) {
      return json({ error: "invalid_amount", message: "Сумма доната: от 1 до 10000 USD" }, 400);
    }
    amountUsd = Math.round(Number(customAmount) * 100) / 100;
    months_ = 0;
  } else {
    if (!isValidProduct(product)) {
      return json({ error: "invalid_product" }, 400);
    }
    if (!isValidMonths(months)) {
      return json({ error: "invalid_months" }, 400);
    }
    amountUsd = calculatePrice(product, months);
    months_ = Number(months);
  }

  const orderId = crypto.randomUUID();
  const now = Date.now();

  // 1) сохраняем заказ как pending ДО обращения к NOWPayments —
  // если сеть оборвётся после создания invoice, у нас всё равно есть запись
  await env.DB.prepare(
    `INSERT INTO orders (id, player_nick, product, months, amount_usd, status, created_at)
     VALUES (?, ?, ?, ?, ?, 'pending', ?)`
  )
    .bind(orderId, nick, product, months_, amountUsd, now)
    .run();

  // 2) создаём invoice в NOWPayments
  // Базовый URL можно переопределить через секрет/переменную NOWPAYMENTS_API_BASE —
  // это позволяет гонять полный цикл через sandbox (https://api-sandbox.nowpayments.io/v1)
  // без единой правки кода. По умолчанию — боевой API.
  const apiBase = env.NOWPAYMENTS_API_BASE || "https://api.nowpayments.io/v1";
  const origin = new URL(request.url).origin;
  const description =
    product === "custom"
      ? `THE LAB — донат $${amountUsd} от ${nick}`
      : `THE LAB — ${PRODUCTS[product].label} x${months} мес. для ${nick}`;
  const npRes = await fetch(`${apiBase}/invoice`, {
    method: "POST",
    headers: {
      "x-api-key": env.NOWPAYMENTS_API_KEY,
      "Content-Type": "application/json",
    },
    body: JSON.stringify({
      price_amount: amountUsd,
      price_currency: "usd",
      order_id: orderId,
      order_description: description,
      ipn_callback_url: `${origin}/api/ipn`,
      success_url: `${origin}/shop.html?paid=1&order=${orderId}`,
      cancel_url: `${origin}/shop.html?cancelled=1`,
    }),
  });

  if (!npRes.ok) {
    const errText = await npRes.text();
    await env.DB.prepare(`UPDATE orders SET status = 'failed' WHERE id = ?`).bind(orderId).run();
    return json({ error: "nowpayments_error", detail: errText }, 502);
  }

  const invoice = await npRes.json();

  return json({ order_id: orderId, invoice_url: invoice.invoice_url, amount_usd: amountUsd });
}

function json(obj, status = 200) {
  return new Response(JSON.stringify(obj), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}
