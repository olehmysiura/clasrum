import pytest

import classroom_ui as ui


@pytest.mark.parametrize("label", ["Повернути", "Повернути роботу", "Return", "Здати", "Скасувати здачу",
                                   "Видалити", "Опублікувати", "Бали"])
def test_forbidden_clicks_refused(label):
    with pytest.raises(ui.UiError, match="Заборонено"):
        ui.guard_click(label)


def test_any_click_refused():
    with pytest.raises(ui.UiError):
        ui.guard_click("Будь-яка кнопка")


def test_only_whitelisted_actions():
    ui._check("fill_grade")
    with pytest.raises(ui.UiError):
        ui._check("click_return")


def test_login_page_detected():
    assert ui.SELECTORS["login_url"].search("https://accounts.google.com/v3/signin/identifier?x=1")
    assert not ui.SELECTORS["login_url"].search("https://classroom.google.com/c/abc/a/def/submissions")
