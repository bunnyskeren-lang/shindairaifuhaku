from routers.timetable_api import _credits_from_term


def test_credits_from_term_none_defaults_to_2():
    assert _credits_from_term(None) == 2
    assert _credits_from_term("") == 2


def test_credits_from_term_quarter_is_1():
    assert _credits_from_term("1クォーター") == 1
    assert _credits_from_term("春クォーター") == 1


def test_credits_from_term_semester_is_2():
    assert _credits_from_term("前期") == 2
    assert _credits_from_term("後期") == 2
    assert _credits_from_term("前期セメスター") == 2


def test_credits_from_term_full_year_is_4():
    assert _credits_from_term("通年") == 4


def test_credits_from_term_unknown_defaults_to_2():
    assert _credits_from_term("集中") == 2
    assert _credits_from_term("不明な区分") == 2
