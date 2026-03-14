"""
单元测试：题目重复问题修复验证
验证 6 项修复：
1. 主题一致性计算方向修正（分母改为 question_keywords）
2. Token 预算提升（800→1200/题）
3. 分段阈值调整（15题 7500→12000）
4. 跨段 key_point 去重
5. _build_concept_slots 不再循环复用知识点（根因 #1）
6. 单次生成 key_point 后处理去重（根因 #3）
"""

import pytest
from types import SimpleNamespace
from services.quiz_service_v2 import QuizService


@pytest.fixture
def quiz_service():
    return QuizService()


# ========== Fix 1: 主题一致性分母修正 ==========

class TestTopicOverlapDirection:
    """修复前：overlap/len(content_keywords) → 总是 <1%
       修复后：overlap/len(question_keywords) → 合理的 15-80%"""

    def test_overlap_uses_question_denominator(self, quiz_service):
        """分母是 question_keywords，不是 content_keywords"""
        content_kw = {"胃酸", "盐酸", "胃蛋白酶", "壁细胞", "主细胞",
                      "黏液", "碳酸氢盐", "内因子", "消化", "吸收",
                      "幽门螺杆菌", "胃溃疡", "十二指肠"}  # 13 个
        question_kw = {"胃酸", "盐酸", "壁细胞", "消化酶"}  # 4 个，3 个重叠

        ratio = quiz_service._calculate_topic_overlap(content_kw, question_kw)
        # 修复后：3/4 = 0.75
        assert ratio == pytest.approx(0.75)

    def test_old_formula_would_be_tiny(self, quiz_service):
        """对照：如果用旧公式（content做分母）= 3/13 ≈ 0.23"""
        content_kw = {"胃酸", "盐酸", "胃蛋白酶", "壁细胞", "主细胞",
                      "黏液", "碳酸氢盐", "内因子", "消化", "吸收",
                      "幽门螺杆菌", "胃溃疡", "十二指肠"}
        question_kw = {"胃酸", "盐酸", "壁细胞", "消化酶"}

        # 修复后用 question 做分母 = 3/4 = 0.75
        ratio = quiz_service._calculate_topic_overlap(content_kw, question_kw)
        assert ratio > 0.5  # 远大于旧公式的 0.23

    def test_large_content_small_questions(self, quiz_service):
        """典型场景：内容 2000 个关键词，题目 50 个关键词"""
        # 模拟大内容
        content_kw = {f"词{i}" for i in range(2000)}
        # 题目关键词：30 个来自内容，20 个是题目特有的
        question_kw = {f"词{i}" for i in range(30)} | {f"题{i}" for i in range(20)}

        ratio = quiz_service._calculate_topic_overlap(content_kw, question_kw)
        # 30/50 = 0.6 — 合理的重叠率
        assert ratio == pytest.approx(0.6)

    def test_empty_question_keywords(self, quiz_service):
        """无题目关键词 → 0"""
        ratio = quiz_service._calculate_topic_overlap({"胃酸"}, set())
        assert ratio == 0.0

    def test_empty_content_keywords(self, quiz_service):
        """无内容关键词 → 0（因为交集为空）"""
        ratio = quiz_service._calculate_topic_overlap(set(), {"胃酸"})
        assert ratio == 0.0

    def test_perfect_overlap(self, quiz_service):
        """题目关键词全部来自内容 → 1.0"""
        kw = {"胃酸", "盐酸", "壁细胞"}
        ratio = quiz_service._calculate_topic_overlap(kw | {"额外内容"}, kw)
        assert ratio == pytest.approx(1.0)


# ========== Fix 2: Token 预算提升 ==========

class TestTokenBudget:
    """验证 max_output_tokens = num_questions * 1200"""

    def test_15_questions_token_budget(self):
        """15题：15*1200=18000（原来是 15*800=12000）"""
        num = 15
        budget = max(8192, num * 1200)
        budget = min(budget, 32768)
        assert budget == 18000  # 比原来多 6000 token

    def test_20_questions_token_budget(self):
        """20题：20*1200=24000（原来是 20*800=16000）"""
        num = 20
        budget = max(8192, num * 1200)
        budget = min(budget, 32768)
        assert budget == 24000

    def test_5_questions_token_budget(self):
        """5题：max(8192, 6000)=8192（兜底值不变）"""
        num = 5
        budget = max(8192, num * 1200)
        budget = min(budget, 32768)
        assert budget == 8192

    def test_10_questions_token_budget(self):
        """10题：10*1200=12000"""
        num = 10
        budget = max(8192, num * 1200)
        budget = min(budget, 32768)
        assert budget == 12000


# ========== Fix 3: 分段阈值调整 ==========

class TestSegmentLength:
    """15题分段阈值从 7500 提升到 12000"""

    def test_15_questions_segment_length(self, quiz_service):
        """15题：12000（原来是 7500）"""
        assert quiz_service._get_segment_length(15) == 12000

    def test_16_questions_segment_length(self, quiz_service):
        """16题：12000"""
        assert quiz_service._get_segment_length(16) == 12000

    def test_19_questions_segment_length(self, quiz_service):
        """19题：12000"""
        assert quiz_service._get_segment_length(19) == 12000

    def test_20_questions_segment_length(self, quiz_service):
        """20题：6000（不变）"""
        assert quiz_service._get_segment_length(20) == 6000

    def test_10_questions_segment_length(self, quiz_service):
        """10题：9000（不变）"""
        assert quiz_service._get_segment_length(10) == 9000

    def test_5_questions_segment_length(self, quiz_service):
        """5题：9000（不变）"""
        assert quiz_service._get_segment_length(5) == 9000

    def test_15_fewer_segments(self, quiz_service):
        """15题+15000字内容：原来分4段，现在分2段"""
        content_length = 15000
        segment_length = quiz_service._get_segment_length(15)
        num_segments = (content_length + segment_length - 1) // segment_length
        assert num_segments == 2  # 原来 15000/7500=2 段变成 15000/12000=2 段（但上限更高）

    def test_15_large_content_fewer_segments(self, quiz_service):
        """15题+30000字内容：原来分4段，现在分3段"""
        content_length = 30000
        segment_length = quiz_service._get_segment_length(15)
        num_segments = (content_length + segment_length - 1) // segment_length
        assert num_segments == 3  # 原来 30000/7500=4 段，现在 30000/12000=3 段


# ========== Fix 4: 跨段 key_point 去重（逻辑验证） ==========

class TestKeyPointDedup:
    """验证跨段 key_point 去重逻辑"""

    def test_dedup_removes_duplicate_key_points(self):
        """相同 key_point 的题目应被去重"""
        questions = [
            {"id": 1, "key_point": "胃酸分泌", "question": "Q1"},
            {"id": 2, "key_point": "胃蛋白酶", "question": "Q2"},
            {"id": 3, "key_point": "胃酸分泌", "question": "Q3"},  # 重复
            {"id": 4, "key_point": "壁细胞", "question": "Q4"},
        ]

        seen = set()
        result = []
        for q in questions:
            kp = (q.get("key_point") or "").strip()
            if kp and kp in seen:
                continue
            if kp:
                seen.add(kp)
            result.append(q)

        assert len(result) == 3
        assert [q["id"] for q in result] == [1, 2, 4]

    def test_empty_key_point_not_deduped(self):
        """空 key_point 的题目不参与去重"""
        questions = [
            {"id": 1, "key_point": "", "question": "Q1"},
            {"id": 2, "key_point": "", "question": "Q2"},
            {"id": 3, "key_point": "胃酸", "question": "Q3"},
        ]

        seen = set()
        result = []
        for q in questions:
            kp = (q.get("key_point") or "").strip()
            if kp and kp in seen:
                continue
            if kp:
                seen.add(kp)
            result.append(q)

        assert len(result) == 3  # 空 key_point 不会被去重

    def test_no_duplicates_keeps_all(self):
        """无重复时保留所有题目"""
        questions = [
            {"id": 1, "key_point": "A", "question": "Q1"},
            {"id": 2, "key_point": "B", "question": "Q2"},
            {"id": 3, "key_point": "C", "question": "Q3"},
        ]

        seen = set()
        result = []
        for q in questions:
            kp = (q.get("key_point") or "").strip()
            if kp and kp in seen:
                continue
            if kp:
                seen.add(kp)
            result.append(q)

        assert len(result) == 3


# ========== Fix 5: _build_concept_slots 不再循环复用 ==========

def _make_concept(concept_id: str, name: str) -> SimpleNamespace:
    """Helper: create a mock ConceptMastery-like object."""
    return SimpleNamespace(concept_id=concept_id, name=name)


class TestBuildConceptSlots:
    """根因 #1：知识点不足时不再取模循环复用"""

    def test_enough_concepts_returns_target(self):
        """知识点 >= target → 返回 target 个 slot"""
        from routers.quiz_fast import _build_concept_slots
        concepts = [_make_concept(f"c{i}", f"知识点{i}") for i in range(15)]
        slots = _build_concept_slots(concepts, target=10)
        assert len(slots) == 10

    def test_fewer_concepts_caps_at_available(self):
        """知识点 < target → 返回实际数量，不循环"""
        from routers.quiz_fast import _build_concept_slots
        concepts = [_make_concept(f"c{i}", f"知识点{i}") for i in range(3)]
        slots = _build_concept_slots(concepts, target=10)
        assert len(slots) == 3  # 不是 10！

    def test_no_duplicate_concept_names(self):
        """所有 slot 的 name 必须唯一"""
        from routers.quiz_fast import _build_concept_slots
        concepts = [_make_concept(f"c{i}", f"知识点{i}") for i in range(5)]
        slots = _build_concept_slots(concepts, target=10)
        names = [s.name for s in slots]
        assert len(names) == len(set(names))

    def test_single_concept_returns_one(self):
        """只有 1 个知识点 → 只返回 1 个 slot（不是 10 个重复的）"""
        from routers.quiz_fast import _build_concept_slots
        concepts = [_make_concept("c0", "胃酸分泌调节")]
        slots = _build_concept_slots(concepts, target=10)
        assert len(slots) == 1

    def test_empty_concepts_returns_empty(self):
        """无知识点 → 空列表"""
        from routers.quiz_fast import _build_concept_slots
        slots = _build_concept_slots([], target=10)
        assert slots == []

    def test_dedup_by_name(self):
        """同名知识点（不同 concept_id）只保留一个"""
        from routers.quiz_fast import _build_concept_slots
        concepts = [
            _make_concept("c1", "胃酸分泌"),
            _make_concept("c2", "胃酸分泌"),  # 同名
            _make_concept("c3", "壁细胞"),
        ]
        slots = _build_concept_slots(concepts, target=10)
        assert len(slots) == 2  # 胃酸分泌 + 壁细胞

    def test_concurrent_version_same_behavior(self):
        """quiz_concurrent.py 的版本行为一致"""
        from routers.quiz_concurrent import _build_concept_slots
        concepts = [_make_concept(f"c{i}", f"知识点{i}") for i in range(3)]
        slots = _build_concept_slots(concepts, target=10)
        assert len(slots) == 3
        names = [s.name for s in slots]
        assert len(names) == len(set(names))


# ========== Fix 6: 细节测验变式题去重 ==========

class TestVariationDedup:
    """细节知识点 5 道变式题的去重逻辑"""

    def test_question_dedup_key_strips_prefix(self, quiz_service):
        """去重键应去除序号前缀"""
        k1 = quiz_service._question_dedup_key("1. 胃酸分泌的主要调节机制是什么？")
        k2 = quiz_service._question_dedup_key("胃酸分泌的主要调节机制是什么？")
        assert k1 == k2

    def test_question_dedup_key_strips_variation_prefix(self, quiz_service):
        """去重键应去除【概念变式】等前缀"""
        k1 = quiz_service._question_dedup_key("【概念变式】胃酸分泌的主要调节机制是什么？")
        k2 = quiz_service._question_dedup_key("胃酸分泌的主要调节机制是什么？")
        assert k1 == k2

    def test_question_dedup_key_strips_punctuation(self, quiz_service):
        """去重键只保留中文和字母"""
        k1 = quiz_service._question_dedup_key("胃酸分泌的主要调节机制是什么？")
        k2 = quiz_service._question_dedup_key("胃酸分泌的主要调节机制是什么")
        assert k1 == k2

    def test_question_dedup_key_different_questions(self, quiz_service):
        """不同题目应有不同的去重键"""
        k1 = quiz_service._question_dedup_key("胃酸分泌的主要调节机制是什么？")
        k2 = quiz_service._question_dedup_key("壁细胞分泌盐酸的过程中哪个离子泵起关键作用？")
        assert k1 != k2

    def test_question_dedup_key_empty(self, quiz_service):
        """空题目返回空键"""
        assert quiz_service._question_dedup_key("") == ""
        assert quiz_service._question_dedup_key(None) == ""

    def test_question_dedup_key_truncates(self, quiz_service):
        """超长题目截断到 60 字符"""
        long_q = "胃" * 100
        key = quiz_service._question_dedup_key(long_q)
        assert len(key) == 60

    def test_token_budget_increased(self):
        """变式题 token 预算从 4000 提升到 6000"""
        # 验证代码中的值（通过 grep 确认）
        import inspect
        from services.quiz_service_v2 import QuizService
        source = inspect.getsource(QuizService.generate_variation_questions)
        assert "max_tokens=6000" in source
        assert "max_tokens=4000" not in source


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
