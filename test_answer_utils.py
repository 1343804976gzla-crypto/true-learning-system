from utils.answer import answers_match, normalize_answer


def test_normalize_answer_handles_single_choice_with_trailing_text():
    assert normalize_answer("B. selected") == "B"
    assert normalize_answer("答案：D") == "D"


def test_normalize_answer_handles_multi_select_separators_and_matching():
    assert normalize_answer("A、C") == "AC"
    assert normalize_answer("C, A") == "AC"
    assert answers_match("A、C", "C, A") is True
