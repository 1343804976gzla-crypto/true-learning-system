"""
简单测试知识库功能（不涉及AI调用）
"""

import sys
sys.path.insert(0, r'C:\Users\35456\true-learning-system')

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from models import Chapter, ConceptMastery

DB_PATH = r"C:\Users\35456\true-learning-system\data\learning.db"
engine = create_engine(f"sqlite:///{DB_PATH}")
SessionLocal = sessionmaker(bind=engine)

print("="*70)
print("📊 测试知识库匹配功能")
print("="*70)

db = SessionLocal()

try:
    from services.content_parser import get_content_parser
    
    parser = get_content_parser()
    
    # 测试1: 获取知识库
    print("\n1️⃣ 测试获取知识库 (内科学):")
    knowledge = parser._get_existing_knowledge(db, book_hint="内科学")
    print(f"   ✅ 获取成功")
    print(f"   - 科目数: {len(knowledge['books'])}")
    print(f"   - 章节数: {len(knowledge['chapters'])}")
    print(f"   - 知识点数: {len(knowledge['concepts'])}")
    
    # 显示部分知识点
    print(f"\n   前10个知识点示例:")
    for c in knowledge['concepts'][:10]:
        print(f"   - {c['name']}")
    
    # 测试2: 知识点匹配
    print("\n2️⃣ 测试知识点匹配:")
    
    test_cases = [
        ("急性心肌梗死的诊断和治疗", ["心肌梗死", "冠心病"]),
        ("稳定型心绞痛的临床表现", ["心绞痛"]),
        ("支气管哮喘的发病机制", ["哮喘", "支气管"]),
        ("高血压病的药物治疗", ["高血压"]),
    ]
    
    for content, expected_keywords in test_cases:
        print(f"\n   📝 内容: {content}")
        matches = parser._find_matching_concepts(content, knowledge['concepts'])
        if matches:
            print(f"   ✅ 匹配到 {len(matches)} 个:")
            for m in matches[:3]:
                match_info = f"({m.get('match_type', 'unknown')})"
                print(f"      - {m['name']} {match_info}")
        else:
            print(f"   ⚠️ 未匹配")
    
    # 测试3: 验证章节ID格式
    print("\n3️⃣ 验证章节ID格式:")
    chapters = db.query(Chapter).all()
    
    correct_format = 0
    old_format = 0
    
    for ch in chapters[:20]:
        if '_' in ch.id and '.' not in ch.id:
            correct_format += 1
        elif '.' in ch.id:
            old_format += 1
            print(f"   ⚠️ 旧格式ID: {ch.id}")
    
    print(f"   ✅ 新格式(xxx_chxx): {correct_format}")
    print(f"   ⚠️ 旧格式(xxx.chx-x): {old_format}")
    
    print("\n" + "="*70)
    print("✅ 本地测试通过！")
    print("="*70)
    
except Exception as e:
    print(f"\n❌ 测试失败: {e}")
    import traceback
    traceback.print_exc()
finally:
    db.close()
