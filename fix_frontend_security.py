"""
批量修复前端 fetch 错误处理和 XSS 漏洞
自动为所有 fetch 调用添加错误处理，并将 innerHTML 改为安全方式
"""

import re
import sys
from pathlib import Path

def fix_fetch_calls(content):
    """
    修复 fetch 调用，添加错误处理
    将 fetch(...).then(...) 改为 safeFetch(...)
    """
    # 模式1: fetch(...).then(r => r.json()).then(...)
    pattern1 = r'fetch\(([^)]+)\)\.then\([^=]+=>\s*\w+\.json\(\)\)\.then\(([^{]+)=>\s*\{'

    def replace1(match):
        url = match.group(1)
        var_name = match.group(2).strip()
        return f'safeFetch({url}, {{}}, "请求失败").then({var_name} => {{'

    content = re.sub(pattern1, replace1, content)

    # 模式2: await fetch(...) 后面跟 await r.json()
    pattern2 = r'(var|const|let)\s+(\w+)\s*=\s*await\s+fetch\(([^)]+)\);\s*(var|const|let)\s+(\w+)\s*=\s*await\s+\2\.json\(\);'

    def replace2(match):
        var_type = match.group(1)
        response_var = match.group(2)
        url = match.group(3)
        data_type = match.group(4)
        data_var = match.group(5)
        return f'{data_type} {data_var} = await safeFetch({url}, {{}}, "请求失败");'

    content = re.sub(pattern2, replace2, content)

    return content

def fix_innerHTML_xss(content):
    """
    修复 innerHTML XSS 漏洞
    将用户数据的 innerHTML 改为 textContent 或 escapeHtml
    """
    # 查找所有 innerHTML 赋值
    # 对于明显是用户数据的（包含 result., data., item. 等），使用 escapeHtml

    # 模式: element.innerHTML = result.xxx 或 data.xxx
    pattern = r'(\w+)\.innerHTML\s*=\s*(result|data|item|record|question|answer|user)\.(\w+)'

    def replace_innerHTML(match):
        element = match.group(1)
        obj = match.group(2)
        prop = match.group(3)
        # 如果是纯文本字段，使用 textContent
        text_fields = ['user_answer', 'question', 'question_text', 'key_point',
                      'explanation', 'feedback', 'analysis', 'diagnosis']
        if prop in text_fields:
            return f'{element}.textContent = {obj}.{prop}'
        else:
            # 其他字段使用 escapeHtml
            return f'{element}.innerHTML = escapeHtml({obj}.{prop})'

    content = re.sub(pattern, replace_innerHTML, content)

    # 模式: element.innerHTML = 'xxx' + variable + 'xxx'
    # 这种需要手动检查，暂时不自动修复

    return content

def process_file(filepath):
    """处理单个文件"""
    print(f"处理文件: {filepath}")

    with open(filepath, 'r', encoding='utf-8') as f:
        content = f.read()

    original_content = content

    # 修复 fetch 调用
    content = fix_fetch_calls(content)

    # 修复 innerHTML XSS
    content = fix_innerHTML_xss(content)

    if content != original_content:
        # 备份原文件
        backup_path = filepath.with_suffix('.html.bak')
        with open(backup_path, 'w', encoding='utf-8') as f:
            f.write(original_content)
        print(f"  已备份到: {backup_path}")

        # 写入修复后的内容
        with open(filepath, 'w', encoding='utf-8') as f:
            f.write(content)
        print(f"  ✅ 已修复")
    else:
        print(f"  ℹ️ 无需修复")

def main():
    templates_dir = Path(__file__).parent / 'templates'

    # 需要修复的文件列表
    files_to_fix = [
        'wrong_answers.html',
        'quiz_batch.html',
        'exam.html',
        'dashboard.html',
        'quiz.html',
        'quiz_detail.html',
        'quiz_fast.html',
        'quiz_practice.html',
    ]

    for filename in files_to_fix:
        filepath = templates_dir / filename
        if filepath.exists():
            try:
                process_file(filepath)
            except Exception as e:
                print(f"  ❌ 错误: {e}")
        else:
            print(f"跳过不存在的文件: {filepath}")

    print("\n✅ 批量修复完成！")
    print("⚠️ 请手动检查以下内容：")
    print("1. 复杂的字符串拼接 innerHTML")
    print("2. 需要保留 HTML 格式的内容")
    print("3. 测试所有功能是否正常")

if __name__ == '__main__':
    main()
