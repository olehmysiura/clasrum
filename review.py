"""Агент перевірки робіт: збирання робіт із Classroom і запис результатів у звіт.

Команди:
  python review.py courses                     — ваші курси (назви і групи для config.yaml)
  python review.py assignments                 — завдання курсів із config.yaml
  python review.py fetch                       — зібрати нові здані роботи в work/<запуск>/packets/
  python review.py apply --run work/<запуск>   — перевірити відповіді моделі й оновити звіт

Між fetch і apply модель (claude -p з AGENT.md) читає packets/ і пише results/.
Модель не має доступу до API, а скрипти не оцінюють роботи.
"""
from __future__ import annotations

import argparse
import csv
import json
import re
import secrets
import sys
from datetime import datetime, timezone
from pathlib import Path

import yaml

import classroom_tools as ct
import rubric as rb
import state as st
import text_utils as tu

ROOT = Path(__file__).resolve().parent
CONFIG = ROOT / "config.yaml"
STATE = ROOT / "state.json"
WORK = ROOT / "work"
HISTORY = ROOT / "history"
EXPORTS = ROOT / "exports"
RISKS = ("низький", "середній", "високий")


def log(msg: str) -> None:
    print(msg, flush=True)


# ---------- конфігурація ----------

def load_config() -> dict:
    if not CONFIG.exists():
        raise SystemExit("Немає config.yaml. Скопіюйте config.example.yaml у config.yaml і заповніть.")
    cfg = yaml.safe_load(CONFIG.read_text(encoding="utf-8")) or {}
    cfg.setdefault("timezone", "Europe/Kyiv")
    cfg.setdefault("max_submissions_per_run", 30)
    cfg.setdefault("dry_run", True)
    cfg.setdefault("courses", [])
    return cfg


def set_config_value(key: str, value: str) -> None:
    """Записує значення в config.yaml, зберігаючи коментарі (заміна рядка `key: ...`)."""
    text = CONFIG.read_text(encoding="utf-8")
    line = f'{key}: "{value}"'
    new, n = re.subn(rf"^{key}:.*$", line, text, count=1, flags=re.M)
    CONFIG.write_text(new if n else text.rstrip() + "\n" + line + "\n", encoding="utf-8")


def services():
    from auth import get_credentials
    return ct.build_services(get_credentials())


def pseudonym_salt(state: dict) -> str:
    if "salt" not in state:
        state["salt"] = secrets.token_hex(8)
    return state["salt"]


# ---------- courses / assignments ----------

def cmd_courses(_args) -> int:
    svc = services()
    for c in ct.list_courses(svc):
        log(f"- course: \"{c.get('name')}\"   section: \"{c.get('section', '')}\"   course_id: \"{c['id']}\"")
    return 0


def cmd_assignments(_args) -> int:
    cfg, svc = load_config(), services()
    courses = ct.list_courses(svc)
    for cc in cfg["courses"]:
        c = ct.find_course(courses, cc.get("course"), cc.get("section"), cc.get("course_id"))
        log(f"\n{ct.sheet_title(c)}:")
        for cw in ct.list_coursework(svc, c["id"]):
            due = cw.get("dueDate")
            due_s = f"{due.get('day'):02}.{due.get('month'):02}.{due.get('year')}" if due else "без терміну"
            log(f"  - title: \"{cw['title']}\"   (макс. {cw.get('maxPoints', '—')}, термін {due_s})")
    return 0


# ---------- fetch ----------

def history_path(pseudo: str) -> Path:
    return HISTORY / f"{pseudo}.jsonl"


def prior_works(pseudo: str, limit: int = 3) -> list[dict]:
    p = history_path(pseudo)
    if not p.exists():
        return []
    lines = [json.loads(x) for x in p.read_text(encoding="utf-8").splitlines() if x.strip()]
    return lines[-limit:]


def cmd_fetch(args) -> int:
    cfg, svc = load_config(), services()
    state = st.load_state(STATE)
    salt = pseudonym_salt(state)
    run_id = datetime.now().strftime("%Y%m%d-%H%M%S")
    run_dir = WORK / run_id
    (run_dir / "packets").mkdir(parents=True)
    (run_dir / "results").mkdir()
    files_dir = run_dir / "files"
    budget = int(cfg["max_submissions_per_run"])
    manifest = {"run_id": run_id, "dry_run": bool(cfg["dry_run"]), "items": [], "warnings": []}
    courses = ct.list_courses(svc)
    left_over = 0

    for cc in cfg["courses"]:
        try:
            course = ct.find_course(courses, cc.get("course"), cc.get("section"), cc.get("course_id"))
            rubric = rb.load_rubric(ROOT / cc["rubric"])
        except (ct.ApiError, rb.RubricError) as e:
            manifest["warnings"].append(str(e))
            log(f"ПОПЕРЕДЖЕННЯ: {e}")
            continue
        cid, sheet = course["id"], ct.sheet_title(course)
        students = ct.list_students(svc, cid)
        cws = ct.list_coursework(svc, cid)
        for ac in cc.get("assignments", []) or []:
            try:
                cw = ct.find_coursework(cws, ac["title"])
                wt = rubric.work_type(ac["work_type"])
            except (ct.ApiError, rb.RubricError) as e:
                manifest["warnings"].append(f"{sheet}: {e}")
                log(f"ПОПЕРЕДЖЕННЯ: {sheet}: {e}")
                continue
            subs = ct.list_submissions(svc, cid, cw["id"])
            state.setdefault("course_stats", {}).setdefault(cid, {})[cw["id"]] = {
                "sheet": sheet, "title": cw["title"],
                "turned_in": sum(1 for s in subs if s.get("state") in ("TURNED_IN", "RETURNED")),
                "not_submitted": sum(1 for s in subs if s.get("state") in ("NEW", "CREATED", "RECLAIMED_BY_STUDENT")),
            }
            task_text = None
            new_packets = []
            for sub in subs:
                if not ct.is_turned_in(sub):
                    continue
                key = st.submission_key(cid, cw["id"], sub["id"])
                tin = ct.turned_in_time(sub) or ""
                if not st.needs_review(state, key, tin):
                    continue
                if budget <= 0:
                    left_over += 1
                    continue
                pseudo = tu.pseudonym(sub["userId"], salt)
                atts = ct.extract_submission(svc, sub, files_dir)
                chash = st.content_hash([a.raw or f"{a.name}:{a.status}:{a.reason}" for a in atts] or ["empty"])
                if not st.needs_review(state, key, tin, chash):
                    state = st.touch_turned_in(state, key, tin)
                    log(f"{pseudo}: повторна здача без змін вмісту — пропущено")
                    continue
                if task_text is None:
                    task_text = ct.coursework_task_text(svc, cw, files_dir)
                budget -= 1
                packet_id = f"{cw['id']}-{sub['id']}"
                readable = [a for a in atts if a.status in ("ok", "image", "pdf_scan")]
                unreadable = [{"file": a.name, "reason": a.reason} for a in atts if a.status == "error"]
                if not atts:
                    unreadable.append({"file": "—", "reason": "порожній (немає вкладень)"})
                full_text = "\n\n".join(a.text for a in readable if a.text)
                item = {
                    "packet_id": packet_id, "key": key, "course_id": cid, "coursework_id": cw["id"],
                    "submission_id": sub["id"], "sheet": sheet, "pseudonym": pseudo,
                    "student_name": students.get(sub["userId"], "") or pseudo,
                    "assignment": cw["title"], "turned_in_at": tin,
                    "late": bool(sub.get("late")), "link": sub.get("alternateLink", ""),
                    "rubric": cc["rubric"], "work_type": wt.name, "content_hash": chash,
                    "gradable": bool(readable), "unreadable": unreadable,
                    "existing_grade": sub.get("assignedGrade", sub.get("draftGrade")),
                    "max_points": cw.get("maxPoints"),
                }
                manifest["items"].append(item)
                if readable:
                    packet = {
                        "packet_id": packet_id, "student": pseudo, "assignment": cw["title"],
                        "task": task_text, "rubric_file": cc["rubric"], "work_type": wt.name,
                        "criteria": [vars(c) for c in wt.criteria],
                        "turned_in_at": tin, "late": bool(sub.get("late")),
                        "attachments": [{"name": a.name, "kind": a.status, "text": a.text,
                                         "file_path": a.file_path, "drive_meta": a.meta} for a in readable],
                        "unreadable": unreadable,
                        "injection_hits": tu.find_injection_attempts(full_text),
                        "style_stats": tu.style_stats(full_text),
                        "prior_works_same_student": prior_works(pseudo),
                        "similar_works": [],
                    }
                    new_packets.append((packet, full_text))
                log(f"{pseudo}: робота зібрана ({len(readable)} файл(и) прочитано, {len(unreadable)} — ні)")
            # Схожість між роботами різних студентів у межах одного завдання
            texts = {p["packet_id"]: t for p, t in new_packets if t}
            for pair in tu.similarity_pairs(texts):
                for p, _ in new_packets:
                    other = pair["b"] if p["packet_id"] == pair["a"] else pair["a"] if p["packet_id"] == pair["b"] else None
                    if other:
                        p["similar_works"].append({"other_packet": other, "jaccard": pair["score"],
                                                   "common_fragment": pair["example"]})
            for p, _ in new_packets:
                (run_dir / "packets" / f"{p['packet_id']}.json").write_text(
                    json.dumps(p, ensure_ascii=False, indent=2), encoding="utf-8")

    manifest["left_over"] = left_over
    (run_dir / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    st.save_state(state, STATE)
    n_packets = len(list((run_dir / "packets").glob("*.json")))
    log(f"\nЗапуск {run_id}: зібрано робіт — {len(manifest['items'])}, для оцінювання — {n_packets}.")
    if left_over:
        log(f"Ще {left_over} робіт чекають наступного запуску (ліміт max_submissions_per_run).")
    log(f"RUN_DIR={run_dir.relative_to(ROOT).as_posix()}")
    return 0


# ---------- apply ----------

def validate_result(res: dict, wt: rb.WorkType, scale) -> tuple[rb.GradeResult, list[dict]]:
    """Перевіряє відповідь моделі: усі критерії, межі балів, рівень ризику. Оцінку рахує Python."""
    crit = res.get("criteria")
    if not isinstance(crit, list):
        raise rb.RubricError("немає списку criteria")
    g = rb.grade_from_scores(crit, wt, scale)
    if res.get("ai_risk") not in RISKS:
        raise rb.RubricError(f"ai_risk має бути одним із {RISKS}")
    for c in crit:
        if not str(c.get("explanation", "")).strip():
            raise rb.RubricError(f"немає пояснення для критерію № {c.get('number')}")
    return g, crit


def format_breakdown(crit: list[dict], wt: rb.WorkType) -> str:
    names = {c.number: c for c in wt.criteria}
    lines = []
    for c in sorted(crit, key=lambda x: int(x["number"])):
        cr = names[int(c["number"])]
        score = c["score"]
        score_s = str(int(score)) if float(score).is_integer() else str(score)
        line = f"{cr.number}. {cr.name}: {score_s}/{cr.max_points} — {c.get('explanation', '').strip()}"
        if c.get("evidence"):
            line += f" [{c['evidence'].strip()}]"
        lines.append(line)
    return "\n".join(lines)


def build_row(item: dict, *, grade: str, total, breakdown: str, remarks: str, ai_risk: str,
              ai_note: str, questions: str, draft_status: str, checked_at: str, tz: str) -> list:
    return [
        item["student_name"], item["assignment"], ct.format_time(item["turned_in_at"], tz),
        "із запізненням" if item["late"] else "вчасно", grade, total, breakdown, remarks,
        ai_risk, ai_note, questions, draft_status, checked_at, item["link"], "",
        row_key(item),
    ]


def row_key(item: dict) -> str:
    """Ключ рядка: та сама здача не з'явиться у звіті двічі, повторна — з'явиться новим рядком."""
    return f"{item['key']}@{item['turned_in_at']}#{item['content_hash'][:10]}"


def summarize(state: dict, sheet_by_course: dict[str, str]) -> list[list]:
    """Рядки аркуша «Підсумок» за даними state.json (чиста функція)."""
    out = []
    for cid, cws in sorted(state.get("course_stats", {}).items(), key=lambda kv: sheet_by_course.get(kv[0], "")):
        sheet = sheet_by_course.get(cid) or next(iter(cws.values()), {}).get("sheet", cid)
        turned = sum(v.get("turned_in", 0) for v in cws.values())
        missing = sum(v.get("not_submitted", 0) for v in cws.values())
        recs = [r for r in state.get("submissions", {}).values()
                if r.get("course_id") == cid and r.get("total") is not None]
        grades = [r["grade_num"] for r in recs if r.get("grade_num") is not None]
        totals = [r["total"] for r in recs]
        risky = sum(1 for r in recs if r.get("ai_risk") in ("середній", "високий"))
        out.append([sheet, len(cws), turned, len(recs), missing,
                    round(sum(grades) / len(grades), 2) if grades else "",
                    round(sum(totals) / len(totals), 1) if totals else "", risky])
    return out


def append_history(item: dict, text_excerpt: str, stats: dict, checked_at: str) -> None:
    HISTORY.mkdir(exist_ok=True)
    with history_path(item["pseudonym"]).open("a", encoding="utf-8") as f:
        f.write(json.dumps({"assignment": item["assignment"], "date": checked_at,
                            "style_stats": stats, "excerpt": text_excerpt[:1500]}, ensure_ascii=False) + "\n")


def cmd_apply(args) -> int:
    cfg = load_config()
    run_dir = Path(args.run)
    if not run_dir.is_absolute():
        run_dir = ROOT / run_dir
    manifest = json.loads((run_dir / "manifest.json").read_text(encoding="utf-8"))
    dry = bool(cfg["dry_run"]) or args.dry_run
    tz = cfg["timezone"]
    state = st.load_state(STATE)
    now = datetime.now(timezone.utc)
    checked_at = ct.format_time(now.isoformat(), tz)
    rows_by_sheet: dict[str, list[list]] = {}
    csv_rows: list[list] = []
    problems = []
    rubrics: dict[str, rb.Rubric] = {}

    for item in manifest["items"]:
        draft_status = "не записувалось (dry-run)" if dry else "не записано: запис чернеток ще не ввімкнено"
        if item.get("existing_grade") is not None:
            draft_status = f"пропущено: у Classroom уже є оцінка ({item['existing_grade']})"
        extra = {"course_id": item["course_id"], "sheet": item["sheet"], "assignment": item["assignment"]}
        if not item["gradable"]:
            reasons = "; ".join(f"{u['file']}: {u['reason']}" for u in item["unreadable"])
            row = build_row(item, grade="не оцінено", total="", breakdown="",
                            remarks=f"Роботу не оцінено — {reasons}", ai_risk="", ai_note="",
                            questions="", draft_status="не записувалось (немає оцінки)",
                            checked_at=checked_at, tz=tz)
            state = st.mark_processed(state, item["key"], item["turned_in_at"], item["content_hash"],
                                      now.isoformat(), status="не оцінено", reason=reasons, **extra)
        else:
            res_path = run_dir / "results" / f"{item['packet_id']}.json"
            if not res_path.exists():
                problems.append(f"{item['pseudonym']}: модель не повернула результат — робота залишиться на наступний запуск")
                continue
            try:
                rub = rubrics.setdefault(item["rubric"], rb.load_rubric(ROOT / item["rubric"]))
                wt = rub.work_type(item["work_type"])
                res = json.loads(res_path.read_text(encoding="utf-8"))
                g, crit = validate_result(res, wt, rub.scale)
            except (json.JSONDecodeError, rb.RubricError, KeyError, TypeError, ValueError) as e:
                problems.append(f"{item['pseudonym']}: некоректний результат моделі ({e}) — робота залишиться на наступний запуск")
                continue
            remarks = str(res.get("teacher_remarks", "")).strip()
            if res.get("comment_for_student"):
                remarks += f"\n\nКоментар для студента (чернетка): {res['comment_for_student'].strip()}"
            if item["unreadable"]:
                remarks += "\n\nНе прочитано: " + "; ".join(f"{u['file']}: {u['reason']}" for u in item["unreadable"])
            if res.get("manipulation_attempt"):
                remarks += f"\n\n⚠ Спроба маніпуляції оцінюванням у тексті роботи: {res.get('manipulation_note', '')}"
            questions = "\n".join(f"{i}. {q}" for i, q in enumerate(res.get("questions") or [], 1))
            row = build_row(item, grade=g.grade, total=g.total, breakdown=format_breakdown(crit, wt),
                            remarks=remarks.strip(), ai_risk=res["ai_risk"], ai_note=str(res.get("ai_note", "")),
                            questions=questions, draft_status=draft_status, checked_at=checked_at, tz=tz)
            state = st.mark_processed(state, item["key"], item["turned_in_at"], item["content_hash"],
                                      now.isoformat(), status="оцінено", grade=g.grade,
                                      grade_num=rb.numeric_grade(g.grade), total=g.total,
                                      ects=g.ects, ai_risk=res["ai_risk"], draft_status=draft_status,
                                      dry_run=dry, **extra)
            packet = json.loads((run_dir / "packets" / f"{item['packet_id']}.json").read_text(encoding="utf-8"))
            text = "\n".join(a["text"] for a in packet["attachments"] if a.get("text"))
            append_history(item, text, packet.get("style_stats", {}), checked_at)
        rows_by_sheet.setdefault(item["sheet"], []).append(row)
        csv_rows.append([item["sheet"]] + row[:-1])

    # CSV — резервна копія пропозицій (локально, не в git)
    EXPORTS.mkdir(exist_ok=True)
    csv_path = EXPORTS / f"{manifest['run_id']}.csv"
    with csv_path.open("w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        w.writerow(["Аркуш"] + ct.HEADERS[:-1])
        w.writerows(csv_rows)

    sheets_ok = True
    try:
        svc = services()
        ssid = cfg.get("spreadsheet_id") or ""
        if not ssid:
            ssid = ct.create_spreadsheet(svc, f"Звіт перевірки робіт {cfg.get('academic_year', '')}".strip())
            set_config_value("spreadsheet_id", ssid)
            log(f"Створено таблицю звіту, id записано в config.yaml")
        added = 0
        for sheet, rows in rows_by_sheet.items():
            ct.ensure_course_sheet(svc, ssid, sheet)
            known = ct.existing_keys(svc, ssid, sheet)
            fresh = [r for r in rows if r[-1] not in known]
            ct.append_rows(svc, ssid, sheet, fresh)
            added += len(fresh)
        sheet_by_course = {i["course_id"]: i["sheet"] for i in manifest["items"]}
        for cid, cws in state.get("course_stats", {}).items():
            sheet_by_course.setdefault(cid, next(iter(cws.values()), {}).get("sheet", cid))
        ct.write_summary(svc, ssid, summarize(state, sheet_by_course))
        log(f"Звіт оновлено: додано рядків — {added}. https://docs.google.com/spreadsheets/d/{ssid}")
    except ct.ApiError as e:
        sheets_ok = False
        log(f"ПОМИЛКА ЗВІТУ: {e}")
        log(f"Пропозиції збережено у {csv_path}. Стан не оновлено — наступний запуск повторить запис.")

    if sheets_ok:
        st.save_state(state, STATE)
    log(f"CSV: {csv_path}")
    if dry:
        log("Режим dry-run: у Classroom нічого не записано.")
    for p in problems:
        log(f"УВАГА: {p}")
    return 0 if sheets_ok else 2


def main() -> int:
    ap = argparse.ArgumentParser(description="Перевірка робіт студентів у Google Classroom")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("courses").set_defaults(fn=cmd_courses)
    sub.add_parser("assignments").set_defaults(fn=cmd_assignments)
    sub.add_parser("fetch").set_defaults(fn=cmd_fetch)
    p = sub.add_parser("apply")
    p.add_argument("--run", required=True)
    p.add_argument("--dry-run", action="store_true")
    p.set_defaults(fn=cmd_apply)
    args = ap.parse_args()
    try:
        return args.fn(args)
    except ct.ApiError as e:
        log(f"ПОМИЛКА: {e}")
        return 2
    except Exception as e:  # noqa: BLE001
        from auth import AuthError
        if isinstance(e, AuthError):
            log(f"ПОМИЛКА АВТОРИЗАЦІЇ: {e}")
            return 3
        raise


if __name__ == "__main__":
    sys.exit(main())
