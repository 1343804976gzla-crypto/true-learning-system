from __future__ import annotations

import pytest

import services.quiz_service_v2 as quiz_service_v2
from services.llmlingua_compactor import LLMLinguaQuizCompactor


class _FakePromptCompressor:
    def compress_prompt(self, context, **kwargs):
        assert context
        return {
            "compressed_prompt": "压缩后知识摘要：胃酸、胃泌素、胃蛋白酶原、幽门螺杆菌、消化性溃疡。",
            "origin_tokens": 120,
            "compressed_tokens": 72,
        }


def test_llmlingua_compactor_skips_short_text(monkeypatch):
    monkeypatch.setenv("QUIZ_LINGUA_ENABLED", "true")
    monkeypatch.setenv("QUIZ_LINGUA_MIN_CHARS", "120")

    compactor = LLMLinguaQuizCompactor()
    result = compactor.compact_for_quiz("过短文本，不需要压缩。")

    assert result.applied is False
    assert result.strategy == "skipped_short_text"
    assert result.final_text == "过短文本，不需要压缩。"


def test_llmlingua_compactor_uses_digest_and_llmlingua(monkeypatch):
    monkeypatch.setenv("QUIZ_LINGUA_ENABLED", "true")
    monkeypatch.setenv("QUIZ_LINGUA_MIN_CHARS", "40")

    compactor = LLMLinguaQuizCompactor()
    compactor.min_chars = 0
    monkeypatch.setattr(
        compactor,
        "_build_digest",
        lambda content: {
            "text": "知识导向摘要：胃酸、胃泌素、胃蛋白酶原、幽门螺杆菌。",
            "blocks": ["知识导向摘要：胃酸、胃泌素、胃蛋白酶原、幽门螺杆菌。"],
            "glossary": ["胃酸", "胃泌素", "胃蛋白酶原", "幽门螺杆菌"],
            "kept_paragraphs": 3,
        },
    )
    monkeypatch.setattr(compactor, "_get_compressor", lambda: _FakePromptCompressor())

    long_text = (
        "第一部分：胃液分泌基础。\n"
        "胃液由壁细胞、主细胞和黏液细胞分泌。壁细胞分泌盐酸和内因子，内因子促进维生素B12吸收。"
        "主细胞分泌胃蛋白酶原，胃酸能够激活胃蛋白酶原。\n"
        "第二部分：胃酸调节机制。\n"
        "胃泌素、乙酰胆碱和组胺均可促进胃酸分泌。神经、体液和局部因素共同参与调节。"
        "这一部分课堂里反复强调胃酸调节机制、胃酸调节机制、胃酸调节机制。\n"
        "第三部分：临床联系。\n"
        "幽门螺杆菌感染和非甾体抗炎药会增加消化性溃疡风险。黏膜防御减弱时，胃酸和胃蛋白酶的攻击作用更明显。"
    )
    result = compactor.compact_for_quiz(long_text)

    assert result.applied is True
    assert result.strategy == "digest_plus_llmlingua"
    assert result.final_text == "压缩后知识摘要：胃酸、胃泌素、胃蛋白酶原、幽门螺杆菌、消化性溃疡。"
    assert result.saved_tokens == 48
    assert result.origin_tokens == 120
    assert result.compressed_tokens == 72


class _FakeAI:
    def __init__(self):
        self.calls = []

    async def generate_json(self, prompt, schema, **kwargs):
        self.calls.append({"prompt": prompt, "schema": schema, "kwargs": kwargs})
        return {
            "paper_title": "测试试卷",
            "total_questions": 5,
            "chapter_prediction": {
                "book": "生理学",
                "chapter_id": "physio_ch16",
                "chapter_title": "口腔食管和胃内消化",
                "confidence": "high",
            },
            "difficulty_distribution": {"基础": 3, "提高": 1, "难题": 1},
            "questions": [
                {
                    "id": 1,
                    "type": "A1",
                    "difficulty": "基础",
                    "question": "胃酸的主要作用是什么？",
                    "options": {
                        "A": "激活胃蛋白酶原",
                        "B": "促进铁吸收",
                        "C": "杀灭细菌",
                        "D": "以上都是",
                        "E": "以上都不是",
                    },
                    "correct_answer": "D",
                    "explanation": "胃酸具有多种生理作用。",
                    "key_point": "胃酸的生理作用",
                    "related_questions": "[2,3]",
                }
            ],
            "summary": {},
        }


class _FakeCompactor:
    def compact_for_quiz(self, content):
        from services.llmlingua_compactor import QuizCompactionResult

        return QuizCompactionResult(
            applied=True,
            strategy="digest_plus_llmlingua",
            final_text="压缩知识摘要：胃酸、胃泌素、胃蛋白酶原、幽门螺杆菌。",
            raw_text=content,
            digest_text="知识导向摘要：胃酸、胃泌素、胃蛋白酶原、幽门螺杆菌。",
            glossary=["胃酸", "胃泌素", "胃蛋白酶原", "幽门螺杆菌"],
            model_name="fake-model",
            device="cpu",
            raw_chars=len(content),
            digest_chars=48,
            final_chars=28,
            origin_tokens=100,
            compressed_tokens=60,
            saved_tokens=40,
            compression_rate=0.6,
            context_blocks=3,
            digest_paragraphs=3,
        )


@pytest.mark.asyncio
async def test_single_paper_uses_compacted_prompt_and_audit(monkeypatch):
    fake_ai = _FakeAI()
    monkeypatch.setattr(quiz_service_v2, "get_ai_client", lambda: fake_ai)
    monkeypatch.setattr(quiz_service_v2, "get_quiz_llmlingua_compactor", lambda: _FakeCompactor())

    service = quiz_service_v2.QuizService()
    monkeypatch.setattr(service, "_get_chapter_catalog", lambda content: "生理学 -> 胃内消化")
    monkeypatch.setattr(
        service,
        "_infer_chapter_prediction",
        lambda content: {
            "book": "生理学",
            "chapter_id": "physio_ch16",
            "chapter_title": "口腔食管和胃内消化",
            "confidence": "high",
        },
    )

    async def _validate_topic_consistency(**kwargs):
        return True, 1.0, ""

    monkeypatch.setattr(service, "_validate_topic_consistency", _validate_topic_consistency)

    raw_marker = "原始超长讲义片段"
    uploaded_content = (raw_marker + " 胃酸和胃泌素相关内容。") * 600
    result = await service._generate_single_paper(
        uploaded_content=uploaded_content,
        num_questions=5,
        difficulty_distribution={"基础": 0.6, "提高": 0.2, "难题": 0.2},
    )

    assert fake_ai.calls
    call = fake_ai.calls[0]
    assert "压缩知识摘要" in call["prompt"]
    assert raw_marker not in call["prompt"]
    assert call["kwargs"]["audit_context"]["quiz_compaction_applied"] is True
    assert call["kwargs"]["audit_context"]["quiz_compaction_saved_tokens"] == 40
    assert result["summary"]["context_compaction"]["enabled"] is True
    assert result["summary"]["context_compaction"]["saved_tokens"] == 40
