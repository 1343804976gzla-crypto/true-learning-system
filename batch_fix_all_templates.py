"""
全面修复所有模板文件的安全问题
"""

import re
from pathlib import Path

# 安全工具函数代码
SECURITY_FUNCTIONS = """
    // ========== 安全工具函数 ==========
    function escapeHtml(text) {
        if (!text) return '';
        const div = document.createElement('div');
        div.textContent = text;
        return div.innerHTML;
    }

    function handleFetchError(error, userMessage, showAlert = true) {
        console.error('API Error:', error);
        const message = userMessage || '操作失败，请稍后重试';
        if (showAlert) alert(message);
        return message;
    }

    async function safeFetch(url, options = {}, errorMessage = '请求失败') {
        try {
            const response = await fetch(url, options);
            if (!response.ok) {
                throw new Error(`HTTP ${response.status}: ${response.statusText}`);
            }
            return await response.json();
        } catch (error) {
            handleFetchError(error, errorMessage);
            throw error;
        }
    }
"""

def has_security_functions(content):
    """检查是否已有安全函数"""
    return 'function escapeHtml' in content and 'function safeFetch' in content

def add_security_functions(content):
    """在 <script> 标签后添加安全函数"""
    if has_security_functions(content):
        print("  ℹ️  已有安全函数，跳过添加")
        return content

    # 查找第一个 <script> 标签
    match = re.search(r'<script[^>]*>', content)
    if match:
        insert_pos = match.end()
        content = content[:insert_pos] + SECURITY_FUNCTIONS + content[insert_pos:]
        print("  ✅ 已添加安全函数")
    else:
        print("  ⚠️  未找到 <script> 标签")

    return content

def fix_fetch_calls(content):
    """修复 fetch 调用"""
    changes = 0

    # 模式1: var r = await fetch(...); var data = await r.json();
    pattern1 = r"(var|const|let)\s+(\w+)\s*=\s*await\s+fetch\(([^;]+)\);\s*(var|const|let)\s+(\w+)\s*=\s*await\s+\2\.json\(\);"

    def replace1(match):
        nonlocal changes
        changes += 1
        return f"{match.group(4)} {match.group(5)} = await safeFetch({match.group(3).strip()}, {{}}, '请求失败');"

    content = re.sub(pattern1, replace1, content)

    # 模式2: await fetch(...);
    pattern2 = r"await\s+fetch\(([^;]+)\);"

    def replace2(match):
        nonlocal changes
        url = match.group(1).strip()
        if 'safeFetch' not in url:
            changes += 1
            return f"await safeFetch({url}, {{}}, '操作失败');"
        return match.group(0)

    content = re.sub(pattern2, replace2, content)

    # 模式3: fetch(...).then(r => r.json()).then(...)
    pattern3 = r"fetch\(([^)]+)\)\.then\([^=]+=>\s*\w+\.json\(\)\)"

    def replace3(match):
        nonlocal changes
        url = match.group(1).strip()
        if 'safeFetch' not in url:
            changes += 1
            return f"safeFetch({url}, {{}}, '请求失败')"
        return match.group(0)

    content = re.sub(pattern3, replace3, content)

    if changes > 0:
        print(f"  ✅ 修复了 {changes} 个 fetch 调用")

    return content

def fix_innerHTML_xss(content):
    """修复 innerHTML XSS 漏洞"""
    changes = 0

    # 用户数据字段
    user_fields = [
        'user_answer', 'question', 'question_text', 'key_point',
        'explanation', 'feedback', 'analysis', 'diagnosis',
        'reflection', 'rationale_text', 'weak_points', 'hint_text'
    ]

    for field in user_fields:
        # 模式: element.innerHTML = obj.field
        pattern = rf"(\w+)\.innerHTML\s*=\s*(\w+)\.{field}(?!\s*\+)"

        def replace_innerHTML(match):
            nonlocal changes
            changes += 1
            element = match.group(1)
            obj = match.group(2)
            # 纯文本字段使用 textContent
            if field in ['user_answer', 'question', 'question_text', 'key_point', 'reflection']:
                return f"{element}.textContent = {obj}.{field}"
            else:
                return f"{element}.innerHTML = escapeHtml({obj}.{field})"

        content = re.sub(pattern, replace_innerHTML, content)

    # 修复字符串拼接中的用户数据
    # 模式: 'xxx' + variable + 'xxx'
    for field in user_fields:
        pattern = rf"'\s*\+\s*(\w+)\.{field}\s*\+\s*'"

        def replace_concat(match):
            nonlocal changes
            changes += 1
            obj = match.group(1)
            return f"' + escapeHtml({obj}.{field}) + '"

        content = re.sub(pattern, replace_concat, content)

    if changes > 0:
        print(f"  ✅ 修复了 {changes} 个 XSS 漏洞")

    return content

def process_file(filepath):
    """处理单个文件"""
    print(f"\n{'='*60}")
    print(f"处理: {filepath.name}")
    print(f"{'='*60}")

    with open(filepath, 'r', encoding='utf-8') as f:
        content = f.read()

    original_content = content

    # 1. 添加安全函数
    content = add_security_functions(content)

    # 2. 修复 fetch 调用
    content = fix_fetch_calls(content)

    # 3. 修复 XSS 漏洞
    content = fix_innerHTML_xss(content)

    if content != original_content:
        # 备份
        backup_path = filepath.with_suffix(filepath.suffix + '.backup')
        with open(backup_path, 'w', encoding='utf-8') as f:
            f.write(original_content)

        # 保存
        with open(filepath, 'w', encoding='utf-8') as f:
            f.write(content)

        print(f"  📝 已备份到: {backup_path.name}")
        print(f"  ✅ 修复完成")
    else:
        print(f"  ℹ️  无需修复")

def main():
    templates_dir = Path(__file__).parent / 'templates'

    # 需要修复的文件
    files_to_fix = [
        'exam.html',
        'quiz_detail.html',
        'quiz_fast.html',
        'quiz_practice.html',
        'quiz.html',
        'dashboard.html',
        'learning_tracking.html',
        'session_review.html',
    ]

    total_files = 0
    for filename in files_to_fix:
        filepath = templates_dir / filename
        if filepath.exists():
            try:
                process_file(filepath)
                total_files += 1
            except Exception as e:
                print(f"  ❌ 错误: {e}")
                import traceback
                traceback.print_exc()
        else:
            print(f"\n⏭️  跳过不存在的文件: {filename}")

    print(f"\n{'='*60}")
    print(f"✅ 批量修复完成！")
    print(f"📊 处理文件数: {total_files}")
    print(f"{'='*60}")
    print(f"\n⚠️  后续工作:")
    print(f"1. 测试所有页面功能")
    print(f"2. 检查控制台是否有错误")
    print(f"3. 测试用户输入和 AI 返回内容")
    print(f"4. 如有问题，可从 .backup 文件恢复")

if __name__ == '__main__':
    main()
