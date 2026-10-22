"""
Ритм → ritm.db для DBeaver.

Берёт свежую копию ritm-backup.json из папки Яндекс Диска на ноутбуке,
при необходимости расшифровывает её вашим паролем и собирает базу SQLite ritm.db.
Каждый раз база пересобирается заново, а сама копия откладывается в папку «архив» с датой.

Запуск: двойной клик по «Обновить базу.bat» или
    py ritm_to_dbeaver.py                      — найти копию автоматически
    py ritm_to_dbeaver.py путь\\к\\ritm-backup.json — взять конкретный файл

Нужен Python 3.9+. Для зашифрованных копий один раз: py -m pip install cryptography
"""
import base64
import getpass
import json
import os
import shutil
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent
DB_PATH = HERE / "ritm.db"
ARCHIVE = HERE / "архив"
NAME = "ritm-backup.json"
MEALS = {"breakfast": "Завтрак", "lunch": "Обед", "dinner": "Ужин", "snack": "Перекус"}


def find_backup() -> Path:
    """Ищет самый свежий ritm-backup.json в папках Яндекс Диска и в Загрузках."""
    home = Path.home()
    roots = [home / "YandexDisk", home / "Яндекс.Диск", home / "Yandex.Disk", home / "Downloads", home / "Загрузки"]
    for drive in "CDEF":
        roots += [Path(f"{drive}:/YandexDisk"), Path(f"{drive}:/Яндекс.Диск")]
    roots = [r for r in roots if r.is_dir()]
    found = []
    # сначала папка «Ритм» на Диске и Загрузки — быстро
    for root in roots:
        for base in (root / "Ритм", root / "Приложения" / "Ритм", root / "Applications" / "Ритм", root):
            if base.is_dir():
                found += [p for p in base.glob("ritm-backup*.json")]
    # не нашли — ищем по всему Диску
    if not found:
        for root in roots:
            found += [p for p in root.rglob("ritm-backup*.json") if "архив" not in p.parts]
    if not found:
        sys.exit("Не нашла ritm-backup.json. Проверьте, что Яндекс Диск на ноутбуке синхронизировался,\n"
                 "или перетащите файл копии на «Обновить базу.bat».")
    return max(found, key=lambda p: p.stat().st_mtime)


def decrypt(env: dict) -> dict:
    try:
        from cryptography.hazmat.primitives import hashes
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM
        from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
    except ImportError:
        sys.exit("Копия зашифрована. Один раз выполните в командной строке:\n    py -m pip install cryptography")
    password = os.environ.get("RITM_PASS") or getpass.getpass("Пароль шифрования (ввод не отображается): ")
    salt, iv, data = (base64.b64decode(env[k]) for k in ("salt", "iv", "data"))
    key = PBKDF2HMAC(algorithm=hashes.SHA256(), length=32, salt=salt, iterations=250000).derive(password.encode())
    try:
        return json.loads(AESGCM(key).decrypt(iv, data, None).decode("utf-8"))
    except Exception:
        sys.exit("Неверный пароль шифрования.")


SCHEMA = """
DROP VIEW IF EXISTS daily_totals;
DROP VIEW IF EXISTS weekly_weight;
DROP TABLE IF EXISTS food;
DROP TABLE IF EXISTS weight;
DROP TABLE IF EXISTS workouts;
DROP TABLE IF EXISTS water;
DROP TABLE IF EXISTS products;
DROP TABLE IF EXISTS profile;
DROP TABLE IF EXISTS info;
DROP TABLE IF EXISTS days;
DROP TABLE IF EXISTS recipe_items;
DROP TABLE IF EXISTS plan;

CREATE TABLE food (           -- дневник еды: одна строка = одна запись
  date TEXT, meal TEXT, name TEXT, amount REAL, unit TEXT,
  kcal REAL, protein REAL, fat REAL, carbs REAL);
CREATE TABLE weight (         -- взвешивания и замеры, см
  date TEXT PRIMARY KEY, kg REAL, waist REAL, hips REAL, chest REAL, arm REAL);
CREATE TABLE workouts (       -- тренировки; feel: 1 (тяжело) … 5 (отлично)
  date TEXT, type TEXT, minutes REAL, feel INTEGER, note TEXT);
CREATE TABLE water (date TEXT PRIMARY KEY, glasses INTEGER, liters REAL);
CREATE TABLE products (       -- мои продукты и блюда; значения на 100 г или на порцию
  name TEXT, brand TEXT, barcode TEXT, per TEXT, kcal REAL, protein REAL, fat REAL, carbs REAL,
  favorite INTEGER, times_used INTEGER);
CREATE TABLE profile (key TEXT PRIMARY KEY, value TEXT);
CREATE TABLE info (key TEXT PRIMARY KEY, value TEXT);
CREATE TABLE days (           -- самочувствие: сон в часах, энергия и настроение 1…5, месячные 0/1, заметка
  date TEXT PRIMARY KEY, sleep_hours REAL, energy INTEGER, mood INTEGER, period INTEGER, note TEXT);
CREATE TABLE recipe_items (   -- состав рецептов
  recipe TEXT, ingredient TEXT, amount REAL, unit TEXT, kcal REAL, protein REAL, fat REAL, carbs REAL);
CREATE TABLE plan (           -- расписание тренировок
  weekday TEXT, type TEXT, time TEXT, minutes REAL);

CREATE VIEW daily_totals AS   -- итоги по дням
SELECT d.date,
       ROUND(COALESCE(SUM(f.kcal),0)) AS kcal,
       ROUND(COALESCE(SUM(f.protein),0),1) AS protein,
       ROUND(COALESCE(SUM(f.fat),0),1) AS fat,
       ROUND(COALESCE(SUM(f.carbs),0),1) AS carbs,
       w.liters AS water_liters,
       (SELECT SUM(minutes) FROM workouts t WHERE t.date = d.date) AS workout_minutes,
       s.sleep_hours, s.energy, s.mood
FROM (SELECT date FROM food UNION SELECT date FROM water UNION SELECT date FROM workouts UNION SELECT date FROM days) d
LEFT JOIN food f ON f.date = d.date
LEFT JOIN water w ON w.date = d.date
LEFT JOIN days s ON s.date = d.date
GROUP BY d.date ORDER BY d.date;

CREATE VIEW weekly_weight AS  -- средний вес по неделям (понедельник — начало недели)
SELECT date(date, '-' || ((CAST(strftime('%w', date) AS INTEGER) + 6) % 7) || ' days') AS week_start,
       ROUND(AVG(kg), 2) AS avg_kg, COUNT(*) AS weighings
FROM weight GROUP BY week_start ORDER BY week_start;
"""


def build(data: dict, source: Path) -> dict:
    con = sqlite3.connect(DB_PATH)
    try:
        con.executescript("BEGIN;" + SCHEMA)
        food = [(d, MEALS.get(e.get("meal"), e.get("meal")), e.get("name"), e.get("qty"),
                 "порц." if e.get("unit") == "pc" else "г",
                 round(e.get("kcal", 0), 1), round(e.get("p", 0), 1), round(e.get("f", 0), 1), round(e.get("c", 0), 1))
                for d, items in sorted(data.get("foods", {}).items()) for e in items]
        con.executemany("INSERT INTO food VALUES (?,?,?,?,?,?,?,?,?)", food)
        con.executemany("INSERT OR REPLACE INTO weight VALUES (?,?,?,?,?,?)",
                        [(w["date"], w.get("kg"), w.get("waist"), w.get("hips"), w.get("chest"), w.get("arm"))
                         for w in data.get("weights", [])])
        con.executemany("INSERT INTO workouts VALUES (?,?,?,?,?)",
                        [(w.get("date"), w.get("type"), w.get("minutes"), w.get("feel"), w.get("note") or None)
                         for w in sorted(data.get("workouts", []), key=lambda x: x.get("date", ""))])
        con.executemany("INSERT INTO water VALUES (?,?,?)",
                        [(d, n, n * 0.25) for d, n in data.get("water", {}).items() if n])
        con.executemany("INSERT INTO products VALUES (?,?,?,?,?,?,?,?,?,?)",
                        [(x.get("name"), x.get("brand") or None, x.get("code") or None,
                          "порция" if x.get("per") == "pc" else "100 г",
                          x.get("kcal"), x.get("p"), x.get("f"), x.get("c"), int(bool(x.get("fav"))), x.get("uses", 0))
                         for x in data.get("library", [])])
        con.executemany("INSERT INTO days VALUES (?,?,?,?,?,?)",
                        [(d, r.get("sleep"), r.get("energy"), r.get("mood"), int(bool(r.get("period"))), r.get("note"))
                         for d, r in sorted(data.get("days", {}).items()) if r])
        ri = []
        for x in data.get("library", []):
            for g in (x.get("recipe") or {}).get("items", []):
                k = g.get("qty", 0) if g.get("per") == "pc" else g.get("qty", 0) / 100
                ri.append((x.get("name"), g.get("name"), g.get("qty"), "порц." if g.get("per") == "pc" else "г",
                           round(g.get("kcal", 0) * k, 1), round(g.get("p", 0) * k, 1), round(g.get("f", 0) * k, 1), round(g.get("c", 0) * k, 1)))
        con.executemany("INSERT INTO recipe_items VALUES (?,?,?,?,?,?,?,?)", ri)
        wd = ["пн", "вт", "ср", "чт", "пт", "сб", "вс"]
        con.executemany("INSERT INTO plan VALUES (?,?,?,?)",
                        [(wd[int(s.get("wd", 0))], s.get("type"), s.get("time"), s.get("minutes"))
                         for s in (data.get("plan") or {}).get("slots", [])])
        con.executemany("INSERT INTO profile VALUES (?,?)",
                        [(k, str(v)) for k, v in data.get("profile", {}).items() if v not in ("", None)])
        con.executemany("INSERT INTO info VALUES (?,?)", [
            ("копия_из_файла", str(source)),
            ("копия_сделана", data.get("exported") or datetime.fromtimestamp(source.stat().st_mtime).isoformat()),
            ("база_собрана", datetime.now().isoformat(timespec="seconds")),
        ])
        con.commit()
    finally:
        con.close()
    return {"еда": len(food), "вес": len(data.get("weights", [])), "тренировки": len(data.get("workouts", [])),
            "продукты": len(data.get("library", []))}


def main():
    source = Path(sys.argv[1]) if len(sys.argv) > 1 else find_backup()
    print(f"Копия: {source}")
    raw = json.loads(source.read_text(encoding="utf-8"))
    data = decrypt(raw) if raw.get("encrypted") else raw
    if "foods" not in data and "weights" not in data:
        sys.exit("Это не похоже на копию Ритма.")

    ARCHIVE.mkdir(exist_ok=True)
    stamp = datetime.fromtimestamp(source.stat().st_mtime).strftime("%Y-%m-%d_%H-%M")
    shutil.copy2(source, ARCHIVE / f"ritm-backup_{stamp}.json")

    counts = build(data, source)
    print("Готово:", ", ".join(f"{k} — {v}" for k, v in counts.items()))
    print(f"База: {DB_PATH}")
    print("В DBeaver нажмите F5 на подключении, чтобы увидеть свежие данные.")


if __name__ == "__main__":
    main()
