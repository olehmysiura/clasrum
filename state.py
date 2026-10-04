"""state.json: які роботи вже оброблено, щоб повторний запуск нічого не дублював.

Структура:
{
  "version": 1,
  "submissions": {
    "<courseId>/<courseWorkId>/<submissionId>": {
      "turned_in_at": "...",      # час останньої здачі
      "content_hash": "...",      # хеш вмісту вкладень
      "processed_at": "...",
      "course_id": "...", "sheet": "...", "grade": "4 (добре)", "total": 80,
      "ai_risk": "низький", "draft_status": "..."
    }
  },
  "write_mode": {"<courseId>/<courseWorkId>": "api" | "api_forbidden"},
  "course_stats": {"<courseId>": {"<courseWorkId>": {"turned_in": 0, "not_submitted": 0}}}
}

Тут лише чисті функції; читання й запис файлу — load_state/save_state.
"""
from __future__ import annotations

import hashlib
import json
import os
import tempfile
from pathlib import Path

EMPTY = {"version": 1, "submissions": {}, "write_mode": {}, "course_stats": {}}


def submission_key(course_id: str, coursework_id: str, submission_id: str) -> str:
    return f"{course_id}/{coursework_id}/{submission_id}"


def content_hash(parts: list[bytes | str]) -> str:
    """Стабільний хеш вмісту вкладень (порядок вкладень не важливий)."""
    digests = sorted(hashlib.sha256(p.encode() if isinstance(p, str) else p).hexdigest() for p in parts)
    return hashlib.sha256("\n".join(digests).encode()).hexdigest()


def needs_review(state: dict, key: str, turned_in_at: str, chash: str | None = None) -> bool:
    """Чи треба (повторно) перевіряти роботу.

    - нової роботи в стані немає -> так;
    - та сама здача (той самий час) -> ні;
    - студент здав повторно (інший час): якщо хеш ще не відомий (None) -> так (треба завантажити);
      якщо вміст не змінився (той самий хеш) -> ні.
    """
    rec = state.get("submissions", {}).get(key)
    if rec is None:
        return True
    if rec.get("turned_in_at") == turned_in_at:
        return False
    if chash is None:
        return True
    return rec.get("content_hash") != chash


def mark_processed(state: dict, key: str, turned_in_at: str, chash: str, processed_at: str, **extra) -> dict:
    """Повертає новий стан з позначкою про оброблену роботу (вхідний стан не змінюється)."""
    new = json.loads(json.dumps(state))
    new.setdefault("submissions", {})[key] = {
        "turned_in_at": turned_in_at, "content_hash": chash, "processed_at": processed_at, **extra}
    return new


def touch_turned_in(state: dict, key: str, turned_in_at: str) -> dict:
    """Повторна здача без змін вмісту: оновити лише час, щоб не завантажувати знову."""
    new = json.loads(json.dumps(state))
    rec = new.get("submissions", {}).get(key)
    if rec is not None:
        rec["turned_in_at"] = turned_in_at
    return new


def load_state(path: str | Path = "state.json") -> dict:
    p = Path(path)
    if not p.exists():
        return json.loads(json.dumps(EMPTY))
    data = json.loads(p.read_text(encoding="utf-8"))
    for k, v in EMPTY.items():
        data.setdefault(k, json.loads(json.dumps(v)))
    return data


def save_state(state: dict, path: str | Path = "state.json") -> None:
    """Атомарний запис: збій посеред запису не зіпсує state.json."""
    p = Path(path)
    fd, tmp = tempfile.mkstemp(dir=p.parent or ".", prefix=".state-", suffix=".json")
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)
    os.replace(tmp, p)
