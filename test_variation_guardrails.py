import asyncio
import json

import services.quiz_service_v2 as quiz_service_module


class FakeAI:
    async def generate_json(self, *args, **kwargs):
        return {
            "variations": [
                {
                    "id": 1,
                    "type": "A1",
                    "difficulty": "基础",
                    "variation_type": "概念变式",
                    "question": "在缺铁性贫血的发生发展过程中，最早出现变化的实验室指标是？",
                    "options": {
                        "A": "血清铁蛋白降低",
                        "B": "血红蛋白浓度下降",
                        "C": "血清铁降低",
                        "D": "总铁结合力增高",
                        "E": "平均红细胞体积减小",
                    },
                    "correct_answer": "A",
                    "explanation": "这是缺铁性贫血的典型考法。",
                },
                {
                    "id": 2,
                    "type": "A2",
                    "difficulty": "提高",
                    "variation_type": "应用变式",
                    "question": "一位因胃溃疡并慢性失血导致缺铁性贫血的患者下一步处理是？",
                    "options": {
                        "A": "???",
                        "B": "???",
                        "C": "???",
                        "D": "???",
                        "E": "???",
                    },
                    "correct_answer": "B",
                    "explanation": "???",
                },
            ]
        }


def test_generate_variation_questions_filters_off_topic_and_placeholder_output(monkeypatch):
    monkeypatch.setattr(quiz_service_module, "get_ai_client", lambda: FakeAI())
    service = quiz_service_module.QuizService()

    base_question = {
        "id": 1,
        "type": "A1",
        "difficulty": "基础",
        "question": "胃底腺中同时分泌盐酸和内因子的细胞是：",
        "options": {
            "A": "主细胞",
            "B": "壁细胞",
            "C": "G细胞",
            "D": "D细胞",
            "E": "黏液颈细胞",
        },
        "correct_answer": "B",
        "explanation": "胃底腺的壁细胞是胃内唯一能同时分泌盐酸和内因子的细胞。",
        "key_point": "胃腺不同细胞的分泌功能",
    }

    variations = asyncio.run(
        service.generate_variation_questions(
            key_point="胃腺不同细胞的分泌功能",
            base_question=base_question,
            uploaded_content="胃内消化 胃酸 壁细胞 主细胞 内因子 胃腺",
            num_variations=5,
        )
    )

    assert len(variations) == 5
    serialized = json.dumps(variations, ensure_ascii=False)
    assert "缺铁性贫血" not in serialized
    assert "???" not in serialized
    assert all(item["options"] == base_question["options"] for item in variations)
    assert all(item["correct_answer"] == base_question["correct_answer"] for item in variations)
