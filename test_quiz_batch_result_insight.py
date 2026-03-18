import json
import subprocess
from pathlib import Path


def test_quiz_batch_result_insight_classification_and_prompt(tmp_path):
    template_path = Path(r"C:\Users\35456\true-learning-system\templates\quiz_batch.html")
    text = template_path.read_text(encoding="utf-8")

    helper_start = text.index("function normalizeOptionLetter")
    helper_end = text.index("(async function loadChapterCatalog()")
    insight_start = text.index("function getInsightQuadrantCatalog")
    insight_end = text.index("function goToDetailPractice")

    helper_snippet = text[helper_start:helper_end]
    insight_snippet = text[insight_start:insight_end]

    script = f"""
const elements = {{}};

function createClassList(target) {{
  return {{
    add: function() {{
      const set = new Set((target.className || '').split(/\\s+/).filter(Boolean));
      Array.from(arguments).forEach(function(cls) {{ set.add(cls); }});
      target.className = Array.from(set).join(' ');
    }},
    remove: function() {{
      const removeSet = new Set(Array.from(arguments));
      target.className = (target.className || '')
        .split(/\\s+/)
        .filter(function(cls) {{ return cls && !removeSet.has(cls); }})
        .join(' ');
    }},
    contains: function(cls) {{
      return (target.className || '').split(/\\s+/).filter(Boolean).includes(cls);
    }},
    toggle: function(cls, enabled) {{
      if (enabled) this.add(cls);
      else this.remove(cls);
    }}
  }};
}}

function registerElement(id, className) {{
  const element = {{
    id: id,
    textContent: '',
    innerHTML: '',
    className: className || '',
    style: {{}},
    children: [],
    appendChild: function(child) {{ this.children.push(child); }},
    setAttribute: function(name, value) {{ this[name] = value; }}
  }};
  element.classList = createClassList(element);
  elements[id] = element;
  return element;
}}

const document = {{
  getElementById: function(id) {{
    return Object.prototype.hasOwnProperty.call(elements, id) ? elements[id] : null;
  }},
  createElement: function() {{
    const element = {{
      className: '',
      innerHTML: '',
      textContent: '',
      style: {{}},
      children: [],
      appendChild: function(child) {{ this.children.push(child); }},
      setAttribute: function(name, value) {{ this[name] = value; }}
    }};
    element.classList = createClassList(element);
    return element;
  }}
}};

globalThis.document = document;
globalThis.setTimeout = function(fn) {{ fn(); return 0; }};
globalThis.clearTimeout = function() {{}};
globalThis.alert = function(message) {{
  throw new Error('Unexpected alert: ' + message);
}};

function escapeHtml(text) {{
  return String(text || '');
}}

[
  'examArea', 'resultArea', 'scoreDisplay', 'correctCount', 'wrongCount', 'accuracy',
  'scoreEmoji', 'insightLead', 'insightNote', 'insightMeta', 'insightSummary',
  'insightQuadrants', 'difficultyAnalysis', 'focusPromptList', 'focusPromptEmpty',
  'knowledgeList', 'answerDetails'
].forEach(function(id) {{
  registerElement(id, (id === 'resultArea' || id === 'focusPromptEmpty') ? 'hidden' : '');
}});

let questions = [
  {{
    type: 'A1',
    difficulty: 'basic',
    question: 'Q1',
    options: {{ A: 'A1', B: 'B1', C: 'C1', D: 'D1', E: '' }},
    key_point: 'heart-failure-classification',
    correct_answer: 'A',
    explanation: 'exp1'
  }},
  {{
    type: 'A2',
    difficulty: 'advanced',
    question: 'Q2',
    options: {{ A: 'A2', B: 'B2', C: 'C2', D: 'D2', E: '' }},
    key_point: 'shock-staging',
    correct_answer: 'A',
    explanation: 'exp2'
  }},
  {{
    type: 'A1',
    difficulty: 'advanced',
    question: 'Q3',
    options: {{ A: 'A3', B: 'B3', C: 'C3', D: 'D3', E: '' }},
    key_point: 'acid-base-balance',
    correct_answer: 'D',
    explanation: 'exp3'
  }},
  {{
    type: 'A2',
    difficulty: 'hard',
    question: 'Q4',
    options: {{ A: 'A4', B: 'B4', C: 'C4', D: 'D4', E: '' }},
    key_point: 'pulmonary-edema-diff',
    correct_answer: 'A',
    explanation: 'exp4'
  }},
  {{
    type: 'X',
    difficulty: 'hard',
    question: 'Q5',
    options: {{ A: 'A5', B: 'B5', C: 'C5', D: 'D5', E: 'E5' }},
    key_point: 'left-heart-vs-edema',
    correct_answer: 'C',
    explanation: 'exp5'
  }}
];

let userConfidence = {{ 0: 'sure', 1: 'unsure', 2: 'no', 3: 'no', 4: 'unsure' }};
let userFuzzyOptions = {{ 1: ['A', 'C'], 4: ['B', 'C'] }};
let lastExamResult = null;
let lastExamInsightModel = null;

function completeTrackingSession() {{
  return Promise.resolve();
}}

eval({json.dumps(helper_snippet + "\n" + insight_snippet)});

const result = {{
  score: 20,
  correct_count: 1,
  wrong_count: 4,
  total: 5,
  wrong_by_difficulty: {{ '基础': 1, '提高': 2, '难题': 1 }},
  confidence_analysis: {{
    sure: 1,
    unsure: 2,
    no: 2,
    marked_count: 5,
    missing_count: 0,
    sure_rate: 20,
    unsure_rate: 40,
    no_rate: 40
  }},
  details: [
    {{ user_answer: 'B', correct_answer: 'A', is_correct: false, difficulty: '基础', confidence: 'sure', explanation: 'exp1', key_point: 'heart-failure-classification' }},
    {{ user_answer: 'C', correct_answer: 'A', is_correct: false, difficulty: '提高', confidence: 'unsure', explanation: 'exp2', key_point: 'shock-staging' }},
    {{ user_answer: 'B', correct_answer: 'D', is_correct: false, difficulty: '提高', confidence: 'no', explanation: 'exp3', key_point: 'acid-base-balance' }},
    {{ user_answer: 'A', correct_answer: 'A', is_correct: true, difficulty: '难题', confidence: 'no', explanation: 'exp4', key_point: 'pulmonary-edema-diff' }},
    {{ user_answer: 'B', correct_answer: 'C', is_correct: false, difficulty: '难题', confidence: 'unsure', explanation: 'exp5', key_point: 'left-heart-vs-edema' }}
  ]
}};

(async function main() {{
  displayResults(result);
  const prompt = buildSocraticPrompt(lastExamInsightModel.focusTopics[0]);
  console.log(JSON.stringify({{
    lead: elements.insightLead.textContent,
    focusCount: lastExamInsightModel.focusTopics.length,
    focusTopics: lastExamInsightModel.focusTopics.map(function(item) {{
      return {{ keyPoint: item.keyPoint, quadrant: item.quadrantKey }};
    }}),
    hasDanger: elements.insightQuadrants.innerHTML.includes('高危盲区'),
    hasSticky: elements.insightQuadrants.innerHTML.includes('概念粘连区'),
    hasGap: elements.insightQuadrants.innerHTML.includes('知识缺口区'),
    hasLucky: elements.insightQuadrants.innerHTML.includes('侥幸命中区'),
    hasCopyButtons: elements.focusPromptList.innerHTML.includes('复制苏格拉底文稿'),
    promptHas90Rule: prompt.includes('90%'),
    promptHasOneQuestionRule: prompt.includes('请直接开始第一个问题，只问一句。'),
    promptHasHiddenInfoRule: prompt.includes('这些信息不要一开始直接告诉我')
  }}));
}})().catch(function(error) {{
  console.error(error);
  process.exit(1);
}});
"""

    script_path = tmp_path / "quiz_batch_result_insight_check.js"
    script_path.write_text(script, encoding="utf-8")

    result = subprocess.run(
        ["node", str(script_path)],
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=True,
    )

    payload = json.loads(result.stdout.strip())
    assert "5 个核心概念" in payload["lead"]
    assert payload["focusCount"] == 5
    assert payload["focusTopics"] == [
        {"keyPoint": "heart-failure-classification", "quadrant": "danger"},
        {"keyPoint": "left-heart-vs-edema", "quadrant": "sticky"},
        {"keyPoint": "shock-staging", "quadrant": "sticky"},
        {"keyPoint": "acid-base-balance", "quadrant": "gap"},
        {"keyPoint": "pulmonary-edema-diff", "quadrant": "lucky"},
    ]
    assert payload["hasDanger"] is True
    assert payload["hasSticky"] is True
    assert payload["hasGap"] is True
    assert payload["hasLucky"] is True
    assert payload["hasCopyButtons"] is True
    assert payload["promptHas90Rule"] is True
    assert payload["promptHasOneQuestionRule"] is True
    assert payload["promptHasHiddenInfoRule"] is True
