
#!/usr/bin/env python3
"""
АЛИЕРА Price Monitor
Парсит каталог Tilda Store через API и сохраняет prices.json.

API: https://store.tildacdn.com/api/getproductslist/?storepartuid={UID}&slice={N}&size=50
Товары без цены (price == 0) — «под заказ», не включаются.
"""

import json
import sys
import os
import time
from datetime import datetime, timezone, timedelta
from urllib.request import urlopen, Request
from urllib.error import URLError, HTTPError

# ── Настройки ──
STORE_PART_UID = "851427411052"  # Основной каталог aliera23.ru/catalog
API_BASE = "https://store.tildacdn.com/api/getproductslist/"
PAGE_SIZE = 50

OUTPUT_FILE = os.environ.get("OUTPUT_FILE", "prices.json")

# Служебные поля варианта товара — всё остальное считается названием опции
EDITION_SERVICE_KEYS = {
    "uid", "price", "priceold", "sku", "quantity", "img", "externalid",
    "text", "title", "descr", "url", "gallery", "unit", "portion", "single", "mark",
}


def fetch_all_products() -> list:
    """Загружает ВСЕ товары из каталога Tilda (пагинация по 50)."""
    all_products = []
    page = 1

    while True:
        url = f"{API_BASE}?storepartuid={STORE_PART_UID}&slice={page}&size={PAGE_SIZE}"
        print(f"  📦 Страница {page}...")

        req = Request(url, headers={
            "User-Agent": "AlieraBot/1.0",
            "Accept": "application/json",
        })

        data = None
        for attempt in range(1, 4):
            try:
                with urlopen(req, timeout=30) as resp:
                    raw = resp.read().decode("utf-8")
                    data = json.loads(raw, strict=False)
                break
            except (URLError, HTTPError, json.JSONDecodeError, TimeoutError) as e:
                print(f"  ⚠️  Попытка {attempt}/3 не удалась: {e}")
                time.sleep(5 * attempt)

        if data is None:
            # Не продолжаем: неполный каталог хуже, чем вчерашний prices.json
            raise RuntimeError(f"Не удалось загрузить страницу {page} каталога")

        products = data.get("products", [])
        if not products:
            break

        all_products.extend(products)
        total = data.get("total", "?")
        print(f"     Получено {len(products)} товаров (всего в каталоге: {total})")

        if len(products) < PAGE_SIZE:
            break

        page += 1

    return all_products


def parse_price(val) -> float:
    """Парсит цену из строки Tilda."""
    if val is None:
        return 0.0
    s = str(val).replace(",", ".").replace(" ", "").replace("\xa0", "")
    try:
        return float(s)
    except ValueError:
        return 0.0


def parse_product(raw: dict) -> dict | None:
    """
    Преобразует товар Tilda API в структуру prices.json.
    Возвращает None если товар без цены (под заказ).
    """
    title = (raw.get("title") or "").strip()
    if not title:
        return None

    price = parse_price(raw.get("price"))
    if price <= 0:
        return None

    old_price = parse_price(raw.get("priceold"))

    product = {
        "uid": str(raw.get("uid", "")),
        "title": title,
        "price": price,
        "sku": (raw.get("sku") or "").strip(),
        "brand": (raw.get("brand") or "").strip(),
        "descr": (raw.get("descr") or "").strip(),
        "url": raw.get("url", ""),
    }

    if old_price > 0:
        product["old_price"] = old_price

    # Характеристики (толщина, плотность и т.д.)
    chars = raw.get("characteristics") or []
    if chars:
        parsed_chars = {}
        for ch in chars:
            ch_title = (ch.get("title") or "").strip()
            ch_value = (ch.get("value") or "").strip()
            if ch_title and ch_value:
                parsed_chars[ch_title] = ch_value
        if parsed_chars:
            product["characteristics"] = parsed_chars

    # Варианты (editions): у Tilda название опции — это отдельный ключ варианта,
    # например {"price": "800.0000", "Размер": "10х120"}. Собираем все такие ключи.
    editions = raw.get("editions") or []
    if editions:
        parsed_eds = []
        for ed in editions:
            ed_price = parse_price(ed.get("price"))
            if ed_price <= 0:
                continue

            options = {
                str(k): str(v).strip()
                for k, v in ed.items()
                if k not in EDITION_SERVICE_KEYS
                and not str(k).startswith("pack_")
                and v not in (None, "")
                and not isinstance(v, (dict, list))
            }

            parsed_eds.append({
                "uid": str(ed.get("uid", "")),
                "option": " / ".join(options.values()),
                "options": options,
                "price": ed_price,
            })

        if parsed_eds:
            product["editions"] = parsed_eds

    return product


def main():
    msk = timezone(timedelta(hours=3))
    now = datetime.now(msk)

    print("=" * 50)
    print("🏗️  АЛИЕРА Price Monitor")
    print(f"📅 {now.strftime('%Y-%m-%d %H:%M:%S')} МСК")
    print("=" * 50)

    try:
        raw_products = fetch_all_products()
    except RuntimeError as e:
        print(f"❌ {e}. Файл не обновлён.")
        return 1

    print(f"\n📊 Всего загружено: {len(raw_products)} товаров")

    # Парсим только товары с ценой; uid товаров без цены сохраняем отдельно,
    # чтобы калькулятор показывал у них «цена по запросу», а не старую цену
    products = []
    no_price_uids = []
    skipped = 0

    for raw in raw_products:
        parsed = parse_product(raw)
        if parsed:
            products.append(parsed)
        else:
            skipped += 1
            if raw.get("uid"):
                no_price_uids.append(str(raw.get("uid")))

    # Убираем дубликаты по uid
    seen = set()
    unique = []

    for p in products:
        if p["uid"] not in seen:
            seen.add(p["uid"])
            unique.append(p)

    result = {
        "updated_at": now.strftime("%Y-%m-%d %H:%M:%S"),
        "timezone": "Europe/Moscow",
        "currency": "RUB",
        "source": "https://aliera23.ru/catalog",
        "total_in_catalog": len(raw_products),
        "total_with_price": len(unique),
        "total_no_price": skipped,
        "no_price_uids": sorted(set(no_price_uids) - seen),
        "products": unique,
    }

    # Защита: если товаров с ценой стало резко меньше, чем в прошлый раз —
    # скорее всего, сбой на стороне Tilda. Старый файл не трогаем.
    try:
        with open(OUTPUT_FILE, encoding="utf-8") as f:
            prev_count = len(json.load(f).get("products", []))
    except (OSError, ValueError):
        prev_count = 0

    if not unique or len(unique) < prev_count * 0.6:
        print(f"❌ Получено {len(unique)} товаров с ценой, в прошлый раз было {prev_count}. Файл не обновлён.")
        return 1

    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)

    print(f"\n✅ Сохранено: {len(unique)} товаров с ценами")
    print(f"⏭️  Пропущено: {skipped} товаров без цены (под заказ)")
    print(f"📁 Файл: {OUTPUT_FILE}")

    # Диапазон цен
    prices = [p["price"] for p in unique]
    if prices:
        print(f"💰 Цены: {min(prices):.0f} – {max(prices):.0f} ₽")

    return 0


if __name__ == "__main__":
    sys.exit(main())
