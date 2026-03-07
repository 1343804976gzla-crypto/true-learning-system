"""
修复知识图谱Mermaid渲染
处理特殊字符，避免Syntax Error
"""

import re
from typing import List, Dict

def sanitize_mermaid_id(original_id: str) -> str:
    """
    将原始ID转换为Mermaid安全ID
    只保留字母数字下划线
    """
    # 只保留字母数字下划线
    safe_id = re.sub(r'[^\w]', '_', original_id)
    # 确保不以数字开头（Mermaid限制）
    if safe_id and safe_id[0].isdigit():
        safe_id = 'n' + safe_id
    return safe_id or 'node'

def sanitize_mermaid_text(text: str) -> str:
    """
    清理Mermaid节点文本
    转义特殊字符
    """
    # 替换特殊字符
    replacements = [
        ('"', '"'),      # 引号
        ('\\', '\\'),     # 反斜杠
        ('<', '<'),      # 小于号
        ('>', '>'),      # 大于号
        ('&', '&'),      # 与号
        ('#', '#'),       # 井号
        ('|', r'\|'),       # 竖线（Mermaid分隔符）
        ('[', r'\['),       # 左方括号
        (']', r'\]'),       # 右方括号
        ('(', r'\('),       # 左括号
        (')', r'\)'),       # 右括号
        ('{', r'\{'),       # 左花括号
        ('}', r'\}'),       # 右花括号
    ]
    
    for old, new in replacements:
        text = text.replace(old, new)
    
    # 限制长度
    if len(text) > 50:
        text = text[:47] + '...'
    
    return text

def generate_safe_mermaid_graph(nodes: List[Dict], links: List[Dict]) -> str:
    """
    生成安全的Mermaid图形定义
    """
    # ID映射表
    id_mapping = {}
    
    graph_def = 'graph TD\n'
    
    # 添加节点定义
    for i, node in enumerate(nodes):
        original_id = node['id']
        safe_id = f'n{i}'  # 使用简单编号
        id_mapping[original_id] = safe_id
        
        # 清理文本
        safe_name = sanitize_mermaid_text(node['name'])
        
        # 确定颜色
        mastery = node.get('mastery', 0)
        if mastery >= 0.8:
            color = '#4ade80'  # 绿色
        elif mastery >= 0.5:
            color = '#facc15'  # 黄色
        elif mastery > 0:
            color = '#f87171'  # 红色
        else:
            color = '#d1d5db'  # 灰色
        
        # 节点定义 - 使用简单文本
        graph_def += f'    {safe_id}["{safe_name}"]\n'
        graph_def += f'    style {safe_id} fill:{color},stroke:#374151,stroke-width:2px\n'
    
    # 添加连接
    for link in links:
        source = id_mapping.get(link['source'])
        target = id_mapping.get(link['target'])
        
        if source and target:
            link_type = link.get('type', 'default')
            
            # 连接线样式
            if link_type == 'contains':
                graph_def += f'    {source} --> {target}\n'
            elif link_type == 'prerequisite':
                graph_def += f'    {source} -->|前置| {target}\n'
            elif link_type == 'leads_to':
                graph_def += f'    {source} ==> {target}\n'
            else:
                graph_def += f'    {source} --- {target}\n'
    
    return graph_def

# 测试
if __name__ == '__main__':
    test_nodes = [
        {'id': 'physiology_ch06', 'name': '消化生理', 'mastery': 0.7},
        {'id': 'physiology_ch06_03_胃内消化①', 'name': '胃内消化① (胃液成分)', 'mastery': 0.8},
        {'id': 'physiology_ch06_04_胃内消化②', 'name': '胃内消化② (胃酸作用)', 'mastery': 0.6},
    ]
    
    test_links = [
        {'source': 'physiology_ch06', 'target': 'physiology_ch06_03_胃内消化①', 'type': 'contains'},
        {'source': 'physiology_ch06', 'target': 'physiology_ch06_04_胃内消化②', 'type': 'contains'},
    ]
    
    result = generate_safe_mermaid_graph(test_nodes, test_links)
    print(result)
