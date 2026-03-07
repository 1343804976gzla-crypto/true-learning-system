"""
修复旧格式章节ID
将 medicine.ch3-2, surgery.ch15 等旧格式ID更新为新格式
"""

import sys
sys.path.insert(0, r'C:\Users\35456\true-learning-system')

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from models import Chapter, ConceptMastery

DB_PATH = r"C:\Users\35456\true-learning-system\data\learning.db"
engine = create_engine(f"sqlite:///{DB_PATH}")
SessionLocal = sessionmaker(bind=engine)

db = SessionLocal()

try:
    print("🔧 检查并修复旧格式ID\n")
    
    # 查找旧格式ID
    old_chapters = []
    chapters = db.query(Chapter).all()
    for ch in chapters:
        if '.' in ch.id:
            old_chapters.append(ch)
    
    if not old_chapters:
        print("✅ 没有旧格式ID需要修复")
    else:
        print(f"发现 {len(old_chapters)} 个旧格式章节:\n")
        
        for ch in old_chapters:
            print(f"  旧ID: {ch.id}")
            print(f"  书名: {ch.book}")
            print(f"  章节: {ch.chapter_number}")
            
            # 生成新ID
            # 映射书名到ID前缀
            book_map = {
                "内科学": "internal_medicine",
                "外科学": "surgery",
                "病理学": "pathology",
                "生理学": "physiology",
                "生物化学": "biochemistry",
                "诊断学": "diagnostics",
                "医学人文": "medical_humanities"
            }
            
            book_id = book_map.get(ch.book, ch.book.lower().replace(' ', '_'))
            chapter_num = ch.chapter_number.replace('-', '_')
            new_id = f"{book_id}_ch{chapter_num}"
            
            print(f"  新ID: {new_id}")
            
            # 检查新ID是否已存在
            existing = db.query(Chapter).filter(Chapter.id == new_id).first()
            if existing:
                print(f"  ⚠️ 新ID已存在，合并数据...")
                # 合并知识点
                old_concepts = db.query(ConceptMastery).filter(ConceptMastery.chapter_id == ch.id).all()
                for c in old_concepts:
                    c.chapter_id = new_id
                # 删除旧章节
                db.delete(ch)
            else:
                print(f"  ✅ 更新ID...")
                # 更新章节ID
                old_id = ch.id
                ch.id = new_id
                
                # 更新关联的知识点
                concepts = db.query(ConceptMastery).filter(ConceptMastery.chapter_id == old_id).all()
                for c in concepts:
                    # 更新chapter_id
                    c.chapter_id = new_id
                    # 更新concept_id (替换前缀)
                    old_concept_prefix = old_id
                    new_concept_prefix = new_id
                    if c.concept_id.startswith(old_concept_prefix):
                        c.concept_id = c.concept_id.replace(old_concept_prefix, new_concept_prefix, 1)
            
            print()
        
        db.commit()
        print("✅ 修复完成！")
    
    # 验证
    print("\n📊 验证结果:")
    chapters = db.query(Chapter).all()
    old_count = sum(1 for ch in chapters if '.' in ch.id)
    print(f"   剩余旧格式ID: {old_count}")
    print(f"   总章节数: {len(chapters)}")
    
except Exception as e:
    print(f"❌ 修复失败: {e}")
    import traceback
    traceback.print_exc()
    db.rollback()
finally:
    db.close()
