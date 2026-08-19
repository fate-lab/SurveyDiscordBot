import { handleCheckout } from "./handlers/checkout.js";
import { handleIpn } from "./handlers/ipn.js";
import { handleOrderStatus } from "./handlers/order-status.js";
import { handleQueueGet, handleQueueAck } from "./handlers/queue.js";

export default {
  async fetch(request, env, ctx) {
    const url = new URL(request.url);
    const { pathname } = url;
    const method = request.method;

    try {
      if (pathname === "/api/checkout" && method === "POST") {
        return await handleCheckout(request, env);
      }
      if (pathname === "/api/ipn" && method === "POST") {
        return await handleIpn(request, env);
      }
      if (pathname === "/api/order-status" && method === "GET") {
        return await handleOrderStatus(request, env);
      }
      if (pathname === "/api/queue") {
        if (method === "GET") return await handleQueueGet(request, env);
        if (method === "POST") return await handleQueueAck(request, env);
      }
    } catch (err) {
      // не даём стектрейсу утечь наружу, но и не глотаем ошибку молча
      console.error("Unhandled error in API route", pathname, err);
      return new Response(JSON.stringify({ error: "internal_error" }), {
        status: 500,
        headers: { "Content-Type": "application/json" },
      });
    }

    // Всё, что не /api/* — статика сайта (index.html, shop.html и т.д.)
    return env.ASSETS.fetch(request);
  },
};
