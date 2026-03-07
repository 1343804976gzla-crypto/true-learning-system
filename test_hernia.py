"""
测试用户上传的疝内容识别
"""

import sys
sys.path.insert(0, r'C:\Users\35456\true-learning-system')

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from models import Chapter, ConceptMastery

DB_PATH = r"C:\Users\35456\true-learning-system\data\learning.db"
engine = create_engine(f"sqlite:///{DB_PATH}")
SessionLocal = sessionmaker(bind=engine)

# 用户上传的内容
test_content = """下面我们来学习第15章浮爱善。首先，了解一些善的基本概念好了。什么叫善？善其实人不仅仅只是服务外善，那么他的概念是很大的，他的体内的脏器或者组织离开正常的部位听经过。先天性或者后天形成的薄弱点缺损或者空隙进到另外一个部位都叫疝，也就是正常的人体组织器官。这本来应该在这个地方的，他不他跑到另外一个地方去了，是通过薄弱的点或者缺损间隙，到了另外一个圈都叫散。所以只不过在腹外疝，在临床上最多见，所以大家最常见的一提起疝就是腹外疝，"""

print("="*70)
print("📝 测试内容（前200字）:")
print("="*70)
print(test_content[:200] + "...")

print("\n" + "="*70)
print("🔍 检查数据库中的匹配章节")
print("="*70)

db = SessionLocal()

# 查找外科学15章
ch15 = db.query(Chapter).filter(Chapter.id == 'surgery_ch15').first()
if ch15:
    print(f"\n✅ 找到章节: {ch15.id}")
    print(f"   书名: {ch15.book}")
    print(f"   章节: {ch15.chapter_title}")
    
    concepts = db.query(ConceptMastery).filter(ConceptMastery.chapter_id == ch15.id).all()
    print(f"\n   知识点列表:")
    for c in concepts:
        print(f"      - {c.name}")
        print(f"        ID: {c.concept_id}")

# 测试匹配
print("\n" + "="*70)
print("🎯 测试内容匹配")
print("="*70)

from services.content_parser import get_content_parser

parser = get_content_parser()

# 获取外科学知识
knowledge = parser._get_existing_knowledge(db, book_hint="外科学")
print(f"\n获取到 {len(knowledge['concepts'])} 个外科学知识点")

# 测试匹配
matches = parser._find_matching_concepts(test_content, knowledge['concepts'])
print(f"\n匹配到 {len(matches)} 个知识点:")
for m in matches[:5]:
    print(f"   - {m['name']} ({m.get('match_type', 'unknown')})")

db.close()

print("\n" + "="*70)
print("💡 问题分析")
print("="*70)
print("""
1. 数据库中的章节ID: surgery_ch15
2. 前端旧代码生成的ID: surgery.ch15 (带点号，错误！)
3. 前端已修复，现在使用服务器返回的 chapter_id

如果你已经重启服务器，刷新页面上传应该能正常工作。
""")
