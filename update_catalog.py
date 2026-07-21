#!/usr/bin/env python3
"""
Обновляет README.md каталогом из RAW_CATALOGUE.md:
  1. Находит в RAW_CATALOGUE.md записи, которых ещё нет в README.md
     (сравнение по нормализованному названию + платформе).
  2. Для новых записей запрашивает Main Story время на howlongtobeat.com
     через пакет howlongtobeatpy и подставляет его в колонку HLTB.
  3. Вставляет новые строки в таблицу раздела "📋 Следующее", сохраняя
     текущий порядок сортировки файла (цифры/спецсимволы -> кириллица ->
     латиница -> CJK, внутри группы - без учёта регистра).

Использование:
    pip install howlongtobeatpy   # один раз
    python3 update_catalog.py [--dry-run] [--threshold 0.4]

Требует сетевой доступ к howlongtobeat.com.
"""
import argparse
import asyncio
import re
import sys
from pathlib import Path

try:
    from howlongtobeatpy import HowLongToBeat
except ImportError:
    print("Нужен пакет howlongtobeatpy: pip install howlongtobeatpy", file=sys.stderr)
    raise

README_PATH = Path(__file__).parent / "README.md"
RAW_PATH = Path(__file__).parent / "RAW_CATALOGUE.md"

TARGET_SECTION = "📋 Следующее"

# Записи из RAW_CATALOGUE.md, которые по названию не совпадают буквально
# с уже существующей строкой README, но по факту — та же игра/копия,
# которая уже учтена (например под другим платформенным ярлыком).
# raw-название -> причина (для отчёта); такие записи не добавляются.
MANUAL_SKIP = {
    "Scott Pilgrim vs The World": (
        "уже учтена как «Scott Pilgrim vs. The World: The Game» "
        "(платформа «XBOX / Steam») в разделе Прошел"
    ),
}

# Русские/кириллические локализованные названия и другие случаи, где
# поисковый запрос к HLTB нужно заменить на англоязычный оригинал.
# Влияет только на поиск HLTB, отображаемое в README название не меняется.
HLTB_SEARCH_ALIASES = {
    "Ведьмак 3: Дикая Охота": "The Witcher 3: Wild Hunt",
    "ЗВЁЗДНЫЕ ВОЙНЫ Джедаи: Павший Орден™": "Star Wars Jedi: Fallen Order",
    "Хаус Флиппер": "House Flipper",
    "Хогвартс. Наследие": "Hogwarts Legacy",
    "King’s Bounty. Легенда о рыцаре": "King's Bounty: The Legend",
    "Legacy of Kain™ Soul Reaver 1&2 Remastered": "Legacy of Kain: Soul Reaver 1 & 2 Remastered",
    "Serious Sam Fusion 2017 (beta)": "Serious Sam Fusion 2017",
    "ZONE OF THE ENDERS THE 2nd RUNNER : MARS / ANUBIS ZONE OF THE ENDERS : MARS": (
        "Zone of the Enders: The 2nd Runner"
    ),
    "Command & Conquer 3: Ярость Кейна": "Command & Conquer 3: Kane's Wrath",
    "Command & Conquer 4: Эпилог": "Command & Conquer 4: Tiberian Twilight",
    "Command & Conquer Red Alert™ 2 and Yuri’s Revenge™": "Command & Conquer: Red Alert 2",
    "Command & Conquer Red Alert™, Counterstrike™ and The Aftermath™": (
        "Command & Conquer: Red Alert"
    ),
    "Command & Conquer™ Red Alert™ 3- Uprising": "Command & Conquer: Red Alert 3 - Uprising",
    "FINAL FANTASY VII (2013)": "Final Fantasy VII",
}

# Записи, для которых на HLTB нет и не будет отдельного HLTB-времени
# (например Steam-плейтесты) — не выводятся в отчёт "требует проверки".
HLTB_NOT_APPLICABLE = {
    "Perceptum Playtest",
}


def clean_query(name: str) -> str:
    n = name.replace("™", "").replace("®", "").replace("©", "")
    n = n.replace("’", "'").replace("‘", "'")
    n = re.sub(r"\s+", " ", n)
    return n.strip()


def normalize_name(name: str) -> str:
    n = name.strip()
    n = n.replace("’", "'").replace("‘", "'").replace("´", "'")
    n = n.replace("“", '"').replace("”", '"')
    n = n.replace("—", "-").replace("–", "-")
    n = re.sub(r"[™®©]", "", n)
    n = re.sub(r"\s+", " ", n)
    return n.strip().casefold()


def platform_tokens(platform: str) -> set:
    return {t for t in re.split(r"[^a-zA-Zа-яА-Я0-9]+", platform.casefold()) if t}


def sort_category(first_char: str) -> int:
    if not first_char:
        return 2
    if first_char.isdigit():
        return 0
    if "Ѐ" <= first_char <= "ӿ":
        return 1
    if ("぀" <= first_char <= "ヿ") or ("一" <= first_char <= "鿿") or (
        "가" <= first_char <= "힣"
    ):
        return 3
    return 2


def sort_key(name: str):
    n = name.strip()
    first = n[0] if n else ""
    return (sort_category(first), n.casefold())


def parse_pipe_row(line: str):
    return [c.strip() for c in line.strip().strip("|").split("|")]


def find_tables(lines):
    """Возвращает список таблиц: (section, header_idx, sep_idx, data_start, data_end)."""
    tables = []
    section = None
    i = 0
    while i < len(lines):
        line = lines[i]
        if line.startswith("## "):
            section = line[3:].strip()
        if (
            line.startswith("|")
            and re.fullmatch(r"\|[\s\-:|]+\|", line.strip())
            and i > 0
            and lines[i - 1].startswith("|")
        ):
            header_idx = i - 1
            sep_idx = i
            data_start = i + 1
            j = data_start
            while j < len(lines) and lines[j].startswith("|"):
                j += 1
            tables.append((section, header_idx, sep_idx, data_start, j))
            i = j
            continue
        i += 1
    return tables


def load_existing_steam_names(lines, tables):
    existing = set()
    for section, header_idx, sep_idx, data_start, data_end in tables:
        for row_line in lines[data_start:data_end]:
            cells = parse_pipe_row(row_line)
            if len(cells) < 2:
                continue
            name, platform = cells[0], cells[1]
            if "steam" in platform_tokens(platform):
                existing.add(normalize_name(name))
    return existing


def load_raw_rows(path: Path):
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.startswith("|"):
            continue
        cells = parse_pipe_row(line)
        if not cells or not cells[0]:
            continue
        rows.append(cells)
    return rows


async def _search_once(query: str):
    try:
        results = await HowLongToBeat().async_search(query)
    except Exception as exc:  # сеть/парсинг HLTB нестабильны, не роняем весь прогон
        return None, f"ошибка запроса: {exc}"
    if not results:
        return None, None
    best = max(results, key=lambda e: e.similarity)
    return best, None


async def lookup_hltb(name: str, threshold: float, sleep: float):
    primary = HLTB_SEARCH_ALIASES.get(name, clean_query(name))
    # для названий КАПСОМ поиск HLTB иногда чувствителен к регистру
    candidates = [primary]
    title_cased = primary.title()
    if title_cased != primary:
        candidates.append(title_cased)

    best_overall = None
    for query in candidates:
        await asyncio.sleep(sleep)
        best, err = await _search_once(query)
        if err:
            return None, err
        if best is not None and (best_overall is None or best.similarity > best_overall.similarity):
            best_overall = best
        if best_overall is not None and best_overall.similarity >= 0.95:
            break

    if best_overall is None:
        return None, "нет результатов на HLTB"
    if best_overall.similarity < threshold:
        return None, f"низкое сходство ({best_overall.similarity:.2f}) с «{best_overall.game_name}»"
    if best_overall.main_story in (None, 0):
        return None, f"у «{best_overall.game_name}» не указано Main Story"
    return (
        round(best_overall.main_story, 2),
        f"«{best_overall.game_name}» (сходство {best_overall.similarity:.2f})",
    )


async def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--readme", default=str(README_PATH))
    ap.add_argument("--raw", default=str(RAW_PATH))
    ap.add_argument("--dry-run", action="store_true", help="не менять README.md, только отчёт")
    ap.add_argument("--threshold", type=float, default=0.4, help="мин. сходство названия для HLTB")
    ap.add_argument("--sleep", type=float, default=0.6, help="пауза между запросами к HLTB, сек")
    args = ap.parse_args()

    readme_path = Path(args.readme)
    raw_path = Path(args.raw)

    lines = readme_path.read_text(encoding="utf-8").splitlines()
    tables = find_tables(lines)

    existing = load_existing_steam_names(lines, tables)
    raw_rows = load_raw_rows(raw_path)

    target = next((t for t in tables if t[0] == TARGET_SECTION), None)
    if target is None:
        print(f"Не найден раздел «{TARGET_SECTION}» в {readme_path}", file=sys.stderr)
        sys.exit(1)
    _, header_idx, sep_idx, data_start, data_end = target

    seen = set()
    new_entries = []
    skipped_manual = []
    skipped_dupe_in_raw = []
    for cells in raw_rows:
        name, platform = cells[0], cells[1] if len(cells) > 1 else "STEAM"
        norm = normalize_name(name)
        if norm in seen:
            skipped_dupe_in_raw.append(name)
            continue
        seen.add(norm)
        if name in MANUAL_SKIP:
            skipped_manual.append((name, MANUAL_SKIP[name]))
            continue
        if norm in existing:
            continue
        new_entries.append((name, platform))

    print(f"Всего строк в RAW_CATALOGUE.md: {len(raw_rows)}")
    print(f"Уже учтено в README (Steam): {len(existing)}")
    print(f"Новых записей для добавления: {len(new_entries)}")
    if skipped_manual:
        print("\nПропущено вручную (уже учтено под другим названием):")
        for name, reason in skipped_manual:
            print(f"  - {name}: {reason}")
    if skipped_dupe_in_raw:
        print(f"\nДубликаты внутри RAW_CATALOGUE.md (пропущены): {skipped_dupe_in_raw}")

    print("\nЗапрашиваю HLTB (Main Story)...")
    hltb_values = {}
    review_needed = []
    for name, platform in new_entries:
        value, info = await lookup_hltb(name, args.threshold, args.sleep)
        hltb_values[name] = value
        status = f"{value}" if value is not None else "—"
        print(f"  {name!r} -> {status}  [{info}]")
        if value is None and name not in HLTB_NOT_APPLICABLE:
            review_needed.append((name, info))

    new_lines = [
        f"| {name} | {platform} | {hltb_values[name] if hltb_values[name] is not None else ''} | | |"
        for name, platform in new_entries
    ]

    existing_data_lines = lines[data_start:data_end]
    combined = existing_data_lines + new_lines
    combined_sorted = sorted(combined, key=lambda l: sort_key(parse_pipe_row(l)[0]))

    new_full_lines = lines[:data_start] + combined_sorted + lines[data_end:]

    print(f"\nИтого строк в разделе «{TARGET_SECTION}»: {len(combined_sorted)} "
          f"(было {len(existing_data_lines)}, добавлено {len(new_lines)})")

    if review_needed:
        print("\nТребуют ручной проверки HLTB (оставлено пустым):")
        for name, info in review_needed:
            print(f"  - {name}: {info}")

    if args.dry_run:
        print("\n--dry-run: README.md не изменён.")
        return

    readme_path.write_text("\n".join(new_full_lines) + "\n", encoding="utf-8")
    print(f"\nREADME.md обновлён: {readme_path}")


if __name__ == "__main__":
    asyncio.run(main())
