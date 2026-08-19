// Единый каталог товаров — редактируйте только здесь, всё остальное берёт цены отсюда.
// Ключ product — именно то, что видно в API, на сайте и в базе (vip, vip+, mvp, mvp+, mvp++).
// luckpermsGroup — отдельное безопасное имя LuckPerms-группы без "+",
// т.к. символ "+" в имени группы у LuckPerms не гарантированно корректно работает
// (используется как разделитель/спецсимвол в некоторых командах и парсерах).
export const PRODUCTS = {
  "vip":     { label: "VIP",    priceUsd: 2,  luckpermsGroup: "vip" },
  "vip+":    { label: "VIP+",   priceUsd: 4,  luckpermsGroup: "vipplus" },
  "mvp":     { label: "MVP",    priceUsd: 8,  luckpermsGroup: "mvp" },
  "mvp+":    { label: "MVP+",   priceUsd: 14, luckpermsGroup: "mvpplus" },
  "mvp++":   { label: "MVP++",  priceUsd: 20, luckpermsGroup: "mvpplusplus" },
};

// Допустимые периоды покупки и скидка на них
export const DISCOUNTS = {
  1: 0,
  3: 0.10,
  6: 0.20,
  12: 0.30,
};

export function isValidProduct(product) {
  return Object.prototype.hasOwnProperty.call(PRODUCTS, product);
}

// Свободный донат без привилегии — просто "спасибо" и статус в базе,
// без записи в очередь на выдачу команд.
export function isValidCustomAmount(amount) {
  const n = Number(amount);
  return Number.isFinite(n) && n >= 1 && n <= 10000;
}

export function isValidMonths(months) {
  return Object.prototype.hasOwnProperty.call(DISCOUNTS, Number(months));
}

// Цена всегда считается на сервере — фронтенду нельзя доверять
export function calculatePrice(product, months) {
  const p = PRODUCTS[product];
  const discount = DISCOUNTS[Number(months)];
  const raw = p.priceUsd * Number(months);
  const final = raw * (1 - discount);
  // округляем до цента
  return Math.round(final * 100) / 100;
}

// Валидный никнейм Minecraft: 3-16 символов, латиница/цифры/подчёркивание
export function isValidNick(nick) {
  return typeof nick === "string" && /^[A-Za-z0-9_]{3,16}$/.test(nick);
}

// Строит консольную команду выдачи привилегии через LuckPerms.
// Группа выдаётся как временная (addtemp) на months*30 дней, accumulate — чтобы
// повторные покупки одного игрока продлевали срок, а не перезаписывали его.
export function buildDeliveryCommand(product, months, playerNick) {
  const p = PRODUCTS[product];
  const days = Number(months) * 30;
  return `lp user ${playerNick} parent addtemp ${p.luckpermsGroup} ${days}d accumulate`;
}
