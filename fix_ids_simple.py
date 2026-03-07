"""
修复旧格式章节ID - 简化版
删除旧格式章节，保留知识点（关联到正确章节）
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
    print("🔧 清理旧格式章节ID\n")
    
    # 查找旧格式ID章节
    chapters = db.query(Chapter).all()
    old_chapters = [ch for ch in chapters if '.' in ch.id]
    
    if not old_chapters:
        print("✅ 没有旧格式ID需要清理")
    else:
        print(f"发现 {len(old_chapters)} 个旧格式章节:\n")
        
        for ch in old_chapters:
            print(f"  处理: {ch.id} ({ch.book} 第{ch.chapter_number}章)")
            
            # 确定目标章节ID
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
            target_id = f"{book_id}_ch{chapter_num}"
            
            # 查找目标章节
            target = db.query(Chapter).filter(Chapter.id == target_id).first()
            
            if target:
                print(f"    目标章节已存在: {target_id}")
                # 将知识点移动到目标章节
                concepts = db.query(ConceptMastery).filter(ConceptMastery.chapter_id == ch.id).all()
                for c in concepts:
                    # 更新chapter_id
                    c.chapter_id = target_id
                    # 更新concept_id前缀
                    old_prefix = ch.id
                    new_prefix = target_id
                    if c.concept_id.startswith(old_prefix):
                        c.concept_id = new_prefix + c.concept_id[len(old_prefix):]
                    db.add(c)
                print(f"    移动了 {len(concepts)} 个知识点到 {target_id}")
            else:
                print(f"    创建新章节: {target_id}")
                # 直接修改章节ID
                ch.id = target_id
                db.add(ch)
                
                # 更新关联的知识点ID
                concepts = db.query(ConceptMastery).filter(
                    ConceptMastery.chapter_id == ch.id
                ).all()
                for c in concepts:
                    old_id = c.concept_id
                    # 替换前缀
                    if c.concept_id.startswith(ch.id.replace(target_id, ch.id)):
                        c.concept_id = target_id + c.concept_id[len(ch.id):]
                    db.add(c)
            
            print()
        
        db.commit()
        print("✅ 清理完成！")
    
    # 验证
    print("\n📊 验证结果:")
    chapters = db.query(Chapter).all()
    old_count = sum(1 for ch in chapters if '.' in ch.id)
    print(f"   剩余旧格式ID: {old_count}")
    print(f"   总章节数: {len(chapters)}")
    
except Exception as e:
    print(f"❌ 清理失败: {e}")
    import traceback
    traceback.print_exc()
    db.rollback()
finally:
    db.close()
