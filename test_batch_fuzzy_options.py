import asyncio
import json
import subprocess
from datetime import date, datetime
from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import routers.quiz_batch as quiz_batch
from models import Base, Chapter


@pytest.fixture
def session_factory():
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    Session = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    try:
        yield Session
    finally:
        Base.metadata.drop_all(engine)
        engine.dispose()


def seed_chapter(session):
    session.add(
        Chapter(
            id="chapter1",
            book="Cardiology",
            edition="test",
            chapter_number="01",
            chapter_title="Heart Failure",
            concepts=[],
            first_uploaded=date.today(),
        )
    )
    session.commit()


def make_question():
    return {
        "id": 1,
        "type": "A1",
        "difficulty": "基础",
        "question": "Which option is correct?",
        "options": {
            "A": "Option A",
            "B": "Option B",
            "C": "Option C",
            "D": "Option D",
            "E": "Option E",
        },
        "correct_answer": "C",
        "explanation": "Because option C is correct.",
        "key_point": "heart-failure-classification",
    }


def test_submit_exam_caches_fuzzy_options_for_detail_flow(monkeypatch, session_factory):
    session = session_factory()
    seed_chapter(session)
    quiz_batch._exam_cache.clear()
    quiz_batch._detail_cache.clear()

    exam_id = "exam-fuzzy-options"
    quiz_batch._exam_cache[exam_id] = {
        "chapter_id": "chapter1",
        "chapter_prediction": {},
        "questions": [make_question()],
        "created_at": datetime.now(),
        "num_questions": 1,
        "uploaded_content": "heart failure lecture notes",
    }

    class FakeQuizService:
        def grade_paper(self, questions, answers, confidence):
            return {
                "score": 0,
                "correct_count": 0,
                "wrong_count": 1,
                "details": [
                    {
                        "id": 1,
                        "type": "A1",
                        "difficulty": "基础",
                        "user_answer": "D",
                        "correct_answer": "C",
                        "is_correct": False,
                        "confidence": "unsure",
                        "explanation": "Because option C is correct.",
                        "key_point": "heart-failure-classification",
                        "related_questions": "[]",
                    }
                ],
                "wrong_by_difficulty": {"基础": 1, "提高": 0, "难题": 0},
                "confidence_analysis": {"sure": 0, "unsure": 1, "no": 0},
                "weak_points": ["heart-failure-classification(基础)"],
                "analysis": "",
                "total": 1,
            }

        def _infer_chapter_prediction(self, content):
            return None

    monkeypatch.setattr(quiz_batch, "get_quiz_service", lambda: FakeQuizService())

    request = quiz_batch.SubmitRequest(
        answers=["D"],
        confidence={"0": "unsure"},
        fuzzy_options={"0": ["d", "C", "Z", "C"]},
    )
    result = asyncio.run(quiz_batch.submit_exam(exam_id=exam_id, request=request, db=session))

    expected = {
        "0": {
            "options": ["C", "D"],
            "option_texts": {"C": "Option C", "D": "Option D"},
            "key_point": "heart-failure-classification",
        }
    }

    assert result["fuzzy_options"] == expected
    assert quiz_batch._detail_cache[exam_id]["fuzzy_options"] == expected

    detail_result = asyncio.run(quiz_batch.get_exam_for_detail(exam_id=exam_id, db=session))
    assert detail_result["fuzzy_options"] == expected


def test_quiz_batch_fuzzy_option_flow_and_report(tmp_path):
    template_path = Path(r"C:\Users\35456\true-learning-system\templates\quiz_batch.html")
    text = template_path.read_text(encoding="utf-8")

    helper_start = text.index("function normalizeOptionLetter")
    helper_end = text.index("function buildMaskedExplanationHtml")
    confidence_start = text.index("function toggleFuzzyOption")
    confidence_end = text.index("function selectOption")
    copy_start = text.index("function copyExamReport()")
    copy_end = text.index("function endStudySession")

    helper_snippet = text[helper_start:helper_end]
    confidence_snippet = text[confidence_start:confidence_end]
    copy_snippet = text[copy_start:copy_end]

    script = helper_snippet + "\n" + confidence_snippet + "\n" + copy_snippet + r"""
const elements = {};

function createClassList(target) {
  return {
    add: function() {
      const set = new Set((target.className || '').split(/\s+/).filter(Boolean));
      Array.from(arguments).forEach(function(cls) { set.add(cls); });
      target.className = Array.from(set).join(' ');
    },
    remove: function() {
      const removeSet = new Set(Array.from(arguments));
      target.className = (target.className || '')
        .split(/\s+/)
        .filter(function(cls) { return cls && !removeSet.has(cls); })
        .join(' ');
    },
    contains: function(cls) {
      return (target.className || '').split(/\s+/).filter(Boolean).includes(cls);
    }
  };
}

function registerElement(id, className) {
  const element = {
    id: id,
    textContent: '',
    innerHTML: '',
    className: className || ''
  };
  element.classList = createClassList(element);
  elements[id] = element;
  return element;
}

const document = {
  getElementById: function(id) {
    return Object.prototype.hasOwnProperty.call(elements, id) ? elements[id] : null;
  }
};

globalThis.document = document;
globalThis.alert = function(message) {
  throw new Error('Unexpected alert: ' + message);
};
globalThis.setTimeout = function(fn) {
  fn();
  return 0;
};

let copiedText = '';
Object.defineProperty(globalThis, 'navigator', {
  value: {
  clipboard: {
    writeText: function(text) {
      copiedText = text;
      return Promise.resolve();
    }
  }
  },
  configurable: true
});

let questions = [
  {
    type: 'A1',
    question: '关于心衰分类，以下哪项正确？',
    options: {
      A: '选项A内容',
      B: '选项B内容',
      C: '选项C内容',
      D: '选项D内容',
      E: '选项E内容'
    }
  }
];
let userConfidence = {};
let userFuzzyOptions = {};
let lastExamResult = {
  correct_count: 0,
  wrong_count: 1,
  total: 1,
  wrong_by_difficulty: { '基础': 0, '提高': 1, '难题': 0 },
  weak_points: ['心衰分类(提高)'],
  details: [
    {
      user_answer: 'D',
      correct_answer: 'C',
      is_correct: false,
      difficulty: '提高',
      key_point: '心衰分类',
      explanation: '解析内容'
    }
  ]
};

registerElement('btn-sure-0');
registerElement('btn-unsure-0');
registerElement('btn-no-0');
registerElement('confidence-display-0', 'mt-2 text-sm text-center hidden');
registerElement('fuzzy-panel-0', 'hidden');
registerElement('fuzzy-summary-0');
registerElement('fuzzy-opt-0-A');
registerElement('fuzzy-opt-0-B');
registerElement('fuzzy-opt-0-C');
registerElement('fuzzy-opt-0-D');
registerElement('fuzzy-opt-0-E');
registerElement('scoreDisplay');
registerElement('accuracy');
registerElement('copyReportBtn', 'bg-indigo-500 hover:bg-indigo-600');
registerElement('copyBtnText');
elements.scoreDisplay.textContent = '0';
elements.accuracy.textContent = '0%';
elements.copyBtnText.textContent = '一键复制报告';

(async function main() {
  setConfidence(0, 'unsure');
  const afterUnsure = {
    panelHidden: elements['fuzzy-panel-0'].classList.contains('hidden'),
    displayText: elements['confidence-display-0'].textContent,
    shortcutIgnoredOutsideUnsure: (function() {
      setConfidence(0, 'sure');
      const changed = tryToggleFuzzyOptionByShortcut(0, '3');
      setConfidence(0, 'unsure');
      return changed;
    })()
  };

  tryToggleFuzzyOptionByShortcut(0, '4');
  tryToggleFuzzyOptionByShortcut(0, '3');
  const afterSelect = {
    displayText: elements['confidence-display-0'].textContent,
    payload: buildFuzzyOptionsPayload(),
    selectedC: elements['fuzzy-opt-0-C'].className,
    selectedD: elements['fuzzy-opt-0-D'].className,
    shortcutOption3: getShortcutOptionByKey('3', questions[0]),
    shortcutOption4: getShortcutOptionByKey('4', questions[0])
  };

  setConfidence(0, 'sure');
  const afterSure = {
    panelHidden: elements['fuzzy-panel-0'].classList.contains('hidden'),
    fuzzyCount: Object.keys(userFuzzyOptions).length
  };

  setConfidence(0, 'unsure');
  toggleFuzzyOption(0, 'C');
  toggleFuzzyOption(0, 'D');
  copyExamReport();
  await Promise.resolve();
  await Promise.resolve();

  console.log(JSON.stringify({
    afterUnsure: afterUnsure,
    afterSelect: afterSelect,
    afterSure: afterSure,
    copiedText: copiedText
  }));
})().catch(function(error) {
  console.error(error);
  process.exit(1);
});
"""

    script_path = tmp_path / "quiz_batch_fuzzy_option_check.js"
    script_path.write_text(script, encoding="utf-8")

    result = subprocess.run(
        ["node", str(script_path)],
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=True,
    )

    payload = json.loads(result.stdout.strip())
    assert payload["afterUnsure"]["panelHidden"] is False
    assert "请勾选具体选项" in payload["afterUnsure"]["displayText"]
    assert payload["afterUnsure"]["shortcutIgnoredOutsideUnsure"] is False
    assert payload["afterSelect"]["displayText"] == "？模糊选项：C、D"
    assert payload["afterSelect"]["payload"] == {"0": ["C", "D"]}
    assert "bg-yellow-500" in payload["afterSelect"]["selectedC"]
    assert "bg-yellow-500" in payload["afterSelect"]["selectedD"]
    assert payload["afterSelect"]["shortcutOption3"] == "C"
    assert payload["afterSelect"]["shortcutOption4"] == "D"
    assert payload["afterSure"]["panelHidden"] is True
    assert payload["afterSure"]["fuzzyCount"] == 0
    assert "模糊选项：C、D" in payload["copiedText"]
    assert "  → C. 选项C内容" in payload["copiedText"]
    assert "  → D. 选项D内容" in payload["copiedText"]
