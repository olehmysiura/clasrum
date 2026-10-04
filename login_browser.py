"""Одноразовий вхід у Google Classroom для запису чернеток через інтерфейс (Спосіб B).

    python login_browser.py

Відкриється видиме вікно Chromium. Увійдіть під акаунтом викладача (корпоративна пошта),
пройдіть двофакторну перевірку, дочекайтеся списку курсів і натисніть Enter у терміналі.
Сесія збережеться в browser_profile/ (тека в .gitignore — не передавайте її нікому).
"""
from __future__ import annotations

import os

from playwright.sync_api import sync_playwright

from classroom_ui import PROFILE_DIR


def main() -> None:
    PROFILE_DIR.mkdir(exist_ok=True)
    with sync_playwright() as pw:
        ctx = pw.chromium.launch_persistent_context(str(PROFILE_DIR), headless=False, locale="uk-UA",
                                                    executable_path=os.environ.get("CLASSROOM_CHROMIUM") or None)
        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        page.goto("https://classroom.google.com/")
        input("Увійдіть у Classroom у вікні браузера, дочекайтеся списку курсів і натисніть Enter тут… ")
        ok = "classroom.google.com" in page.url and "accounts.google.com" not in page.url
        ctx.close()
    print("Сесію збережено." if ok else "Схоже, вхід не завершено — запустіть ще раз.")


if __name__ == "__main__":
    main()
