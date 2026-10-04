from pathlib import Path

import pytest

import rubric as rb

ROOT = Path(__file__).resolve().parent.parent

MINI = """# Рубрика: Тест
## Шкала переведення
| Бали | Оцінка | ECTS |
|---|---|---|
| 90-100 | 5 (відмінно) | A |
| 74-89 | 4 (добре) | B |
| 60-73 | 3 (задовільно) | D |
| 0-59 | 2 (незадовільно) | F |

## Вид роботи: Практична
| № | Критерій | Макс. бали | Опис |
|---|---|---|---|
| 1 | Правильність | 60 | а |
| 2 | Висновки | 40 | б |

### Типові помилки
- Немає висновків

## Доповнення викладача
- Посилання на КЗпП без номера статті
"""


def test_parse_minimal():
    r = rb.parse_rubric(MINI)
    wt = r.work_type("практична")
    assert [c.name for c in wt.criteria] == ["Правильність", "Висновки"]
    assert wt.total_max == 100
    assert wt.typical_errors == ["Немає висновків"]
    assert r.teacher_notes == ["Посилання на КЗпП без номера статті"]


def test_real_rubric_files_parse():
    for path in (ROOT / "criteria").glob("*.md"):
        r = rb.load_rubric(path)
        assert r.work_types, path
        for wt in r.work_types.values():
            assert wt.total_max == 100


def test_labour_law_rubric_has_all_work_types():
    r = rb.load_rubric(ROOT / "criteria" / "трудове-право.md")
    assert set(r.work_types) == {"практична", "семінарська", "самостійна", "реферат", "презентація", "доповідь", "контрольна"}


@pytest.mark.parametrize("total,grade,ects", [
    (100, "5 (відмінно)", "A"), (90, "5 (відмінно)", "A"), (89.5, "5 (відмінно)", "A"),
    (89.4, "4 (добре)", "B"), (82, "4 (добре)", "B"), (81, "4 (добре)", "C"), (74, "4 (добре)", "C"),
    (73, "3 (задовільно)", "D"), (64, "3 (задовільно)", "D"), (63, "3 (задовільно)", "E"),
    (60, "3 (задовільно)", "E"), (59, "2 (незадовільно)", "FX"), (35, "2 (незадовільно)", "FX"),
    (34, "2 (незадовільно)", "F"), (0, "2 (незадовільно)", "F"),
])
def test_score_to_grade_labour_law_scale(total, grade, ects):
    r = rb.load_rubric(ROOT / "criteria" / "трудове-право.md")
    g = rb.score_to_grade(total, r.scale)
    assert (g.grade, g.ects) == (grade, ects)


def test_score_out_of_range():
    r = rb.parse_rubric(MINI)
    with pytest.raises(rb.RubricError):
        rb.score_to_grade(101, r.scale)


def test_grade_from_scores():
    r = rb.parse_rubric(MINI)
    wt = r.work_type("Практична")
    g = rb.grade_from_scores([{"number": 1, "score": 50}, {"number": 2, "score": 30}], wt, r.scale)
    assert (g.total, g.grade) == (80, "4 (добре)")
    assert rb.numeric_grade(g.grade) == 4


@pytest.mark.parametrize("scores,msg", [
    ([{"number": 1, "score": 50}], "Не оцінено"),
    ([{"number": 1, "score": 61}, {"number": 2, "score": 1}], "поза межами"),
    ([{"number": 1, "score": 5}, {"number": 1, "score": 5}, {"number": 2, "score": 1}], "двічі"),
    ([{"number": 3, "score": 5}], "немає"),
])
def test_grade_from_scores_rejects_bad_model_output(scores, msg):
    r = rb.parse_rubric(MINI)
    with pytest.raises(rb.RubricError, match=msg):
        rb.grade_from_scores(scores, r.work_type("практична"), r.scale)


def test_scale_gap_rejected():
    bad = MINI.replace("| 60-73 |", "| 61-73 |")
    with pytest.raises(rb.RubricError, match="пропуск"):
        rb.parse_rubric(bad)


def test_weights_must_sum_to_100():
    bad = MINI.replace("| 2 | Висновки | 40 |", "| 2 | Висновки | 30 |")
    with pytest.raises(rb.RubricError, match="100"):
        rb.parse_rubric(bad)


def test_unknown_work_type():
    with pytest.raises(rb.RubricError, match="немає виду"):
        rb.parse_rubric(MINI).work_type("курсова")
