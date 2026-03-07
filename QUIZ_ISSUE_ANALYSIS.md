# 整卷测试题目和选项缺失问题分析报告

## 问题现象

用户反馈：整卷测试经常出现题目和选项缺失

## 诊断结果

### 测试1：短内容（298字，5题）
- ✅ 生成成功，所有题目完整
- ⚠️ 出现 429 错误，但重试后成功

### 测试2：长内容（3540字，10题）
- ❌ AI 只返回 1 道题（且是 schema 模板）
- ❌ 后续 9 道题都是占位符
- ⚠️ 出现 2 次 429 错误重试

## 根本原因分析

### 1. **Gemini API 429 限流问题**

```
[AIClient] Gemini 临时错误，第1次重试: 当前分组上游负载已饱和
[AIClient] Gemini 临时错误，第2次重试: 当前分组上游负载已饱和
```

**原因**：
- Gemini API 当前负载高，频繁返回 429
- 重试机制虽然最终成功，但 AI 返回质量下降

### 2. **AI 返回不完整 JSON**

当 AI 遇到 429 重试后，可能返回：
- Schema 模板本身（"题目"、"题型"等占位符）
- 只返回 1 道题而不是请求的 10 道
- 选项字段为空字符串

**示例**：
```json
{
  "id": 1,
  "type": "题型",        // ❌ 应该是 "A1"
  "difficulty": "难度",  // ❌ 应该是 "基础"
  "question": "题目",    // ❌ 应该是实际题目
  "options": {
    "A": "",            // ❌ 选项为空
    "B": "",
    ...
  }
}
```

### 3. **Prompt 过长导致 AI 理解困难**

当前 Prompt 约 **1500+ tokens**：
- 包含完整章节目录
- 详细的出题要求
- 复杂的 JSON schema

在 429 重试后，AI 可能：
- 截断响应
- 理解偏差
- 直接返回 schema 模板

### 4. **补救机制不够健壮**

当前代码虽然有补救：
```python
# 检查选项是否为空
if opt not in q["options"] or not q["options"][opt]:
    q["options"][opt] = f"（选项{opt}缺失）"
```

但问题是：
- **只补充了缺失的 key，没有检测空字符串**
- **没有检测 schema 模板本身**（"题目"、"题型"等）
- **没有重新生成，直接使用占位符**

## 修复方案

### 方案1：增强 AI 响应验证（推荐）

在 `quiz_service_v2.py` 中增加严格验证：

```python
def _validate_question(self, q: Dict, index: int) -> bool:
    """验证题目是否有效（不是 schema 模板）"""

    # 检查是否是 schema 模板
    template_keywords = ["题目", "题型", "难度", "选项A", "选项B", "答案", "解析", "考点"]

    if q.get("question") in template_keywords:
        return False

    if q.get("type") in ["题型", "type"]:
        return False

    # 检查选项是否为空或模板
    options = q.get("options", {})
    for opt in ["A", "B", "C", "D", "E"]:
        val = options.get(opt, "").strip()
        if not val or val in template_keywords or f"选项{opt}" == val:
            return False

    # 检查必填字段
    if not q.get("question") or not q.get("correct_answer"):
        return False

    return True
```

### 方案2：降低 Prompt 复杂度

**简化章节目录**：
```python
# 当前：返回所有章节（可能几百行）
catalog = self._get_chapter_catalog()

# 优化：只返回科目列表
catalog = "生理学、生物化学、病理学、内科学、外科学"
```

**分离 schema**：
```python
# 当前：在 prompt 中嵌入完整 schema
prompt = f"...{json.dumps(schema, indent=2)}..."

# 优化：只给示例，不给完整 schema
prompt = f"...输出格式示例：{{'questions': [{{'id': 1, 'question': '实际题目'...}}]}}"
```

### 方案3：增加重新生成机制

当检测到无效题目时，重新生成：

```python
# 检查有效题目数
valid_questions = [q for q in questions if self._validate_question(q, i)]

if len(valid_questions) < num_questions * 0.5:  # 少于一半有效
    print(f"[QuizService] ⚠️ 有效题目不足，重新生成")
    # 重新调用 AI（使用更简单的 prompt）
    result = await self._regenerate_with_simple_prompt(...)
```

### 方案4：使用 DeepSeek 作为 Fallback

当 Gemini 429 频繁时，自动切换到 DeepSeek：

```python
# 在 ai_client.py 中
if use_heavy and self._is_transient_error(e):
    print("[AIClient] Gemini 不稳定，切换到 DeepSeek")
    return await self._call_model_with_retries(
        client=self.ds_client,
        model=self.ds_model,
        ...
    )
```

### 方案5：分段生成阈值调整

当前阈值 9000 字可能太高，建议降低：

```python
# 当前
MAX_SEGMENT_LENGTH = 9000

# 优化：降低到 5000，减少单次请求压力
MAX_SEGMENT_LENGTH = 5000
```

## 推荐修复顺序

1. **立即修复**：增强 AI 响应验证（方案1）
2. **短期优化**：降低 Prompt 复杂度（方案2）
3. **中期改进**：增加重新生成机制（方案3）
4. **长期优化**：多模型负载均衡（方案4）

## 预期效果

- ✅ 检测并过滤无效题目（schema 模板）
- ✅ 自动重新生成不完整的题目
- ✅ 降低 AI 理解难度，提高成功率
- ✅ 减少 429 错误影响

## 测试验证

修复后需要测试：
1. 短内容（<1000字）+ 5题
2. 中等内容（3000-5000字）+ 10题
3. 长内容（>8000字）+ 20题
4. 模拟 429 错误场景

---

**报告时间**: 2026-03-04
**诊断工具**: diagnose_quiz_issues.py, diagnose_quiz_long_content.py
