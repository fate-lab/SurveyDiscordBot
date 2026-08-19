// Проверка подписи NOWPayments IPN.
// Алгоритм по документации NOWPayments: рекурсивно сортируем ключи JSON,
// сериализуем без пробелов, считаем HMAC-SHA512 с IPN Secret, сравниваем
// с заголовком x-nowpayments-sig.

function sortObjectDeep(obj) {
  if (Array.isArray(obj)) {
    return obj.map(sortObjectDeep);
  }
  if (obj !== null && typeof obj === "object") {
    return Object.keys(obj)
      .sort()
      .reduce((acc, key) => {
        acc[key] = sortObjectDeep(obj[key]);
        return acc;
      }, {});
  }
  return obj;
}

async function hmacSha512Hex(secret, message) {
  const enc = new TextEncoder();
  const key = await crypto.subtle.importKey(
    "raw",
    enc.encode(secret),
    { name: "HMAC", hash: "SHA-512" },
    false,
    ["sign"]
  );
  const sigBuf = await crypto.subtle.sign("HMAC", key, enc.encode(message));
  return [...new Uint8Array(sigBuf)].map((b) => b.toString(16).padStart(2, "0")).join("");
}

// bodyObj — уже распарсенный JSON.parse(rawBody). Важно валидировать по объекту,
// а не по сырой строке — NOWPayments сам сортирует ключи перед подписью на своей стороне.
export async function verifyNowPaymentsSignature(bodyObj, signatureHeader, ipnSecret) {
  if (!signatureHeader || !ipnSecret) return false;
  const sorted = sortObjectDeep(bodyObj);
  const sortedJson = JSON.stringify(sorted);
  const expected = await hmacSha512Hex(ipnSecret, sortedJson);
  return timingSafeEqual(expected, signatureHeader);
}

function timingSafeEqual(a, b) {
  if (a.length !== b.length) return false;
  let result = 0;
  for (let i = 0; i < a.length; i++) {
    result |= a.charCodeAt(i) ^ b.charCodeAt(i);
  }
  return result === 0;
}
