"""Розбір файлів рубрик criteria/<дисципліна>.md і переведення балів в оцінку.

Формат рубрики (Markdown):
  ## Шкала переведення           — таблиця «Бали (зі 100) | Оцінка | ECTS», діапазони «90-100»
  ## Вид роботи: <назва>          — далі таблиця «№ | Критерій | Макс. бали | Що оцінюється»
  ### Типові помилки               — маркований список (усередині виду роботи)
  ## Доповнення викладача          — маркований список, спільний для всіх видів робіт

Тут лише чисті функції без доступу до мережі.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path


class RubricError(ValueError):
    pass


@dataclass
class Criterion:
    number: int
    name: str
    max_points: int
    description: str


@dataclass
class ScaleRow:
    low: int
    high: int
    grade: str
    ects: str


@dataclass
class WorkType:
    name: str
    criteria: list[Criterion] = field(default_factory=list)
    typical_errors: list[str] = field(default_factory=list)

    @property
    def total_max(self) -> int:
        return sum(c.max_points for c in self.criteria)


@dataclass
class Rubric:
    title: str
    scale: list[ScaleRow]
    work_types: dict[str, WorkType]
    teacher_notes: list[str]

    def work_type(self, name: str) -> WorkType:
        key = name.strip().lower()
        if key not in self.work_types:
            known = ", ".join(self.work_types)
            raise RubricError(f"У рубриці «{self.title}» немає виду роботи «{name}». Є: {known}")
        return self.work_types[key]


def _table_rows(lines: list[str]) -> list[list[str]]:
    """Рядки Markdown-таблиці без заголовка і роздільника."""
    rows = []
    for line in lines:
        s = line.strip()
        if not s.startswith("|"):
            continue
        cells = [c.strip() for c in s.strip("|").split("|")]
        if all(re.fullmatch(r":?-{2,}:?", c) for c in cells if c):
            continue
        rows.append(cells)
    return rows[1:] if rows else []  # перший рядок — заголовок


def _bullets(lines: list[str]) -> list[str]:
    out = []
    for line in lines:
        m = re.match(r"\s*[-*]\s+(.*)", line)
        if m and m.group(1).strip() and m.group(1).strip() != "(поки порожньо)":
            out.append(m.group(1).strip())
    return out


def parse_rubric(text: str) -> Rubric:
    title_m = re.search(r"^#\s+(.+)$", text, re.M)
    title = title_m.group(1).strip() if title_m else "Рубрика"

    # Розбиваємо на секції за заголовками ## і ###
    sections: list[tuple[int, str, list[str]]] = []
    for line in text.splitlines():
        m = re.match(r"^(#{2,3})\s+(.+?)\s*$", line)
        if m:
            sections.append((len(m.group(1)), m.group(2), []))
        elif sections:
            sections[-1][2].append(line)

    scale: list[ScaleRow] = []
    work_types: dict[str, WorkType] = {}
    teacher_notes: list[str] = []
    current: WorkType | None = None

    for level, heading, body in sections:
        h = heading.lower()
        if level == 2:
            current = None
            if h.startswith("шкала переведення"):
                for cells in _table_rows(body):
                    if len(cells) < 3:
                        raise RubricError(f"Рядок шкали має бути «бали | оцінка | ECTS»: {cells}")
                    m = re.fullmatch(r"(\d+)\s*[-–]\s*(\d+)", cells[0])
                    if not m:
                        raise RubricError(f"Діапазон балів має вигляд «90-100», а не «{cells[0]}»")
                    scale.append(ScaleRow(int(m.group(1)), int(m.group(2)), cells[1], cells[2]))
            elif h.startswith("вид роботи:"):
                name = heading.split(":", 1)[1].strip().lower()
                current = WorkType(name)
                for cells in _table_rows(body):
                    if len(cells) < 3:
                        raise RubricError(f"Рядок критерію має бути «№ | критерій | макс. бали | опис»: {cells}")
                    try:
                        num, pts = int(cells[0]), int(cells[2])
                    except ValueError as e:
                        raise RubricError(f"Некоректні номер або бали в рядку {cells}") from e
                    current.criteria.append(
                        Criterion(num, cells[1], pts, cells[3] if len(cells) > 3 else ""))
                if not current.criteria:
                    raise RubricError(f"Вид роботи «{name}» не має таблиці критеріїв")
                work_types[name] = current
            elif h.startswith("доповнення викладача"):
                teacher_notes = _bullets(body)
        elif level == 3 and current is not None and h.startswith("типові помилки"):
            current.typical_errors = _bullets(body)

    if not scale:
        raise RubricError("У рубриці немає розділу «## Шкала переведення»")
    if not work_types:
        raise RubricError("У рубриці немає жодного розділу «## Вид роботи: …»")
    validate_scale(scale)
    for wt in work_types.values():
        if wt.total_max != 100:
            raise RubricError(
                f"Сума максимальних балів для «{wt.name}» = {wt.total_max}, має бути 100")
    return Rubric(title, scale, work_types, teacher_notes)


def load_rubric(path: str | Path) -> Rubric:
    p = Path(path)
    if not p.exists():
        raise RubricError(f"Файл рубрики {p} не знайдено. Створіть його на основі положення в criteria/.")
    return parse_rubric(p.read_text(encoding="utf-8"))


def validate_scale(scale: list[ScaleRow]) -> None:
    """Шкала має покривати 0..100 без пропусків і перетинів."""
    rows = sorted(scale, key=lambda r: r.low)
    expected = 0
    for r in rows:
        if r.low > r.high:
            raise RubricError(f"Діапазон {r.low}-{r.high} задано навпаки")
        if r.low != expected:
            raise RubricError(f"Шкала має пропуск або перетин біля {expected} балів")
        expected = r.high + 1
    if expected != 101:
        raise RubricError("Шкала має закінчуватись на 100 балах")


@dataclass
class GradeResult:
    total: int
    grade: str
    ects: str


def score_to_grade(total: float, scale: list[ScaleRow]) -> GradeResult:
    """Переводить суму балів (0..100) в оцінку. Дробові бали округлюються до цілого (0.5 — вгору)."""
    if total < 0 or total > 100:
        raise RubricError(f"Сума балів {total} поза межами 0..100")
    t = int(total + 0.5)
    for r in scale:
        if r.low <= t <= r.high:
            return GradeResult(t, r.grade, r.ects)
    raise RubricError(f"Для {t} балів немає рядка у шкалі")


def numeric_grade(grade: str) -> int | None:
    """«4 (добре)» -> 4."""
    m = re.match(r"\s*(\d+)", grade)
    return int(m.group(1)) if m else None


def grade_from_scores(scores: list[dict], work_type: WorkType, scale: list[ScaleRow]) -> GradeResult:
    """Перевіряє бали моделі за критеріями (кожен критерій рівно раз, 0..макс) і рахує оцінку.

    scores: [{"number": 1, "score": 20}, ...]
    """
    by_num = {c.number: c for c in work_type.criteria}
    seen: set[int] = set()
    total = 0.0
    for s in scores:
        n = int(s["number"])
        if n not in by_num:
            raise RubricError(f"Критерію № {n} немає у виді роботи «{work_type.name}»")
        if n in seen:
            raise RubricError(f"Критерій № {n} оцінено двічі")
        seen.add(n)
        v = float(s["score"])
        if v < 0 or v > by_num[n].max_points:
            raise RubricError(f"Бали за критерій № {n} ({v}) поза межами 0..{by_num[n].max_points}")
        total += v
    missing = set(by_num) - seen
    if missing:
        raise RubricError(f"Не оцінено критерії № {sorted(missing)}")
    return score_to_grade(total * 100.0 / work_type.total_max, scale)
