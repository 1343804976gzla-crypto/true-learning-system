"""
医学考研出题服务 - 整卷生成模式
核心要求：
1. 一次性生成整套试卷（避免重复）
2. 可选择题目数量（5/10/15/20）
3. 题目之间要有辨析、对比、变式
4. 考察知识点之间的联系，不是孤立考察
"""

import asyncio
import os
import re
import hashlib
import logging
import time
from datetime import datetime, timedelta
from typing import Dict, Any, List, Optional, Tuple, Set
from services.ai_client import get_ai_client

# 配置日志
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(name)s: %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)
logger = logging.getLogger(__name__)


class QuizService:
    """整卷出题服务 - 医学考研专家模式"""

    def __init__(self):
        self.ai = get_ai_client()
        max_concurrency_raw = (os.getenv("QUIZ_SEGMENT_MAX_CONCURRENCY") or "2").strip()
        try:
            self.segment_max_concurrency = max(1, int(max_concurrency_raw))
        except ValueError:
            self.segment_max_concurrency = 2
        self._segment_semaphore = asyncio.Semaphore(self.segment_max_concurrency)

        timeout_cap_raw = (os.getenv("QUIZ_TOTAL_TIMEOUT_SECONDS") or "1200").strip()
        timeout_min_raw = (os.getenv("QUIZ_MIN_TOTAL_TIMEOUT_SECONDS") or "240").strip()
        timeout_per_q_raw = (os.getenv("QUIZ_TIMEOUT_PER_QUESTION_SECONDS") or "45").strip()
        try:
            self.total_timeout_cap_seconds = max(60, int(timeout_cap_raw))
        except ValueError:
            self.total_timeout_cap_seconds = 1200
        try:
            self.total_timeout_min_seconds = max(30, int(timeout_min_raw))
        except ValueError:
            self.total_timeout_min_seconds = 240
        try:
            self.timeout_per_question_seconds = max(5, int(timeout_per_q_raw))
        except ValueError:
            self.timeout_per_question_seconds = 45

        # 智能缓存系统
        cache_enabled_raw = (os.getenv("QUIZ_CACHE_ENABLED") or "true").strip().lower()
        self.cache_enabled = cache_enabled_raw in ("true", "1", "yes")

        cache_ttl_raw = (os.getenv("QUIZ_CACHE_TTL_SECONDS") or "3600").strip()
        try:
            self.cache_ttl_seconds = max(60, int(cache_ttl_raw))
        except ValueError:
            self.cache_ttl_seconds = 3600

        self._cache = {}  # {cache_key: (result, expire_time)}

        # P2级优化：分段结果缓存
        segment_cache_enabled_raw = (os.getenv("QUIZ_SEGMENT_CACHE_ENABLED") or "true").strip().lower()
        self.segment_cache_enabled = segment_cache_enabled_raw in ("true", "1", "yes")

        self._segment_cache = {}  # {segment_key: (questions, expire_time)}

        # 主题一致性校验配置
        topic_check_enabled_raw = (os.getenv("QUIZ_TOPIC_CHECK_ENABLED") or "true").strip().lower()
        self.topic_check_enabled = topic_check_enabled_raw in ("true", "1", "yes")

        topic_threshold_raw = (os.getenv("QUIZ_TOPIC_OVERLAP_THRESHOLD") or "0.3").strip()
        try:
            self.topic_overlap_threshold = max(0.1, min(1.0, float(topic_threshold_raw)))
        except ValueError:
            self.topic_overlap_threshold = 0.3

        print(f"[QuizService] 缓存系统: {'启用' if self.cache_enabled else '禁用'} (TTL={self.cache_ttl_seconds}秒)")
        print(f"[QuizService] 分段缓存: {'启用' if self.segment_cache_enabled else '禁用'}")
        print(f"[QuizService] 主题校验: {'启用' if self.topic_check_enabled else '禁用'} (阈值={self.topic_overlap_threshold})")

    def _extract_keywords(self, text: str) -> Set[str]:
        """
        提取文本中的关键词（医学术语）

        策略：
        1. 提取2-6个字的中文词汇
        2. 过滤常见停用词
        3. 保留医学相关术语
        """
        if not text:
            return set()

        # 提取中文词汇（2-6个字）
        words = re.findall(r'[\u4e00-\u9fa5]{2,6}', text)

        # 停用词列表
        stopwords = {
            "的", "是", "在", "有", "和", "与", "等", "及", "或", "为", "了", "到", "由",
            "对", "从", "以", "可以", "能够", "进行", "通过", "根据", "关于", "因为",
            "所以", "但是", "如果", "这个", "那个", "什么", "怎么", "哪个", "多少",
            "可能", "应该", "需要", "主要", "重要", "常见", "一般", "正常", "异常",
            "增加", "减少", "升高", "降低", "出现", "发生", "引起", "导致", "造成",
            "下列", "以下", "上述", "题目", "选项", "答案", "解析", "考点", "正确",
            "错误", "哪项", "哪些", "描述", "说法", "叙述", "表述"
        }

        # 过滤停用词
        keywords = {w for w in words if w not in stopwords and len(w) >= 2}

        return keywords

    def _calculate_topic_overlap(self, content_keywords: Set[str], question_keywords: Set[str]) -> float:
        """
        计算主题重叠度

        Returns:
            重叠度（0-1之间）
        """
        if not content_keywords:
            return 0.0

        overlap = len(content_keywords & question_keywords)
        overlap_ratio = overlap / len(content_keywords)

        return overlap_ratio

    async def _validate_topic_consistency(
        self,
        uploaded_content: str,
        generated_questions: List[Dict],
        num_questions: int
    ) -> Tuple[bool, float, str]:
        """
        验证生成题目与输入内容的主题一致性

        Returns:
            (is_consistent, overlap_ratio, message)
        """
        if not self.topic_check_enabled:
            return True, 1.0, "主题校验已禁用"

        if not generated_questions:
            return False, 0.0, "无题目可验证"

        # 提取输入内容的关键词
        content_keywords = self._extract_keywords(uploaded_content)

        # 提取生成题目的关键词
        question_texts = []
        for q in generated_questions:
            question_texts.append(q.get("question", ""))
            question_texts.append(q.get("explanation", ""))
            question_texts.append(q.get("key_point", ""))

        question_keywords = self._extract_keywords(" ".join(question_texts))

        # 计算重叠度
        overlap_ratio = self._calculate_topic_overlap(content_keywords, question_keywords)

        print(f"[QuizService] 主题一致性: {overlap_ratio:.2%}")
        print(f"[QuizService] 输入关键词样本: {list(content_keywords)[:10]}")
        print(f"[QuizService] 题目关键词样本: {list(question_keywords)[:10]}")
        print(f"[QuizService] 共同关键词: {list(content_keywords & question_keywords)[:10]}")

        # 判断是否一致
        if overlap_ratio < self.topic_overlap_threshold:
            message = f"主题一致性不足（{overlap_ratio:.2%} < {self.topic_overlap_threshold:.2%}），可能跑偏"
            print(f"[QuizService] ⚠️ {message}")
            return False, overlap_ratio, message

        message = f"主题一致性良好（{overlap_ratio:.2%}）"
        print(f"[QuizService] ✅ {message}")
        return True, overlap_ratio, message

    def _get_cache_key(self, content: str, num_questions: int) -> str:
        """生成缓存键"""
        # 使用内容hash + 题目数量作为缓存键
        content_hash = hashlib.md5(content.encode('utf-8')).hexdigest()
        return f"quiz_{content_hash}_{num_questions}"

    def _get_segment_cache_key(self, segment_content: str, num_questions: int) -> str:
        """生成分段缓存键"""
        segment_hash = hashlib.md5(segment_content.encode('utf-8')).hexdigest()
        return f"segment_{segment_hash}_{num_questions}"

    def _get_from_cache(self, cache_key: str) -> Optional[Dict]:
        """从缓存获取"""
        if not self.cache_enabled:
            return None

        if cache_key in self._cache:
            result, expire_time = self._cache[cache_key]
            if datetime.now() < expire_time:
                print(f"[QuizService] ✅ 缓存命中: {cache_key[:32]}...")
                return result
            else:
                # 过期，删除
                del self._cache[cache_key]
                print(f"[QuizService] ⏰ 缓存过期: {cache_key[:32]}...")

        return None

    def _get_from_segment_cache(self, segment_key: str) -> Optional[List[Dict]]:
        """从分段缓存获取"""
        if not self.segment_cache_enabled:
            return None

        if segment_key in self._segment_cache:
            questions, expire_time = self._segment_cache[segment_key]
            if datetime.now() < expire_time:
                print(f"[QuizService] ✅ 分段缓存命中: {segment_key[:32]}...")
                return questions
            else:
                # 过期，删除
                del self._segment_cache[segment_key]
                print(f"[QuizService] ⏰ 分段缓存过期: {segment_key[:32]}...")

        return None

    def _save_to_cache(self, cache_key: str, result: Dict):
        """保存到缓存"""
        if not self.cache_enabled:
            return

        expire_time = datetime.now() + timedelta(seconds=self.cache_ttl_seconds)
        self._cache[cache_key] = (result, expire_time)
        print(f"[QuizService] 💾 已缓存: {cache_key[:32]}... (过期时间: {expire_time.strftime('%H:%M:%S')})")

    def _save_to_segment_cache(self, segment_key: str, questions: List[Dict]):
        """保存到分段缓存"""
        if not self.segment_cache_enabled:
            return

        expire_time = datetime.now() + timedelta(seconds=self.cache_ttl_seconds)
        self._segment_cache[segment_key] = (questions, expire_time)
        print(f"[QuizService] 💾 已缓存分段: {segment_key[:32]}... (过期时间: {expire_time.strftime('%H:%M:%S')})")

    def _clean_expired_cache(self):
        """清理过期缓存"""
        if not self.cache_enabled:
            return

        now = datetime.now()

        # 清理整卷缓存
        expired_keys = [k for k, (_, expire_time) in self._cache.items() if now >= expire_time]
        for key in expired_keys:
            del self._cache[key]

        # 清理分段缓存
        if self.segment_cache_enabled:
            expired_segment_keys = [k for k, (_, expire_time) in self._segment_cache.items() if now >= expire_time]
            for key in expired_segment_keys:
                del self._segment_cache[key]

            if expired_keys or expired_segment_keys:
                total_cleaned = len(expired_keys) + len(expired_segment_keys)
                print(f"[QuizService] 🧹 清理过期缓存: {total_cleaned} 条（整卷{len(expired_keys)}+分段{len(expired_segment_keys)}）")
        elif expired_keys:
            print(f"[QuizService] 🧹 清理过期缓存: {len(expired_keys)} 条")

    def _get_segment_length(self, num_questions: int) -> int:
        """
        动态分段阈值：
        - 20题时降低到 6000，减少单次输出体积导致的 JSON 截断概率。
        - 15题时适度提前分段。
        - 其余保持 9000。
        """
        if num_questions >= 20:
            return 6000
        if num_questions >= 15:
            return 7500
        return 9000

    def _get_total_timeout_seconds(self, content_length: int, num_questions: int) -> int:
        """
        总耗时保护阈值（动态）：
        - 题量越多、内容越长，阈值适当增加；
        - 但受到全局上限保护，避免无限等待。
        """
        by_questions = num_questions * self.timeout_per_question_seconds
        by_content = min(120, max(0, content_length // 250))
        estimated = max(self.total_timeout_min_seconds, by_questions + by_content)
        return min(self.total_timeout_cap_seconds, estimated)

    async def _generate_single_paper_with_limit(
        self,
        uploaded_content: str,
        num_questions: int,
        difficulty_distribution: Dict,
        segment_key: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        通过信号量控制分段并发，缓解 429 限流。

        Args:
            segment_key: 分段缓存键，如果提供则保存到分段缓存
        """
        async with self._segment_semaphore:
            result = await self._generate_single_paper(
                uploaded_content=uploaded_content,
                num_questions=num_questions,
                difficulty_distribution=difficulty_distribution
            )

            # 如果提供了segment_key，保存到分段缓存
            if segment_key and result and result.get("questions"):
                self._save_to_segment_cache(segment_key, result["questions"])

            return result

    def _chinese_numeral_to_int(self, raw: str) -> Optional[int]:
        mapping = {
            "\u96f6": 0,
            "\u3007": 0,
            "\u4e00": 1,
            "\u4e8c": 2,
            "\u4e24": 2,
            "\u4e09": 3,
            "\u56db": 4,
            "\u4e94": 5,
            "\u516d": 6,
            "\u4e03": 7,
            "\u516b": 8,
            "\u4e5d": 9,
        }
        units = {
            "\u5341": 10,
            "\u767e": 100,
            "\u5343": 1000,
        }
        text = (raw or "").strip()
        if not text:
            return None
        text = text.translate(str.maketrans("\uff10\uff11\uff12\uff13\uff14\uff15\uff16\uff17\uff18\uff19", "0123456789"))
        if text.isdigit():
            return int(text)

        total = 0
        current = 0
        has_unit = False
        for ch in text:
            if ch in mapping:
                current = mapping[ch]
                continue
            if ch in units:
                has_unit = True
                unit = units[ch]
                if current == 0:
                    current = 1
                total += current * unit
                current = 0
                continue
            return None
        total += current
        if total == 0 and has_unit:
            return None
        return total if total > 0 else None

    def _extract_chapter_number_and_title(self, content: str) -> Tuple[str, str]:
        text = (content or "").strip()
        if not text:
            return "", ""

        pat = re.search(
            r"\u7b2c\s*([0-9\uff10-\uff19\u4e00-\u9fa5]{1,8})\s*\u7ae0\s*([^\n\uff0c\u3002\uff1b;:]{1,40})",
            text,
        )
        if pat:
            raw_num = pat.group(1).strip()
            raw_num = raw_num.translate(str.maketrans("\uff10\uff11\uff12\uff13\uff14\uff15\uff16\uff17\uff18\uff19", "0123456789"))
            chapter_title = pat.group(2).strip()
            as_int = self._chinese_numeral_to_int(raw_num)
            chapter_number = str(as_int) if as_int is not None else raw_num
            return chapter_number, chapter_title

        title_only = re.search(
            r"(?:\u7ae0\u8282|\u672c\u7ae0|\u672c\u8282|\u4e3b\u9898)\s*[:\uff1a]\s*([^\n\uff0c\u3002\uff1b;:]{2,40})",
            text,
        )
        if title_only:
            return "", title_only.group(1).strip()

        return "", ""

    def _extract_book_hint(self, content: str) -> str:
        from models import get_db, Chapter

        text = (content or "").strip()
        if not text:
            return ""

        db = next(get_db())
        try:
            books = [b[0] for b in db.query(Chapter.book).distinct().all() if b and b[0]]
        finally:
            db.close()

        books = [b for b in books if b not in {"\u672a\u5206\u7c7b", "unknown"}]
        books.sort(key=len, reverse=True)
        for b in books:
            if b in text:
                return b
        return ""

    def _resolve_chapter_from_db(
        self,
        book: str = "",
        chapter_id: str = "",
        chapter_title: str = "",
        chapter_number: str = "",
        confidence: str = "medium",
    ) -> Optional[Dict[str, str]]:
        from models import get_db, Chapter

        def _pack(ch, conf: str) -> Dict[str, str]:
            return {
                "book": ch.book,
                "chapter_id": ch.id,
                "chapter_title": ch.chapter_title,
                "confidence": conf if conf in {"high", "medium", "low"} else "medium",
            }

        db = next(get_db())
        try:
            # 1) direct id lookup
            if chapter_id:
                ch = db.query(Chapter).filter(Chapter.id == chapter_id).first()
                if ch:
                    return _pack(ch, confidence)

                # normalize ids like physiology_ch06 -> physiology_ch6
                m = re.match(r"^(.+_ch)0+([0-9]+)$", chapter_id)
                if m:
                    normalized = f"{m.group(1)}{int(m.group(2))}"
                    ch = db.query(Chapter).filter(Chapter.id == normalized).first()
                    if ch:
                        return _pack(ch, confidence)

            # 2) book + number + title (best precision when all hints are present)
            if book and chapter_number and chapter_title:
                num = str(chapter_number).strip()
                num_candidates = [num]
                if num.isdigit():
                    num_candidates.append(str(int(num)))
                else:
                    parsed = self._chinese_numeral_to_int(num)
                    if parsed is not None:
                        num_candidates.append(str(parsed))

                for cand in dict.fromkeys(num_candidates):
                    ch = (
                        db.query(Chapter)
                        .filter(
                            Chapter.book == book,
                            Chapter.chapter_number == cand,
                            Chapter.chapter_title.contains(chapter_title[:8]),
                        )
                        .first()
                    )
                    if ch:
                        return _pack(ch, "high")

            # 3) book + title fuzzy (prefer title before number for higher precision)
            if book and chapter_title:
                ch = (
                    db.query(Chapter)
                    .filter(
                        Chapter.book == book,
                        Chapter.chapter_title.contains(chapter_title[:8])
                    )
                    .first()
                )
                if ch:
                    return _pack(ch, "medium")

            # 4) book + chapter number
            if book and chapter_number:
                num = str(chapter_number).strip()
                num_candidates = [num]
                if num.isdigit():
                    num_candidates.append(str(int(num)))
                else:
                    parsed = self._chinese_numeral_to_int(num)
                    if parsed is not None:
                        num_candidates.append(str(parsed))

                for cand in dict.fromkeys(num_candidates):
                    matches = (
                        db.query(Chapter)
                        .filter(Chapter.book == book, Chapter.chapter_number == cand)
                        .all()
                    )
                    if matches:
                        # Prefer non-auto-filled titles when multiple chapter ids share one number.
                        matches.sort(
                            key=lambda x: (
                                (x.chapter_title or "").startswith("\u81ea\u52a8\u8865\u9f50\u7ae0\u8282"),
                                len(x.chapter_title or ""),
                            )
                        )
                        return _pack(matches[0], "high")

            # 5) title-only fuzzy
            if chapter_title:
                ch = db.query(Chapter).filter(Chapter.chapter_title.contains(chapter_title[:8])).first()
                if ch:
                    return _pack(ch, "low")
        finally:
            db.close()

        return None

    def _infer_chapter_prediction(self, content: str) -> Optional[Dict[str, str]]:
        text = (content or "").strip()
        if not text:
            return None

        book = self._extract_book_hint(text)
        chapter_number, chapter_title = self._extract_chapter_number_and_title(text)
        pred = self._resolve_chapter_from_db(
            book=book,
            chapter_title=chapter_title,
            chapter_number=chapter_number,
            confidence="medium",
        )
        if pred:
            return pred

        if book or chapter_title or chapter_number:
            return {
                "book": book or "\u672a\u5206\u7c7b",
                "chapter_id": "",
                "chapter_title": chapter_title or (f"\u7b2c{chapter_number}\u7ae0" if chapter_number else ""),
                "confidence": "low",
            }
        return None

    def _normalize_chapter_prediction(self, pred: Any, content: str) -> Optional[Dict[str, str]]:
        inferred = self._infer_chapter_prediction(content)

        if isinstance(pred, dict):
            book = str(pred.get("book") or "").strip()
            chapter_id = str(pred.get("chapter_id") or "").strip()
            chapter_title = str(pred.get("chapter_title") or "").strip()
            confidence = str(pred.get("confidence") or "medium").strip().lower()
            resolved = self._resolve_chapter_from_db(
                book=book,
                chapter_id=chapter_id,
                chapter_title=chapter_title,
                confidence=confidence if confidence in {"high", "medium", "low"} else "medium",
            )
            if resolved:
                hinted_book = self._extract_book_hint(content)
                _, hinted_title = self._extract_chapter_number_and_title(content)
                mismatch = False
                if hinted_book and resolved.get("book") and hinted_book != resolved.get("book"):
                    mismatch = True
                if hinted_title:
                    token = hinted_title[:4]
                    if token and token not in (resolved.get("chapter_title") or ""):
                        mismatch = True
                if mismatch and inferred and inferred.get("chapter_id") and inferred.get("chapter_id") != resolved.get("chapter_id"):
                    return inferred
                return resolved
            # If AI prediction cannot be resolved to DB, prefer local inference from content.
            if inferred and inferred.get("chapter_id"):
                return inferred
            if book or chapter_id or chapter_title:
                return {
                    "book": book,
                    "chapter_id": chapter_id,
                    "chapter_title": chapter_title,
                    "confidence": confidence if confidence in {"high", "medium", "low"} else "medium",
                }

        return inferred

    def _get_chapter_catalog(self, content: str = "") -> str:
        """
        获取章节目录，优先返回与内容匹配科目的章节列表。

        策略：
        1. 从 content 中识别科目（_extract_book_hint）
        2. 若识别到科目，返回该科目的所有章节（chapter_id + title）
        3. 若未识别到，仅返回科目名称列表（避免 token 爆炸）
        """
        from models import get_db, Chapter
        db = next(get_db())
        try:
            # 尝试识别内容所属科目
            matched_book = self._extract_book_hint(content) if content else ""

            if matched_book:
                chapters = (
                    db.query(Chapter.id, Chapter.chapter_number, Chapter.chapter_title)
                    .filter(Chapter.book == matched_book)
                    .order_by(Chapter.chapter_number)
                    .all()
                )
                if chapters:
                    lines = [f"科目：{matched_book}，可选章节："]
                    for ch_id, ch_num, ch_title in chapters:
                        # 过滤自动补齐章节，避免噪音
                        if ch_title and not ch_title.startswith("自动补齐章节"):
                            lines.append(f"  - {ch_id}（第{ch_num}章 {ch_title}）")
                    if len(lines) > 1:
                        return "\n".join(lines)

            # 未匹配到科目，返回科目列表
            books = db.query(Chapter.book).distinct().all()
            book_list = [b[0] for b in books if b and b[0] and b[0] not in {"未分类", "unknown"}]
            if not book_list:
                return "生理学、生物化学、病理学、内科学、外科学"
            return "可选科目：" + "、".join(sorted(book_list))
        finally:
            db.close()

    async def generate_exam_paper(
        self,
        uploaded_content: str,
        num_questions: int = 10,
        difficulty_distribution: Optional[Dict] = None
    ) -> Dict[str, Any]:
        """
        一次性生成整套试卷
        如果内容过长，自动分段生成

        Args:
            uploaded_content: 上传的讲课内容
            num_questions: 题目数量（5/10/15/20）
            difficulty_distribution: 难度分布，默认基础:提高:难题 = 5:3:2

        Returns:
            整套试卷，包含题目、解析、难度分布
        """

        if difficulty_distribution is None:
            difficulty_distribution = {"基础": 0.5, "提高": 0.3, "难题": 0.2}

        # 清理过期缓存
        self._clean_expired_cache()

        # 检查缓存
        cache_key = self._get_cache_key(uploaded_content, num_questions)
        cached_result = self._get_from_cache(cache_key)
        if cached_result:
            logger.info("🚀 使用缓存结果，跳过生成")
            return cached_result

        # 检查内容长度，超过阈值再分段生成。
        # 20题场景优先保证稳定性（降低截断/解析失败概率），会提前分段。
        content_length = len(uploaded_content)
        max_segment_length = self._get_segment_length(num_questions)
        total_timeout_s = self._get_total_timeout_seconds(content_length, num_questions)

        logger.info(f"=== 生成任务配置 ===")
        logger.info(f"题目数量: {num_questions}")
        logger.info(f"内容长度: {content_length} 字符")
        logger.info(f"分段阈值: {max_segment_length} 字符")
        logger.info(f"外层总超时: {total_timeout_s}s ({total_timeout_s/60:.1f}分钟)")

        if content_length > max_segment_length:
            num_segments = (content_length + max_segment_length - 1) // max_segment_length
            logger.info(f"📦 启动分段生成模式 (预计{num_segments}个分段, 并发上限={self.segment_max_concurrency})")
            job = self._generate_paper_in_segments(
                uploaded_content, num_questions, difficulty_distribution, max_segment_length
            )
        else:
            logger.info(f"📄 内容长度适中，使用单次生成")
            job = self._generate_single_paper(
                uploaded_content, num_questions, difficulty_distribution
            )

        try:
            start_time = time.time()
            result = await asyncio.wait_for(job, timeout=total_timeout_s)
            elapsed = time.time() - start_time

            logger.info(f"✅ 生成完成，总耗时: {elapsed:.1f}s")

            # 保存到缓存
            self._save_to_cache(cache_key, result)

            return result
        except asyncio.TimeoutError as exc:
            elapsed = time.time() - start_time
            logger.error(f"❌ 生成超时: {elapsed:.1f}s / {total_timeout_s}s")
            timeout_message = (
                f"生成超时：超过 {total_timeout_s} 秒已自动中止。"
                "请减少题量（建议先 10 题）或稍后重试。"
            )
            print(f"[QuizService] ⏱️ {timeout_message}")
            raise RuntimeError(f"QUIZ_TIMEOUT|{timeout_message}") from exc

    async def _generate_paper_in_segments(
        self,
        uploaded_content: str,
        num_questions: int,
        difficulty_distribution: Dict,
        max_segment_length: int = 9000
    ) -> Dict[str, Any]:
        """
        分段生成试卷（并行版本）
        将长内容分成多段，并行生成题目，最后合并

        优化：使用 asyncio.gather 并行执行，大幅提升性能
        - 原来：2段 × 60秒 = 120秒（串行）
        - 现在：max(60秒, 60秒) = 60秒（并行）
        """
        content_length = len(uploaded_content)
        max_segment_length = max(1, max_segment_length)

        # 计算需要分成几段
        num_segments = (content_length + max_segment_length - 1) // max_segment_length
        tail_length = content_length - (num_segments - 1) * max_segment_length if num_segments > 1 else content_length
        min_tail_length = max(500, int(max_segment_length * 0.2))
        if num_segments >= 3 and tail_length < min_tail_length:
            num_segments -= 1
            print(
                f"[QuizService] 检测到极小尾段({tail_length}字)，重平衡为 {num_segments} 段"
            )

        # 采用均衡切分，避免最后一段过短导致不稳定
        segment_ranges = []
        avg_length = content_length // num_segments
        remainder = content_length % num_segments
        cursor = 0
        for i in range(num_segments):
            current_len = avg_length + (1 if i < remainder else 0)
            next_cursor = cursor + current_len
            segment_ranges.append((cursor, next_cursor))
            cursor = next_cursor

        print(
            f"[QuizService] 将内容分成 {num_segments} 段，启用并行生成模式 "
            f"(单段上限={max_segment_length}字, 并发上限={self.segment_max_concurrency})"
        )

        # 计算每段生成多少题
        questions_per_segment = num_questions // num_segments
        remaining_questions = num_questions % num_segments

        # 创建并行任务列表
        tasks = []
        segment_info = []  # 记录每段的信息，用于日志输出
        cached_segments = 0  # 统计缓存命中的段数

        for i, (start_idx, end_idx) in enumerate(segment_ranges):
            segment_content = uploaded_content[start_idx:end_idx]

            # 计算这一段生成多少题
            segment_questions = questions_per_segment
            if i < remaining_questions:
                segment_questions += 1

            segment_info.append({
                "index": i + 1,
                "length": len(segment_content),
                "questions": segment_questions
            })

            # 检查分段缓存
            segment_key = self._get_segment_cache_key(segment_content, segment_questions)
            cached_questions = self._get_from_segment_cache(segment_key)

            if cached_questions:
                # 缓存命中，直接使用缓存结果
                cached_segments += 1
                print(f"[QuizService] 第 {i+1}/{num_segments} 段缓存命中 ({len(segment_content)}字, {segment_questions}题)")
                # 创建一个立即返回缓存结果的协程
                async def return_cached(questions=cached_questions):
                    return {"questions": questions}
                tasks.append(return_cached())
            else:
                # 缓存未命中，需要生成
                print(f"[QuizService] 准备第 {i+1}/{num_segments} 段 ({len(segment_content)}字, {segment_questions}题)")
                # 创建异步任务（不立即执行）
                task = self._generate_single_paper_with_limit(
                    uploaded_content=segment_content,
                    num_questions=segment_questions,
                    difficulty_distribution=difficulty_distribution,
                    segment_key=segment_key  # 传递segment_key用于保存缓存
                )
                tasks.append(task)

        if cached_segments > 0:
            print(f"[QuizService] 🎯 分段缓存命中: {cached_segments}/{num_segments} 段")

        # 并行执行所有任务，使用 return_exceptions=True 确保单个失败不影响其他任务
        print(f"[QuizService] 🚀 开始并行生成 {num_segments - cached_segments} 段（{cached_segments}段使用缓存）...")
        import time
        start_time = time.time()

        results = await asyncio.gather(*tasks, return_exceptions=True)

        elapsed = time.time() - start_time
        print(f"[QuizService] ✅ 并行生成完成，耗时 {elapsed:.2f} 秒")

        # 收集所有成功的题目
        all_questions = []
        chapter_prediction = None

        for i, result in enumerate(results):
            segment_idx = i + 1

            # 检查是否是异常
            if isinstance(result, Exception):
                print(f"[QuizService] ❌ 第 {segment_idx} 段生成失败: {result}")
                continue

            # 检查结果是否有效
            if not result or not isinstance(result, dict):
                print(f"[QuizService] ⚠️ 第 {segment_idx} 段返回无效结果")
                continue

            # 收集题目
            segment_questions = result.get("questions", [])
            if segment_questions:
                filtered_segment_questions = [
                    q for q in segment_questions if not self._is_placeholder_question(q)
                ]
                dropped = len(segment_questions) - len(filtered_segment_questions)
                all_questions.extend(filtered_segment_questions)
                print(
                    f"[QuizService] ✅ 第 {segment_idx} 段成功生成 {len(filtered_segment_questions)} 道题"
                    + (f"（过滤占位题 {dropped} 道）" if dropped > 0 else "")
                )

                # 获取章节预测（使用第一个有效的）
                if not chapter_prediction and result.get("chapter_prediction"):
                    # 使用第一段的内容进行章节预测
                    first_segment = uploaded_content[:max_segment_length]
                    chapter_prediction = self._normalize_chapter_prediction(
                        result.get("chapter_prediction"),
                        first_segment,
                    )
            else:
                print(f"[QuizService] ⚠️ 第 {segment_idx} 段未生成题目")

        # 尝试补充真实题目（减少占位题下发）
        if len(all_questions) < num_questions:
            missing = num_questions - len(all_questions)
            print(f"[QuizService] ⚠️ 当前仅 {len(all_questions)}/{num_questions} 道，尝试补生 {missing} 道真实题")
            try:
                refill_result = await self._generate_single_paper_with_limit(
                    uploaded_content=uploaded_content[:max_segment_length],
                    num_questions=missing,
                    difficulty_distribution=difficulty_distribution
                )
                refill_questions = [
                    q for q in refill_result.get("questions", [])
                    if self._is_valid_question(q, 0) and not self._is_placeholder_question(q)
                ]
                if refill_questions:
                    all_questions.extend(refill_questions[:missing])
                    print(f"[QuizService] ✅ 追加补生 {min(len(refill_questions), missing)} 道题")
            except Exception as refill_error:
                print(f"[QuizService] ⚠️ 补生题目失败: {refill_error}")

        # 检查是否生成了足够的题目
        if len(all_questions) < num_questions:
            print(f"[QuizService] ⚠️ 只生成了 {len(all_questions)}/{num_questions} 道题，补充占位符")
            while len(all_questions) < num_questions:
                all_questions.append(self._create_placeholder_question(len(all_questions) + 1))

        # 重新编号
        for i, q in enumerate(all_questions[:num_questions], 1):
            q["id"] = i

        # 收集知识点
        knowledge_points = []
        for q in all_questions[:num_questions]:
            kp = q.get("key_point", "").strip()
            if kp and kp not in knowledge_points:
                knowledge_points.append(kp)

        # 计算实际难度分布
        actual_distribution = {"基础": 0, "提高": 0, "难题": 0}
        for q in all_questions[:num_questions]:
            diff = q.get("difficulty", "基础")
            if diff in actual_distribution:
                actual_distribution[diff] += 1

        print(f"[QuizService] ✅ 分段生成完成，共 {len(all_questions[:num_questions])} 道题")
        if not chapter_prediction:
            chapter_prediction = self._infer_chapter_prediction(uploaded_content)

        return {
            "paper_title": "医学考研模拟试卷（分段生成）",
            "total_questions": num_questions,
            "chapter_prediction": chapter_prediction,
            "difficulty_distribution": actual_distribution,
            "questions": all_questions[:num_questions],
            "knowledge_points": knowledge_points,
            "summary": {
                "coverage": f"覆盖 {len(knowledge_points)} 个知识点",
                "focus": "基础知识和临床应用",
                "advice": "注意辨析易混淆概念"
            }
        }

    async def _generate_single_paper(
        self,
        uploaded_content: str,
        num_questions: int,
        difficulty_distribution: Dict
    ) -> Dict[str, Any]:
        """
        单次生成试卷（原有逻辑）
        """
        
        if difficulty_distribution is None:
            difficulty_distribution = {"基础": 0.5, "提高": 0.3, "难题": 0.2}
        
        # 截取内容（最多20000字）
        content = uploaded_content[:20000] if len(uploaded_content) > 20000 else uploaded_content

        # 获取章节目录（优先匹配当前内容的科目）
        chapter_catalog = self._get_chapter_catalog(content)

        # 计算各难度题数
        basic = int(num_questions * difficulty_distribution["基础"])
        improve = int(num_questions * difficulty_distribution["提高"])
        hard = num_questions - basic - improve

        prompt = f"""【角色】你是资深西医综合（306）考研命题专家，拥有20年命题经验。

【任务】基于以下医学讲课内容，**一次性生成{num_questions}道高质量考研选择题**。
⚠️ 禁止一道一道出题！必须一次性输出全部{num_questions}道题！

【科目与章节】
{chapter_catalog}

请根据讲课内容判断最匹配的章节，在 chapter_prediction 中填写对应的 chapter_id（若上方有列出）和 chapter_title。若无法确定具体章节，填写科目名和大致章节标题即可。

【核心约束 - 违反则试卷无效】
1. **绝对禁止逐题生成**：所有{num_questions}道题必须同时构思，确保全局知识点覆盖
2. **知识点零重复**：每道题的key_point必须完全不同
3. **辨析对比设计**：相似概念必须放在一起对比出题
4. **变式考察**：同一知识点用3种以上不同形式出现
5. **内在逻辑链**：第1题→第2题→第3题...形成知识递进链
6. **每道题必须有且仅有 A、B、C、D、E 五个选项**：不得少于五个，options 中 A/B/C/D/E 缺一不可，X型多选题也必须提供五个选项供选择

【难度分布】
- 基础题（{basic}道）：考察单一核心概念
- 提高题（{improve}道）：考察2-3个知识点的综合鉴别
- 难题（{hard}道）：考察知识点之间的因果关系、临床决策

【题型分配】
- A1型：{max(2, num_questions//5)}道（概念题）
- A2型：{max(3, num_questions//3)}道（病例题）
- A3型：{max(2, num_questions//5)}道（病例组）
- X型：{max(1, num_questions//10)}道（多选）

【讲课内容】
{content}

【输出格式示例】
{{
    "paper_title": "实际试卷标题（不要写'试卷标题'）",
    "total_questions": {num_questions},
    "chapter_prediction": {{"book": "生理学", "chapter_id": "", "chapter_title": "消化系统", "confidence": "high"}},
    "difficulty_distribution": {{"基础": {basic}, "提高": {improve}, "难题": {hard}}},
    "questions": [
        {{
            "id": 1,
            "type": "A1",
            "difficulty": "基础",
            "question": "实际题目内容（不要写'题目'或'题目内容'）",
            "options": {{"A": "实际选项A内容", "B": "实际选项B内容", "C": "实际选项C内容", "D": "实际选项D内容", "E": "实际选项E内容"}},
            "correct_answer": "A",
            "explanation": "实际解析内容（不要写'解析'）",
            "key_point": "实际考点（不要写'考点'）",
            "related_questions": "[2,3]"
        }}
    ],
    "summary": {{"coverage": "实际覆盖的知识点", "focus": "实际重点", "advice": "实际建议"}}
}}

⚠️ 重要：不要返回模板占位符（如"题目"、"选项A"、"解析"等），必须填写实际内容！"""

        schema = {
            "paper_title": "西医综合考研XX专项测试",
            "total_questions": num_questions,
            "chapter_prediction": {"book": "生理学", "chapter_id": "", "chapter_title": "消化系统", "confidence": "high"},
            "difficulty_distribution": {"基础": basic, "提高": improve, "难题": hard},
            "questions": [
                {
                    "id": 1,
                    "type": "A1",
                    "difficulty": "基础",
                    "question": "胃液中盐酸的主要作用是什么？",
                    "options": {
                        "A": "激活胃蛋白酶原",
                        "B": "促进铁的吸收",
                        "C": "杀死细菌",
                        "D": "以上都是",
                        "E": "以上都不是"
                    },
                    "correct_answer": "D",
                    "explanation": "盐酸的作用包括激活胃蛋白酶原、促进铁的吸收、杀死细菌等",
                    "key_point": "胃酸的生理作用",
                    "related_questions": "[2,3]"
                }
            ],
            "summary": {"coverage": "胃液分泌、消化吸收", "focus": "基础概念和临床应用", "advice": "注意辨析易混淆概念"}
        }
        fallback_prediction = self._infer_chapter_prediction(uploaded_content)
        
        try:
            start_time = time.time()

            # 动态计算 AI 调用超时和输出 token 上限
            # 考虑题目数量和内容长度两个因素
            content_len = len(content)
            base_timeout = num_questions * 40  # 每题40秒基础
            content_bonus = min(300, content_len // 100)  # 每100字符+1秒，最多+300秒
            ai_timeout = max(300, base_timeout + content_bonus)

            max_output_tokens = max(8192, num_questions * 800)  # 15题=12000, 20题=16000
            max_output_tokens = min(max_output_tokens, 32768)  # 模型上限保护

            logger.info(f"=== 开始生成 {num_questions} 道题 ===")
            logger.info(f"内容长度: {content_len} 字符")
            logger.info(f"AI超时: {ai_timeout}s (基础{base_timeout}s + 内容加成{content_bonus}s)")
            logger.info(f"max_tokens: {max_output_tokens}")
            logger.info(f"预计每个模型分配: {ai_timeout // 4}s (Heavy池4个模型)")

            result = await self.ai.generate_json(prompt, schema, max_tokens=max_output_tokens, temperature=0.3, use_heavy=True, timeout=ai_timeout)

            elapsed = time.time() - start_time
            logger.info(f"AI 调用完成，耗时: {elapsed:.1f}s")
            logger.info(f"AI 返回结果类型: {type(result)}")
            logger.info(f"AI 返回题目数: {len(result.get('questions', []))}")

            # 检查第一题选项内容（调试用）
            if result.get("questions") and len(result.get("questions", [])) > 0:
                q0 = result["questions"][0]
                logger.debug(f"第1题题目: {q0.get('question', '无')[:50]}...")
                logger.debug(f"第1题选项A: {q0.get('options', {}).get('A', '无')[:30]}")
                logger.debug(f"第1题选项B: {q0.get('options', {}).get('B', '无')[:30]}")
            else:
                logger.warning("⚠️ AI 返回的题目列表为空！")
                logger.warning(f"完整返回: {result}")

            # 验证题目有效性，过滤无效题目
            raw_questions = result.get("questions", [])
            valid_questions = []

            for i, q in enumerate(raw_questions, 1):
                if self._is_valid_question(q, i):
                    valid_questions.append(q)
                else:
                    logger.warning(f"⚠️ 第{i}题无效，已过滤")

            logger.info(f"有效题目: {len(valid_questions)}/{len(raw_questions)}")

            # 主题一致性校验
            is_consistent, overlap_ratio, consistency_message = await self._validate_topic_consistency(
                uploaded_content=uploaded_content,
                generated_questions=valid_questions,
                num_questions=num_questions
            )

            # 如果主题不一致，记录警告但不重新生成（避免超时）
            if not is_consistent:
                print(f"[QuizService] ⚠️ {consistency_message}")
                print(f"[QuizService] ⚠️ 跳过重新生成（避免超时风险），使用当前结果")
                # 可以在结果中添加警告信息
                if "summary" not in result:
                    result["summary"] = {}
                result["summary"]["topic_warning"] = consistency_message

            # 如果有效题目不足，用占位符补充
            questions = valid_questions[:num_questions]
            if len(questions) < num_questions:
                print(f"[QuizService] ⚠️ 有效题目不足 ({len(questions)}/{num_questions})，使用占位符补充")

                while len(questions) < num_questions:
                    questions.append(self._create_placeholder_question(len(questions) + 1))

            # 重新编号
            for i, q in enumerate(questions, 1):
                q["id"] = i

            result["questions"] = questions
            result["total_questions"] = len(questions)
            result["chapter_prediction"] = (
                self._normalize_chapter_prediction(
                    result.get("chapter_prediction"),
                    uploaded_content,
                )
                or fallback_prediction
                or self._resolve_chapter_from_db(chapter_id="0", confidence="low")
                or {"book": "未分类", "chapter_id": "0", "chapter_title": "自动补齐章节(0)", "confidence": "low"}
            )

            print(f"[QuizService] ✅ 最终返回 {len(questions)} 道题")

            return result

        except Exception as e:
            print(f"[QuizService] ❌ 出卷失败: {e}")
            import traceback
            traceback.print_exc()
            print(f"[QuizService] 使用默认试卷作为 fallback")

            # 返回一个带有错误信息的默认试卷
            default_paper = self._generate_default_paper(num_questions)
            default_paper["error_message"] = f"AI 生成失败: {str(e)}"
            default_paper["paper_title"] = "⚠️ AI 生成失败 - 请重试"
            default_paper["chapter_prediction"] = (
                fallback_prediction
                or self._resolve_chapter_from_db(chapter_id="0", confidence="low")
                or {"book": "未分类", "chapter_id": "0", "chapter_title": "自动补齐章节(0)", "confidence": "low"}
            )
            return default_paper
    
    def _is_valid_question(self, q: Dict, index: int) -> bool:
        """
        验证题目是否有效（不是 schema 模板或空内容）

        Returns:
            True: 题目有效
            False: 题目无效（是模板或缺失关键内容）
        """
        # Schema 模板关键词（中英文）
        template_keywords = [
            "题目", "题型", "难度", "答案", "解析", "考点", "相关题号",
            "question", "type", "difficulty", "answer", "explanation", "key_point"
        ]

        # 1. 检查题目内容
        question_text = (q.get("question") or "").strip()
        if (
            not question_text
            or question_text in template_keywords
            or ("请根据讲课内容回答问题" in question_text)
            or ("AI生成失败" in question_text)
            or ("请重新生成试卷" in question_text)
        ):
            print(f"[QuizService] 第{index}题无效：题目是模板或为空")
            return False

        # 2. 检查题型
        q_type = (q.get("type") or "").strip()
        if not q_type or q_type in template_keywords or q_type not in ["A1", "A2", "A3", "X", "B"]:
            print(f"[QuizService] 第{index}题无效：题型无效 ({q_type})")
            return False

        # 3. 检查难度
        difficulty = (q.get("difficulty") or "").strip()
        if not difficulty or difficulty in template_keywords or difficulty not in ["基础", "提高", "难题"]:
            print(f"[QuizService] 第{index}题无效：难度无效 ({difficulty})")
            return False

        # 4. 检查选项
        options = q.get("options", {})
        if not options or not isinstance(options, dict):
            print(f"[QuizService] 第{index}题无效：选项字段缺失")
            return False

        for opt in ["A", "B", "C", "D", "E"]:
            val = (options.get(opt) or "").strip()
            # 检查是否为空、模板关键词、或占位符
            if (
                not val
                or val in template_keywords
                or val == f"选项{opt}"
                or "占位符" in val
                or val.startswith("（选项")
                or val == "..."
            ):
                print(f"[QuizService] 第{index}题无效：选项{opt}无效 ({val[:20]})")
                return False

        # 5. 检查答案
        correct_answer = (q.get("correct_answer") or "").strip().upper()
        if not correct_answer or correct_answer in template_keywords:
            print(f"[QuizService] 第{index}题无效：答案无效 ({correct_answer})")
            return False

        normalized = (
            correct_answer.replace("，", ",")
            .replace("、", ",")
            .replace(" ", "")
        )
        answer_parts = normalized.split(",") if "," in normalized else list(normalized)
        # 只保留 A-E 字母（过滤掉 AI 可能附带的句号、选项文本等噪声）
        answer_parts = [p for p in answer_parts if p in ["A", "B", "C", "D", "E"]]
        if not answer_parts:
            print(f"[QuizService] 第{index}题无效：答案选项超出 A-E ({correct_answer})")
            return False
        if q_type == "X":
            if len(set(answer_parts)) < 2:
                print(f"[QuizService] 第{index}题无效：X型题答案至少两个选项 ({correct_answer})")
                return False
        else:
            if len(answer_parts) != 1:
                print(f"[QuizService] 第{index}题无效：单选题答案必须唯一 ({correct_answer})")
                return False

        # 将规范化后的答案写回题目字典（防止存入带空格/小写/多余文本的原始值）
        q["correct_answer"] = "".join(sorted(set(answer_parts))) if q_type == "X" else answer_parts[0]

        # 6. 检查解析（可选，但不能是模板）
        explanation = (q.get("explanation") or "").strip()
        if explanation in template_keywords or "占位符" in explanation:
            print(f"[QuizService] 第{index}题无效：解析是模板")
            return False

        return True

    def _create_placeholder_question(self, id: int) -> Dict:
        """创建占位题目"""
        return {
            "id": id,
            "type": "A1",
            "difficulty": "基础",
            "question": f"第{id}题（AI生成失败，请重新生成试卷）",
            "options": {
                "A": "选项A（占位符）",
                "B": "选项B（占位符）",
                "C": "选项C（占位符）",
                "D": "选项D（占位符）",
                "E": "选项E（占位符）"
            },
            "correct_answer": "A",
            "explanation": "此题为占位符，请重新生成试卷",
            "key_point": f"考点{id}",
            "related_questions": "[]"
        }

    def _is_placeholder_question(self, question: Dict[str, Any]) -> bool:
        """判断是否为占位题（尽量不向前端下发）。"""
        q_text = (question.get("question") or "").strip()
        explanation = (question.get("explanation") or "").strip()
        options = question.get("options") or {}

        if "AI生成失败" in q_text or "请重新生成试卷" in q_text:
            return True
        if "占位符" in explanation:
            return True
        for _, value in options.items():
            if "占位符" in str(value):
                return True
        return False
    
    def _generate_default_paper(self, num_questions: int) -> Dict[str, Any]:
        """生成失败兜底试卷（显式占位，避免伪装成真实题目）。"""
        questions = [self._create_placeholder_question(i) for i in range(1, num_questions + 1)]

        return {
            "paper_title": "⚠️ AI 生成失败 - 请重试",
            "total_questions": num_questions,
            "difficulty_distribution": {"基础": num_questions, "提高": 0, "难题": 0},
            "questions": questions,
            "summary": {
                "coverage": "当前为失败兜底占位题",
                "focus": "请重新生成试卷",
                "advice": "若反复失败，请稍后重试或减少题量"
            }
        }
    
    def grade_paper(
        self,
        questions: List[Dict[str, Any]],
        user_answers: List[str],
        user_confidence: Optional[Dict[str, str]] = None
    ) -> Dict[str, Any]:
        """
        批改试卷 - 直接对比
        """
        if user_confidence is None:
            user_confidence = {}

        # 答案清理函数：只保留 A-E 字母
        def clean_answer(ans: str) -> str:
            import re
            return re.sub(r'[^A-E]', '', (ans or '').strip().upper())

        details = []
        correct_count = 0
        wrong_by_difficulty = {"基础": 0, "提高": 0, "难题": 0}

        # 自信度统计
        confidence_stats = {"sure": 0, "unsure": 0, "no": 0}
        confidence_correct = {"sure": 0, "unsure": 0, "no": 0}

        for i, question in enumerate(questions):
            if i >= len(user_answers):
                break

            # 清理答案：只保留 A-E 字母
            user_ans = clean_answer(user_answers[i])
            correct_ans = clean_answer(question.get("correct_answer", ""))
            difficulty = question.get("difficulty", "基础")
            conf = user_confidence.get(str(i), user_confidence.get(i, ""))

            # 判断对错
            if question.get("type") == "X":
                # 多选题：排序后比较
                is_correct = sorted(user_ans) == sorted(correct_ans)
            else:
                # 单选题：直接比较
                is_correct = user_ans == correct_ans

            if is_correct:
                correct_count += 1
            else:
                wrong_by_difficulty[difficulty] = wrong_by_difficulty.get(difficulty, 0) + 1

            # 统计自信度
            if conf in confidence_stats:
                confidence_stats[conf] = confidence_stats.get(conf, 0) + 1
                if is_correct:
                    confidence_correct[conf] = confidence_correct.get(conf, 0) + 1

            details.append({
                "id": question.get("id", i + 1),
                "type": question.get("type"),
                "difficulty": difficulty,
                "user_answer": user_ans,  # 返回清理后的答案
                "correct_answer": correct_ans,  # 返回清理后的答案
                "is_correct": is_correct,
                "confidence": conf,
                "explanation": question.get("explanation", ""),
                "key_point": question.get("key_point", ""),
                "related_questions": question.get("related_questions", "[]")
            })

        total = len(details)
        score = int(correct_count / total * 100) if total > 0 else 0

        # 计算自信度正确率
        confidence_analysis = {
            "sure": confidence_stats.get("sure", 0),
            "unsure": confidence_stats.get("unsure", 0),
            "no": confidence_stats.get("no", 0),
            "sure_rate": int(confidence_correct.get("sure", 0) / confidence_stats.get("sure", 1) * 100) if confidence_stats.get("sure", 0) > 0 else 0,
            "unsure_rate": int(confidence_correct.get("unsure", 0) / confidence_stats.get("unsure", 1) * 100) if confidence_stats.get("unsure", 0) > 0 else 0,
            "no_rate": int(confidence_correct.get("no", 0) / confidence_stats.get("no", 1) * 100) if confidence_stats.get("no", 0) > 0 else 0,
        }

        # 分析薄弱环节
        weak_points = []
        for d in details:
            if not d["is_correct"]:
                weak_points.append(f"{d['key_point']}({d['difficulty']})")

        return {
            "score": score,
            "correct_count": correct_count,
            "wrong_count": total - correct_count,
            "total": total,
            "wrong_by_difficulty": wrong_by_difficulty,
            "confidence_analysis": confidence_analysis,
            "details": details,
            "weak_points": list(set(weak_points))[:5],
            "analysis": self._generate_analysis(score, wrong_by_difficulty)
        }
    
    def _generate_analysis(self, score: int, wrong_by_difficulty: Dict) -> str:
        """生成考试分析"""
        if score >= 80:
            return "基础扎实，继续保持！建议在难题部分加强训练。"
        elif score >= 60:
            return "基础尚可，但需要加强理解和应用。建议重点复习错题涉及的知识点。"
        else:
            return "需要系统复习基础知识点，建议重新学习相关章节内容。"

    async def generate_variation_questions(
        self,
        key_point: str,
        base_question: Dict[str, Any],
        uploaded_content: str,
        num_variations: int = 5
    ) -> List[Dict[str, Any]]:
        """
        基于知识点生成变式题
        
        变式策略：
        1. 同一概念，不同问法
        2. 同一机制，正反两面
        3. 相似疾病，鉴别对比
        4. 病例变式，不同表现
        5. 选项变式，干扰项调整
        """
        
        content = uploaded_content[:5000] if len(uploaded_content) > 5000 else uploaded_content
        
        base_q = base_question.get("question", "")
        base_type = base_question.get("type", "A1")
        base_diff = base_question.get("difficulty", "基础")
        base_exp = base_question.get("explanation", "")
        
        prompt = f"""【角色】你是资深西医综合考研命题专家，擅长设计高质量变式题。

【任务】基于以下知识点和原题，生成{num_variations}道变式题。

【原题信息】
知识点：{key_point}
题型：{base_type}
难度：{base_diff}
原题：{base_q}
解析：{base_exp}

【讲课内容参考】
{content}

【变式设计要求】
每道题必须是同一知识点的不同考察角度：

1. **概念变式**（第1题）：
   - 同一概念，换种问法
   - 或从定义→机制→应用的递进

2. **病例变式**（第2题）：
   - 类似临床表现，不同诊断
   - 或同一疾病，不同分期/表现

3. **机制变式**（第3题）：
   - 同一机制，正反两面考察
   - 或从生理→病理→治疗的逻辑

4. **鉴别变式**（第4题）：
   - 相似疾病/症状的鉴别
   - 易混淆考点的对比

5. **应用变式**（第5题）：
   - 临床应用，治疗方案选择
   - 或药物使用、并发症处理

【重要要求】
- 5道题必须围绕"{key_point}"这个核心知识点
- 题目不能重复，每道题考察不同角度
- 选项要有干扰性，似是而非
- 答案和解析必须准确
- **每道题必须有且仅有 A、B、C、D、E 五个选项，缺一不可**

【输出格式】
{{
    "variations": [
        {{
            "id": 1,
            "type": "A1/A2/A3/X",
            "difficulty": "基础/提高/难题",
            "variation_type": "概念变式/病例变式/机制变式/鉴别变式/应用变式",
            "question": "题目内容",
            "options": {{"A": "...", "B": "...", "C": "...", "D": "...", "E": "..."}},
            "correct_answer": "A/B/C/D/E",
            "explanation": "详细解析，说明为什么对、为什么错"
        }}
    ]
}}"""

        schema = {
            "variations": [
                {
                    "id": 1,
                    "type": "题型",
                    "difficulty": "难度",
                    "variation_type": "变式类型",
                    "question": "题目",
                    "options": {"A": "", "B": "", "C": "", "D": "", "E": ""},
                    "correct_answer": "答案",
                    "explanation": "解析"
                }
            ]
        }
        
        try:
            # use_heavy=True: Gemini优先（创造力强），失败自动走快速兜底链路
            result = await self.ai.generate_json(prompt, schema, max_tokens=4000, temperature=0.4, use_heavy=True, timeout=360)
            variations = result.get("variations", [])

            print(f"[Variation] AI返回了 {len(variations)} 道题")

            # 检查并修复题目完整性
            valid_variations = []
            for i, v in enumerate(variations):
                options = v.get("options", {})

                # 检查选项完整性，缺失的用原题选项补充
                has_missing_options = False
                for opt in ["A", "B", "C", "D", "E"]:
                    if not options.get(opt):
                        has_missing_options = True
                        # 用原题选项补充
                        base_opt = base_question.get("options", {}).get(opt, f"选项{opt}")
                        options[opt] = base_opt
                        print(f"[Variation] 第{i+1}题选项{opt}缺失，用原题选项补充")

                # 检查解析是否缺失
                if not v.get("explanation"):
                    v["explanation"] = base_exp or "暂无解析"
                    print(f"[Variation] 第{i+1}题解析缺失，用原题解析补充")

                # 检查题目是否缺失
                if not v.get("question"):
                    print(f"[Variation] 警告：第{i+1}题题目缺失，跳过")
                    continue

                # 确保所有必需字段都存在
                v["options"] = options
                v["type"] = v.get("type") or base_type
                v["difficulty"] = v.get("difficulty") or base_diff
                v["correct_answer"] = v.get("correct_answer") or base_question.get("correct_answer", "A")

                valid_variations.append(v)

            # 如果有效题目不足，用原题变式补充
            while len(valid_variations) < num_variations:
                missing_count = num_variations - len(valid_variations)
                print(f"[Variation] 有效题目不足，需要补充 {missing_count} 道题")
                for i in range(missing_count):
                    fallback_q = self._create_variation_from_base(
                        len(valid_variations) + 1,
                        key_point,
                        base_question
                    )
                    valid_variations.append(fallback_q)
                    if len(valid_variations) >= num_variations:
                        break

            return valid_variations[:num_variations]

        except Exception as e:
            print(f"[Variation] AI生成失败: {e}")
            import traceback
            traceback.print_exc()
            # 不再静默返回原题冒充变式，直接抛异常让上层处理
            raise RuntimeError(f"变式题AI生成失败: {e}") from e

    def _create_variation_from_base(self, id: int, key_point: str, base_question: Dict) -> Dict:
        """基于原题创建变式（保留原题选项）"""
        variation_types = ["概念变式", "病例变式", "机制变式", "鉴别变式", "应用变式"]
        vtype = variation_types[(id - 1) % len(variation_types)]

        return {
            "id": id,
            "type": base_question.get("type", "A1"),
            "difficulty": base_question.get("difficulty", "基础"),
            "variation_type": vtype,
            "question": f"【{vtype}】{base_question.get('question', '')}",
            "options": base_question.get("options", {}),  # 使用原题的真实选项
            "correct_answer": base_question.get("correct_answer", "A"),
            "explanation": base_question.get("explanation", "")
        }


_quiz_service = None

def get_quiz_service():
    global _quiz_service
    if _quiz_service is None:
        _quiz_service = QuizService()
    return _quiz_service
