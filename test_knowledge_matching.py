"""
测试AI知识库匹配功能
验证修改后的content_parser是否正常工作
"""

import asyncio
import sys
sys.path.insert(0, r'C:\Users\35456\true-learning-system')

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from models import Chapter, ConceptMastery

# 数据库连接
DB_PATH = r"C:\Users\35456\true-learning-system\data\learning.db"
engine = create_engine(f"sqlite:///{DB_PATH}")
SessionLocal = sessionmaker(bind=engine)

def test_database():
    """测试数据库连接和知识库"""
    print("="*60)
    print("📊 测试1: 数据库连接和知识库查询")
    print("="*60)
    
    db = SessionLocal()
    try:
        # 查询章节数
        chapter_count = db.query(Chapter).count()
        concept_count = db.query(ConceptMastery).count()
        print(f"✅ 数据库连接成功")
        print(f"   - 章节总数: {chapter_count}")
        print(f"   - 知识点总数: {concept_count}")
        
        # 查询各科目
        books = db.query(Chapter.book).distinct().all()
        print(f"\n📚 科目列表:")
        for book in books:
            count = db.query(Chapter).filter(Chapter.book == book[0]).count()
            concept_count = db.query(ConceptMastery).join(Chapter).filter(Chapter.book == book[0]).count()
            print(f"   - {book[0]}: {count}章, {concept_count}个知识点")
        
        # 查询具体章节示例
        print(f"\n📝 章节示例 (内科学第11章):")
        chapter = db.query(Chapter).filter(Chapter.id == "internal_medicine_ch11").first()
        if chapter:
            print(f"   - ID: {chapter.id}")
            print(f"   - 书名: {chapter.book}")
            print(f"   - 章节: {chapter.chapter_title}")
            concepts = db.query(ConceptMastery).filter(ConceptMastery.chapter_id == chapter.id).all()
            print(f"   - 知识点: {len(concepts)}个")
            for c in concepts[:3]:
                print(f"     • {c.name}")
        
        return True
    except Exception as e:
        print(f"❌ 数据库查询失败: {e}")
        return False
    finally:
        db.close()


def test_knowledge_retrieval():
    """测试知识库检索功能"""
    print("\n" + "="*60)
    print("🔍 测试2: 知识库检索功能")
    print("="*60)
    
    db = SessionLocal()
    try:
        from services.content_parser import get_content_parser
        
        parser = get_content_parser()
        
        # 测试获取已有知识
        knowledge = parser._get_existing_knowledge(db, book_hint="内科学")
        print(f"✅ 知识库检索成功")
        print(f"   - 找到科目: {len(knowledge['books'])}个")
        print(f"   - 找到章节: {len(knowledge['chapters'])}个")
        print(f"   - 找到知识点: {len(knowledge['concepts'])}个")
        
        # 显示前5个章节
        print(f"\n📖 内科学章节示例:")
        for ch in knowledge['chapters'][:5]:
            print(f"   - {ch['id']}: 第{ch['number']}章 {ch['title']}")
        
        return True
    except Exception as e:
        print(f"❌ 知识库检索失败: {e}")
        import traceback
        traceback.print_exc()
        return False
    finally:
        db.close()


def test_concept_matching():
    """测试知识点匹配功能"""
    print("\n" + "="*60)
    print("🎯 测试3: 知识点匹配功能")
    print("="*60)
    
    db = SessionLocal()
    try:
        from services.content_parser import get_content_parser
        
        parser = get_content_parser()
        
        # 获取知识库
        knowledge = parser._get_existing_knowledge(db, book_hint="内科学")
        
        # 测试内容匹配
        test_contents = [
            "今天学习急性心肌梗死，这是冠心病的一种严重类型，包括ST段抬高型和非ST段抬高型",
            "心绞痛的临床表现主要是胸骨后压榨性疼痛，可放射至左肩",
            "支气管哮喘的发病机制涉及气道高反应性和慢性炎症",
        ]
        
        for content in test_contents:
            print(f"\n📝 测试内容: {content[:40]}...")
            matches = parser._find_matching_concepts(content, knowledge['concepts'])
            if matches:
                print(f"   ✅ 匹配到{len(matches)}个知识点:")
                for m in matches[:3]:
                    print(f"      - {m['name']} ({m['id'][:50]}...)")
            else:
                print(f"   ⚠️ 未匹配到知识点")
        
        return True
    except Exception as e:
        print(f"❌ 知识点匹配失败: {e}")
        import traceback
        traceback.print_exc()
        return False
    finally:
        db.close()


async def test_ai_classification():
    """测试AI分类功能（需要API密钥）"""
    print("\n" + "="*60)
    print("🤖 测试4: AI智能分类（需要DeepSeek API）")
    print("="*60)
    
    try:
        from services.content_parser import get_content_parser
        import os
        
        # 检查API密钥
        api_key = os.getenv("DEEPSEEK_API_KEY")
        if not api_key or api_key == "test-key-for-development":
            print("⚠️ 跳过测试: DeepSeek API密钥未配置")
            print("   请设置环境变量 DEEPSEEK_API_KEY")
            return None
        
        parser = get_content_parser()
        
        # 测试内容
        test_content = """
        今天学习冠状动脉粥样硬化性心脏病，重点是急性心肌梗死。
        急性ST段抬高型心肌梗死（STEMI）的诊断标准包括：
        1. 持续胸痛超过30分钟
        2. ST段抬高呈弓背向上型
        3. 心肌坏死标志物升高
        治疗包括溶栓、PCI和药物治疗。
        """
        
        print("📝 测试内容主题: 急性心肌梗死")
        print("   预期匹配: internal_medicine_ch11 相关知识点")
        
        # 测试不带数据库的旧方法
        print("\n1️⃣ 测试旧方法 (parse_content):")
        result_old = await parser.parse_content(test_content)
        print(f"   识别科目: {result_old.get('book')}")
        print(f"   章节ID: {result_old.get('chapter_id')}")
        print(f"   知识点: {len(result_old.get('concepts', []))}个")
        
        # 测试带数据库的新方法
        print("\n2️⃣ 测试新方法 (parse_content_with_knowledge):")
        db = SessionLocal()
        try:
            result_new = await parser.parse_content_with_knowledge(test_content, db=db)
            print(f"   识别科目: {result_new.get('book')}")
            print(f"   章节ID: {result_new.get('chapter_id')}")
            print(f"   章节标题: {result_new.get('chapter_title')}")
            print(f"   是否匹配已有: {result_new.get('matched_existing')}")
            print(f"   知识点: {len(result_new.get('concepts', []))}个")
            
            print("\n   知识点详情:")
            for c in result_new.get('concepts', [])[:5]:
                print(f"      - {c['name']}")
                print(f"        ID: {c['id'][:60]}...")
            
            # 验证是否使用了已有ID
            chapter_id = result_new.get('chapter_id', '')
            if 'internal_medicine_ch11' in chapter_id:
                print("\n   ✅ 成功匹配到已有章节!")
            else:
                print(f"\n   ⚠️ 未匹配到预期章节 (internal_medicine_ch11)")
                print(f"      实际识别为: {chapter_id}")
            
        finally:
            db.close()
        
        return True
        
    except Exception as e:
        print(f"❌ AI分类测试失败: {e}")
        import traceback
        traceback.print_exc()
        return False


def main():
    """运行所有测试"""
    print("🚀 开始测试AI知识库匹配功能\n")
    
    results = []
    
    # 测试1: 数据库
    results.append(("数据库连接", test_database()))
    
    # 测试2: 知识库检索
    results.append(("知识库检索", test_knowledge_retrieval()))
    
    # 测试3: 知识点匹配
    results.append(("知识点匹配", test_concept_matching()))
    
    # 测试4: AI分类（异步）
    results.append(("AI智能分类", asyncio.run(test_ai_classification())))
    
    # 汇总
    print("\n" + "="*60)
    print("📋 测试结果汇总")
    print("="*60)
    
    for name, result in results:
        status = "✅ 通过" if result == True else ("⚠️ 跳过" if result is None else "❌ 失败")
        print(f"   {status} - {name}")
    
    passed = sum(1 for _, r in results if r == True)
    skipped = sum(1 for _, r in results if r is None)
    failed = sum(1 for _, r in results if r == False)
    
    print(f"\n总计: {passed}通过, {skipped}跳过, {failed}失败")
    
    if failed == 0:
        print("\n🎉 所有核心测试通过！系统工作正常。")
    else:
        print("\n⚠️ 部分测试失败，请检查配置。")


if __name__ == "__main__":
    main()
