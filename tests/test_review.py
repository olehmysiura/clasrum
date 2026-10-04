import review as rv


def _item(**kw):
    base = dict(key="c/w/s", turned_in_at="2026-10-01T17:30:00Z", content_hash="abcdef1234567890",
                student_name="Іваненко Іван", assignment="ПР 1", late=True, link="https://x")
    base.update(kw)
    return base


def test_row_key_changes_on_resubmission():
    assert rv.row_key(_item()) != rv.row_key(_item(turned_in_at="2026-10-02T09:00:00Z"))
    assert rv.row_key(_item()) == rv.row_key(_item())


def test_build_row_columns():
    row = rv.build_row(_item(), grade="4 (добре)", total=80, breakdown="b", remarks="r", ai_risk="низький",
                       ai_note="n", questions="", draft_status="d", checked_at="t", tz="Europe/Kyiv")
    assert len(row) == len(rv.ct.HEADERS)
    assert row[2] == "01.10.2026 20:30"
    assert row[3] == "із запізненням"
    assert row[14] == ""  # «Рішення викладача» — порожня


def test_summarize():
    state = {"course_stats": {"c1": {"w1": {"sheet": "ТП", "turned_in": 3, "not_submitted": 2}}},
             "submissions": {
                 "c1/w1/a": {"course_id": "c1", "total": 90, "grade_num": 5, "ai_risk": "високий"},
                 "c1/w1/b": {"course_id": "c1", "total": 70, "grade_num": 3, "ai_risk": "низький"},
                 "c1/w1/c": {"course_id": "c1", "status": "не оцінено"}}}
    assert rv.summarize(state, {"c1": "ТП"}) == [["ТП", 1, 3, 2, 2, 4.0, 80.0, 1]]
