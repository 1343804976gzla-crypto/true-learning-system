"""
集成测试：重构后的错题本 API 端点
使用项目自身数据库引擎 + FastAPI TestClient + 事务回滚隔离。

覆盖场景：
1. submit_retry - 原题提交、变式提交、跳过回忆、跳过自证
2. generate_variant - 无 severity 限制、24h 缓存
3. judge_variant_answer - Phase 2 自证提交 + SM-2 更新
4. challenge/submit - SM-2 集成
5. SM-2 自动归档（连续3次正确）
6. detail 端点 SM-2 字段
"""

import sys
sys.path.insert(0, ".")

import pytest
from datetime import datetime, date, timedelta
from unittest.mock import patch

from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker, Session
from fastapi.testclient import TestClient

from models import Base, get_db, engine as prod_engine
from learning_tracking_models import WrongAnswerV2, WrongAnswerRetry
from main import app


# ========== 测试数据库 Fixture ==========

# 使用独立的 SQLite 内存引擎，避免影响生产数据
_test_engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
Base.metadata.create_all(bind=_test_engine)
_TestSession = sessionmaker(bind=_test_engine)


@pytest.fixture
def db_session():
    """每个测试用一个干净的事务，测试结束回滚"""
    connection = _test_engine.connect()
    transaction = connection.begin()
    session = _TestSession(bind=connection)

    # 嵌套事务 savepoint，让 app 内部的 commit 不会真正提交
    nested = connection.begin_nested()

    @event.listens_for(session, "after_transaction_end")
    def restart_savepoint(session, trans):
        nonlocal nested
        if trans.nested and not trans._parent.nested:
            nested = connection.begin_nested()

    yield session

    session.close()
    transaction.rollback()
    connection.close()


@pytest.fixture
def client(db_session):
    """TestClient 使用测试 session"""
    def _override_get_db():
        try:
            yield db_session
        finally:
            pass

    app.dependency_overrides[get_db] = _override_get_db
    with TestClient(app, raise_server_exceptions=False) as c:
        yield c
    app.dependency_overrides.clear()


def _create_wrong_answer(db: Session, **kwargs) -> WrongAnswerV2:
    """创建测试错题"""
    import time
    defaults = {
        "question_fingerprint": f"fp_{time.time()}_{id(kwargs)}",
        "question_text": "测试题目：哪个是正确答案？",
        "options": {"A": "选项A", "B": "选项B", "C": "选项C", "D": "选项D"},
        "correct_answer": "B",
        "explanation": "B 是正确的，因为...",
        "key_point": "测试知识点",
        "question_type": "A1",
        "difficulty": "基础",
        "severity_tag": "normal",
        "mastery_status": "active",
        "error_count": 1,
        "encounter_count": 1,
        "retry_count": 0,
        "sm2_ef": 2.5,
        "sm2_interval": 0,
        "sm2_repetitions": 0,
        "first_wrong_at": datetime.now(),
        "last_wrong_at": datetime.now(),
        "created_at": datetime.now(),
        "updated_at": datetime.now(),
    }
    defaults.update(kwargs)
    wa = WrongAnswerV2(**defaults)
    db.add(wa)
    db.flush()
    return wa


# ========== Test: submit_retry 基本功能 ==========

class TestSubmitRetry:

    def test_correct_answer(self, client, db_session):
        """答对原题 → is_correct=True, SM-2 更新"""
        wa = _create_wrong_answer(db_session)

        resp = client.post(f"/api/wrong-answers/{wa.id}/retry", json={
            "user_answer": "B",
            "confidence": "sure",
        })
        assert resp.status_code == 200
        data = resp.json()

        assert data["is_correct"] is True
        assert data["sm2_repetitions"] == 1
        assert data["sm2_interval"] == 1
        assert data["auto_archived"] is False
        assert data["can_archive"] is True

    def test_wrong_answer(self, client, db_session):
        """答错 → is_correct=False, error_count 增加"""
        wa = _create_wrong_answer(db_session)

        resp = client.post(f"/api/wrong-answers/{wa.id}/retry", json={
            "user_answer": "A",
            "confidence": "unsure",
        })
        assert resp.status_code == 200
        data = resp.json()

        assert data["is_correct"] is False
        assert data["error_count"] == 2
        assert data["sm2_repetitions"] == 0
        assert data["sm2_interval"] == 1

    def test_wrong_and_sure_becomes_critical(self, client, db_session):
        """答错+确定 → severity 升级为 critical"""
        wa = _create_wrong_answer(db_session, severity_tag="normal")

        resp = client.post(f"/api/wrong-answers/{wa.id}/retry", json={
            "user_answer": "A",
            "confidence": "sure",
        })
        assert resp.status_code == 200
        assert resp.json()["severity_tag"] == "critical"

    def test_correct_sure_landmine_downgrade(self, client, db_session):
        """答对+确定 → landmine 降级为 normal"""
        wa = _create_wrong_answer(db_session, severity_tag="landmine")

        resp = client.post(f"/api/wrong-answers/{wa.id}/retry", json={
            "user_answer": "B",
            "confidence": "sure",
        })
        assert resp.status_code == 200
        assert resp.json()["severity_tag"] == "normal"

    def test_not_found(self, client, db_session):
        """不存在的 ID → 404"""
        resp = client.post("/api/wrong-answers/99999/retry", json={
            "user_answer": "A",
        })
        assert resp.status_code == 404

    def test_variant_answer_judge(self, client, db_session):
        """变式题提交 → 用 variant_answer 判断"""
        wa = _create_wrong_answer(db_session, variant_data={
            "variant_question": "变式题目",
            "variant_options": {"A": "VA", "B": "VB", "C": "VC", "D": "VD"},
            "variant_answer": "C",
            "generated_at": datetime.now().isoformat(),
        })

        resp = client.post(f"/api/wrong-answers/{wa.id}/retry", json={
            "user_answer": "C",
            "confidence": "sure",
            "is_variant": True,
        })
        assert resp.status_code == 200
        data = resp.json()
        assert data["is_correct"] is True
        assert data["variant_answer"] == "C"

    def test_variant_wrong(self, client, db_session):
        """变式题答错"""
        wa = _create_wrong_answer(db_session, variant_data={
            "variant_question": "变式题目",
            "variant_options": {"A": "VA", "B": "VB", "C": "VC", "D": "VD"},
            "variant_answer": "C",
            "generated_at": datetime.now().isoformat(),
        })

        resp = client.post(f"/api/wrong-answers/{wa.id}/retry", json={
            "user_answer": "A",
            "confidence": "unsure",
            "is_variant": True,
        })
        assert resp.status_code == 200
        assert resp.json()["is_correct"] is False


# ========== Test: 跳过回忆/自证 SM-2 惩罚 ==========

class TestSkipPenalties:

    def test_skip_recall_lowers_quality(self, client, db_session):
        """跳过回忆 → quality 降1档，EF 更低"""
        wa_normal = _create_wrong_answer(db_session, question_fingerprint="fp_sr_normal")
        wa_skip = _create_wrong_answer(db_session, question_fingerprint="fp_sr_skip")

        resp1 = client.post(f"/api/wrong-answers/{wa_normal.id}/retry", json={
            "user_answer": "B", "confidence": "sure",
        })
        resp2 = client.post(f"/api/wrong-answers/{wa_skip.id}/retry", json={
            "user_answer": "B", "confidence": "sure", "skip_recall": True,
        })

        assert resp2.json()["sm2_ef"] < resp1.json()["sm2_ef"]

    def test_skip_rationale_lowers_quality(self, client, db_session):
        """跳过自证 → quality 降1档"""
        wa_normal = _create_wrong_answer(db_session, question_fingerprint="fp_sra_norm")
        wa_skip = _create_wrong_answer(db_session, question_fingerprint="fp_sra_skip")

        resp1 = client.post(f"/api/wrong-answers/{wa_normal.id}/retry", json={
            "user_answer": "B", "confidence": "sure",
        })
        resp2 = client.post(f"/api/wrong-answers/{wa_skip.id}/retry", json={
            "user_answer": "B", "confidence": "sure", "skipped_rationale": True,
        })

        assert resp2.json()["sm2_ef"] < resp1.json()["sm2_ef"]

    def test_both_skips_double_penalty(self, client, db_session):
        """跳过回忆 + 跳过自证 → quality 降2档"""
        wa = _create_wrong_answer(db_session)

        resp = client.post(f"/api/wrong-answers/{wa.id}/retry", json={
            "user_answer": "B", "confidence": "sure",
            "skip_recall": True, "skipped_rationale": True,
        })
        data = resp.json()
        assert data["sm2_ef"] < 2.5


# ========== Test: SM-2 自动归档 ==========

class TestAutoArchive:

    def test_three_consecutive_correct_archives(self, client, db_session):
        """连续3次答对 → 自动归档"""
        wa = _create_wrong_answer(db_session)

        for i in range(3):
            resp = client.post(f"/api/wrong-answers/{wa.id}/retry", json={
                "user_answer": "B", "confidence": "sure",
            })

        data = resp.json()
        assert data["auto_archived"] is True
        assert data["can_archive"] is False

    def test_wrong_answer_breaks_streak(self, client, db_session):
        """答错重置连续计数"""
        wa = _create_wrong_answer(db_session)

        client.post(f"/api/wrong-answers/{wa.id}/retry", json={
            "user_answer": "B", "confidence": "sure",
        })
        client.post(f"/api/wrong-answers/{wa.id}/retry", json={
            "user_answer": "B", "confidence": "sure",
        })
        resp = client.post(f"/api/wrong-answers/{wa.id}/retry", json={
            "user_answer": "A", "confidence": "unsure",
        })
        assert resp.json()["auto_archived"] is False
        assert resp.json()["sm2_repetitions"] == 0


# ========== Test: generate_variant ==========

class TestGenerateVariant:

    @patch("services.variant_surgery_service.generate_variant")
    def test_normal_severity_can_generate(self, mock_gen, client, db_session):
        """normal severity 也可以生成变式题"""
        mock_gen.return_value = {
            "variant_question": "变式问题",
            "variant_options": {"A": "a", "B": "b", "C": "c", "D": "d"},
            "variant_answer": "A",
            "transform_type": "反向推理",
            "core_knowledge": "核心考点",
            "generated_at": datetime.now().isoformat(),
        }

        wa = _create_wrong_answer(db_session, severity_tag="normal")

        resp = client.post(f"/api/wrong-answers/{wa.id}/variant/generate")
        assert resp.status_code == 200
        data = resp.json()
        assert data["variant_question"] == "变式问题"
        assert data["variant_answer"] == "A"
        assert data["cached"] is False

    def test_cached_variant_within_24h(self, client, db_session):
        """24h 内缓存直接返回"""
        wa = _create_wrong_answer(db_session, variant_data={
            "variant_question": "缓存变式",
            "variant_options": {"A": "a", "B": "b", "C": "c", "D": "d"},
            "variant_answer": "B",
            "generated_at": datetime.now().isoformat(),
            "transform_type": "情景变换",
            "core_knowledge": "缓存考点",
        })

        resp = client.post(f"/api/wrong-answers/{wa.id}/variant/generate")
        assert resp.status_code == 200
        data = resp.json()
        assert data["cached"] is True
        assert data["variant_answer"] == "B"

    def test_not_found(self, client, db_session):
        resp = client.post("/api/wrong-answers/99999/variant/generate")
        assert resp.status_code == 404


# ========== Test: judge_variant_answer ==========

class TestJudgeVariantAnswer:

    @patch("services.variant_surgery_service.evaluate_rationale")
    def test_correct_with_rationale(self, mock_eval, client, db_session):
        """变式答对 + 自证 → AI 评估 + SM-2"""
        mock_eval.return_value = {
            "verdict": "logic_closed",
            "reasoning_score": 85,
            "diagnosis": "推理完整",
            "weak_links": [],
        }

        wa = _create_wrong_answer(db_session, variant_data={
            "variant_question": "变式题",
            "variant_options": {"A": "a", "B": "b", "C": "c", "D": "d"},
            "variant_answer": "D",
        })

        resp = client.post(f"/api/wrong-answers/{wa.id}/variant/judge", json={
            "user_answer": "D",
            "confidence": "sure",
            "rationale_text": "因为D选项是正确的，原因如下...",
        })
        assert resp.status_code == 200
        data = resp.json()
        assert data["is_correct"] is True
        assert data["verdict"] == "logic_closed"
        assert data["can_archive"] is True
        assert data["sm2_repetitions"] == 1

    @patch("services.variant_surgery_service.evaluate_rationale")
    def test_wrong_with_rationale(self, mock_eval, client, db_session):
        """变式答错 + 自证 → 错误路径"""
        mock_eval.return_value = {
            "verdict": "failed",
            "reasoning_score": 20,
            "diagnosis": "理解不足",
            "weak_links": ["概念混淆"],
        }

        wa = _create_wrong_answer(db_session, variant_data={
            "variant_question": "变式题",
            "variant_options": {"A": "a", "B": "b", "C": "c", "D": "d"},
            "variant_answer": "D",
        })

        resp = client.post(f"/api/wrong-answers/{wa.id}/variant/judge", json={
            "user_answer": "A",
            "confidence": "sure",
            "rationale_text": "我觉得是A...",
        })
        assert resp.status_code == 200
        data = resp.json()
        assert data["is_correct"] is False
        assert data["verdict"] == "failed"
        assert data["severity_tag"] == "critical"

    @patch("services.variant_surgery_service.evaluate_rationale")
    def test_lucky_guess_becomes_landmine(self, mock_eval, client, db_session):
        """AI判定蒙对 → landmine"""
        mock_eval.return_value = {
            "verdict": "lucky_guess",
            "reasoning_score": 30,
            "diagnosis": "推理漏洞多",
            "weak_links": ["不理解原理"],
        }

        wa = _create_wrong_answer(db_session, variant_data={
            "variant_question": "变式题",
            "variant_options": {"A": "a", "B": "b", "C": "c", "D": "d"},
            "variant_answer": "D",
        })

        resp = client.post(f"/api/wrong-answers/{wa.id}/variant/judge", json={
            "user_answer": "D",
            "confidence": "unsure",
            "rationale_text": "可能是D吧...",
        })
        assert resp.status_code == 200
        assert resp.json()["severity_tag"] == "landmine"

    def test_no_variant_data_400(self, client, db_session):
        """没有变式数据 → 400"""
        wa = _create_wrong_answer(db_session, variant_data=None)
        resp = client.post(f"/api/wrong-answers/{wa.id}/variant/judge", json={
            "user_answer": "A",
            "rationale_text": "test",
        })
        assert resp.status_code == 400


# ========== Test: detail 端点 ==========

class TestDetailEndpoint:

    def test_sm2_fields_in_detail(self, client, db_session):
        """详情接口包含 SM-2 字段"""
        wa = _create_wrong_answer(db_session,
            sm2_ef=2.36,
            sm2_interval=3,
            sm2_repetitions=2,
            next_review_date=date.today() + timedelta(days=3),
        )

        resp = client.get(f"/api/wrong-answers/{wa.id}")
        assert resp.status_code == 200
        data = resp.json()
        assert data["sm2_ef"] == 2.36
        assert data["sm2_interval"] == 3
        assert data["sm2_repetitions"] == 2
        assert data["next_review_date"] is not None


# ========== Test: challenge/submit ==========

class TestChallengeSubmit:

    def test_challenge_submit_sm2(self, client, db_session):
        """闯关提交 → SM-2 更新"""
        wa = _create_wrong_answer(db_session)

        resp = client.post("/api/challenge/submit", json={
            "wrong_answer_id": wa.id,
            "user_answer": "B",
            "confidence": "sure",
            "time_spent_seconds": 30,
        })
        assert resp.status_code == 200
        data = resp.json()
        assert data["is_correct"] is True
        assert data["sm2_repetitions"] == 1
        assert data["sm2_interval"] == 1

    def test_challenge_variant_submit(self, client, db_session):
        """闯关变式提交"""
        wa = _create_wrong_answer(db_session, variant_data={
            "variant_question": "闯关变式",
            "variant_options": {"A": "a", "B": "b", "C": "c", "D": "d"},
            "variant_answer": "A",
            "generated_at": datetime.now().isoformat(),
        })

        resp = client.post("/api/challenge/submit", json={
            "wrong_answer_id": wa.id,
            "user_answer": "A",
            "confidence": "sure",
            "is_variant": True,
        })
        assert resp.status_code == 200
        assert resp.json()["is_correct"] is True

    def test_challenge_auto_archive(self, client, db_session):
        """闯关连续3次答对 → 自动归档"""
        wa = _create_wrong_answer(db_session)

        for _ in range(3):
            resp = client.post("/api/challenge/submit", json={
                "wrong_answer_id": wa.id,
                "user_answer": "B",
                "confidence": "sure",
            })

        data = resp.json()
        assert data["auto_archived"] is True
        assert data["sm2_repetitions"] == 3

    def test_challenge_skip_recall_penalty(self, client, db_session):
        """闯关跳过回忆 → quality 降1档"""
        wa_normal = _create_wrong_answer(db_session, question_fingerprint="fp_ch_sr_n")
        wa_skip = _create_wrong_answer(db_session, question_fingerprint="fp_ch_sr_s")

        resp1 = client.post("/api/challenge/submit", json={
            "wrong_answer_id": wa_normal.id,
            "user_answer": "B", "confidence": "sure",
        })
        resp2 = client.post("/api/challenge/submit", json={
            "wrong_answer_id": wa_skip.id,
            "user_answer": "B", "confidence": "sure",
            "skip_recall": True,
        })

        assert resp2.json()["sm2_ef"] < resp1.json()["sm2_ef"]

    def test_challenge_skip_rationale_penalty(self, client, db_session):
        """闯关跳过自证 → quality 降1档"""
        wa_normal = _create_wrong_answer(db_session, question_fingerprint="fp_ch_sra_n")
        wa_skip = _create_wrong_answer(db_session, question_fingerprint="fp_ch_sra_s")

        resp1 = client.post("/api/challenge/submit", json={
            "wrong_answer_id": wa_normal.id,
            "user_answer": "B", "confidence": "sure",
        })
        resp2 = client.post("/api/challenge/submit", json={
            "wrong_answer_id": wa_skip.id,
            "user_answer": "B", "confidence": "sure",
            "skipped_rationale": True,
        })

        assert resp2.json()["sm2_ef"] < resp1.json()["sm2_ef"]

    def test_challenge_recall_text_in_response(self, client, db_session):
        """闯关回忆文本在响应中返回"""
        wa = _create_wrong_answer(db_session)

        resp = client.post("/api/challenge/submit", json={
            "wrong_answer_id": wa.id,
            "user_answer": "B", "confidence": "sure",
            "recall_text": "这道题考的是心脏解剖",
        })
        assert resp.status_code == 200
        assert resp.json()["recall_text"] == "这道题考的是心脏解剖"

    def test_challenge_can_archive_field(self, client, db_session):
        """闯关返回 can_archive 字段"""
        wa = _create_wrong_answer(db_session)

        resp = client.post("/api/challenge/submit", json={
            "wrong_answer_id": wa.id,
            "user_answer": "B", "confidence": "sure",
        })
        data = resp.json()
        assert data["can_archive"] is True  # 答对+确定 → 可手动归档


# ========== Test: 边缘用例 ==========

class TestEdgeCases:

    def test_multi_select_answer(self, client, db_session):
        """多选题答案匹配（不同顺序）"""
        wa = _create_wrong_answer(db_session, correct_answer="ACD")

        resp = client.post(f"/api/wrong-answers/{wa.id}/retry", json={
            "user_answer": "DCA",
            "confidence": "sure",
        })
        assert resp.status_code == 200
        assert resp.json()["is_correct"] is True

    def test_multi_select_partial_wrong(self, client, db_session):
        """多选题少选 → 判错"""
        wa = _create_wrong_answer(db_session, correct_answer="ACD")

        resp = client.post(f"/api/wrong-answers/{wa.id}/retry", json={
            "user_answer": "AC",
            "confidence": "unsure",
        })
        assert resp.status_code == 200
        assert resp.json()["is_correct"] is False

    def test_noisy_user_answer(self, client, db_session):
        """用户答案带噪声"""
        wa = _create_wrong_answer(db_session, correct_answer="B")

        resp = client.post(f"/api/wrong-answers/{wa.id}/retry", json={
            "user_answer": "B. 选项B的内容",
            "confidence": "sure",
        })
        assert resp.status_code == 200
        assert resp.json()["is_correct"] is True

    def test_recall_text_stored(self, client, db_session):
        """回忆文本被存储并返回"""
        wa = _create_wrong_answer(db_session)

        resp = client.post(f"/api/wrong-answers/{wa.id}/retry", json={
            "user_answer": "B",
            "confidence": "sure",
            "recall_text": "心肌细胞动作电位相关知识",
        })
        assert resp.status_code == 200
        assert resp.json()["recall_text"] == "心肌细胞动作电位相关知识"

    def test_retry_count_increments(self, client, db_session):
        """retry_count 递增"""
        wa = _create_wrong_answer(db_session)

        for i in range(3):
            resp = client.post(f"/api/wrong-answers/{wa.id}/retry", json={
                "user_answer": "B", "confidence": "sure",
            })
            assert resp.json()["retry_count"] == i + 1

    def test_stubborn_on_double_error(self, client, db_session):
        """错误次数 >= 2 且非 critical → stubborn"""
        wa = _create_wrong_answer(db_session, error_count=1, severity_tag="normal")

        resp = client.post(f"/api/wrong-answers/{wa.id}/retry", json={
            "user_answer": "A",
            "confidence": "unsure",
        })
        assert resp.status_code == 200
        assert resp.json()["severity_tag"] == "stubborn"


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])
