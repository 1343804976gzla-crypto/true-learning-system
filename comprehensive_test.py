"""
全面系统测试
检查所有模板、API、数据库的一致性和潜在问题
"""

import sys
sys.path.insert(0, r'C:\Users\35456\true-learning-system')

import re
import os
from pathlib import Path
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from models import Chapter, ConceptMastery

DB_PATH = r"C:\Users\35456\true-learning-system\data\learning.db"
engine = create_engine(f"sqlite:///{DB_PATH}")
SessionLocal = sessionmaker(bind=engine)

class SystemTester:
    def __init__(self):
        self.issues = []
        self.warnings = []
        self.base_path = r"C:\Users\35456\true-learning-system"
        
    def log_issue(self, category, message, fix=None):
        self.issues.append({"category": category, "message": message, "fix": fix})
        print(f"  ❌ [{category}] {message}")
        if fix:
            print(f"     💡 {fix}")
    
    def log_warning(self, category, message):
        self.warnings.append({"category": category, "message": message})
        print(f"  ⚠️  [{category}] {message}")
    
    def test_database_ids(self):
        """测试数据库ID格式"""
        print("\n" + "="*70)
        print("📊 测试1: 数据库ID格式一致性")
        print("="*70)
        
        db = SessionLocal()
        try:
            chapters = db.query(Chapter).all()
            concepts = db.query(ConceptMastery).all()
            
            # 检查章节ID格式
            old_format_chapters = []
            for ch in chapters:
                if '.' in ch.id or '-' in ch.id:
                    old_format_chapters.append(ch.id)
            
            if old_format_chapters:
                self.log_issue("数据库", f"发现{len(old_format_chapters)}个旧格式章节ID", 
                              f"需要修复: {', '.join(old_format_chapters[:3])}")
            else:
                print(f"  ✅ 所有{len(chapters)}个章节ID格式正确")
            
            # 检查知识点ID是否与章节ID匹配
            mismatched = []
            for c in concepts:
                chapter_id = c.chapter_id
                if not c.concept_id.startswith(chapter_id):
                    mismatched.append((c.concept_id, chapter_id))
            
            if mismatched:
                self.log_issue("数据库", f"发现{len(mismatched)}个知识点ID与章节ID不匹配",
                              "知识点ID应该以章节ID开头")
            else:
                print(f"  ✅ 所有{len(concepts)}个知识点ID与章节ID匹配")
            
            # 检查orphan知识点（关联不存在的章节）
            chapter_ids = {ch.id for ch in chapters}
            orphans = [c for c in concepts if c.chapter_id not in chapter_ids]
            
            if orphans:
                self.log_issue("数据库", f"发现{len(orphans)}个孤儿知识点(关联不存在的章节)",
                              f"示例: {orphans[0].concept_id} -> {orphans[0].chapter_id}")
            else:
                print(f"  ✅ 没有孤儿知识点")
                
        finally:
            db.close()
    
    def test_template_links(self):
        """测试模板文件中的链接生成"""
        print("\n" + "="*70)
        print("🎨 测试2: 模板文件链接生成")
        print("="*70)
        
        templates_dir = Path(self.base_path) / "templates"
        
        for template_file in templates_dir.glob("*.html"):
            content = template_file.read_text(encoding='utf-8')
            
            # 检查是否有手动构造ID的情况
            patterns = [
                (r'\.ch\d+', f"旧格式章节ID (如: .ch15)"),
                (r'chapterId\s*=\s*[`\'"].*?\+.*?chapter_number', "手动构造chapter_id"),
                (r'\.toLowerCase\(\).*\.replace.*chapter', "可能的手动ID构造"),
            ]
            
            for pattern, desc in patterns:
                if re.search(pattern, content):
                    self.log_issue("模板", f"{template_file.name} 可能存在手动ID构造: {desc}",
                                  "应使用服务器返回的 chapter_id")
                    break
            else:
                # 检查是否使用了 extracted.chapter_id
                if 'extracted.chapter_id' in content or "extracted['chapter_id']" in content:
                    print(f"  ✅ {template_file.name} 正确使用服务器返回的ID")
                elif 'chapter' in content.lower():
                    self.log_warning("模板", f"{template_file.name} 需要检查ID生成逻辑")
    
    def test_api_endpoints(self):
        """测试API端点"""
        print("\n" + "="*70)
        print("🔌 测试3: API端点检查")
        print("="*70)
        
        routers_dir = Path(self.base_path) / "routers"
        
        # 检查upload路由
        upload_file = routers_dir / "upload.py"
        if upload_file.exists():
            content = upload_file.read_text(encoding='utf-8')
            
            if 'parse_content_with_knowledge' in content:
                print(f"  ✅ upload.py 使用知识库匹配")
            elif 'parse_content' in content:
                self.log_issue("API", "upload.py 使用旧版parse_content", 
                              "应改为 parse_content_with_knowledge")
            
            if 'chapter_id' in content:
                print(f"  ✅ upload.py 正确处理chapter_id")
            else:
                self.log_issue("API", "upload.py 未处理chapter_id")
    
    def test_content_parser(self):
        """测试内容解析器"""
        print("\n" + "="*70)
        print("🧠 测试4: 内容解析器")
        print("="*70)
        
        parser_file = Path(self.base_path) / "services" / "content_parser.py"
        content = parser_file.read_text(encoding='utf-8')
        
        checks = [
            ('parse_content_with_knowledge', "新知识库匹配方法"),
            ('_get_existing_knowledge', "知识库检索方法"),
            ('_find_matching_concepts', "知识点匹配方法"),
            ('chapter_id', "返回chapter_id"),
        ]
        
        for keyword, desc in checks:
            if keyword in content:
                print(f"  ✅ 包含 {desc}")
            else:
                self.log_issue("解析器", f"缺少 {desc}")
    
    def test_chapter_routes(self):
        """测试章节路由"""
        print("\n" + "="*70)
        print("📚 测试5: 章节路由")
        print("="*70)
        
        main_file = Path(self.base_path) / "main.py"
        content = main_file.read_text(encoding='utf-8')
        
        # 检查路由定义
        routes = [
            ('/chapter/{chapter_id}', "章节详情页"),
            ('/quiz/{concept_id}', "测试页"),
            ('/feynman/{concept_id}', "费曼页"),
        ]
        
        for route, desc in routes:
            if route in content:
                print(f"  ✅ 路由 {route} ({desc})")
            else:
                self.log_issue("路由", f"缺少路由 {route}")
    
    def test_id_consistency(self):
        """测试ID命名一致性"""
        print("\n" + "="*70)
        print("🏷️  测试6: ID命名一致性")
        print("="*70)
        
        db = SessionLocal()
        try:
            chapters = db.query(Chapter).all()
            
            # 检查章节ID命名规范
            invalid_ids = []
            for ch in chapters:
                # 规范格式: xxx_chxx (下划线格式)
                if not re.match(r'^[a-z_]+_ch\d+(_\d+)?$', ch.id):
                    invalid_ids.append(ch.id)
            
            if invalid_ids:
                self.log_issue("ID规范", f"发现{len(invalid_ids)}个非标准章节ID",
                              f"示例: {', '.join(invalid_ids[:3])}")
            else:
                print(f"  ✅ 所有章节ID符合规范")
            
            # 检查科目映射一致性
            book_mapping = {
                "内科学": "internal_medicine",
                "外科学": "surgery",
                "病理学": "pathology",
                "生理学": "physiology",
                "生物化学": "biochemistry",
                "诊断学": "diagnostics",
                "医学人文": "medical_humanities"
            }
            
            mismatched_books = []
            for ch in chapters:
                expected_prefix = book_mapping.get(ch.book)
                if expected_prefix and not ch.id.startswith(expected_prefix):
                    mismatched_books.append((ch.book, ch.id))
            
            if mismatched_books:
                self.log_issue("ID映射", f"发现{len(mismatched_books)}个科目与ID前缀不匹配",
                              f"示例: {mismatched_books[0]}")
            else:
                print(f"  ✅ 科目与ID前缀映射正确")
                
        finally:
            db.close()
    
    def test_edge_cases(self):
        """测试边界情况"""
        print("\n" + "="*70)
        print("🔍 测试7: 边界情况检查")
        print("="*70)
        
        db = SessionLocal()
        try:
            # 检查空章节（没有知识点）
            chapters = db.query(Chapter).all()
            empty_chapters = []
            for ch in chapters:
                count = db.query(ConceptMastery).filter(ConceptMastery.chapter_id == ch.id).count()
                if count == 0:
                    empty_chapters.append(ch.id)
            
            if empty_chapters:
                self.log_warning("边界", f"发现{len(empty_chapters)}个空章节(无知识点)")
            else:
                print(f"  ✅ 所有章节都有知识点")
            
            # 检查重复概念名称
            from sqlalchemy import func
            duplicates = db.query(ConceptMastery.name, func.count(ConceptMastery.name)).\
                group_by(ConceptMastery.name).\
                having(func.count(ConceptMastery.name) > 1).all()
            
            if duplicates:
                self.log_warning("边界", f"发现{len(duplicates)}个重复知识点名称")
            else:
                print(f"  ✅ 知识点名称唯一")
                
        finally:
            db.close()
    
    def generate_report(self):
        """生成测试报告"""
        print("\n" + "="*70)
        print("📋 测试报告汇总")
        print("="*70)
        
        print(f"\n问题统计:")
        print(f"  ❌ 错误: {len(self.issues)} 项")
        print(f"  ⚠️  警告: {len(self.warnings)} 项")
        
        if self.issues:
            print(f"\n❌ 需要修复的问题:")
            for i, issue in enumerate(self.issues[:10], 1):
                print(f"  {i}. [{issue['category']}] {issue['message']}")
                if issue['fix']:
                    print(f"     💡 {issue['fix']}")
            if len(self.issues) > 10:
                print(f"  ... 还有 {len(self.issues) - 10} 个问题")
        
        if self.warnings:
            print(f"\n⚠️  需要注意的警告:")
            for i, warning in enumerate(self.warnings[:5], 1):
                print(f"  {i}. [{warning['category']}] {warning['message']}")
        
        if not self.issues:
            print("\n🎉 恭喜！所有核心测试通过，系统工作正常。")
        
        return len(self.issues) == 0


def main():
    print("🚀 开始全面系统测试\n")
    
    tester = SystemTester()
    
    # 运行所有测试
    tester.test_database_ids()
    tester.test_template_links()
    tester.test_api_endpoints()
    tester.test_content_parser()
    tester.test_chapter_routes()
    tester.test_id_consistency()
    tester.test_edge_cases()
    
    # 生成报告
    success = tester.generate_report()
    
    return 0 if success else 1


if __name__ == "__main__":
    sys.exit(main())
