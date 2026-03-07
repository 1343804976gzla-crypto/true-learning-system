"""
批量修复 wrong_answers.html 的安全问题
1. 将所有 fetch 调用改为 safeFetch
2. 将用户数据的 innerHTML 改为 textContent 或 escapeHtml
"""

import re
from pathlib import Path

def fix_file(filepath):
    """修复单个文件"""
    print(f"\n{'='*60}")
    print(f"修复文件: {filepath.name}")
    print(f"{'='*60}")

    with open(filepath, 'r', encoding='utf-8') as f:
        content = f.read()

    original_content = content
    changes = []

    # 1. 修复 await fetch 模式
    # 模式: var r = await fetch(...); var data = await r.json();
    pattern1 = r"(var|const|let)\s+(\w+)\s*=\s*await\s+fetch\(([^;]+)\);\s*(var|const|let)\s+(\w+)\s*=\s*await\s+\2\.json\(\);"

    def replace_fetch(match):
        changes.append(f"修复 fetch: {match.group(3)[:50]}...")
        var_type = match.group(4)
        data_var = match.group(5)
        url = match.group(3).strip()
        return f"{var_type} {data_var} = await safeFetch({url}, {{}}, '请求失败');"

    content = re.sub(pattern1, replace_fetch, content)

    # 2. 修复 await fetch 单行模式
    # 模式: await fetch(...);
    pattern2 = r"await\s+fetch\(([^;]+)\);"

    def replace_fetch_single(match):
        url = match.group(1).strip()
        # 检查是否已经在 safeFetch 中
        if 'safeFetch' not in url:
            changes.append(f"修复单行 fetch: {url[:50]}...")
            return f"await safeFetch({url}, {{}}, '操作失败');"
        return match.group(0)

    content = re.sub(pattern2, replace_fetch_single, content)

    # 3. 修复 innerHTML = result.xxx 模式（用户数据）
    user_data_fields = [
        'user_answer', 'question', 'question_text', 'key_point',
        'explanation', 'feedback', 'analysis', 'diagnosis',
        'reflection', 'rationale_text', 'weak_points'
    ]

    for field in user_data_fields:
        # 模式: element.innerHTML = obj.field
        pattern = rf"(\w+)\.innerHTML\s*=\s*(\w+)\.{field}"

        def replace_innerHTML(match):
            element = match.group(1)
            obj = match.group(2)
            changes.append(f"修复 XSS: {element}.innerHTML = {obj}.{field}")
            # 纯文本字段使用 textContent
            if field in ['user_answer', 'question', 'question_text', 'key_point', 'reflection']:
                return f"{element}.textContent = {obj}.{field}"
            else:
                # 需要保留格式的使用 escapeHtml
                return f"{element}.innerHTML = escapeHtml({obj}.{field})"

        content = re.sub(pattern, replace_innerHTML, content)

    # 4. 修复字符串拼接中的用户数据
    # 模式: 'xxx' + variable + 'xxx' 或 `xxx${variable}xxx`
    # 这个比较复杂，需要手动检查

    # 统计修改
    if content != original_content:
        # 备份
        backup_path = filepath.with_suffix('.html.backup')
        with open(backup_path, 'w', encoding='utf-8') as f:
            f.write(original_content)

        # 保存修复后的内容
        with open(filepath, 'w', encoding='utf-8') as f:
            f.write(content)

        print(f"\n✅ 修复完成！")
        print(f"📝 备份文件: {backup_path.name}")
        print(f"🔧 修改数量: {len(changes)}")
        if changes:
            print(f"\n修改详情:")
            for i, change in enumerate(changes[:10], 1):
                print(f"  {i}. {change}")
            if len(changes) > 10:
                print(f"  ... 还有 {len(changes) - 10} 处修改")
    else:
        print(f"\nℹ️  无需修复")

    return len(changes)

def main():
    # 修复 wrong_answers.html
    filepath = Path(__file__).parent / 'templates' / 'wrong_answers.html'

    if not filepath.exists():
        print(f"❌ 文件不存在: {filepath}")
        return

    total_changes = fix_file(filepath)

    print(f"\n{'='*60}")
    print(f"✅ 批量修复完成！")
    print(f"📊 总修改数: {total_changes}")
    print(f"{'='*60}")
    print(f"\n⚠️  请注意:")
    print(f"1. 已自动备份原文件为 .html.backup")
    print(f"2. 请手动检查复杂的字符串拼接")
    print(f"3. 测试所有功能是否正常")
    print(f"4. 特别测试用户输入和 AI 返回内容")

if __name__ == '__main__':
    main()
