# THE LAB — автодонат: инструкция по развёртыванию (Cloudflare Workers)

Что у вас получится: сайт на **Cloudflare Workers** (статика + API в одном Worker'е)
со страницей `/shop.html`, оплата через NOWPayments (крипта), и Java-плагин
`AutoDonate` на вашем Paper 1.21.11 сервере, который сам стучится на сайт раз
в 15 секунд, забирает купленные привилегии и выдаёт их через LuckPerms.

Порядок действий важен — идите строго по разделам сверху вниз.

---

## 0. Что нужно заранее

- Аккаунт на [Cloudflare](https://dash.cloudflare.com)
- Аккаунт на [nowpayments.io](https://nowpayments.io) — с подтверждённым кошельком для вывода крипты
- Node.js 18+ на вашем компьютере (для `wrangler`, консольной утилиты Cloudflare)
- Java 21 и Maven на вашем компьютере (для сборки плагина) — или просто скачайте готовый `.jar`, если я вам его пришлю отдельно
- LuckPerms уже установлен на сервере (или установите — см. раздел 5)

---

## 1. NOWPayments — получаем ключи

1. Зайдите в [личный кабинет NOWPayments](https://account.nowpayments.io) → **Store settings**.
2. **Outcome wallet** — укажите свой некастодиальный крипто-кошелёк (куда будут падать деньги). Обязательно перед тем как принимать платежи.
3. Сгенерируйте **API key** — сохраните его, он нужен в разделе 3.
4. Сгенерируйте **IPN Secret key** (там же, в Store settings) — он показывается **один раз**, сразу сохраните в надёжное место. Он тоже нужен в разделе 3.
5. Включите приём нужных вам монет во вкладке **Coins settings** (например USDT TRC20 — низкая комиссия за перевод, это стоит учитывать для мелких донатов).

> ⚠️ Не путайте продовый и sandbox-аккаунты — у sandbox.nowpayments.io свои отдельные ключи, они не взаимозаменяемы.

---

## 2. Cloudflare — создаём базу D1

Установите wrangler и авторизуйтесь:

```bash
npm install -g wrangler
wrangler login
```

Создайте базу данных:

```bash
wrangler d1 create thelab-donate
```

Команда выведет что-то вроде:

```
[[d1_databases]]
binding = "DB"
database_name = "thelab-donate"
database_id = "1a2b3c4d-....."
```

Скопируйте `database_id` и вставьте его в файл `wrangler.toml` (в корне проекта),
заменив `PASTE_DATABASE_ID_HERE`.

Примените схему таблиц:

```bash
cd site   # папка с index.html, shop.html, wrangler.toml, src/
wrangler d1 execute thelab-donate --file=./schema.sql --remote
```

---

## 3. Cloudflare Workers — деплой сайта

Структура проекта теперь такая:

```
site/
├── index.html          — главная страница
├── shop.html            — страница магазина
├── wrangler.toml         — конфиг Worker'а (main + assets + D1)
├── schema.sql            — схема базы D1
├── .assetsignore         — что НЕ отдавать как публичную статику
└── src/
    ├── worker.js          — точка входа: роутер /api/* + отдача статики
    ├── lib/
    │   ├── products.js     — каталог товаров, цены, скидки
    │   └── verify.js        — проверка подписи NOWPayments IPN
    └── handlers/
        ├── checkout.js
        ├── ipn.js
        ├── order-status.js
        └── queue.js
```

### Деплой вручную (проще всего для первого раза)

```bash
cd site
wrangler deploy
```

Это стандартная команда для Worker'ов — никаких флагов `--project-name` или
кастомных Deploy command не нужно, `wrangler.toml` уже содержит всё необходимое
(`main = "src/worker.js"` и `[assets]`).

### Если подключаете через Git (Workers Builds)

В дашборде: **Workers & Pages → Create → Workers → Connect to Git** → выберите репозиторий.
На этапе настройки сборки:
- **Build command**: оставьте пустым (сборка не нужна, это чистый JS без компиляции)
- **Deploy command**: можно оставить пустым/по умолчанию — раз в `wrangler.toml`
  уже есть `main` и `[assets]`, Cloudflare сам корректно выполнит `wrangler deploy`.

### Привязываем базу D1

В панели Cloudflare: **Workers & Pages → ваш Worker → Bindings** (отдельная вкладка
прямо на странице проекта, не спрятана внутри Settings) **→ Add binding → D1 database**:
- **Variable name**: `DB`
- **D1 database**: `thelab-donate`

Если деплоите через `wrangler deploy`, биндинг из `wrangler.toml` подхватится
автоматически — можно просто свериться в дашборде, что он появился после первого деплоя.

### Задаём секретные переменные

Через терминал (самый надёжный способ для Workers):

```bash
wrangler secret put NOWPAYMENTS_API_KEY
wrangler secret put NOWPAYMENTS_IPN_SECRET
wrangler secret put SERVER_SECRET
```

Для каждой команды wrangler спросит значение в интерактивном режиме — вставьте
соответствующий ключ (для `SERVER_SECRET` придумайте длинную случайную строку,
например результат `openssl rand -hex 32`). Секреты не появляются в логах и в
`wrangler.toml`, их не нужно коммитить.

Либо то же самое через дашборд: **ваш Worker → Settings → Variables and Secrets
→ Add → Secret**.

### Указываем IPN-адрес в NOWPayments (необязательно, но полезно)

В коде уже передаётся `ipn_callback_url` при создании каждого счёта, так что отдельно
в кабинете NOWPayments ничего указывать не обязательно. Но можно дополнительно
прописать дефолтный IPN URL в Store settings как подстраховку:
`https://ваш-worker.workers.dev/api/ipn` (или ваш кастомный домен, если подключите).

---

## 4. Проверяем, что бэкенд работает

Откройте `https://ваш-worker.workers.dev/shop.html`, введите тестовый ник, нажмите
«Купить» на любом тарифе. Вас должно перекинуть на страницу оплаты NOWPayments.

Если вместо этого ошибка — проверьте:
- `nowpayments_error` → неверный `NOWPAYMENTS_API_KEY` или не настроен outcome wallet
- сайт вообще не отвечает на `/api/checkout` → не привязана база D1 (см. раздел 3) или секреты не заданы
- страница вообще не открывается → проверьте, что `.assetsignore` не исключил лишнее, и что деплой прошёл без ошибок (`wrangler deploy` покажет ссылку на сайт в конце)

Проверить, что деньги дойдут до сервера, можно куда быстрее через саму NOWPayments —
у них есть **sandbox** с тестовыми платежами без реальных денег. Полное описание — в
разделе 4.1 ниже.

---

## 4.1. Полный тестовый прогон через NOWPayments Sandbox (без реальных денег)

Это отдельный, изолированный от продакшена контур — у него свой сайт для регистрации,
свои ключи, свой API-домен. Пройдя тест здесь, вы проверяете весь цикл целиком:
создание счёта → оплата → IPN-вебхук → запись в очередь → (при желании) выдача плагином.

1. Зарегистрируйтесь на **[account-sandbox.nowpayments.io](https://account-sandbox.nowpayments.io)** — это отдельный аккаунт, не тот же самый, что для реальных платежей.
2. В Store settings sandbox-аккаунта укажите любой outcome wallet (можно тестовый/любой валидный адрес — реальные средства всё равно никуда не уйдут) и сгенерируйте **sandbox API key** и **sandbox IPN Secret**.
3. Временно переключите секреты вашего Worker'а на sandbox-значения:

```bash
wrangler secret put NOWPAYMENTS_API_KEY
# вставьте SANDBOX API key

wrangler secret put NOWPAYMENTS_IPN_SECRET
# вставьте SANDBOX IPN Secret

wrangler secret put NOWPAYMENTS_API_BASE
# вставьте: https://api-sandbox.nowpayments.io/v1
```

4. Откройте `https://ваш-домен.workers.dev/shop.html`, купите любой тариф с тестовым ником.
5. Вас перекинет на хостед-страницу sandbox-инвойса — она сама эмулирует прохождение оплаты, реальную крипту слать не нужно.
6. Проверьте в D1, что заказ дошёл до статуса `paid`/`delivered` и появилась команда в очереди:

```bash
wrangler d1 execute thelab-donate --remote --command="SELECT * FROM orders ORDER BY created_at DESC LIMIT 5"
wrangler d1 execute thelab-donate --remote --command="SELECT * FROM delivery_queue ORDER BY id DESC LIMIT 5"
```

7. Если хотите проверить и сторону майнкрафт-плагина — просто дайте ему поработать
   ещё пару опросов (`/autodonate poll` в консоли сервера), команда должна выполниться
   и исчезнуть из очереди (`status` станет `done`).

**Обязательно верните боевые значения перед стартом на реальных деньгах:**

```bash
wrangler secret put NOWPAYMENTS_API_KEY
# вставьте ПРОДОВЫЙ API key

wrangler secret put NOWPAYMENTS_IPN_SECRET
# вставьте ПРОДОВЫЙ IPN Secret

wrangler secret delete NOWPAYMENTS_API_BASE
```

Последняя команда удаляет override — без неё код сам вернётся на боевой
`https://api.nowpayments.io/v1` (это значение по умолчанию в коде).

> ⚠️ Тестовые заказы из sandbox останутся в вашей боевой базе D1 как обычные записи
> (с реальными на вид order_id). Это не проблема для работы системы, но если хотите
> чистоты — удалите их вручную после тестов:
> `wrangler d1 execute thelab-donate --remote --command="DELETE FROM orders WHERE player_nick='ваш_тестовый_ник'"`
> (delivery_queue и processed_ipn можно почистить аналогично при желании).

---

## 5. LuckPerms на сервере

Если ещё не установлен — скачайте `LuckPerms-Bukkit-*.jar` с
[luckperms.net/download](https://luckperms.net/download) и киньте в `plugins/`.

Коды товаров на сайте и в API — именно `vip`, `vip+`, `mvp`, `mvp+`, `mvp++` (так, как
вы их и назвали). Но у самого LuckPerms символ `+` в имени группы не гарантированно
корректно работает, поэтому внутри бэкенда каждому коду товара сопоставлена
отдельная, "безопасная" группа:

| Код товара (сайт/API) | Группа LuckPerms |
|---|---|
| `vip`   | `vip` |
| `vip+`  | `vipplus` |
| `mvp`   | `mvp` |
| `mvp+`  | `mvpplus` |
| `mvp++` | `mvpplusplus` |

Эта таблица зашита в `src/lib/products.js` (поле `luckpermsGroup`) — если
захотите назвать группы иначе, меняйте только там, больше нигде.

Создайте группы именно с этими именами (не с `+`):

```
lp creategroup vip
lp creategroup vipplus
lp creategroup mvp
lp creategroup mvpplus
lp creategroup mvpplusplus
```

Настройте права/наследование групп под себя (например `mvpplusplus` наследует
`mvpplus`, тот наследует `mvp` и т.д.) — это уже вопрос игрового баланса привилегий,
не автодоната как такового:

```
lp group mvpplus parent add mvp
lp group mvpplusplus parent add mvpplus
```

Плагин выдаёт группу командой вида:

```
lp user Notch parent addtemp vip 30d accumulate
```

`accumulate` — если игрок купит VIP ещё раз до истечения срока, время **прибавится**,
а не перезапишется. Это то поведение, которое обычно и нужно для донат-систем.

---

## 6. Сборка и установка плагина AutoDonate

### Собираем jar

На своём компьютере (не обязательно на сервере):

```bash
cd plugin
mvn clean package
```

Готовый файл появится в `plugin/target/autodonate-1.0.0.jar`.

### Устанавливаем на сервер

1. Скопируйте `autodonate-1.0.0.jar` в папку `plugins/` вашего Paper 1.21.11 сервера.
2. Запустите сервер один раз, чтобы плагин создал `plugins/AutoDonate/config.yml`.
3. Остановите сервер и отредактируйте `plugins/AutoDonate/config.yml`:

```yaml
api-url: "https://ваш-worker.workers.dev"
server-secret: "то же значение, что вы задали через wrangler secret put SERVER_SECRET"
poll-interval-seconds: 15
http-timeout-seconds: 10
```

4. Запустите сервер снова. В консоли должно появиться:
   `[AutoDonate] AutoDonate включён. Опрос очереди каждые 15 сек.`

### Проверка

- `/autodonate status` — покажет когда плагин последний раз опрашивал очередь
- `/autodonate poll` — форсирует опрос прямо сейчас, не дожидаясь таймера
- `/autodonate reload` — перечитать config.yml без рестарта сервера

Если после тестовой оплаты привилегия не пришла в течение минуты — смотрите логи
сервера на `[AutoDonate]`, там будет видно либо `401 Unauthorized` (не совпадает
секрет), либо сетевую ошибку (сервер не может достучаться до Cloudflare — проверьте
исходящий интернет с хостинга сервера).

---

## 7. Полный путь одной оплаты — как всё это работает вместе

1. Игрок открывает `/shop.html`, выбирает тариф и срок, вводит ник → жмёт «Купить»
2. Worker (`/api/checkout`) создаёт заказ в D1 со статусом `pending` и запрашивает
   счёт в NOWPayments → игрока перекидывает на страницу оплаты
3. Игрок платит крипту
4. NOWPayments шлёт вебхук на `/api/ipn` → Worker проверяет подпись, ставит заказу
   статус `paid` и кладёт готовую команду LuckPerms в очередь (`delivery_queue`)
5. Плагин на сервере (раз в 15 сек) стучится на `/api/queue`, видит новую команду,
   выполняет её в консоли, отчитывается обратно — заказ помечается `delivered`
6. Игрок заходит на сервер (или уже онлайн) — привилегия уже выдана

---

## 8. На будущее: интеграция Tebex

Архитектура уже это учитывает: и `orders`, и `delivery_queue` не привязаны к
конкретному способу оплаты. Когда будете добавлять Tebex, есть два пути:

**Вариант А (проще)**: ставите официальный плагин Tebex (BuycraftX) рядом с
AutoDonate. Они не конфликтуют — просто у вас будет два независимых канала оплаты
(крипта через свой сайт, карты через Tebex), группы LuckPerms общие для обоих.

**Вариант Б (чище архитектурно)**: добавляете обработчик вебхука Tebex как ещё
один роут в `src/worker.js` (`/api/webhook/tebex`), который парсит вебхук и кладёт
команду в ту же таблицу `delivery_queue`. Тогда единственная точка выдачи привилегий
на сервере остаётся одна — ваш плагин AutoDonate, а Tebex используется только как
приём платежей.

Если решите делать вариант Б — напишите, соберу такой обработчик по образцу `ipn.js`.

---

## Чеклист перед стартом на реальных деньгах

- [ ] `NOWPAYMENTS_API_KEY`, `NOWPAYMENTS_IPN_SECRET`, `SERVER_SECRET` заданы через `wrangler secret put`
- [ ] `database_id` в `wrangler.toml` — настоящий, не заглушка
- [ ] Схема `schema.sql` применена к `--remote` базе (не только локально)
- [ ] D1-биндинг `DB` привязан к Worker'у (видно во вкладке Bindings)
- [ ] Outcome wallet в NOWPayments указан и проверен
- [ ] `server-secret` в `config.yml` плагина совпадает 1-в-1 с тем, что задали через `wrangler secret put`
- [ ] Группы LuckPerms созданы с именами `vip`, `vipplus`, `mvp`, `mvpplus`, `mvpplusplus`
- [ ] Прогнан полный тест через NOWPayments Sandbox (раздел 4.1) — заказ дошёл до `delivered`
- [ ] После sandbox-теста секреты возвращены на боевые (`NOWPAYMENTS_API_KEY`, `NOWPAYMENTS_IPN_SECRET`), `NOWPAYMENTS_API_BASE` удалён (`wrangler secret delete`)
- [ ] Сделан один тестовый платёж на минимальную сумму и проверено, что привилегия реально пришла
