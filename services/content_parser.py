"""
内容解析服务
识别讲课内容所属书/章/知识点
结合已有知识库进行智能匹配
"""

import re
from typing import Dict, Any, List, Optional, Tuple
from services.ai_client import get_ai_client
from sqlalchemy.orm import Session
from models import Chapter, ConceptMastery


class ContentParser:
    """讲课内容解析器 - 支持知识库匹配"""
    
    def __init__(self):
        self.ai = get_ai_client()
    
    def _get_existing_knowledge(self, db: Session, book_hint: str = None) -> Dict[str, Any]:
        """
        从数据库获取已有知识库结构
        用于辅助AI分类
        """
        result = {
            "books": [],
            "chapters": [],
            "concepts": []
        }
        
        # 获取所有科目
        books = db.query(Chapter.book).distinct().all()
        result["books"] = [b[0] for b in books]
        
        # 如果提供了科目提示，获取该科目的章节和知识点
        if book_hint:
            chapters = db.query(Chapter).filter(Chapter.book == book_hint).all()
        else:
            chapters = db.query(Chapter).limit(50).all()
        
        for ch in chapters:
            result["chapters"].append({
                "id": ch.id,
                "book": ch.book,
                "number": ch.chapter_number,
                "title": ch.chapter_title
            })
            
            # 获取该章节的知识点（获取更多以便匹配）
            concepts = db.query(ConceptMastery).filter(
                ConceptMastery.chapter_id == ch.id
            ).limit(100).all()
            
            for c in concepts:
                result["concepts"].append({
                    "id": c.concept_id,
                    "chapter_id": ch.id,
                    "name": c.name
                })
        
        return result
    
    def _find_matching_concepts(self, content: str, existing_concepts: List[Dict]) -> List[Dict]:
        """
        在已有知识点中查找可能匹配的概念
        使用智能匹配算法（支持模糊匹配）
        """
        matches = []
        content_lower = content.lower()
        
        # 清理内容：去除标点，分词
        import re
        content_clean = re.sub(r'[^\w\u4e00-\u9fff]', '', content_lower)
        
        for concept in existing_concepts:
            concept_name = concept["name"]
            concept_lower = concept_name.lower()
            
            # 匹配策略1：完整包含
            if concept_lower in content_lower:
                matches.append({**concept, "match_type": "exact"})
                continue
            
            # 匹配策略2：清理后匹配（去除标点和序号如①②③）
            concept_clean = re.sub(r'[^\w\u4e00-\u9fff]', '', concept_lower)
            concept_clean = re.sub(r'[①②③④⑤⑥⑦⑧⑨⑩]', '', concept_clean)
            
            if concept_clean in content_clean:
                matches.append({**concept, "match_type": "clean"})
                continue
            
            # 匹配策略3：双向模糊匹配（处理"胃内的消化"匹配"胃内消化"）
            # 去除"的"、"和"、"与"等连接词后进行匹配
            content_no_connectors = re.sub(r'[的之和与及在]', '', content_clean)
            concept_no_connectors = re.sub(r'[的之和与及在①②③④⑤⑥⑦⑧⑨⑩]', '', concept_clean)
            
            if concept_no_connectors in content_no_connectors or content_no_connectors in concept_no_connectors:
                if len(concept_no_connectors) >= 4:  # 至少4个字符才匹配
                    matches.append({**concept, "match_type": "fuzzy"})
                    continue
            
            # 匹配策略4：部分关键词匹配（2字以上关键词）
            keywords = [k for k in concept_clean.split() if len(k) >= 2]
            for kw in keywords:
                if len(kw) >= 4 and kw in content_clean:  # 4字以上关键词匹配
                    matches.append({**concept, "match_type": "keyword", "keyword": kw})
                    break
        
        # 去重并排序（精确匹配优先）
        seen = set()
        unique_matches = []
        for m in matches:
            if m["id"] not in seen:
                seen.add(m["id"])
                unique_matches.append(m)
        
        return unique_matches[:10]  # 最多返回10个匹配

    def _build_analysis_excerpt(self, content: str, max_chars: int = 12000) -> str:
        """
        构建长文本分析片段（头+中+尾）。
        避免只看前 5000 字导致章节信息在后半段时识别失败。
        """
        text = (content or "").strip()
        if len(text) <= max_chars:
            return text

        head_len = int(max_chars * 0.4)
        mid_len = int(max_chars * 0.2)
        tail_len = max_chars - head_len - mid_len

        head = text[:head_len]
        mid_start = max((len(text) // 2) - (mid_len // 2), 0)
        mid = text[mid_start:mid_start + mid_len]
        tail = text[-tail_len:]

        return (
            "【开头片段】\n"
            f"{head}\n\n"
            "【中间片段】\n"
            f"{mid}\n\n"
            "【结尾片段】\n"
            f"{tail}"
        )

    def _build_preliminary_excerpt(self, content: str, max_chars: int = 3200) -> str:
        """初步分类用短片段（头+尾），优先保证章节信息不被截断。"""
        text = (content or "").strip()
        if len(text) <= max_chars:
            return text

        head_len = int(max_chars * 0.6)
        tail_len = max_chars - head_len
        return text[:head_len] + "\n\n【后段线索】\n" + text[-tail_len:]

    def _normalize_book_name(self, value: str) -> str:
        v = (value or "").strip()
        if not v or v in {"未知", "无法识别", "unknown", "Unknown"}:
            return ""
        return v

    def _normalize_chapter_title(self, value: str) -> str:
        v = (value or "").strip()
        if not v or v in {"未知", "无法识别", "未识别章节"}:
            return ""
        return v

    def _chinese_numeral_to_int(self, text: str) -> Optional[int]:
        """中文数字（常见章号写法）转整数。"""
        mapping = {
            "零": 0, "一": 1, "二": 2, "两": 2, "三": 3, "四": 4, "五": 5,
            "六": 6, "七": 7, "八": 8, "九": 9
        }
        unit_map = {"十": 10, "百": 100}

        if not text:
            return None
        if text.isdigit():
            return int(text)

        total = 0
        current = 0
        has_unit = False
        for ch in text:
            if ch in mapping:
                current = mapping[ch]
            elif ch in unit_map:
                has_unit = True
                unit = unit_map[ch]
                if current == 0:
                    current = 1
                total += current * unit
                current = 0
            else:
                return None
        total += current
        if total == 0 and has_unit:
            return None
        return total if total > 0 else None

    def _normalize_chapter_number(self, raw: Any) -> str:
        """
        规范化章节号：
        - 第六章 -> 6
        - 第12章 -> 12
        - 6-3 保留
        """
        s = str(raw or "").strip()
        if not s:
            return "0"

        s = re.sub(r"^\s*第", "", s)
        s = re.sub(r"\s*章\s*$", "", s)
        s = re.sub(r"\s+", "", s)
        if not s:
            return "0"

        if re.fullmatch(r"[0-9]+(?:[-_.][0-9]+)*", s):
            return s

        num = self._chinese_numeral_to_int(s)
        if num is not None:
            return str(num)

        return s

    def _fallback_extract_book_chapter(self, content: str, preliminary: Dict[str, str]) -> Tuple[str, str, str]:
        """
        当 AI 识别不稳定时，使用规则兜底提取 book/chapter。
        """
        text = content or ""

        book = self._normalize_book_name(preliminary.get("book", ""))
        known_books = [
            "生理学", "内科学", "病理学", "生物化学", "外科学", "诊断学", "药理学",
            "病理生理学", "医学微生物学", "医学免疫学", "解剖学", "组织胚胎学", "系统解剖学"
        ]
        if not book:
            for b in known_books:
                if b in text:
                    book = b
                    break

        chapter_number = ""
        chapter_title = ""

        # 例：第六章 胃内消化 / 第6章胃内消化
        m = re.search(r"第\s*([一二三四五六七八九十百千0-9]+)\s*章\s*([^\n，。；;：:]{1,32})", text)
        if m:
            chapter_number = m.group(1).strip()
            chapter_title = m.group(2).strip()
        else:
            # 例：章节：胃内消化 / 本节：胃液分泌
            m2 = re.search(r"(?:章节|本章|本节|主题)\s*[：:]\s*([^\n，。；;]{2,32})", text)
            if m2:
                chapter_title = m2.group(1).strip()

        if chapter_number and not chapter_title:
            chapter_title = f"第{chapter_number}章"

        return book, chapter_number, chapter_title
    
    async def parse_content(self, content: str) -> Dict[str, Any]:
        """
        解析讲课内容（向后兼容，不依赖知识库）
        
        Args:
            content: 讲课内容文本
        
        Returns:
            解析结果
        """
        return await self.parse_content_with_knowledge(content, db=None)
    
    async def parse_content_with_knowledge(
        self, 
        content: str, 
        db: Session = None
    ) -> Dict[str, Any]:
        """
        解析讲课内容，结合已有知识库进行智能分类
        
        Args:
            content: 讲课内容文本
            db: 数据库会话，用于查询已有知识
        
        Returns:
            解析结果，优先匹配已有知识点ID
        """
        if not content or not content.strip():
            return {
                "book": "未知",
                "edition": "贺银成2027",
                "chapter_number": "0",
                "chapter_title": "未识别章节",
                "chapter_id": "unknown_ch0",
                "concepts": [],
                "summary": "内容为空，无法解析"
            }

        analysis_content = self._build_analysis_excerpt(content, max_chars=12000)
        preliminary_content = self._build_preliminary_excerpt(content, max_chars=3200)
        
        # 第一步：初步识别科目和章节
        preliminary = await self._preliminary_classification(preliminary_content)
        book_hint = preliminary.get("book")
        
        # 第二步：如果有数据库连接，获取相关知识
        existing_knowledge = None
        matched_concepts = []
        if db:
            existing_knowledge = self._get_existing_knowledge(db, book_hint)
            matched_concepts = self._find_matching_concepts(
                analysis_content,
                existing_knowledge.get("concepts", [])
            )
        
        # 第三步：结合已有知识进行精确分类
        result = await self._classify_with_knowledge(
            analysis_content,
            preliminary,
            existing_knowledge,
            matched_concepts
        )

        # 第四步：book/chapter 兜底纠偏
        fallback_book, fallback_num, fallback_title = self._fallback_extract_book_chapter(content, preliminary)

        if not self._normalize_book_name(result.get("book", "")) and fallback_book:
            result["book"] = fallback_book

        if not self._normalize_chapter_title(result.get("chapter_title", "")) and fallback_title:
            result["chapter_title"] = fallback_title

        if (not str(result.get("chapter_number", "")).strip() or str(result.get("chapter_number")) in {"0", "未知", "无法识别"}) and fallback_num:
            result["chapter_number"] = self._normalize_chapter_number(fallback_num)

        # 如果兜底修正了关键信息，重建 chapter_id，避免 unknown_ch0 污染后续出题
        if result.get("book") and result.get("chapter_number"):
            book_id = self._generate_book_id(result.get("book", "未知"))
            chapter_number = self._normalize_chapter_number(result.get("chapter_number", "0")).replace("-", "_")
            result["chapter_id"] = f"{book_id}_ch{chapter_number}"
        
        return result
    
    async def _preliminary_classification(self, content: str) -> Dict[str, str]:
        """初步分类，识别科目和大致章节"""
        prompt = f"""快速识别医学讲课内容的科目和章节线索：

内容片段：
{content}

只返回JSON。"""
        schema = {"book": "科目如病理学", "possible_chapters": "可能的章节关键词"}
        
        try:
            result = await self.ai.generate_json(prompt, schema, max_tokens=300, temperature=0.1, use_heavy=False, timeout=45)
            return {
                "book": str(result.get("book") or "").strip(),
                "possible_chapters": str(result.get("possible_chapters") or "").strip()
            }
        except Exception as e:
            print(f"[ContentParser] 初步分类失败: {e}")
            return {"book": "未知", "possible_chapters": ""}
    
    async def _classify_with_knowledge(
        self,
        content: str,
        preliminary: Dict[str, str],
        existing_knowledge: Optional[Dict],
        matched_concepts: List[Dict]
    ) -> Dict[str, Any]:
        """结合已有知识库进行分类"""
        
        content_for_analysis = content
        content_length = len(content_for_analysis)
        print(f"[ContentParser] 片段长度: {content_length} 字符")
        
        prompt = f"""【角色】你是医学考研辅导专家，分析讲课内容提取关键信息。

【已有知识库】
科目：{', '.join(existing_knowledge['books'][:8]) if existing_knowledge else '生理学,病理学,内科学,外科学,生物化学'}

【讲课内容片段（含头中尾）】
{content_for_analysis}

【已匹配的候选知识点】
{', '.join([m.get('name', '') for m in matched_concepts[:10]]) if matched_concepts else '无'}

【输出JSON】
{{
  "book": "识别到的科目",
  "chapter_number": "章节号",
  "chapter_title": "章节标题",
  "concepts": [
    {{"name": "知识点1", "importance": "main"}},
    {{"name": "知识点2", "importance": "secondary"}}
  ],
  "summary": "100字摘要"
}}"""
        schema = {
            "book": "识别到的科目",
            "chapter_number": "章节号",
            "chapter_title": "章节标题",
            "concepts": [{"name": "知识点1", "importance": "main"}],
            "summary": "100字摘要"
        }
        
        try:
            result = await self.ai.generate_json(prompt, schema, max_tokens=2400, temperature=0.2, use_heavy=False, timeout=60)
            
            # 验证返回结果
            normalized_book = self._normalize_book_name(result.get("book", ""))
            if not normalized_book:
                raise ValueError("AI未能识别科目")
            result["book"] = normalized_book
            
            # 标准化处理
            book_id = self._generate_book_id(result.get("book", "未知"))
            chapter_number = self._normalize_chapter_number(result.get("chapter_number", "0")).replace("-", "_")
            chapter_id = f"{book_id}_ch{chapter_number}"
            
            # 确保知识点ID格式正确
            for i, concept in enumerate(result.get("concepts", [])):
                concept_id = concept.get("id", "")
                if not concept_id:
                    concept_name = concept.get("name", f"concept_{i}")
                    concept_id = f"{chapter_id}_{i}_{concept_name[:20]}"
                if chapter_id not in concept_id:
                    concept_id = f"{chapter_id}_{concept_id}"
                concept["id"] = concept_id.replace(".", "_").replace("-", "_")
                concept["evidence"] = concept.get("evidence", "从内容中提取")
            
            result["chapter_number"] = self._normalize_chapter_number(result.get("chapter_number", "0"))
            result["chapter_id"] = chapter_id
            result["edition"] = result.get("edition", "贺银成2027")
            print(f"[ContentParser] 识别成功: {result['book']} - {result['chapter_title']}")
            return result
            
        except Exception as e:
            print(f"[ContentParser] 分类错误: {e}")
            import traceback
            traceback.print_exc()
            # 返回默认结构，但保留preliminary信息
            return {
                "book": preliminary.get("book", "未知"),
                "edition": "贺银成2027",
                "chapter_number": "0",
                "chapter_title": "未识别章节",
                "chapter_id": "unknown_ch0",
                "concepts": [],
                "summary": f"内容解析失败: {str(e)}",
                "is_new_chapter": "true",
                "matched_existing": "false",
                "error": str(e)
            }
    
    def _generate_book_id(self, book_name: str) -> str:
        """生成书的唯一标识"""
        book_name = book_name.strip()
        
        mapping = {
            "生理学": "physiology",
            "内科学": "internal_medicine",
            "病理学": "pathology",
            "生物化学": "biochemistry",
            "外科学": "surgery",
            "诊断学": "diagnostics",
            "药理学": "pharmacology",
            "病理生理学": "pathophysiology",
            "医学微生物学": "microbiology",
            "医学免疫学": "immunology",
            "解剖学": "anatomy",
            "组织胚胎学": "histology",
            "系统解剖学": "systematic_anatomy"
        }
        
        # 尝试匹配
        for chinese, english in mapping.items():
            if chinese in book_name:
                return english
        
        # 默认使用拼音简化（只保留字母数字下划线）
        safe_name = "".join(c if c.isalnum() else "_" for c in book_name.lower())
        return safe_name[:20]


# 单例实例
_parser: ContentParser = None


def get_content_parser() -> ContentParser:
    """获取内容解析器"""
    global _parser
    if _parser is None:
        _parser = ContentParser()
    return _parser
