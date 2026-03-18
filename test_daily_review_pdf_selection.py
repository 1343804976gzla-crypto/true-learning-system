from datetime import date, datetime, timedelta

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from models import Base
from learning_tracking_models import DailyReviewPaper, DailyReviewPaperItem, WrongAnswerV2, make_fingerprint
from routers.wrong_answers_v2 import (
    _build_daily_review_candidates,
    ReviewCandidate,
    _build_daily_review_stem_fingerprint,
    _resolve_daily_review_actor,
    _select_daily_review_candidates,
    _sort_due_candidates,
    _sort_supplement_candidates,
)
from services.data_identity import DEFAULT_DEVICE_ID, build_actor_key


@pytest.fixture(autouse=True)
def disable_single_user_mode(monkeypatch):
    from services.data_identity import clear_identity_caches_for_tests

    monkeypatch.setenv("SINGLE_USER_MODE", "false")
    clear_identity_caches_for_tests()
    try:
        yield
    finally:
        monkeypatch.delenv("SINGLE_USER_MODE", raising=False)
        clear_identity_caches_for_tests()


def _candidate(
    wrong_answer_id: int,
    *,
    question_text: str,
    knowledge_key: str,
    is_multi: bool = False,
    is_hard: bool = False,
    next_review_date: date | None = None,
) -> ReviewCandidate:
    question_type = "X" if is_multi else "A1"
    difficulty = "难题" if is_hard else "基础"
    stem_fingerprint = _build_daily_review_stem_fingerprint(question_text)

    return ReviewCandidate(
        wrong_answer_id=wrong_answer_id,
        stem_fingerprint=stem_fingerprint,
        normalized_stem=question_text,
        source_bucket="due" if next_review_date else "supplement",
        next_review_date=next_review_date,
        severity_tag="critical",
        question_type=question_type,
        difficulty=difficulty,
        knowledge_key=knowledge_key,
        is_multi=is_multi,
        is_hard=is_hard,
        error_count=3,
        first_wrong_at=datetime(2026, 3, 1) + timedelta(minutes=wrong_answer_id),
        last_wrong_at=datetime(2026, 3, 2) + timedelta(minutes=wrong_answer_id),
        recently_used=False,
        snapshot={"question_text": question_text},
    )


def _make_db_session(tmp_path):
    db_path = tmp_path / "daily-review-selection.db"
    engine = create_engine(
        f"sqlite:///{db_path.as_posix()}",
        connect_args={"check_same_thread": False},
    )
    session_local = sessionmaker(
        autocommit=False,
        autoflush=False,
        bind=engine,
        expire_on_commit=False,
    )
    Base.metadata.create_all(bind=engine)
    return session_local(), engine


def _make_wrong_answer(
    wrong_answer_id: int,
    *,
    device_id: str,
    question_text: str,
    created_at: datetime,
) -> WrongAnswerV2:
    return WrongAnswerV2(
        id=wrong_answer_id,
        device_id=device_id,
        question_fingerprint=make_fingerprint(f"{question_text}::{wrong_answer_id}"),
        question_text=question_text,
        options={"A": "选项 A", "B": "选项 B"},
        correct_answer="A",
        key_point=f"kp-{wrong_answer_id}",
        question_type="A1",
        difficulty="基础",
        error_count=1,
        encounter_count=1,
        severity_tag="normal",
        mastery_status="active",
        first_wrong_at=created_at,
        last_wrong_at=created_at,
        created_at=created_at,
        updated_at=created_at,
    )


def test_daily_review_stem_fingerprint_normalizes_punctuation_and_spacing():
    left = _build_daily_review_stem_fingerprint("关于 心输出量 的叙述，正确的是：")
    right = _build_daily_review_stem_fingerprint("关于心输出量的叙述,正确的是")
    assert left == right


def test_daily_review_stem_fingerprint_ignores_variant_prefix_only_differences():
    left = _build_daily_review_stem_fingerprint("【机制变式】（接上题）在该信号通路中，被激活的丝氨酸/苏氨酸蛋白激酶直接导致糖原代谢关键酶活性改变。下列叙述正确的是")
    right = _build_daily_review_stem_fingerprint("【应用变式】（接上题）在该信号通路中，被激活的丝氨酸/苏氨酸蛋白激酶直接导致糖原代谢关键酶活性改变。下列叙述正确的是")
    assert left == right


def test_daily_review_selection_dedupes_variant_prefix_only_questions():
    ordered_candidates = [
        _candidate(1, question_text="【机制变式】（接上题）共同题干", knowledge_key="kp-shared"),
        _candidate(2, question_text="【应用变式】（接上题）共同题干", knowledge_key="kp-shared"),
        _candidate(3, question_text="题目 3", knowledge_key="kp3"),
        _candidate(4, question_text="题目 4", knowledge_key="kp4"),
        _candidate(5, question_text="题目 5", knowledge_key="kp5"),
        _candidate(6, question_text="题目 6", knowledge_key="kp6"),
        _candidate(7, question_text="题目 7", knowledge_key="kp7"),
        _candidate(8, question_text="题目 8", knowledge_key="kp8"),
        _candidate(9, question_text="题目 9", knowledge_key="kp9"),
        _candidate(10, question_text="题目 10", knowledge_key="kp10"),
        _candidate(11, question_text="题目 11", knowledge_key="kp11"),
    ]

    selected = _select_daily_review_candidates(ordered_candidates, min_multi=0, min_hard=0)

    assert len(selected) == 10
    assert len({item.stem_fingerprint for item in selected}) == 10
    assert sum(1 for item in selected if item.knowledge_key == "kp-shared") == 1


def test_daily_review_selection_preserves_multi_and_hard_quota_when_feasible():
    today = date(2026, 3, 15)
    ordered_candidates = [
        _candidate(1, question_text="普通题 1", knowledge_key="kp1", next_review_date=today),
        _candidate(2, question_text="普通题 2", knowledge_key="kp2", next_review_date=today),
        _candidate(3, question_text="普通题 3", knowledge_key="kp3", next_review_date=today),
        _candidate(4, question_text="普通题 4", knowledge_key="kp4", next_review_date=today),
        _candidate(5, question_text="普通题 5", knowledge_key="kp5", next_review_date=today),
        _candidate(6, question_text="普通题 6", knowledge_key="kp6", next_review_date=today),
        _candidate(7, question_text="多选难题 1", knowledge_key="kp7", is_multi=True, is_hard=True, next_review_date=today),
        _candidate(8, question_text="多选难题 2", knowledge_key="kp8", is_multi=True, is_hard=True, next_review_date=today),
        _candidate(9, question_text="多选难题 3", knowledge_key="kp9", is_multi=True, is_hard=True, next_review_date=today),
        _candidate(10, question_text="多选难题 4", knowledge_key="kp10", is_multi=True, is_hard=True, next_review_date=today),
        _candidate(11, question_text="多选难题 5", knowledge_key="kp11", is_multi=True, is_hard=True, next_review_date=today),
        _candidate(12, question_text="补位题", knowledge_key="kp12", next_review_date=today),
    ]

    selected = _select_daily_review_candidates(ordered_candidates)

    assert len(selected) == 10
    assert sum(1 for item in selected if item.is_multi) >= 5
    assert sum(1 for item in selected if item.is_hard) >= 5
    regular_ids = {item.wrong_answer_id for item in selected if not item.is_multi}
    assert len(regular_ids & {1, 2, 3, 4, 5, 6}) == 5


def test_daily_review_selection_caps_same_knowledge_point_at_two():
    today = date(2026, 3, 15)
    ordered_candidates = [
        _candidate(1, question_text="同知识点 1", knowledge_key="same-kp", next_review_date=today),
        _candidate(2, question_text="同知识点 2", knowledge_key="same-kp", next_review_date=today),
        _candidate(3, question_text="同知识点 3", knowledge_key="same-kp", next_review_date=today),
        _candidate(4, question_text="题目 4", knowledge_key="kp4", next_review_date=today),
        _candidate(5, question_text="题目 5", knowledge_key="kp5", next_review_date=today),
        _candidate(6, question_text="题目 6", knowledge_key="kp6", next_review_date=today),
        _candidate(7, question_text="题目 7", knowledge_key="kp7", next_review_date=today),
        _candidate(8, question_text="题目 8", knowledge_key="kp8", next_review_date=today),
        _candidate(9, question_text="题目 9", knowledge_key="kp9", next_review_date=today),
        _candidate(10, question_text="题目 10", knowledge_key="kp10", next_review_date=today),
        _candidate(11, question_text="题目 11", knowledge_key="kp11", next_review_date=today),
    ]

    selected = _select_daily_review_candidates(ordered_candidates, min_multi=0, min_hard=0)

    same_kp_count = sum(1 for item in selected if item.knowledge_key == "same-kp")
    assert len(selected) == 10
    assert same_kp_count == 2


def test_sort_due_candidates_prioritizes_older_items_with_same_due_date():
    today = date(2026, 3, 15)
    ordered = _sort_due_candidates([
        _candidate(5, question_text="new-hard", knowledge_key="kp-new", is_hard=True, next_review_date=today),
        _candidate(1, question_text="old-basic", knowledge_key="kp-old", next_review_date=today),
        _candidate(3, question_text="mid-multi", knowledge_key="kp-mid", is_multi=True, next_review_date=today),
    ])

    assert [item.wrong_answer_id for item in ordered] == [1, 3, 5]


def test_sort_supplement_candidates_prioritizes_older_wrong_answers():
    ordered = _sort_supplement_candidates([
        _candidate(5, question_text="new-hard", knowledge_key="kp-new", is_hard=True),
        _candidate(1, question_text="old-basic", knowledge_key="kp-old"),
        _candidate(3, question_text="mid-multi", knowledge_key="kp-mid", is_multi=True),
    ])

    assert [item.wrong_answer_id for item in ordered] == [1, 3, 5]


def test_daily_review_selection_relaxes_same_knowledge_point_cap_to_fill_target():
    ordered_candidates = [
        _candidate(1, question_text="kp1-a", knowledge_key="kp1"),
        _candidate(2, question_text="kp2-a", knowledge_key="kp2"),
        _candidate(3, question_text="kp3-a", knowledge_key="kp3"),
        _candidate(4, question_text="kp4-a", knowledge_key="kp4"),
        _candidate(5, question_text="kp1-b", knowledge_key="kp1"),
        _candidate(6, question_text="kp2-b", knowledge_key="kp2"),
        _candidate(7, question_text="kp3-b", knowledge_key="kp3"),
        _candidate(8, question_text="kp1-c", knowledge_key="kp1"),
        _candidate(9, question_text="kp2-c", knowledge_key="kp2"),
        _candidate(10, question_text="kp3-c", knowledge_key="kp3"),
        _candidate(11, question_text="kp1-d", knowledge_key="kp1"),
    ]

    selected = _select_daily_review_candidates(ordered_candidates, min_multi=0, min_hard=0)

    key_point_counts = {}
    for item in selected:
        key_point_counts[item.knowledge_key] = key_point_counts.get(item.knowledge_key, 0) + 1

    assert [item.wrong_answer_id for item in selected] == list(range(1, 11))
    assert len(selected) == 10
    assert max(key_point_counts.values()) == 3


def test_resolve_daily_review_actor_keeps_legacy_local_default_scope():
    actor = _resolve_daily_review_actor(device_id="local-current")

    assert actor["scope_device_ids"] == ["local-current", DEFAULT_DEVICE_ID]
    assert actor["actor_key"] == "device:local-current"
    assert actor["actor_keys"] == ["device:local-current", "device:local-default"]


def test_daily_review_candidates_include_legacy_default_pool_and_recent_papers(tmp_path):
    db, engine = _make_db_session(tmp_path)
    today = date(2026, 3, 18)
    current_device_id = "local-current"
    current_actor = build_actor_key(None, current_device_id)
    default_actor = build_actor_key(None, DEFAULT_DEVICE_ID)

    try:
        created_base = datetime(2026, 3, 1, 8, 0, 0)
        wrong_answers = [
            _make_wrong_answer(
                item_id,
                device_id=DEFAULT_DEVICE_ID,
                question_text=f"默认设备题目 {item_id}",
                created_at=created_base + timedelta(minutes=item_id),
            )
            for item_id in range(1, 13)
        ]
        wrong_answers.append(
            _make_wrong_answer(
                13,
                device_id=current_device_id,
                question_text="当前设备新增题目",
                created_at=created_base + timedelta(minutes=13),
            )
        )
        db.add_all(wrong_answers)
        db.flush()

        yesterday_paper = DailyReviewPaper(
            device_id=DEFAULT_DEVICE_ID,
            actor_key=default_actor,
            paper_date=today - timedelta(days=1),
            total_questions=10,
            created_at=created_base + timedelta(days=1),
            updated_at=created_base + timedelta(days=1),
        )
        db.add(yesterday_paper)
        db.flush()

        for position, wrong_answer in enumerate(wrong_answers[:10], start=1):
            yesterday_paper.items.append(
                DailyReviewPaperItem(
                    wrong_answer_id=wrong_answer.id,
                    position=position,
                    stem_fingerprint=_build_daily_review_stem_fingerprint(wrong_answer.question_text),
                    source_bucket="supplement",
                    snapshot={"question_text": wrong_answer.question_text},
                    created_at=created_base + timedelta(days=1),
                )
            )

        db.commit()

        actor = _resolve_daily_review_actor(device_id=current_device_id)
        assert actor["scope_device_ids"] == [current_device_id, DEFAULT_DEVICE_ID]
        assert actor["actor_keys"] == [current_actor, default_actor]

        ordered_candidates = _build_daily_review_candidates(
            db,
            today,
            user_id=actor["scope_user_id"],
            device_id=actor["scope_device_id"],
            device_ids=actor["scope_device_ids"],
            actor_key=actor["actor_key"],
            actor_keys=actor["actor_keys"],
        )

        assert [item.wrong_answer_id for item in ordered_candidates[:3]] == [11, 12, 13]
    finally:
        db.close()
        engine.dispose()
