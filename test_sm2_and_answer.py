"""
单元测试：utils/sm2.py + utils/answer.py
纯逻辑测试，不依赖数据库或网络。
"""

import pytest
from datetime import date, timedelta
from types import SimpleNamespace

from utils.sm2 import sm2_update, quality_from_result
from utils.answer import normalize_answer, answers_match


# ========== normalize_answer ==========

class TestNormalizeAnswer:
    """测试答案标准化"""

    def test_single_letter(self):
        assert normalize_answer("A") == "A"
        assert normalize_answer("b") == "B"
        assert normalize_answer("  C  ") == "C"

    def test_multi_select_sorted(self):
        assert normalize_answer("BCA") == "ABC"
        assert normalize_answer("D,B,A") == "ABD"
        assert normalize_answer("EDCBA") == "ABCDE"

    def test_with_noise(self):
        """带噪声格式（AI 返回 "B. 答案内容"）"""
        assert normalize_answer("B. 选项内容") == "B"
        assert normalize_answer("A、正确答案") == "A"

    def test_deduplication(self):
        """重复字母去重"""
        assert normalize_answer("AABB") == "AB"
        assert normalize_answer("CCCC") == "C"

    def test_empty_and_none(self):
        assert normalize_answer("") == ""
        assert normalize_answer(None) == ""
        assert normalize_answer("  ") == ""

    def test_no_valid_letters(self):
        """完全无效输入"""
        assert normalize_answer("XYZ") == ""
        assert normalize_answer("123") == ""
        assert normalize_answer("答案") == ""

    def test_mixed_case(self):
        assert normalize_answer("aBcD") == "ABCD"


# ========== answers_match ==========

class TestAnswersMatch:
    """测试答案比较"""

    def test_exact_match(self):
        assert answers_match("A", "A") is True
        assert answers_match("B", "B") is True

    def test_case_insensitive(self):
        assert answers_match("a", "A") is True
        assert answers_match("bcd", "BCD") is True

    def test_order_insensitive(self):
        """多选题选项顺序无关"""
        assert answers_match("BCA", "ABC") is True
        assert answers_match("DC", "CD") is True

    def test_mismatch(self):
        assert answers_match("A", "B") is False
        assert answers_match("AB", "AC") is False

    def test_empty_both(self):
        assert answers_match("", "") is True

    def test_empty_vs_non_empty(self):
        assert answers_match("", "A") is False
        assert answers_match("A", "") is False

    def test_noisy_inputs(self):
        assert answers_match("B. 选项B", "B") is True
        assert answers_match("A", "A. 正确答案") is True


# ========== quality_from_result ==========

class TestQualityFromResult:
    """测试 SM-2 quality 评分映射"""

    def test_correct_sure(self):
        assert quality_from_result(True, "sure") == 5

    def test_correct_unsure(self):
        assert quality_from_result(True, "unsure") == 4

    def test_correct_no(self):
        assert quality_from_result(True, "no") == 3

    def test_wrong_sure(self):
        """盲区：答错+确定"""
        assert quality_from_result(False, "sure") == 0

    def test_wrong_unsure(self):
        assert quality_from_result(False, "unsure") == 1

    def test_wrong_no(self):
        assert quality_from_result(False, "no") == 1


# ========== sm2_update ==========

def _make_wa(**kwargs):
    """构造模拟的 WrongAnswerV2 对象"""
    defaults = {
        "sm2_ef": 2.5,
        "sm2_repetitions": 0,
        "sm2_interval": 0,
        "next_review_date": None,
        "mastery_status": "active",
        "archived_at": None,
    }
    defaults.update(kwargs)
    return SimpleNamespace(**defaults)


class TestSm2Update:
    """测试 SM-2 间隔重复算法"""

    def test_first_correct(self):
        """第一次答对 → interval=1, reps=1"""
        wa = _make_wa()
        sm2_update(wa, quality=5)
        assert wa.sm2_repetitions == 1
        assert wa.sm2_interval == 1
        assert wa.next_review_date == date.today() + timedelta(days=1)
        assert wa.mastery_status == "active"

    def test_second_correct(self):
        """第二次答对 → interval=3, reps=2"""
        wa = _make_wa(sm2_repetitions=1, sm2_interval=1)
        sm2_update(wa, quality=5)
        assert wa.sm2_repetitions == 2
        assert wa.sm2_interval == 3

    def test_third_correct(self):
        """第三次答对 → interval=7, reps=3 → auto-archive"""
        wa = _make_wa(sm2_repetitions=2, sm2_interval=3)
        sm2_update(wa, quality=5)
        assert wa.sm2_repetitions == 3
        assert wa.sm2_interval == 7
        assert wa.mastery_status == "archived"
        assert wa.archived_at is not None

    def test_fourth_correct_uses_ef(self):
        """第四次答对 → interval = int(prev * ef)"""
        wa = _make_wa(sm2_repetitions=3, sm2_interval=7, sm2_ef=2.5)
        sm2_update(wa, quality=5)
        assert wa.sm2_repetitions == 4
        # interval = int(7 * 2.6) = 18 (EF updated before capping)
        # 实际 EF 先更新为 2.5 + 0.1 = 2.6，然后 interval = min(int(7 * 2.6), 60)
        # 但注意 interval 计算用的是更新前还是更新后的 EF？
        # 代码逻辑：先算 interval = min(int(interval * ef), 60)，再更新 ef
        assert wa.sm2_interval == min(int(7 * 2.5), 60)  # 17

    def test_wrong_answer_resets(self):
        """答错 → reps=0, interval=1, EF 下降"""
        wa = _make_wa(sm2_repetitions=2, sm2_interval=3, sm2_ef=2.5)
        sm2_update(wa, quality=1)
        assert wa.sm2_repetitions == 0
        assert wa.sm2_interval == 1
        assert wa.sm2_ef == 2.3  # 2.5 - 0.2
        assert wa.mastery_status == "active"  # 不会 archive

    def test_ef_floor_at_1_3(self):
        """EF 下限 1.3"""
        wa = _make_wa(sm2_ef=1.3)
        sm2_update(wa, quality=0)
        assert wa.sm2_ef == 1.3  # max(1.3, 1.3 - 0.2) = 1.3

    def test_interval_cap_at_60(self):
        """间隔上限 60 天"""
        wa = _make_wa(sm2_repetitions=5, sm2_interval=50, sm2_ef=2.5)
        sm2_update(wa, quality=5)
        assert wa.sm2_interval <= 60

    def test_quality_3_boundary(self):
        """quality=3 是正确的边界（答对+不确定）"""
        wa = _make_wa()
        sm2_update(wa, quality=3)
        assert wa.sm2_repetitions == 1  # 答对分支
        assert wa.sm2_interval == 1

    def test_quality_2_is_wrong(self):
        """quality=2 走答错分支"""
        wa = _make_wa(sm2_repetitions=2, sm2_interval=3)
        sm2_update(wa, quality=2)
        assert wa.sm2_repetitions == 0  # 重置
        assert wa.sm2_interval == 1

    def test_auto_archive_threshold(self):
        """reps >= 3 → 自动归档"""
        wa = _make_wa(sm2_repetitions=2, sm2_interval=3)
        sm2_update(wa, quality=4)
        assert wa.mastery_status == "archived"

    def test_no_archive_below_threshold(self):
        """reps < 3 → 不归档"""
        wa = _make_wa(sm2_repetitions=1, sm2_interval=1)
        sm2_update(wa, quality=5)
        assert wa.mastery_status == "active"


# ========== 跳过回忆/自证的 quality 降档 ==========

class TestQualityPenalties:
    """测试跳过回忆/自证时的 quality 降档逻辑（模拟 submit_retry 中的行为）"""

    def test_skip_recall_penalty(self):
        """跳过回忆 → quality - 1"""
        base_quality = quality_from_result(True, "sure")  # 5
        penalized = max(0, base_quality - 1)  # 4
        assert penalized == 4

    def test_skip_rationale_penalty(self):
        """跳过自证 → quality - 1"""
        base_quality = quality_from_result(True, "unsure")  # 4
        penalized = max(0, base_quality - 1)  # 3
        assert penalized == 3

    def test_both_skip_penalty(self):
        """跳过回忆 + 跳过自证 → quality - 2"""
        base_quality = quality_from_result(True, "sure")  # 5
        penalized = max(0, base_quality - 2)  # 3
        assert penalized == 3

    def test_penalty_floor_at_zero(self):
        """惩罚不会降到 0 以下"""
        base_quality = quality_from_result(False, "sure")  # 0
        penalized = max(0, base_quality - 1)  # 0
        assert penalized == 0

    def test_skip_recall_impacts_sm2(self):
        """跳过回忆：quality 5→4，SM-2 结果不同"""
        wa_full = _make_wa()
        sm2_update(wa_full, quality=5)
        ef_full = wa_full.sm2_ef

        wa_skip = _make_wa()
        sm2_update(wa_skip, quality=4)  # 跳过回忆降档
        ef_skip = wa_skip.sm2_ef

        assert ef_skip < ef_full  # 跳过回忆 → EF 更低

    def test_skip_rationale_prevents_fast_archive(self):
        """跳过自证可能阻止快速归档（通过降低 quality）"""
        # quality=5 走答对，但降到4仍然走答对，都会增加 reps
        # 关键场景：quality 降到 2 可能会走答错分支
        base_quality = quality_from_result(True, "no")  # 3
        penalized = max(0, base_quality - 1)  # 2 → 走答错分支！
        wa = _make_wa(sm2_repetitions=2, sm2_interval=3)
        sm2_update(wa, penalized)
        assert wa.sm2_repetitions == 0  # 被重置了！不会归档
        assert wa.mastery_status == "active"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
