import json
import subprocess
from pathlib import Path


def test_quiz_detail_fuzzy_confidence_and_copy_summary(tmp_path):
    template_path = Path(r"C:\Users\35456\true-learning-system\templates\quiz_detail.html")
    text = template_path.read_text(encoding="utf-8")

    helper_start = text.index("function normalizeOptionLetter")
    helper_end = text.index("function getSeverityBadgeText")
    confidence_start = text.index("function setPracticeConfidence")
    confidence_end = text.index("function selectPracticeOption")
    submit_start = text.index("function submitPractice()")
    submit_end = text.index("function bindEvents()")

    helper_snippet = text[helper_start:helper_end]
    confidence_snippet = text[confidence_start:confidence_end]
    submit_snippet = text[submit_start:submit_end]

    script = helper_snippet + "\n" + confidence_snippet + "\n" + submit_snippet + r"""
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
    className: className || '',
    children: [],
    appendChild: function(child) {
      this.children.push(child);
    }
  };
  element.classList = createClassList(element);
  elements[id] = element;
  return element;
}

const document = {
  getElementById: function(id) {
    return Object.prototype.hasOwnProperty.call(elements, id) ? elements[id] : null;
  },
  createElement: function() {
    const element = {
      className: '',
      innerHTML: '',
      children: [],
      appendChild: function(child) {
        this.children.push(child);
      }
    };
    element.classList = createClassList(element);
    return element;
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

function escapeHtml(text) {
  return text || '';
}

function buildMaskedExplanationHtml() {
  return '<div>解析</div>';
}

function completeDetailTrackingSession() {
  return Promise.resolve();
}

let currentKnowledge = '心衰分类';
let practiceQuestions = [
  {
    question: '关于心衰分类，以下哪项正确？',
    type: 'A1',
    difficulty: '提高',
    options: {
      A: '选项A内容',
      B: '选项B内容',
      C: '选项C内容',
      D: '选项D内容',
      E: '选项E内容'
    },
    correct_answer: 'C',
    explanation: '解析内容'
  }
];
let currentPracticeIndex = 0;
let practiceAnswers = { 0: 'D' };
let practiceConfidence = {};
let practiceFuzzyOptions = {};
let lastPracticeResult = null;

registerElement('practice-btn-sure-0');
registerElement('practice-btn-unsure-0');
registerElement('practice-btn-no-0');
registerElement('practice-confidence-display-0', 'mt-2 text-sm text-center hidden');
registerElement('practice-fuzzy-panel-0', 'hidden');
registerElement('practice-fuzzy-summary-0');
registerElement('practice-fuzzy-opt-0-A');
registerElement('practice-fuzzy-opt-0-B');
registerElement('practice-fuzzy-opt-0-C');
registerElement('practice-fuzzy-opt-0-D');
registerElement('practice-fuzzy-opt-0-E');

registerElement('questionArea');
registerElement('resultArea', 'hidden');
registerElement('resultKnowledgeTitle');
registerElement('practiceScore');
registerElement('practiceCorrect');
registerElement('practiceWrong');
registerElement('practiceEmoji');
registerElement('practiceDetails');
registerElement('copyPracticeSummaryBtn', 'bg-indigo-500 hover:bg-indigo-600');
registerElement('copyPracticeSummaryText');
elements.copyPracticeSummaryText.textContent = '一键复制本知识点总结';

(async function main() {
  setPracticeConfidence(0, 'unsure');
  const afterUnsure = {
    panelHidden: elements['practice-fuzzy-panel-0'].classList.contains('hidden'),
    displayText: elements['practice-confidence-display-0'].textContent,
    shortcutIgnoredOutsideUnsure: (function() {
      setPracticeConfidence(0, 'sure');
      const changed = tryTogglePracticeFuzzyOptionByShortcut(0, '3');
      setPracticeConfidence(0, 'unsure');
      return changed;
    })()
  };

  tryTogglePracticeFuzzyOptionByShortcut(0, '4');
  tryTogglePracticeFuzzyOptionByShortcut(0, '3');
  const afterSelect = {
    displayText: elements['practice-confidence-display-0'].textContent,
    fuzzyOptions: practiceFuzzyOptions['0'],
    selectedC: elements['practice-fuzzy-opt-0-C'].className,
    selectedD: elements['practice-fuzzy-opt-0-D'].className,
    shortcutOption3: getPracticeShortcutOptionByKey('3', practiceQuestions[0]),
    shortcutOption4: getPracticeShortcutOptionByKey('4', practiceQuestions[0])
  };

  setPracticeConfidence(0, 'sure');
  const afterSure = {
    panelHidden: elements['practice-fuzzy-panel-0'].classList.contains('hidden'),
    fuzzyCount: Object.keys(practiceFuzzyOptions).length
  };

  setPracticeConfidence(0, 'unsure');
  togglePracticeFuzzyOption(0, 'C');
  togglePracticeFuzzyOption(0, 'D');

  submitPractice();
  copyPracticeSummary();
  await Promise.resolve();
  await Promise.resolve();

  console.log(JSON.stringify({
    afterUnsure: afterUnsure,
    afterSelect: afterSelect,
    afterSure: afterSure,
    resultKnowledgeTitle: elements.resultKnowledgeTitle.textContent,
    practiceScore: elements.practiceScore.textContent,
    detailHtml: elements.practiceDetails.children[0].innerHTML,
    copiedText: copiedText
  }));
})().catch(function(error) {
  console.error(error);
  process.exit(1);
});
"""

    script_path = tmp_path / "quiz_detail_fuzzy_copy_check.js"
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
    assert payload["afterSelect"]["fuzzyOptions"] == ["C", "D"]
    assert "bg-yellow-500" in payload["afterSelect"]["selectedC"]
    assert "bg-yellow-500" in payload["afterSelect"]["selectedD"]
    assert payload["afterSelect"]["shortcutOption3"] == "C"
    assert payload["afterSelect"]["shortcutOption4"] == "D"
    assert payload["afterSure"]["panelHidden"] is True
    assert payload["afterSure"]["fuzzyCount"] == 0
    assert payload["resultKnowledgeTitle"] == "心衰分类"
    assert payload["practiceScore"] == 0
    assert "模糊选项：C、D" in payload["detailHtml"]
    assert "一键复制本知识点总结" not in payload["copiedText"]
    assert "知识点：心衰分类" in payload["copiedText"]
    assert "模糊选项：C、D" in payload["copiedText"]
    assert "  → C. 选项C内容" in payload["copiedText"]
    assert "  → D. 选项D内容" in payload["copiedText"]
