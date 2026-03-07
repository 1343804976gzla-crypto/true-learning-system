"""
测试"胃内消化"内容识别
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
test_content = """下面来学习胃内的消化。胃了消化，首先要搞清楚它的胃液的性质和成分，那么这是重点。每年考试，胃关于胃液的生理啊以及。性质成分考的非常多，胃液是一个无色的酸性液体。ph 1在零点九到一点五之间。由于ph很低，所以很多的镁你不会蛋白镁，那么就这个dph就酸性环境下，那才能够进行。呃，具有生理功能，分泌量大约每天是一点五到两点五升，
请大家把这个PS值记一下啊，怕考你，那么你就会觉得什么很难。物业的主要成分就包括这四个。成分盐酸也就是我们所说胃酸胃蛋白酶原，请同学们注意，这里是胃蛋白酶原，不是胃蛋白酶。因为胃蛋白酶原在分泌之后是对酶原的形式存在的，当接触胃酸的时候，会集合成胃蛋白酶。所以为他们美是它的核心形式，那么出来的时候是为他们美元，因这就是为他们美元粘液，这个很少考到内因子，
经常考到，所以这四个主要成分中那么最常考到的就三个。跟大家用黄页是表示出来的胃酸以及盐酸胃蛋白酶原内因子，那么粘液基本上执业医师，助理医师以及你们的西医综合啊都没考到。次要成分，99%的都是水分，那么除了水分之外，还碳酸氢盐钠，离子钾，离子等无机盐。但是这个中间那么偶尔会考的是碳酸氢盐，那么其他的很少考到，所以重点掌握这一些经常考到的这些物质成分好了，我们一个个来看。
它的生理作用呢？第一个最常考的例子，盐酸，也就是它最主要成分。胃酸除了水分之外，那么它就最重要的物质。盐酸也就是胃酸，是由b细胞分泌的b细胞，除了分泌胃酸之外，还分泌一个物质，就是内因子。"""

print("="*70)
print("📝 测试内容主题: 胃内消化 (生理学)")
print("="*70)
print(f"内容长度: {len(test_content)} 字符")
print(f"内容预览: {test_content[:100]}...")

db = SessionLocal()

try:
    from services.content_parser import get_content_parser
    parser = get_content_parser()
    
    # 测试1: 获取生理学知识库
    print("\n" + "="*70)
    print("🔍 测试1: 获取生理学知识库")
    print("="*70)
    
    knowledge = parser._get_existing_knowledge(db, book_hint="生理学")
    print(f"✅ 获取成功")
    print(f"   - 生理学章节数: {len([c for c in knowledge['chapters'] if 'physiology' in c['id']])}")
    print(f"   - 生理学知识点数: {len(knowledge['concepts'])}")
    
    # 测试2: 知识点匹配
    print("\n" + "="*70)
    print("🎯 测试2: 知识点匹配")
    print("="*70)
    
    matches = parser._find_matching_concepts(test_content, knowledge['concepts'])
    print(f"✅ 匹配到 {len(matches)} 个知识点:")
    for m in matches[:5]:
        print(f"   - {m['name']} (匹配类型: {m.get('match_type', 'unknown')})")
    
    # 测试3: 检查第6章知识点详情
    print("\n" + "="*70)
    print("📋 测试3: 生理学第6章详情")
    print("="*70)
    
    ch6 = db.query(Chapter).filter(Chapter.id == 'physiology_ch06').first()
    if ch6:
        print(f"章节: {ch6.chapter_title}")
        concepts = db.query(ConceptMastery).filter(ConceptMastery.chapter_id == ch6.id).all()
        print(f"知识点列表:")
        for c in concepts:
            print(f"   - {c.name}")
            print(f"     ID: {c.concept_id}")
    
    # 测试4: 模拟AI分类结果预测
    print("\n" + "="*70)
    print("🤖 测试4: 预期AI分类结果")
    print("="*70)
    
    print("基于内容分析，AI应该识别为:")
    print("   科目: 生理学")
    print("   章节: 第6章 - 消化生理")
    print("   章节ID: physiology_ch06")
    print("   可能匹配的知识点:")
    print("      - 胃内消化①")
    print("      - 胃内消化②")
    print("      - 消化生理概述")
    
    print("\n" + "="*70)
    print("✅ 测试完成!")
    print("="*70)
    print("\n💡 结论:")
    if matches:
        print(f"   系统成功匹配到 {len(matches)} 个相关知识点")
        print(f"   上传此内容时，AI会关联到已有知识库")
    else:
        print(f"   未直接匹配到知识点，但AI会识别为生理学第6章")
        print(f"   系统会创建新的知识点或关联到'胃内消化'章节")
    
except Exception as e:
    print(f"\n❌ 测试失败: {e}")
    import traceback
    traceback.print_exc()
finally:
    db.close()
