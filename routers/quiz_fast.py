"""
棰勭敓鎴愭祴楠岃矾鐢?棰樼洰銆佺瓟妗堛€佽В鏋愪竴璧烽鐢熸垚锛屾湰鍦板揩閫熸壒鏀癸紝AI缁煎悎鍒嗘瀽
"""

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from datetime import date, datetime, timedelta
import json
import random
from types import SimpleNamespace
from typing import List

from models import get_db, QuizSession, ConceptMastery, Chapter, TestRecord, WrongAnswer
from services.pre_generated_quiz import (
    get_pre_gen_service,
    get_local_grader,
    get_comprehensive_analyzer
)

router = APIRouter(prefix="/api/quiz-fast", tags=["quiz-fast"])


def _normalize_name(name: str) -> str:
    return "".join((name or "").strip().lower().split())


def _concept_rank(concept: ConceptMastery) -> int:
    cid = (concept.concept_id or "").lower()
    rank = 0
    if "_repeat_" not in cid:
        rank += 2
    if "_seed_" not in cid:
        rank += 1
    return rank


def _build_concept_slots(concepts: List[ConceptMastery], target: int = 10) -> List[SimpleNamespace]:
    """Build concept slots, capping at available unique concepts to avoid duplicates.

    When unique concepts < target, we generate fewer slots rather than cycling
    the same concept name through modulo — AI can't reliably produce distinct
    questions when it receives the same concept_name multiple times.
    """
    best_by_name = {}
    for concept in concepts:
        name = (concept.name or "").strip()
        if not name:
            continue
        key = _normalize_name(name)
        current = best_by_name.get(key)
        if current is None or _concept_rank(concept) > _concept_rank(current):
            best_by_name[key] = concept

    selected = list(best_by_name.values())
    if not selected:
        return []

    random.shuffle(selected)

    # Cap at available unique concepts — no modulo cycling
    actual_count = min(target, len(selected))
    slots: List[SimpleNamespace] = []

    for i in range(actual_count):
        slots.append(
            SimpleNamespace(
                concept_id=selected[i].concept_id,
                name=selected[i].name,
                description="",
            )
        )

    if actual_count < target:
        print(f"[_build_concept_slots] 知识点不足: {len(selected)}/{target}，生成 {actual_count} 题（不循环复用）")

    return slots


@router.post("/start/{chapter_id}")
async def start_pre_gen_quiz(
    chapter_id: str,
    db: Session = Depends(get_db)
):
    """
    寮€濮嬮鐢熸垚娴嬮獙
    棰樼洰銆佺瓟妗堛€佽В鏋愪竴璧风敓鎴愶紝瀛樺偍鍦ㄦ暟鎹簱
    """
    concepts = db.query(ConceptMastery).filter(
        ConceptMastery.chapter_id == chapter_id
    ).all()

    # 鑷姩淇锛氳嫢绔犺妭瀛樺湪浣?concept_mastery 缂哄け锛屽垯浠?chapters.concepts 鍥炲～
    if not concepts:
        chapter = db.query(Chapter).filter(Chapter.id == chapter_id).first()
        repaired = 0
        if chapter and isinstance(chapter.concepts, list):
            for i, item in enumerate(chapter.concepts):
                if not isinstance(item, dict):
                    continue
                name = (item.get("name") or "").strip()
                if not name:
                    continue
                concept_id = (item.get("id") or f"{chapter_id}_auto_{i}").strip()
                exists = db.query(ConceptMastery).filter(
                    ConceptMastery.concept_id == concept_id
                ).first()
                if exists:
                    continue
                db.add(ConceptMastery(
                    concept_id=concept_id,
                    chapter_id=chapter_id,
                    name=name,
                    retention=0.0,
                    understanding=0.0,
                    application=0.0,
                ))
                repaired += 1
        if repaired:
            db.commit()
            print(f"[quiz-fast] 鑷姩鍥炲～鐭ヨ瘑鐐? chapter={chapter_id}, repaired={repaired}")
        concepts = db.query(ConceptMastery).filter(
            ConceptMastery.chapter_id == chapter_id
        ).all()

        # 鍐嶉檷绾э細绔犺妭娌℃湁缁撴瀯鍖栫煡璇嗙偣鏃讹紝鑷姩娉ㄥ叆绔犺妭绾х瀛愮煡璇嗙偣锛岄伩鍏嶆帴鍙ｇ洿鎺?04
        if not concepts and chapter:
            base = (chapter.chapter_title or chapter.chapter_number or chapter_id).strip()
            seed_topics = [
                f"{base}-鍩虹姒傚康",
                f"{base}-鐥呭洜鏈哄埗",
                f"{base}-涓村簥琛ㄧ幇",
                f"{base}-璇婃柇瑕佺偣",
                f"{base}-娌荤枟鍘熷垯",
            ]
            seeded = 0
            for i, name in enumerate(seed_topics):
                concept_id = f"{chapter_id}_seed_{i}"
                exists = db.query(ConceptMastery).filter(
                    ConceptMastery.concept_id == concept_id
                ).first()
                if exists:
                    continue
                db.add(ConceptMastery(
                    concept_id=concept_id,
                    chapter_id=chapter_id,
                    name=name,
                    retention=0.0,
                    understanding=0.0,
                    application=0.0,
                ))
                seeded += 1
            if seeded:
                db.commit()
                print(f"[quiz-fast] 娉ㄥ叆绔犺妭绉嶅瓙鐭ヨ瘑鐐? chapter={chapter_id}, seeded={seeded}")
            concepts = db.query(ConceptMastery).filter(
                ConceptMastery.chapter_id == chapter_id
            ).all()

    if not concepts:
        raise HTTPException(status_code=404, detail="No available concepts in this chapter. Please upload or enrich chapter concepts first.")


    concepts = _build_concept_slots(concepts, target=10)
    if not concepts:
        raise HTTPException(status_code=404, detail="章节没有可用知识点，请先上传该章节内容或在章节中补充知识点")

    concept_names = [c.name for c in concepts]
    concept_descriptions = [c.description for c in concepts]
    print(f"[棰勭敓鎴怾 寮€濮嬩负 {len(concept_names)} 涓煡璇嗙偣鐢熸垚棰樼洰+绛旀+瑙ｆ瀽...")
    
    
    service = get_pre_gen_service()
    import asyncio
    quizzes = await service.generate_batch(concept_names, concept_descriptions)
    
    print(f"[quiz-fast] generated {len(quizzes)} questions")
    
    
    questions = []
    for i, (quiz, concept) in enumerate(zip(quizzes, concepts)):
        test_record = TestRecord(
            concept_id=concept.concept_id,
            test_type="pre_generated",
            ai_question=quiz["question"],
            ai_options=quiz.get("options", {}),
            ai_correct_answer=quiz.get("correct_answer", "A"),
            ai_explanation=quiz.get("explanation", ""),
            score=0
        )
        db.add(test_record)
        db.commit()
        db.refresh(test_record)
        
        # Store full question payload (answer/explanation hidden in frontend).
        questions.append({
            "question_id": f"q_{test_record.id}",
            "test_id": test_record.id,
            "concept_id": concept.concept_id,
            "concept_name": concept.name,
            "question": quiz["question"],
            "options": quiz.get("options", {"A": "", "B": "", "C": "", "D": ""}),
            # 浠ヤ笅瀛楁瀛樺偍浣嗕笉杩斿洖缁欏墠绔紙鎴栬€呭墠绔殣钘忥級
            "correct_answer": quiz.get("correct_answer", "A"),
            "explanation": quiz.get("explanation", ""),
            "key_points": quiz.get("key_points", []),
            "difficulty": quiz.get("difficulty", "medium"),
            "common_mistakes": quiz.get("common_mistakes", [])
        })
    
    
    session = QuizSession(
        session_type="pre_generated",
        chapter_id=chapter_id,
        questions=questions,
        answers=[],
        total_questions=len(questions),
        correct_count=0,
        score=0,
        started_at=datetime.now()
    )
    db.add(session)
    db.commit()
    db.refresh(session)
    
    
    questions_for_frontend = [
        {
            "question_id": q["question_id"],
            "test_id": q["test_id"],
            "concept_id": q["concept_id"],
            "concept_name": q["concept_name"],
            "question": q["question"],
            "options": q["options"],
            "difficulty": q["difficulty"],
            "key_points": q["key_points"]
        }
        for q in questions
    ]
    
    return {
        "session_id": session.id,
        "total_questions": len(questions),
        "questions": questions_for_frontend,
        "generation_method": "pre_generated"
    }


@router.post("/submit/{session_id}")
async def submit_pre_gen_quiz(
    session_id: int,
    data: dict,
    db: Session = Depends(get_db)
):
    """
    鎻愪氦棰勭敓鎴愭祴楠岀瓟妗?    鏈湴蹇€熸壒鏀?+ AI缁煎悎鍒嗘瀽
    """
    session = db.query(QuizSession).filter(QuizSession.id == session_id).first()
    if not session:
        raise HTTPException(status_code=404, detail="Quiz session not found")
    
    answers = data.get("answers", [])
    if len(answers) != len(session.questions):
        raise HTTPException(status_code=400, detail="Invalid request payload")
    
    print(f"[鏈湴鎵规敼] 寮€濮嬫壒鏀?{len(answers)} 閬撻鐩?..")
    
    
    graded_results = grader.grade_batch(session.questions, answers)
    
    print(f"[鏈湴鎵规敼] 瀹屾垚")
    
    
    answer_records = []
    correct_count = 0
    
    for i, (question, answer, graded) in enumerate(zip(session.questions, answers, graded_results)):
        is_correct = graded.get("is_correct", False)
        if is_correct:
            correct_count += 1
        
        record = {
            "question_index": i,
            "test_id": question["test_id"],
            "user_answer": answer.get("user_answer"),
            "correct_answer": question["correct_answer"],
            "is_correct": is_correct,
            "confidence": answer.get("confidence"),
            "time_spent": answer.get("time_spent", 0),
            "score": graded.get("score", 0),
            "feedback": graded.get("feedback", ""),
            "explanation": question["explanation"],  # 鎶婅В鏋愪篃瀛樿捣鏉?            "weak_points": graded.get("weak_points", []),
            "error_type": graded.get("error_type"),
            "confidence_analysis": graded.get("confidence_analysis", "")
        }
        answer_records.append(record)
        
        
        test = db.query(TestRecord).filter(TestRecord.id == question["test_id"]).first()
        if test:
            test.user_answer = answer.get("user_answer")
            test.confidence = answer.get("confidence")
            test.is_correct = is_correct
            test.ai_feedback = graded.get("feedback", "")
            test.weak_points = graded.get("weak_points", [])
            test.score = graded.get("score", 0)
        
        
        concept = db.query(ConceptMastery).filter(
            ConceptMastery.concept_id == question["concept_id"]
        ).first()
        if concept:
            if is_correct:
                concept.retention = min(concept.retention + 0.1, 1.0)
            else:
                concept.retention = max(concept.retention - 0.05, 0.0)
            concept.last_tested = date.today()
        
        # 璁板綍閿欓
        if not is_correct:
            existing = db.query(WrongAnswer).filter(
                WrongAnswer.concept_id == question["concept_id"],
                WrongAnswer.question == question["question"]
            ).first()
            
            if not existing:
                wrong = WrongAnswer(
                    concept_id=question["concept_id"],
                    question=question["question"],
                    options=json.dumps(question.get("options", {})),
                    correct_answer=question["correct_answer"],
                    user_answer=answer.get("user_answer"),
                    explanation=question["explanation"],
                    error_type=graded.get("error_type", "unknown"),
                    weak_points=graded.get("weak_points", []),
                    review_count=1,
                    last_reviewed=datetime.now(),
                    next_review=date.today() + timedelta(days=1),
                    mastery_level=0,
                    is_mastered=False
                )
                db.add(wrong)
            else:
                existing.review_count += 1
                existing.last_reviewed = datetime.now()
                existing.user_answer = answer.get("user_answer")
    
    # AI缁煎悎鍒嗘瀽
    print(f"[AI鍒嗘瀽] 寮€濮嬬敓鎴愮患鍚堝垎鏋愭姤鍛?..")
    analyzer = get_comprehensive_analyzer()
    import asyncio
    analysis = await analyzer.analyze_comprehensive(
        session.questions,
        graded_results,
        answers
    )
    print(f"[AI鍒嗘瀽] 瀹屾垚")
    
    # 鏇存柊浼氳瘽
    session.answers = answer_records
    session.correct_count = correct_count
    session.score = int(correct_count / len(session.questions) * 100)
    session.completed_at = datetime.now()
    db.commit()
    
    return {
        "session_id": session_id,
        "score": session.score,
        "correct_count": correct_count,
        "wrong_count": len(session.questions) - correct_count,
        "answers": answer_records,
        "ai_analysis": analysis
    }


@router.get("/result/{session_id}")
async def get_result(
    session_id: int,
    db: Session = Depends(get_db)
):
    """
    鑾峰彇娴嬮獙缁撴灉鍜岃缁嗚В鏋?    """
    session = db.query(QuizSession).filter(QuizSession.id == session_id).first()
    if not session:
        raise HTTPException(status_code=404, detail="Quiz session not found")
    
    if not session.answers:
        raise HTTPException(status_code=400, detail="娴嬮獙灏氭湭瀹屾垚")
    
    return {
        "session_id": session_id,
        "score": session.score,
        "correct_count": session.correct_count,
        "wrong_count": session.total_questions - session.correct_count,
        "answers": session.answers
    }



