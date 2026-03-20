from __future__ import annotations

import logging
import os
import re
import threading
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

_CJK_RE = re.compile(r"[\u4e00-\u9fffA-Za-z0-9]{2,16}")
_SENTENCE_SPLIT_RE = re.compile(r"(?<=[。！？；;!?])\s*")
_WHITESPACE_RE = re.compile(r"\s+")
_STOP_TERMS = {
    "这个",
    "那个",
    "这些",
    "那些",
    "主要",
    "一般",
    "常见",
    "可以",
    "能够",
    "进行",
    "通过",
    "需要",
    "注意",
    "包括",
    "以及",
    "其中",
    "具有",
    "表现",
    "发生",
    "引起",
    "导致",
    "相关",
    "内容",
    "课程",
    "讲课",
    "问题",
    "分析",
    "说明",
    "知识点",
    "考点",
    "正确",
    "错误",
    "题目",
    "选项",
    "答案",
    "解析",
}
_SIGNAL_TERMS = (
    "定义",
    "机制",
    "作用",
    "病因",
    "诱因",
    "症状",
    "体征",
    "表现",
    "并发症",
    "诊断",
    "鉴别",
    "检查",
    "治疗",
    "处理",
    "分型",
    "特点",
    "禁忌",
    "风险",
    "指标",
    "细胞",
    "激素",
    "受体",
    "蛋白",
    "酶",
    "酸",
    "代谢",
    "感染",
    "炎症",
    "溃疡",
    "肿瘤",
    "心率",
    "血压",
    "呼吸",
    "维生素",
)
_HEADING_RE = re.compile(
    r"^\s*(第[一二三四五六七八九十0-9]+[章节部分篇]|[0-9]+[.、)]|[①②③④⑤⑥⑦⑧⑨⑩]|[A-Za-z][.、)])"
)


def _env_flag(name: str, default: bool) -> bool:
    raw = (os.getenv(name) or "").strip().lower()
    if not raw:
        return default
    return raw in {"1", "true", "yes", "on"}


def _safe_int(name: str, default: int, minimum: int = 0) -> int:
    raw = (os.getenv(name) or "").strip()
    if not raw:
        return default
    try:
        return max(minimum, int(raw))
    except ValueError:
        return default


def _safe_float(name: str, default: float, minimum: float = 0.0, maximum: float = 1.0) -> float:
    raw = (os.getenv(name) or "").strip()
    if not raw:
        return default
    try:
        return min(maximum, max(minimum, float(raw)))
    except ValueError:
        return default


def _normalize_text(text: str) -> str:
    text = str(text or "").replace("\r\n", "\n").replace("\r", "\n")
    text = _WHITESPACE_RE.sub(" ", text)
    return text.strip()


@dataclass
class QuizCompactionResult:
    applied: bool
    strategy: str
    final_text: str
    raw_text: str
    digest_text: str
    glossary: List[str]
    model_name: str
    device: str
    raw_chars: int
    digest_chars: int
    final_chars: int
    origin_tokens: int
    compressed_tokens: int
    saved_tokens: int
    compression_rate: float
    context_blocks: int
    digest_paragraphs: int
    error: Optional[str] = None

    def as_metadata(self) -> Dict[str, Any]:
        return {
            "quiz_compaction_applied": self.applied,
            "quiz_compaction_strategy": self.strategy,
            "quiz_compaction_model": self.model_name,
            "quiz_compaction_device": self.device,
            "quiz_compaction_raw_chars": self.raw_chars,
            "quiz_compaction_digest_chars": self.digest_chars,
            "quiz_compaction_final_chars": self.final_chars,
            "quiz_compaction_origin_tokens": self.origin_tokens,
            "quiz_compaction_compressed_tokens": self.compressed_tokens,
            "quiz_compaction_saved_tokens": self.saved_tokens,
            "quiz_compaction_rate": round(self.compression_rate, 4),
            "quiz_compaction_blocks": self.context_blocks,
            "quiz_compaction_digest_paragraphs": self.digest_paragraphs,
            "quiz_compaction_glossary_count": len(self.glossary),
            "quiz_compaction_error": self.error,
        }

    def as_summary(self) -> Dict[str, Any]:
        return {
            "enabled": self.applied,
            "strategy": self.strategy,
            "model": self.model_name,
            "device": self.device,
            "raw_chars": self.raw_chars,
            "digest_chars": self.digest_chars,
            "final_chars": self.final_chars,
            "origin_tokens": self.origin_tokens,
            "compressed_tokens": self.compressed_tokens,
            "saved_tokens": self.saved_tokens,
            "compression_rate": round(self.compression_rate, 4),
            "glossary": self.glossary[:8],
            "error": self.error,
        }


class LLMLinguaQuizCompactor:
    def __init__(self) -> None:
        self.enabled = _env_flag("QUIZ_LINGUA_ENABLED", default=True)
        self.min_chars = _safe_int("QUIZ_LINGUA_MIN_CHARS", default=6000, minimum=1000)
        self.digest_ratio = _safe_float("QUIZ_LINGUA_DIGEST_RATIO", default=0.58, minimum=0.2, maximum=0.9)
        self.llmlingua_rate = _safe_float("QUIZ_LINGUA_RATE", default=0.8, minimum=0.5, maximum=0.98)
        self.min_final_ratio = _safe_float("QUIZ_LINGUA_MIN_FINAL_RATIO", default=0.55, minimum=0.2, maximum=0.95)
        self.max_digest_chars = _safe_int("QUIZ_LINGUA_MAX_DIGEST_CHARS", default=7000, minimum=1200)
        self.max_glossary_terms = _safe_int("QUIZ_LINGUA_MAX_GLOSSARY_TERMS", default=10, minimum=0)
        self.allow_cpu_model = _env_flag("QUIZ_LINGUA_ALLOW_CPU_MODEL", default=False)
        self.model_name = (
            os.getenv("QUIZ_LINGUA_MODEL")
            or "microsoft/llmlingua-2-bert-base-multilingual-cased-meetingbank"
        ).strip()
        self.device = self._resolve_device()
        self._compressor = None
        self._lock = threading.Lock()

    def _resolve_device(self) -> str:
        raw = (os.getenv("QUIZ_LINGUA_DEVICE") or "auto").strip().lower()
        if raw in {"cpu", "cuda"}:
            return raw
        try:
            import torch

            return "cuda" if torch.cuda.is_available() else "cpu"
        except Exception:
            return "cpu"

    def _get_compressor(self):
        if self._compressor is not None:
            return self._compressor
        with self._lock:
            if self._compressor is not None:
                return self._compressor
            os.environ.setdefault("USE_TF", "0")
            os.environ.setdefault("TRANSFORMERS_NO_TF", "1")
            from llmlingua import PromptCompressor

            self._compressor = PromptCompressor(
                model_name=self.model_name,
                use_llmlingua2=True,
                device_map=self.device,
            )
            logger.info(
                "[LLMLingua] quiz compactor ready: model=%s device=%s",
                self.model_name,
                self.device,
            )
        return self._compressor

    def _split_paragraphs(self, text: str) -> List[str]:
        parts = [line.strip() for line in text.replace("\r", "\n").split("\n")]
        paragraphs = [part for part in parts if part]
        if paragraphs:
            return paragraphs
        return [text] if text else []

    def _split_sentences(self, paragraph: str) -> List[str]:
        paragraph = paragraph.strip()
        if not paragraph:
            return []
        parts = [item.strip() for item in _SENTENCE_SPLIT_RE.split(paragraph) if item.strip()]
        return parts if parts else [paragraph]

    def _is_heading_like(self, text: str) -> bool:
        stripped = text.strip()
        return bool(
            stripped
            and len(stripped) <= 32
            and (_HEADING_RE.match(stripped) or stripped.endswith(("：", ":")))
        )

    def _sentence_score(self, sentence: str, index: int, total: int) -> float:
        score = 0.0
        length = len(sentence)
        if 10 <= length <= 140:
            score += 2.0
        elif length > 140:
            score += 1.0

        if index == 0:
            score += 0.8
        if index == total - 1:
            score += 0.4

        for marker in _SIGNAL_TERMS:
            if marker in sentence:
                score += 1.15

        if any(ch.isdigit() for ch in sentence):
            score += 0.4
        if any(token in sentence for token in ("因此", "所以", "但是", "而", "并", "与", "区别", "鉴别")):
            score += 0.6
        if self._is_heading_like(sentence):
            score += 2.0
        return score

    def _extract_glossary(self, text: str) -> List[str]:
        frequencies: Dict[str, int] = {}
        first_seen: Dict[str, int] = {}
        for index, match in enumerate(_CJK_RE.findall(text)):
            term = match.strip()
            if len(term) < 2 or term in _STOP_TERMS:
                continue
            if term.isdigit():
                continue
            frequencies[term] = frequencies.get(term, 0) + 1
            first_seen.setdefault(term, index)

        def _term_score(term: str) -> float:
            marker_bonus = 0.0
            for marker in _SIGNAL_TERMS:
                if marker in term:
                    marker_bonus += 1.0
            if any(ch.isdigit() for ch in term):
                marker_bonus += 0.5
            return frequencies.get(term, 0) * 2.0 + marker_bonus + min(len(term), 10) * 0.05

        ranked = sorted(frequencies.keys(), key=lambda item: (-_term_score(item), first_seen[item]))
        return ranked[: self.max_glossary_terms]

    def _summarize_paragraph(self, paragraph: str) -> str:
        if self._is_heading_like(paragraph):
            return paragraph.strip()
        sentences = self._split_sentences(paragraph)
        if len(sentences) <= 2:
            return " ".join(sentences).strip()

        keep_count = 1
        if len(paragraph) >= 180:
            keep_count = 2
        if len(paragraph) >= 420:
            keep_count = 3

        ranked = sorted(
            range(len(sentences)),
            key=lambda idx: (-self._sentence_score(sentences[idx], idx, len(sentences)), idx),
        )
        chosen = sorted(ranked[:keep_count])
        if 0 not in chosen:
            chosen = [0] + chosen
        chosen = sorted(set(chosen))
        return " ".join(sentences[idx] for idx in chosen if sentences[idx]).strip()

    def _build_digest(self, content: str) -> Dict[str, Any]:
        text = _normalize_text(content)
        paragraphs = self._split_paragraphs(content)
        digest_limit = min(self.max_digest_chars, max(1800, int(len(text) * self.digest_ratio)))
        glossary = self._extract_glossary(text)
        blocks: List[str] = []
        total_chars = 0

        if glossary:
            glossary_block = "【核心术语】" + " | ".join(glossary)
            blocks.append(glossary_block)
            total_chars += len(glossary_block)

        kept_paragraphs = 0
        for paragraph in paragraphs:
            summary = self._summarize_paragraph(paragraph)
            if not summary:
                continue
            addition = len(summary) + (1 if blocks else 0)
            if blocks and total_chars + addition > digest_limit:
                continue
            if not blocks and addition > digest_limit:
                summary = summary[:digest_limit]
                addition = len(summary)
            blocks.append(summary)
            total_chars += addition
            kept_paragraphs += 1
            if total_chars >= digest_limit:
                break

        digest_text = "\n".join(blocks).strip()
        if not digest_text:
            digest_text = text[:digest_limit]

        return {
            "text": digest_text,
            "blocks": blocks if blocks else [digest_text],
            "glossary": glossary,
            "kept_paragraphs": kept_paragraphs,
        }

    def compact_for_quiz(self, content: str) -> QuizCompactionResult:
        raw_text = str(content or "")
        normalized = _normalize_text(raw_text)
        if not self.enabled:
            return QuizCompactionResult(
                applied=False,
                strategy="disabled",
                final_text=raw_text,
                raw_text=raw_text,
                digest_text=raw_text,
                glossary=[],
                model_name=self.model_name,
                device=self.device,
                raw_chars=len(raw_text),
                digest_chars=len(raw_text),
                final_chars=len(raw_text),
                origin_tokens=0,
                compressed_tokens=0,
                saved_tokens=0,
                compression_rate=1.0,
                context_blocks=0,
                digest_paragraphs=0,
            )
        if len(normalized) < self.min_chars:
            return QuizCompactionResult(
                applied=False,
                strategy="skipped_short_text",
                final_text=raw_text,
                raw_text=raw_text,
                digest_text=raw_text,
                glossary=[],
                model_name=self.model_name,
                device=self.device,
                raw_chars=len(raw_text),
                digest_chars=len(raw_text),
                final_chars=len(raw_text),
                origin_tokens=0,
                compressed_tokens=0,
                saved_tokens=0,
                compression_rate=1.0,
                context_blocks=0,
                digest_paragraphs=0,
            )

        digest = self._build_digest(raw_text)
        digest_text = digest["text"]
        glossary = list(digest["glossary"])
        blocks = list(digest["blocks"])
        digest_chars = len(digest_text)
        raw_chars = len(raw_text)

        if not digest_text or digest_chars >= raw_chars:
            return QuizCompactionResult(
                applied=False,
                strategy="skipped_no_digest_gain",
                final_text=raw_text,
                raw_text=raw_text,
                digest_text=digest_text or raw_text,
                glossary=glossary,
                model_name=self.model_name,
                device=self.device,
                raw_chars=raw_chars,
                digest_chars=digest_chars or raw_chars,
                final_chars=raw_chars,
                origin_tokens=0,
                compressed_tokens=0,
                saved_tokens=0,
                compression_rate=1.0,
                context_blocks=len(blocks),
                digest_paragraphs=int(digest["kept_paragraphs"]),
            )

        final_text = digest_text
        strategy = "digest_only"
        origin_tokens = 0
        compressed_tokens = 0
        saved_tokens = 0
        compression_rate = round(digest_chars / max(raw_chars, 1), 4)
        error = None

        if self.device != "cuda" and not self.allow_cpu_model:
            return QuizCompactionResult(
                applied=True,
                strategy="digest_only_cpu_skip",
                final_text=final_text,
                raw_text=raw_text,
                digest_text=digest_text,
                glossary=glossary,
                model_name=self.model_name,
                device=self.device,
                raw_chars=raw_chars,
                digest_chars=digest_chars,
                final_chars=len(final_text),
                origin_tokens=0,
                compressed_tokens=0,
                saved_tokens=0,
                compression_rate=compression_rate,
                context_blocks=len(blocks),
                digest_paragraphs=int(digest["kept_paragraphs"]),
            )

        try:
            compressor = self._get_compressor()
            compressed = compressor.compress_prompt(
                blocks,
                rate=self.llmlingua_rate,
                use_context_level_filter=False,
                use_token_level_filter=True,
                force_reserve_digit=True,
            )
            compressed_text = str(compressed.get("compressed_prompt") or "").strip()
            candidate_origin_tokens = int(compressed.get("origin_tokens") or 0)
            candidate_compressed_tokens = int(compressed.get("compressed_tokens") or 0)
            candidate_saved_tokens = max(0, candidate_origin_tokens - candidate_compressed_tokens)
            if (
                compressed_text
                and candidate_saved_tokens > 0
                and len(compressed_text) >= int(max(24, digest_chars * self.min_final_ratio))
            ):
                final_text = compressed_text
                strategy = "digest_plus_llmlingua"
                origin_tokens = candidate_origin_tokens
                compressed_tokens = candidate_compressed_tokens
                saved_tokens = candidate_saved_tokens
                compression_rate = round(compressed_tokens / max(origin_tokens, 1), 4)
        except Exception as exc:
            error = str(exc)
            logger.warning("[LLMLingua] quiz compaction fallback to digest: %s", exc)

        return QuizCompactionResult(
            applied=final_text != raw_text,
            strategy=strategy,
            final_text=final_text,
            raw_text=raw_text,
            digest_text=digest_text,
            glossary=glossary,
            model_name=self.model_name,
            device=self.device,
            raw_chars=raw_chars,
            digest_chars=digest_chars,
            final_chars=len(final_text),
            origin_tokens=origin_tokens,
            compressed_tokens=compressed_tokens,
            saved_tokens=saved_tokens,
            compression_rate=compression_rate,
            context_blocks=len(blocks),
            digest_paragraphs=int(digest["kept_paragraphs"]),
            error=error,
        )


_quiz_compactor: Optional[LLMLinguaQuizCompactor] = None


def get_quiz_llmlingua_compactor() -> LLMLinguaQuizCompactor:
    global _quiz_compactor
    if _quiz_compactor is None:
        _quiz_compactor = LLMLinguaQuizCompactor()
    return _quiz_compactor
