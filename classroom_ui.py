"""Запис ЧЕРНЕТКИ оцінки через інтерфейс Classroom (Спосіб B), коли API відмовляє (403).

Працює лише під обліковим записом викладача з постійним профілем browser_profile/
(вхід виконується один раз вручну: python login_browser.py).

Безпека:
- Модуль НЕ клікає жодних кнопок. Дозволені дії (білий список ALLOWED_ACTIONS):
  відкрити сторінку роботи студента, ввести число в поле оцінки, натиснути Tab
  (Classroom зберігає введене як чернетку, доки викладач не натисне «Повернути»).
- Якщо поле оцінки вже заповнене (оцінку ввів викладач) — нічого не змінює.
- Якщо Google просить вхід або підтвердження — зупиняється з SessionError.
- Усі селектори зібрано в SELECTORS: інтерфейс Classroom змінюється, правити треба лише тут.
- При збої зберігає скріншот у screenshots/ (тека в .gitignore).
"""
from __future__ import annotations

import os
import re
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent
PROFILE_DIR = ROOT / "browser_profile"
SCREENSHOTS = ROOT / "screenshots"

# Єдине місце з селекторами інтерфейсу Classroom.
SELECTORS = {
    # Поле оцінки на сторінці роботи студента (права панель «Оцінка» / «Grade»).
    "grade_input": [
        'input[aria-label*="Оцінка" i]',
        'input[aria-label*="оцінк" i]',
        'input[aria-label*="Grade" i]',
        'input[aria-label*="бал" i]',
        'input[aria-label*="points" i]',
    ],
    # Ознаки, що Google просить увійти / підтвердити особу.
    "login_url": re.compile(r"accounts\.google\.com|/signin|/challenge|ServiceLogin", re.I),
}

# Дії, які модуль взагалі вміє виконувати. Кліків серед них немає.
ALLOWED_ACTIONS = {"open_submission", "fill_grade", "blur_with_tab", "read_grade"}

# Кнопки, які заборонено натискати за будь-яких умов (перевіряється в guard_click).
FORBIDDEN_LABELS = ["Повернути", "Return", "Здати", "Turn in", "Скасувати здачу", "Unsubmit",
                    "Видалити", "Delete", "Опублікувати", "Post", "Publish", "Термін", "Due",
                    "Бали", "Points"]


class UiError(RuntimeError):
    pass


class SessionError(UiError):
    """Сесія протухла або Google просить підтвердження — потрібен викладач."""


class AlreadyGraded(UiError):
    pass


def _check(action: str) -> None:
    if action not in ALLOWED_ACTIONS:
        raise UiError(f"Дія «{action}» не дозволена білим списком")


def guard_click(label: str) -> None:
    """Захист на випадок майбутніх змін: будь-який клік має пройти цю перевірку."""
    if any(f.lower() in (label or "").lower() for f in FORBIDDEN_LABELS):
        raise UiError(f"Заборонено натискати «{label}»")
    raise UiError("Кліки в інтерфейсі Classroom вимкнено: дозволено лише введення оцінки")


def screenshot(page, tag: str) -> str:
    SCREENSHOTS.mkdir(exist_ok=True)
    p = SCREENSHOTS / f"{datetime.now():%Y%m%d-%H%M%S}-{tag}.png"
    try:
        page.screenshot(path=str(p), full_page=True)
    except Exception:  # noqa: BLE001
        return ""
    return str(p)


def open_browser(headless: bool = True):
    from playwright.sync_api import sync_playwright
    if not PROFILE_DIR.exists():
        raise SessionError("Немає browser_profile/. Спершу виконайте: python login_browser.py")
    pw = sync_playwright().start()
    try:
        ctx = pw.chromium.launch_persistent_context(
            str(PROFILE_DIR), headless=headless, locale="uk-UA",
            viewport={"width": 1400, "height": 1000},
            executable_path=os.environ.get("CLASSROOM_CHROMIUM") or None)
    except Exception as e:  # noqa: BLE001
        pw.stop()
        raise UiError("Не вдалося запустити браузер. Виконайте: playwright install chromium "
                      f"({str(e).splitlines()[0]})") from e
    return pw, ctx


def _ensure_session(page) -> None:
    if SELECTORS["login_url"].search(page.url):
        raise SessionError("Google просить увійти або підтвердити особу. Запустіть python login_browser.py "
                           "і увійдіть вручну. Автоматично це не обходиться.")


def _grade_input(page, timeout_ms: int = 20000):
    deadline = datetime.now().timestamp() + timeout_ms / 1000
    while datetime.now().timestamp() < deadline:
        _ensure_session(page)
        for sel in SELECTORS["grade_input"]:
            loc = page.locator(sel)
            if loc.count() == 1 and loc.first.is_visible():
                return loc.first
        page.wait_for_timeout(500)
    raise UiError("Не знайдено поле оцінки на сторінці. Можливо, змінився інтерфейс Classroom — "
                  "оновіть SELECTORS у classroom_ui.py")


def _normalize(v: str) -> str:
    return (v or "").strip().replace(",", ".").removesuffix(".0")


def write_draft_grade_ui(ctx, submission_url: str, student_name: str, grade: str) -> str:
    """Вводить оцінку-чернетку на сторінці роботи студента і звіряє її після перезавантаження.

    submission_url — alternateLink роботи студента з Classroom API (сторінка конкретного студента).
    Повертає значення, прочитане зі сторінки після запису.
    """
    page = ctx.new_page()
    try:
        _check("open_submission")
        page.goto(submission_url, wait_until="domcontentloaded")
        _ensure_session(page)
        if not submission_url.startswith("https://classroom.google.com/"):
            raise UiError("Дозволено відкривати лише сторінки classroom.google.com")
        field = _grade_input(page)
        # Переконуємось, що відкрито роботу саме цього студента.
        if student_name and page.get_by_text(student_name, exact=False).count() == 0:
            raise UiError("На сторінці не знайдено ПІБ студента — запис скасовано")

        _check("read_grade")
        current = _normalize(field.input_value())
        if current:
            raise AlreadyGraded(f"у полі вже є оцінка ({current}) — не перезаписую")

        _check("fill_grade")
        field.fill(str(grade))
        _check("blur_with_tab")
        field.press("Tab")
        page.wait_for_timeout(3000)  # Classroom зберігає чернетку асинхронно

        # Перевірка: перезавантажити і прочитати значення назад.
        page.reload(wait_until="domcontentloaded")
        _ensure_session(page)
        back = _normalize(_grade_input(page).input_value())
        if back != _normalize(str(grade)):
            raise UiError(f"Після запису в полі «{back}», очікувалось «{grade}»")
        return back
    except UiError as e:
        shot = screenshot(page, "draft-failed")
        raise type(e)(f"{e}" + (f" (скріншот: {shot})" if shot else "")) from e
    except Exception as e:  # noqa: BLE001 — помилки Playwright (таймаути тощо)
        shot = screenshot(page, "draft-error")
        raise UiError(f"Помилка браузера: {e}" + (f" (скріншот: {shot})" if shot else "")) from e
    finally:
        page.close()
