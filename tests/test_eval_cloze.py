import math

import pytest

from eval import scorers

ITEM = {"lang": "vi", "choices": ["16 tuổi", "18 tuổi", "Hai mươi mốt tuổi"], "answer_key": "B",
        "messages": [{"role": "user", "content": "Tuổi thành niên?\nDòng thứ hai của đề.\nA. 16 tuổi\nB. 18 tuổi\n"
                                                 "C. Hai mươi mốt tuổi"}]}


def test_stem_keeps_every_question_line_and_drops_only_options():
    assert scorers.question_stem(ITEM) == "Tuổi thành niên?\nDòng thứ hai của đề."


def test_cloze_sentence_moves_the_space_before_the_blank_into_each_continuation():
    item = ITEM | {"meta": {"cloze": "Người thành niên là người từ đủ ___ trở lên."}}
    pairs = scorers.cloze_pairs(item)
    assert {c for c, _ in pairs} == {"Người thành niên là người từ đủ"}
    assert [x for _, x in pairs] == [" 16 tuổi trở lên.", " 18 tuổi trở lên.", " Hai mươi mốt tuổi trở lên."]


def test_items_without_a_cloze_sentence_score_the_choice_after_stem_and_answer_cue():
    context, continuation = scorers.cloze_pairs(ITEM)[1]
    assert context == "Tuổi thành niên?\nDòng thứ hai của đề.\nĐáp án:"
    assert continuation == " 18 tuổi"


def test_cloze_result_picks_per_byte_and_reports_mass_on_gold():
    # The short third choice has the highest total but the lowest per-byte log-prob: acc and acc_raw disagree.
    scores, n_bytes = [-12.0, -6.0, -5.0], [10, 10, 4]
    r = scorers.cloze_result(ITEM, scores, n_bytes)
    assert r["pred"] == "B" and r["correct"] and not r["correct_raw"]
    expected = math.exp(-6.0) / sum(math.exp(s) for s in scores)
    assert r["p_correct"] == pytest.approx(expected, abs=1e-5)


def test_summary_breaks_cloze_metrics_down_by_domain_and_difficulty():
    rows = [{"group": "f", "lang": "vi", "domain": d, "difficulty": k, "correct": c, "correct_raw": c, "p_correct": p}
            for d, k, c, p in [("law", 1, True, 0.6), ("law", 3, False, 0.2), ("science", 3, True, 0.5)]]
    s = scorers.summarize(rows)
    assert set(s) == {"f", "f:law", "f:science", "f:d1", "f:d3"}
    assert s["f:law"]["n"] == 2 and s["f:law"]["p_correct"] == pytest.approx(0.4, abs=1e-4)
    assert s["f"]["acc_raw"] == pytest.approx(2 / 3, abs=1e-4)
