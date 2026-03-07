"""Content parser v2.

This module keeps legacy chapter parsing behavior (via inheritance) and adds a
robust external wrong-question parser for OCR/PDF text imports.
"""

from __future__ import annotations

import re
from typing import Any, Dict, List, Tuple

from services.content_parser import ContentParser as LegacyContentParser


class ContentParser(LegacyContentParser):
    async def parse_external_wrong_questions(self, raw_text: str, max_items: int = 200) -> Dict[str, Any]:
        """Parse OCR/PDF wrong-question text into structured question records."""
        if not raw_text or not raw_text.strip():
            return {"book_name": "", "chapter_name": "", "questions": []}

        text = self._prepare_external_text(raw_text)
        rule_result = self._parse_external_by_rules(text, max_items=max_items)
        if rule_result.get("questions"):
            return rule_result

        # AI fallback only when rule parser cannot extract any question.
        prompt = self._build_external_wrong_prompt(text, max_items=max_items)
        schema = {
            "book_name": "Book name if available",
            "chapter_name": "Chapter name if available",
            "questions": [
                {
                    "question_no": 1,
                    "question_text": "Full question stem",
                    "options": {"A": "Option A", "B": "Option B", "C": "Option C", "D": "Option D", "E": "Option E"},
                    "correct_answer": "A",
                }
            ],
        }

        try:
            ai_result = await self.ai.generate_json(
                prompt,
                schema,
                max_tokens=7000,
                temperature=0.1,
                use_heavy=False,
                timeout=90,
            )
        except Exception as e:
            print(f"[ContentParserV2] External AI fallback parse failed: {e}")
            return rule_result

        normalized = self._normalize_external_questions(ai_result.get("questions", []), max_items=max_items)
        if not normalized:
            return rule_result

        return {
            "book_name": str(ai_result.get("book_name") or "").strip() or rule_result.get("book_name", ""),
            "chapter_name": str(ai_result.get("chapter_name") or "").strip() or rule_result.get("chapter_name", ""),
            "questions": normalized,
        }

    def _prepare_external_text(self, raw_text: str) -> str:
        text = (raw_text or "").replace("\r\n", "\n").replace("\r", "\n").strip()
        text = re.sub(r"\n{3,}", "\n\n", text)
        if len(text) <= 36000:
            return text
        return text[:25000] + "\n\n...[truncated middle content]...\n\n" + text[-9000:]

    def _parse_external_by_rules(self, text: str, max_items: int = 200) -> Dict[str, Any]:
        book_name, chapter_name = self._extract_book_and_chapter(text)
        question_part, answer_part = self._split_question_and_answer_sections(text)
        answer_map = self._extract_answer_map(answer_part)
        blocks = self._extract_question_blocks(question_part, max_items=max_items * 2)

        questions: List[Dict[str, Any]] = []
        seen: set[Tuple[int, str]] = set()

        for block in blocks:
            q_no = block["question_no"]
            answer = answer_map.get(q_no)
            if not answer:
                continue

            unique_key = (q_no, block["question_text"])
            if unique_key in seen:
                continue
            seen.add(unique_key)

            questions.append(
                {
                    "question_no": q_no,
                    "question_text": block["question_text"],
                    "options": block["options"],
                    "correct_answer": answer,
                    "chapter_name": chapter_name,
                    "book_name": book_name,
                }
            )
            if len(questions) >= max_items:
                break

        questions.sort(key=lambda x: x.get("question_no", 0))
        return {
            "book_name": book_name,
            "chapter_name": chapter_name,
            "questions": questions,
        }

    def _split_question_and_answer_sections(self, text: str) -> Tuple[str, str]:
        markers = list(
            re.finditer(
                r"(参考答案|答案[:：]|答\s*案\s*键|answer\s*key)",
                text,
                flags=re.IGNORECASE,
            )
        )
        if markers:
            marker = markers[-1]
            return text[: marker.start()].strip(), text[marker.start() :].strip()

        matches = list(re.finditer(r"(?:^|\n)\s*(\d{1,4})\s*[\.．、:：]\s*([A-Ea-e])\b", text))
        if len(matches) >= 3:
            split_idx = matches[0].start()
            if split_idx >= int(len(text) * 0.4):
                return text[:split_idx].strip(), text[split_idx:].strip()

        return text, text

    def _extract_answer_map(self, answer_text: str) -> Dict[int, str]:
        answer_map: Dict[int, str] = {}
        if not answer_text:
            return answer_map

        patterns = [
            r"(?:^|\n)\s*(\d{1,4})\s*[\.．、:：]\s*([A-Ea-e])\b",
            r"(?:^|\n)\s*(\d{1,4})\s*[)）]\s*([A-Ea-e])\b",
        ]

        for pat in patterns:
            for m in re.finditer(pat, answer_text):
                try:
                    q_no = int(m.group(1))
                except Exception:
                    continue
                answer_map[q_no] = m.group(2).upper()

        return answer_map

    def _extract_question_blocks(self, question_text: str, max_items: int = 200) -> List[Dict[str, Any]]:
        blocks: List[Dict[str, Any]] = []
        pattern = re.compile(
            r"(?ms)(?:^|\n)\s*(\d{1,4})\s*[\.．、]\s*(.+?)(?=(?:\n\s*\d{1,4}\s*[\.．、]\s)|\Z)"
        )

        for m in pattern.finditer(question_text):
            if len(blocks) >= max_items:
                break

            try:
                q_no = int(m.group(1))
            except Exception:
                continue

            block_body = str(m.group(2) or "").strip()
            if not block_body:
                continue

            options = self._extract_options(block_body)
            if len(options) < 2:
                continue

            first_option = re.search(r"(?:^|\n)\s*[A-Ea-e]\s*[\.．、:：]\s*", block_body)
            stem = block_body[: first_option.start()].strip() if first_option else block_body
            stem = re.sub(r"\s+", " ", stem).strip()
            if not stem or self._is_low_quality_text(stem):
                continue

            blocks.append({"question_no": q_no, "question_text": stem, "options": options})

        return blocks

    def _extract_options(self, block_body: str) -> Dict[str, str]:
        options: Dict[str, str] = {}
        option_pattern = re.compile(
            r"(?ms)(?:^|\n)\s*([A-Ea-e])\s*[\.．、:：]\s*(.+?)(?=(?:\n\s*[A-Ea-e]\s*[\.．、:：]\s)|\Z)"
        )

        for m in option_pattern.finditer(block_body):
            key = m.group(1).upper()
            value = re.sub(r"\s+", " ", str(m.group(2) or "").strip())
            if value and not self._is_low_quality_text(value):
                options[key] = value

        if len(options) < 2:
            inline_pattern = re.compile(
                r"([A-Ea-e])\s*[\.．、:：]\s*(.+?)(?=(?:\s+[A-Ea-e]\s*[\.．、:：])|$)",
                flags=re.S,
            )
            for m in inline_pattern.finditer(block_body):
                key = m.group(1).upper()
                value = re.sub(r"\s+", " ", str(m.group(2) or "").strip())
                if value and not self._is_low_quality_text(value):
                    options[key] = value

        return {k: options[k] for k in ["A", "B", "C", "D", "E"] if k in options}

    def _extract_book_and_chapter(self, text: str) -> Tuple[str, str]:
        lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
        header_lines: List[str] = []
        for ln in lines:
            if re.match(r"^\d{1,4}\s*[\.．、]", ln):
                break
            header_lines.append(ln)
        if not header_lines:
            header_lines = lines[:3]
        header = " ".join(header_lines[:3]) if header_lines else ""

        chapter_name = ""
        chapter_match = re.search(r"(第[一二三四五六七八九十百千0-9]+[章节][^\n]{0,32})", header)
        if chapter_match:
            chapter_name = chapter_match.group(1).strip()

        book_name = ""
        if chapter_match:
            prefix = header[: chapter_match.start()]
            prefix = re.sub(r"(我的错题|错题本|题库|练习|整理|汇总|参考答案|答案)", "", prefix)
            prefix = re.sub(r"\s+", " ", prefix).strip(" -|_:：")
            if prefix:
                book_name = prefix.split(" ")[-1].strip()

        if not book_name:
            book_match = re.search(r"([A-Za-z\u4e00-\u9fff]{2,20}(?:科学|学))", header)
            if book_match:
                book_name = book_match.group(1).strip()

        return book_name, chapter_name

    def _is_low_quality_text(self, text: str) -> bool:
        text = (text or "").strip()
        if not text:
            return True
        if len(text) >= 4 and text.count("?") / max(len(text), 1) >= 0.5:
            return True
        return False

    def _build_external_wrong_prompt(self, text: str, max_items: int = 200) -> str:
        return f"""You are extracting multiple-choice wrong-question data from OCR text.
The text usually has:
1) numbered questions with options A-E
2) a consolidated answer key at the end (e.g. 1.C 2.A 3.D)

Task:
- Output ONLY valid JSON.
- Keep questions in original order.
- Match each question with its answer by question number.
- If a question has no reliable answer key, skip it.
- Maximum {max_items} questions.
- Try to infer book_name and chapter_name from header context.

Required JSON shape:
{{
  "book_name": "string",
  "chapter_name": "string",
  "questions": [
    {{
      "question_no": 1,
      "question_text": "string",
      "options": {{"A":"...", "B":"...", "C":"...", "D":"...", "E":"..."}},
      "correct_answer": "A"
    }}
  ]
}}

Text:
{text}
"""

    def _normalize_external_questions(self, questions: Any, max_items: int = 200) -> List[Dict[str, Any]]:
        if not isinstance(questions, list):
            return []

        normalized: List[Dict[str, Any]] = []
        seen: set[Tuple[int, str]] = set()

        for idx, q in enumerate(questions):
            if not isinstance(q, dict):
                continue

            q_no = q.get("question_no", idx + 1)
            try:
                q_no = int(q_no)
            except Exception:
                q_no = idx + 1

            q_text = str(q.get("question_text") or "").strip()
            q_text = re.sub(r"^\s*\d+\s*[\.．、]\s*", "", q_text)
            q_text = re.sub(r"\s+", " ", q_text).strip()
            if not q_text or self._is_low_quality_text(q_text):
                continue

            raw_options = q.get("options") or {}
            options: Dict[str, str] = {}
            if isinstance(raw_options, dict):
                for key, value in raw_options.items():
                    k = str(key or "").upper().strip()
                    k = re.sub(r"[^A-E]", "", k)[:1]
                    v = re.sub(r"\s+", " ", str(value or "").strip())
                    if k and v and not self._is_low_quality_text(v):
                        options[k] = v

            options = {k: options[k] for k in ["A", "B", "C", "D", "E"] if k in options}
            if len(options) < 2:
                continue

            ans_raw = str(q.get("correct_answer") or "").upper()
            match = re.search(r"[A-E]", ans_raw)
            if not match:
                continue
            answer = match.group(0)

            unique_key = (q_no, q_text)
            if unique_key in seen:
                continue
            seen.add(unique_key)

            normalized.append(
                {
                    "question_no": q_no,
                    "question_text": q_text,
                    "options": options,
                    "correct_answer": answer,
                    "chapter_name": str(q.get("chapter_name") or "").strip(),
                    "book_name": str(q.get("book_name") or "").strip(),
                }
            )
            if len(normalized) >= max_items:
                break

        normalized.sort(key=lambda x: x.get("question_no", 0))
        return normalized


_parser: ContentParser | None = None


def get_content_parser() -> ContentParser:
    global _parser
    if _parser is None:
        _parser = ContentParser()
    return _parser


def reset_content_parser() -> None:
    global _parser
    _parser = None
