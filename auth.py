"""Одноразова авторизація в Google: створює token.json.

    python auth.py            # відкриє браузер на цьому комп'ютері
    python auth.py --manual   # якщо браузер на іншому комп'ютері: показує посилання,
                              # після входу вставте адресу сторінки, на яку вас перекинуло

Права (scopes):
- classroom.courses.readonly   — список курсів;
- classroom.coursework.students — читання робіт і запис ЧЕРНЕТКИ оцінки (draftGrade);
  повернення робіт і остаточні оцінки код не виконує (див. classroom_tools.py);
- classroom.rosters.readonly   — ПІБ студентів;
- drive.readonly               — читання вкладень і історії версій;
- spreadsheets                 — звіт у Google Sheets.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow

SCOPES = [
    "https://www.googleapis.com/auth/classroom.courses.readonly",
    "https://www.googleapis.com/auth/classroom.coursework.students",
    "https://www.googleapis.com/auth/classroom.rosters.readonly",
    "https://www.googleapis.com/auth/drive.readonly",
    "https://www.googleapis.com/auth/spreadsheets",
]

ROOT = Path(__file__).resolve().parent
CREDENTIALS = ROOT / "credentials.json"
TOKEN = ROOT / "token.json"


class AuthError(RuntimeError):
    pass


def get_credentials() -> Credentials:
    """Повертає дійсні облікові дані з token.json (оновлює за потреби).

    Не запускає інтерактивний вхід: для запуску за розкладом це має бути помилкою,
    а не вікно браузера, яке ніхто не побачить.
    """
    if not TOKEN.exists():
        raise AuthError("Немає token.json. Запустіть один раз: python auth.py")
    creds = Credentials.from_authorized_user_file(str(TOKEN), SCOPES)
    if not set(SCOPES) <= set(creds.scopes or []):
        raise AuthError("token.json створено з іншими правами. Видаліть token.json і запустіть python auth.py")
    if not creds.valid:
        if creds.expired and creds.refresh_token:
            try:
                creds.refresh(Request())
            except Exception as e:  # noqa: BLE001
                raise AuthError(
                    "Не вдалося оновити токен (можливо, доступ відкликано). "
                    "Видаліть token.json і запустіть python auth.py") from e
            _save(creds)
        else:
            raise AuthError("Токен недійсний. Видаліть token.json і запустіть python auth.py")
    return creds


def _save(creds: Credentials) -> None:
    TOKEN.write_text(creds.to_json(), encoding="utf-8")
    try:
        os.chmod(TOKEN, 0o600)
    except OSError:
        pass


def main() -> int:
    ap = argparse.ArgumentParser(description="Авторизація в Google для агента перевірки робіт")
    ap.add_argument("--manual", action="store_true",
                    help="вхід без локального браузера: вставити адресу після перенаправлення")
    ap.add_argument("--print-url", action="store_true",
                    help="(двокроковий вхід) лише показати посилання для входу")
    ap.add_argument("--finish", metavar="URL",
                    help="(двокроковий вхід) завершити вхід адресою після перенаправлення")
    args = ap.parse_args()

    if not CREDENTIALS.exists():
        print("Не знайдено credentials.json у корені проєкту. Завантажте OAuth client (Desktop app) "
              "з Google Cloud Console і збережіть як credentials.json.", file=sys.stderr)
        return 1

    flow = InstalledAppFlow.from_client_secrets_file(str(CREDENTIALS), SCOPES)
    if args.print_url or args.finish:
        # Двокроковий вхід (для середовищ без інтерактивного вводу): code_verifier
        # зберігається між кроками в .auth_pending.json і видаляється після входу.
        os.environ.setdefault("OAUTHLIB_INSECURE_TRANSPORT", "1")
        flow.redirect_uri = "http://localhost:8765/"
        pending = ROOT / ".auth_pending.json"
        if args.print_url:
            url, state = flow.authorization_url(access_type="offline", prompt="consent")
            pending.write_text(json.dumps({"state": state, "code_verifier": flow.code_verifier}))
            print(url)
            return 0
        saved = json.loads(pending.read_text())
        flow.code_verifier = saved["code_verifier"]
        flow.fetch_token(authorization_response=args.finish)
        pending.unlink()
        creds = flow.credentials
    elif args.manual:
        os.environ.setdefault("OAUTHLIB_INSECURE_TRANSPORT", "1")  # лише для redirect на localhost
        flow.redirect_uri = "http://localhost:8765/"
        url, _ = flow.authorization_url(access_type="offline", prompt="consent")
        print("1. Відкрийте посилання і увійдіть під акаунтом викладача:\n")
        print(url)
        print("\n2. Браузер перекине на http://localhost:8765/… і покаже помилку — це нормально.")
        print("   Скопіюйте повну адресу з адресного рядка і вставте сюди:")
        response = input("> ").strip()
        flow.fetch_token(authorization_response=response)
        creds = flow.credentials
    else:
        creds = flow.run_local_server(port=0, access_type="offline", prompt="consent")
    _save(creds)
    print(f"Готово: {TOKEN.name} створено. Не передавайте цей файл нікому.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
