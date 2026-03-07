"""
批量导入贺银成西综全部课程资源
处理所有科目：病理、内科、外科、生化、诊断、生理、人文
"""

import sqlite3
import json
import re
from datetime import date
from pathlib import Path

DB_PATH = r"C:\Users\35456\true-learning-system\data\learning.db"

SUBJECT_MAP = {
    "病理": "pathology",
    "内科": "internal_medicine", 
    "外科": "surgery",
    "生化": "biochemistry",
    "诊断": "diagnostics",
    "生理": "physiology",
    "人文": "medical_humanities"
}

SUBJECT_NAME = {
    "pathology": "病理学",
    "internal_medicine": "内科学",
    "surgery": "外科学", 
    "biochemistry": "生物化学",
    "diagnostics": "诊断学",
    "physiology": "生理学",
    "medical_humanities": "医学人文"
}


def parse_video_path(path: str) -> dict:
    """解析视频路径，提取科目、章节、知识点"""
    filename = Path(path).name
    
    # 提取科目
    subject_key = None
    subject_name = None
    for cn, en in SUBJECT_MAP.items():
        if cn in path or cn in filename:
            subject_key = en
            subject_name = SUBJECT_NAME[en]
            break
    
    if not subject_key:
        return None
    
    # 匹配文件名模式：科目+章号+节号+标题
    # 如：病理01章-01细胞和组织的适应（63分钟）.mp4
    # 如：内科18章-01胃食管反流病（37分钟）.mp4
    pattern = r'(?:病理|内科|外科|生化|诊断|生理|人文)(\d+)章[-_](\d+)([^（(]+)'
    match = re.search(pattern, filename)
    
    if match:
        chapter_num = match.group(1)
        sub_chapter = match.group(2)
        title_raw = match.group(3).strip()
        
        # 清理标题
        title = re.sub(r'[（(]\d+分钟[）)]', '', title_raw).strip()
        title = re.sub(r'\.mp4$', '', title)
        
        # 生成ID（下划线格式，避免URL问题）
        chapter_id = f"{subject_key}_ch{chapter_num}"
        
        # 知识点ID：安全化处理
        concept_id_safe = title.replace('（', '_').replace('）', '_').replace('(', '_').replace(')', '_')
        concept_id_safe = concept_id_safe.replace(' ', '_').replace('、', '_').replace('/', '_')
        concept_id_safe = re.sub(r'[^\w_]', '', concept_id_safe)
        concept_id = f"{chapter_id}_{sub_chapter}_{concept_id_safe[:40]}"
        
        return {
            "subject_key": subject_key,
            "subject_name": subject_name,
            "chapter_number": chapter_num,
            "sub_chapter": sub_chapter,
            "chapter_id": chapter_id,
            "concept_id": concept_id,
            "title": title,
            "raw_path": path
        }
    
    return None


def init_database():
    """初始化数据库表"""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    cursor.executescript("""
        CREATE TABLE IF NOT EXISTS chapters (
            id TEXT PRIMARY KEY,
            book TEXT NOT NULL,
            edition TEXT,
            chapter_number TEXT NOT NULL,
            chapter_title TEXT NOT NULL,
            content_summary TEXT,
            concepts JSON,
            first_uploaded DATE,
            last_reviewed DATE
        );
        
        CREATE TABLE IF NOT EXISTS concept_mastery (
            concept_id TEXT PRIMARY KEY,
            chapter_id TEXT NOT NULL,
            name TEXT NOT NULL,
            retention REAL DEFAULT 0.0,
            understanding REAL DEFAULT 0.0,
            application REAL DEFAULT 0.0,
            last_tested DATE,
            next_review DATE,
            FOREIGN KEY (chapter_id) REFERENCES chapters(id)
        );
    """)
    
    conn.commit()
    conn.close()
    print("✅ 数据库表初始化完成")


def import_all_courses():
    """导入全部课程数据"""
    # 所有视频路径（完整列表）
    all_paths = [
        # ===== 病理学（已导入21章87知识点，这里继续添加其他科目）=====
        
        # ===== 内科学 =====
        # 内科第1-5章
        ("内科01章-01呼吸系统疾病总论（24分钟）.mp4", "internal_medicine"),
        ("内科02章-01支气管哮喘①（82分钟）.mp4", "internal_medicine"),
        ("内科02章-02支气管哮喘②（58分钟）.mp4", "internal_medicine"),
        ("内科02章-03支气管扩张症（36分钟）.mp4", "internal_medicine"),
        ("内科03章-01肺炎概述（34分钟）.mp4", "internal_medicine"),
        ("内科03章-02肺炎链球菌肺炎（24分钟）.mp4", "internal_medicine"),
        ("内科03章-03葡萄球菌肺炎（15分钟）.mp4", "internal_medicine"),
        ("内科03章-04肺炎克雷伯杆菌肺炎（8分钟）.mp4", "internal_medicine"),
        ("内科03章-05军团菌肺炎（13分钟）.mp4", "internal_medicine"),
        ("内科03章-06肺炎支原体肺炎（20分钟）.mp4", "internal_medicine"),
        ("内科03章-07病毒性肺炎（12分钟）.mp4", "internal_medicine"),
        ("内科03章-08肺脓肿（21分钟）.mp4", "internal_medicine"),
        ("内科04章-01肺结核①（44分钟）.mp4", "internal_medicine"),
        ("内科04章-02肺结核②（54分钟）.mp4", "internal_medicine"),
        ("内科04章-03肺癌（37分钟）.mp4", "internal_medicine"),
        ("内科05章-01间质性肺疾病概述（25分钟）.mp4", "internal_medicine"),
        ("内科05章-02特发性肺纤维化（IPF）（21分钟）.mp4", "internal_medicine"),
        ("内科05章-03结节病（26分钟）.mp4", "internal_medicine"),
        ("内科05章-04过敏性肺炎（11分钟）.mp4", "internal_medicine"),
        ("内科05章-05肺血栓栓塞症①（25分钟）.mp4", "internal_medicine"),
        ("内科05章-06肺血栓栓塞症②（23分钟）.mp4", "internal_medicine"),
        
        # 内科第6-10章
        ("内科06章-01肺动脉高压概述（4分钟）.mp4", "internal_medicine"),
        ("内科06章-02特发性肺动脉高压（22分钟）.mp4", "internal_medicine"),
        ("内科06章-03慢性肺源性心脏病（37分钟）.mp4", "internal_medicine"),
        ("内科07章-01胸腔积液①（50分钟）.mp4", "internal_medicine"),
        ("内科07章-02胸腔积液②（42分钟）.mp4", "internal_medicine"),
        ("内科07章-03气胸（28分钟）.mp4", "internal_medicine"),
        ("内科08章-01急性呼吸窘迫综合征（38分钟）.mp4", "internal_medicine"),
        ("内科08章-02呼吸衰竭（72分钟）.mp4", "internal_medicine"),
        ("内科08章-03酸碱平衡失调与电解质紊乱（15分钟）.mp4", "internal_medicine"),
        ("内科09章-01慢性心力衰竭①（80分钟）.mp4", "internal_medicine"),
        ("内科09章-02慢性心力衰竭②（71分钟）.mp4", "internal_medicine"),
        ("内科09章-03慢性心力衰竭③（59分钟）.mp4", "internal_medicine"),
        ("内科09章-04慢性心力衰竭④（52分钟）.mp4", "internal_medicine"),
        ("内科09章-05急性心力衰竭（13分钟）.mp4", "internal_medicine"),
        ("内科10章-01心律失常的分类及发病机制（20分钟）.mp4", "internal_medicine"),
        ("内科10章-02窦性心律失常（33分钟）.mp4", "internal_medicine"),
        ("内科10章-03期前收缩（早搏）（64分钟）.mp4", "internal_medicine"),
        ("内科10章-04心动过速（62分钟）.mp4", "internal_medicine"),
        ("内科10章-05扑动（18分钟）.mp4", "internal_medicine"),
        ("内科10章-06颤动（56分钟）.mp4", "internal_medicine"),
        ("内科10章-07房室阻滞（26分钟）.mp4", "internal_medicine"),
        ("内科10章-08预激综合征（WPW综合征）（22分钟）.mp4", "internal_medicine"),
        ("内科10章-09抗快速心律失常药物的分类（7分钟）.mp4", "internal_medicine"),
        ("内科10章-10心律失常的介入治疗（3分钟）.mp4", "internal_medicine"),
        
        # 内科第11-17章
        ("内科11章-01动脉粥样硬化（39分钟）.mp4", "internal_medicine"),
        ("内科11章-02稳定型心绞痛（77分钟）.mp4", "internal_medicine"),
        ("内科11章-03不稳定型心绞痛、非ST段抬高型心肌梗死（43分钟）.mp4", "internal_medicine"),
        ("内科11章-04急性ST段抬高型急性心肌梗死①（63分钟）.mp4", "internal_medicine"),
        ("内科11章-05急性ST段抬高型急性心肌梗死②（54分钟）.mp4", "internal_medicine"),
        ("内科11章-06急性ST段抬高型急性心肌梗死③（54分钟）.mp4", "internal_medicine"),
        ("内科12章-01原发性高血压①（58分钟）.mp4", "internal_medicine"),
        ("内科12章-02原发性高血压②（52分钟）.mp4", "internal_medicine"),
        ("内科12章-03继发性高血压（7分钟）.mp4", "internal_medicine"),
        ("内科13章-01心肌病①（55分钟）.mp4", "internal_medicine"),
        ("内科13章-02心肌病②（43分钟）.mp4", "internal_medicine"),
        ("内科13章-03心肌炎（13分钟）.mp4", "internal_medicine"),
        ("内科14章-01二尖瓣狭窄（二狭）（69分钟）.mp4", "internal_medicine"),
        ("内科14章-02二尖瓣关闭不全（二闭）（57分钟）.mp4", "internal_medicine"),
        ("内科14章-03主动脉瓣狭窄（主狭）（32分钟）.mp4", "internal_medicine"),
        ("内科14章-04主动脉瓣关闭不全（主闭）（17分钟）.mp4", "internal_medicine"),
        ("内科15章-01急性心包炎（34分钟）.mp4", "internal_medicine"),
        ("内科15章-02心包积液和心脏压塞（29分钟）.mp4", "internal_medicine"),
        ("内科16章-01自体瓣膜心内膜炎（50分钟）.mp4", "internal_medicine"),
        ("内科16章-02人工瓣膜心内膜炎（2分钟）.mp4", "internal_medicine"),
        ("内科17章-01概述①（12分钟）.mp4", "internal_medicine"),
        ("内科17章-02概述②（10分钟）.mp4", "internal_medicine"),
        
        # 内科第18-24章
        ("内科18章-01胃食管反流病（37分钟）.mp4", "internal_medicine"),
        ("内科18章-02慢性胃炎（32分钟）.mp4", "internal_medicine"),
        ("内科19章-01消化性溃疡（70分钟）.mp4", "internal_medicine"),
        ("内科19章-02肠易激综合征（17分钟）.mp4", "internal_medicine"),
        ("内科20章-01肠结核（27分钟）.mp4", "internal_medicine"),
        ("内科20章-02结核性腹膜炎（21分钟）.mp4", "internal_medicine"),
        ("内科21章-01炎症性肠病概述（14分钟）.mp4", "internal_medicine"),
        ("内科21章-02溃疡性结肠炎（78分钟）.mp4", "internal_medicine"),
        ("内科21章-03克罗恩病（22分钟）.mp4", "internal_medicine"),
        ("内科22章-01肝硬化①（57分钟）.mp4", "internal_medicine"),
        ("内科22章-02肝硬化②（54分钟）.mp4", "internal_medicine"),
        ("内科22章-03肝硬化③（63分钟）.mp4", "internal_medicine"),
        ("内科22章-04原发性肝癌（25分钟）.mp4", "internal_medicine"),
        ("内科23章-01肾的解剖与生理功能（25分钟）.mp4", "internal_medicine"),
        ("内科23章-02肾脏疾病的检查（6分钟）.mp4", "internal_medicine"),
        ("内科23章-03肾脏疾病常见综合征与诊治（20分钟）.mp4", "internal_medicine"),
        ("内科24章-01急性肾小球肾炎（43分钟）.mp4", "internal_medicine"),
        ("内科24章-02急进性肾小球肾炎（29分钟）.mp4", "internal_medicine"),
        ("内科24章-03IgA肾病（23分钟）.mp4", "internal_medicine"),
        ("内科24章-04肾病综合征（65分钟）.mp4", "internal_medicine"),
        ("内科24章-05无症状血尿和（或）蛋白尿（6分钟）.mp4", "internal_medicine"),
        ("内科24章-06慢性肾小球肾炎（14分钟）.mp4", "internal_medicine"),
        
        # 内科第25-30章
        ("内科25章-01尿路感染概述（56分钟）.mp4", "internal_medicine"),
        ("内科25章-02急性肾盂肾炎（4分钟）.mp4", "internal_medicine"),
        ("内科25章-03急性膀胱炎（5分钟）.mp4", "internal_medicine"),
        ("内科25章-04慢性肾盂肾炎（7分钟）.mp4", "internal_medicine"),
        ("内科25章-05无症状细菌尿（3分钟）.mp4", "internal_medicine"),
        ("内科26章-01急性肾损伤（64分钟）.mp4", "internal_medicine"),
        ("内科26章-02慢性肾衰竭（49分钟）.mp4", "internal_medicine"),
        ("内科27章-01血液系统疾病总论（15分钟）.mp4", "internal_medicine"),
        ("内科27章-02贫血概述（34分钟）.mp4", "internal_medicine"),
        ("内科27章-03缺铁性贫血（71分钟）.mp4", "internal_medicine"),
        ("内科28章-01再生障碍性贫血（44分钟）.mp4", "internal_medicine"),
        ("内科28章-02溶血性贫血概述（59分钟）.mp4", "internal_medicine"),
        ("内科28章-03溶血性贫血各论（56分钟）.mp4", "internal_medicine"),
        ("内科29章-01骨髓增生异常综合征（50分钟）.mp4", "internal_medicine"),
        ("内科29章-02急性白血病①（64分钟）.mp4", "internal_medicine"),
        ("内科29章-03急性白血病②（63分钟）.mp4", "internal_medicine"),
        ("内科29章-04急性白血病③（30分钟）.mp4", "internal_medicine"),
        ("内科29章-05慢性髓系白血病（慢粒）（22分钟）.mp4", "internal_medicine"),
        ("内科30章-01霍奇金淋巴瘤（HL）（45分钟）.mp4", "internal_medicine"),
        ("内科30章-02非霍奇金淋巴瘤（NHL）（21分钟）.mp4", "internal_medicine"),
        ("内科30章-03多发性骨髓瘤（29分钟）.mp4", "internal_medicine"),
        
        # 内科第31-36章
        ("内科31章-01出血性疾病概述（52分钟）.mp4", "internal_medicine"),
        ("内科31章-02原发免疫性血小板减少症（ITP）（32分钟）.mp4", "internal_medicine"),
        ("内科32章-01内分泌系统疾病总论（26分钟）.mp4", "internal_medicine"),
        ("内科32章-02Graves病①（73分钟）.mp4", "internal_medicine"),
        ("内科32章-03Graves病②（57分钟）.mp4", "internal_medicine"),
        ("内科32章-04甲状腺功能减退症（17分钟）.mp4", "internal_medicine"),
        ("内科33章-01库欣综合征①（58分钟）.mp4", "internal_medicine"),
        ("内科33章-02库欣综合征②（75分钟）.mp4", "internal_medicine"),
        ("内科33章-03库欣综合征③（25分钟）.mp4", "internal_medicine"),
        ("内科33章-04原发性醛固酮增多症（23分钟）.mp4", "internal_medicine"),
        ("内科33章-05嗜铬细胞瘤（41分钟）.mp4", "internal_medicine"),
        ("内科34章-01糖尿病①（74分钟）.mp4", "internal_medicine"),
        ("内科34章-02糖尿病②（67分钟）.mp4", "internal_medicine"),
        ("内科34章-03糖尿病③（35分钟）.mp4", "internal_medicine"),
        ("内科34章-04糖尿病酮症酸中毒（DKA）（34分钟）.mp4", "internal_medicine"),
        ("内科34章-05高渗高血糖综合征（HHS）（11分钟）.mp4", "internal_medicine"),
        ("内科35章-01风湿性疾病总论（54分钟）.mp4", "internal_medicine"),
        ("内科35章-02类风湿关节炎（62分钟）.mp4", "internal_medicine"),
        ("内科35章-03系统性红斑狼疮（SLE）（48分钟）.mp4", "internal_medicine"),
        ("内科35章-04干燥综合征（23分钟）.mp4", "internal_medicine"),
        ("内科35章-05原发性血管炎概论（7分钟）.mp4", "internal_medicine"),
        ("内科35章-06显微镜下多血管炎（MPA）（2分钟）.mp4", "internal_medicine"),
        ("内科35章-07贝赫切特病（8分钟）.mp4", "internal_medicine"),
        ("内科36章-01中毒概述（33分钟）.mp4", "internal_medicine"),
        ("内科36章-02急性有机磷杀虫药中毒（16分钟）.mp4", "internal_medicine"),
        
        # ===== 外科学 =====
        # 外科第1-6章
        ("外科01章-01无菌术的基本概念与常用方法（26分钟）.mp4", "surgery"),
        ("外科01章-02手术进行中的无菌原则（7分钟）.mp4", "surgery"),
        ("外科02章-01概述（11分钟）.mp4", "surgery"),
        ("外科02章-02水和钠的代谢紊乱（73分钟）.mp4", "surgery"),
        ("外科02章-03钾代谢紊乱（27分钟）.mp4", "surgery"),
        ("外科02章-04镁及钙磷代谢紊乱（21分钟）.mp4", "surgery"),
        ("外科02章-05酸碱平衡失调（34分钟）.mp4", "surgery"),
        ("外科03章-01输血适应症与注意事项（11分钟）.mp4", "surgery"),
        ("外科03章-02输血的并发症及其防治（13分钟）.mp4", "surgery"),
        ("外科03章-03自体输血（自身输血）（19分钟）.mp4", "surgery"),
        ("外科04章-01休克概论（42分钟）.mp4", "surgery"),
        ("外科04章-02失血性休克和感染性休克（16分钟）.mp4", "surgery"),
        ("外科05章-01麻醉前准备和麻醉前用药（20分钟）.mp4", "surgery"),
        ("外科05章-02全身麻醉（29分钟）.mp4", "surgery"),
        ("外科05章-03局部麻醉（26分钟）.mp4", "surgery"),
        ("外科05章-04椎管内麻醉（47分钟）.mp4", "surgery"),
        ("外科06章-01重症监护治疗（3分钟）.mp4", "surgery"),
        ("外科06章-02心肺脑复苏（27分钟）.mp4", "surgery"),
        ("外科06章-03常见器官功能衰竭的治疗原则（8分钟）.mp4", "surgery"),
        
        # 外科第7-11章
        ("外科07章-01疼痛治疗（10分钟）.mp4", "surgery"),
        ("外科07章-02围术期处理（49分钟）.mp4", "surgery"),
        ("外科08章-01外科病人的代谢改变及营养状态的评定（31分钟）.mp4", "surgery"),
        ("外科08章-02肠外营养（50分钟）.mp4", "surgery"),
        ("外科08章-03肠内营养（14分钟）.mp4", "surgery"),
        ("外科09章-01概论（7分钟）.mp4", "surgery"),
        ("外科09章-02浅部组织细菌性感染（24分钟）.mp4", "surgery"),
        ("外科09章-03手部急性化脓性细菌感染（31分钟）.mp4", "surgery"),
        ("外科09章-04脓毒症（全身性外科感染）（15分钟）.mp4", "surgery"),
        ("外科09章-05破伤风（29分钟）.mp4", "surgery"),
        ("外科09章-06气性坏疽（12分钟）.mp4", "surgery"),
        ("外科09章-07外科应用抗菌药的原则（8分钟）.mp4", "surgery"),
        ("外科10章-01创伤（21分钟）.mp4", "surgery"),
        ("外科10章-02烧伤（66分钟）.mp4", "surgery"),
        ("外科11章-01移植（27分钟）.mp4", "surgery"),
        ("外科11章-02外科微创技术（6分钟）.mp4", "surgery"),
        
        # 外科第12-14章
        ("外科12章-01甲状腺的解剖生理概要（17分钟）.mp4", "surgery"),
        ("外科12章-02单纯性甲状腺肿（31分钟）.mp4", "surgery"),
        ("外科12章-03甲状腺功能亢进症的外科治疗①（61分钟）.mp4", "surgery"),
        ("外科12章-04甲状腺功能亢进症的外科治疗②（27分钟）.mp4", "surgery"),
        ("外科12章-05甲状腺炎（15分钟）.mp4", "surgery"),
        ("外科12章-06甲状腺癌（22分钟）.mp4", "surgery"),
        ("外科12章-07甲状腺结节的诊断和处理原则（3分钟）.mp4", "surgery"),
        ("外科12章-08甲状旁腺功能亢进症（10分钟）.mp4", "surgery"),
        ("外科12章-09颈部肿块（6分钟）.mp4", "surgery"),
        ("外科13章-01乳房的检查方法（16分钟）.mp4", "surgery"),
        ("外科13章-02急性乳腺炎（20分钟）.mp4", "surgery"),
        ("外科13章-03乳腺囊性增生病和乳腺良性肿瘤（14分钟）.mp4", "surgery"),
        ("外科13章-04乳腺癌（61分钟）.mp4", "surgery"),
        ("外科14章-01肋骨骨折（20分钟）.mp4", "surgery"),
        ("外科14章-02气胸（23分钟）.mp4", "surgery"),
        ("外科14章-03血胸（15分钟）.mp4", "surgery"),
        ("外科14章-04创伤性窒息（6分钟）.mp4", "surgery"),
        ("外科14章-05肺癌（34分钟）.mp4", "surgery"),
        ("外科14章-06食管癌（14分钟）.mp4", "surgery"),
        ("外科14章-07腐蚀性食管灼伤（5分钟）.mp4", "surgery"),
        ("外科14章-08贲门失弛缓症（7分钟）.mp4", "surgery"),
        ("外科14章-09原发性纵膈肿瘤（7分钟）.mp4", "surgery"),
        
        # 外科第15-19章
        ("外科15章-01疝的基本概念和临床类型（20分钟）.mp4", "surgery"),
        ("外科15章-02腹股沟疝（73分钟）.mp4", "surgery"),
        ("外科15章-03股疝（6分钟）.mp4", "surgery"),
        ("外科15章-04切口疝（3分钟）.mp4", "surgery"),
        ("外科16章-01概论（52分钟）.mp4", "surgery"),
        ("外科16章-02常见内脏损伤的特征和处理（31分钟）.mp4", "surgery"),
        ("外科17章-01急性弥漫性腹膜炎（32分钟）.mp4", "surgery"),
        ("外科17章-02腹腔脓肿（7分钟）.mp4", "surgery"),
        ("外科17章-03腹腔间隔室综合征（ACS）（7分钟）.mp4", "surgery"),
        ("外科18章-01解剖生理概要（13分钟）.mp4", "surgery"),
        ("外科18章-02胃十二指肠溃疡的外科治疗①（72分钟）.mp4", "surgery"),
        ("外科18章-03胃十二指肠溃疡的外科治疗②（39分钟）.mp4", "surgery"),
        ("外科18章-04胃癌（31分钟）.mp4", "surgery"),
        ("外科18章-05胃淋巴瘤（6分钟）.mp4", "surgery"),
        ("外科18章-06胃肠道间质瘤（10分钟）.mp4", "surgery"),
        ("外科18章-07胃的良性肿瘤（2分钟）.mp4", "surgery"),
        ("外科18章-08先天性肥厚性幽门狭窄（3分钟）.mp4", "surgery"),
        ("外科18章-09十二指肠憩室（1分钟）.mp4", "surgery"),
        ("外科19章-01肠炎性疾病（9分钟）.mp4", "surgery"),
        ("外科19章-02肠梗阻（66分钟）.mp4", "surgery"),
        ("外科19章-03肠系膜血管缺血性疾病（11分钟）.mp4", "surgery"),
        
        # 外科第20-22章
        ("外科20章-01阑尾的解剖与生理（10分钟）.mp4", "surgery"),
        ("外科20章-02急性阑尾炎（26分钟）.mp4", "surgery"),
        ("外科20章-03特殊类型阑尾炎（7分钟）.mp4", "surgery"),
        ("外科21章-01解剖生理概要与检查方法（30分钟）.mp4", "surgery"),
        ("外科21章-02结肠癌（44分钟）.mp4", "surgery"),
        ("外科21章-03直肠癌（55分钟）.mp4", "surgery"),
        ("外科21章-04肛裂与痔（29分钟）.mp4", "surgery"),
        ("外科21章-05肛瘘（10分钟）.mp4", "surgery"),
        ("外科21章-06直肠肛管周围脓肿（12分钟）.mp4", "surgery"),
        ("外科21章-07直肠脱垂（2分钟）.mp4", "surgery"),
        ("外科22章-01肝的解剖生理概要（14分钟）.mp4", "surgery"),
        ("外科22章-02肝脓肿（19分钟）.mp4", "surgery"),
        ("外科22章-03肝肿瘤（5分钟）.mp4", "surgery"),
        ("外科22章-04肝囊肿（2分钟）.mp4", "surgery"),
        ("外科22章-05门静脉高压症（78分钟）.mp4", "surgery"),
        
        # 外科第23-27章
        ("外科23章-01概述（53分钟）.mp4", "surgery"),
        ("外科23章-02胆道畸形（21分钟）.mp4", "surgery"),
        ("外科23章-03胆石病①（29分钟）.mp4", "surgery"),
        ("外科23章-04胆石病②（50分钟）.mp4", "surgery"),
        ("外科23章-05胆道感染（30分钟）.mp4", "surgery"),
        ("外科23章-06胆道蛔虫病（5分钟）.mp4", "surgery"),
        ("外科23章-07胆道疾病常见并发症（7分钟）.mp4", "surgery"),
        ("外科23章-08胆囊息肉和胆道肿瘤（25分钟）.mp4", "surgery"),
        ("外科24章-01急性胰腺炎①（48分钟）.mp4", "surgery"),
        ("外科24章-02急性胰腺炎②（46分钟）.mp4", "surgery"),
        ("外科24章-02慢性胰腺炎（6分钟）.mp4", "surgery"),
        ("外科24章-03胰腺癌（19分钟）.mp4", "surgery"),
        ("外科24章-04壶腹周围癌（5分钟）.mp4", "surgery"),
        ("外科24章-05胰腺内分泌肿瘤（9分钟）.mp4", "surgery"),
        ("外科25章-01脾切除术的适应证（3分钟）.mp4", "surgery"),
        ("外科25章-02脾切除术后常见并发症（5分钟）.mp4", "surgery"),
        ("外科26章-上消化道大出血（60分钟）.mp4", "surgery"),
        ("外科27章-01周围血管疾病的临床表现和周围血管损伤（11分钟）.mp4", "surgery"),
        ("外科27章-02动脉疾病（60分钟）.mp4", "surgery"),
        ("外科27章-03静脉疾病（42分钟）.mp4", "surgery"),
        
        # 外科第28-31章
        ("外科28章-01泌尿系统疾病总论（10分钟）.mp4", "surgery"),
        ("外科28章-02泌尿系统外伤（34分钟）.mp4", "surgery"),
        ("外科29章-01泌尿、男生殖系统感染（23分钟）.mp4", "surgery"),
        ("外科29章-02尿路梗阻（33分钟）.mp4", "surgery"),
        ("外科30章-01尿石症总论（17分钟）.mp4", "surgery"),
        ("外科30章-02上尿路结石（15分钟）.mp4", "surgery"),
        ("外科30章-03膀胱结石（3分钟）.mp4", "surgery"),
        ("外科30章-04肾细胞癌（肾癌）（14分钟）.mp4", "surgery"),
        ("外科30章-05肾母细胞癌（7分钟）.mp4", "surgery"),
        ("外科30章-06膀胱癌（21分钟）.mp4", "surgery"),
        ("外科30章-07肾盂癌（11分钟）.mp4", "surgery"),
        ("外科30章-08前列腺癌（10分钟）.mp4", "surgery"),
        ("外科31章-01先天性肌性斜颈（13分钟）.mp4", "surgery"),
        ("外科31章-02先天性手部畸形（3分钟）.mp4", "surgery"),
        ("外科31章-03发育性髋关节脱位（26分钟）.mp4", "surgery"),
        ("外科31章-04先天性马蹄内翻足（4分钟）.mp4", "surgery"),
        ("外科31章-05平足症（扁平足）（9分钟）.mp4", "surgery"),
        ("外科31章-06踇外翻（6分钟）.mp4", "surgery"),
        ("外科31章-07脊柱侧凸（20分钟）.mp4", "surgery"),
        
        # 外科第32-35章
        ("外科32章-01骨折的成因、分类、移位、临床表现与X线检查（30分钟）.mp4", "surgery"),
        ("外科32章-02骨折的并发症（37分钟）.mp4", "surgery"),
        ("外科32章-03骨折愈合与急救处理（20分钟）.mp4", "surgery"),
        ("外科32章-04骨折的治疗（16分钟）.mp4", "surgery"),
        ("外科33章-01骨折①（64分钟）.mp4", "surgery"),
        ("外科33章-02骨折②（76分钟）.mp4", "surgery"),
        ("外科33章-03骨折③（70分钟）.mp4", "surgery"),
        ("外科33章-04骨折④（79分钟）.mp4", "surgery"),
        ("外科33章-05骨折⑤（17分钟）.mp4", "surgery"),
        ("外科33章-06关节脱位（38分钟）.mp4", "surgery"),
        ("外科34章-01手外伤（31分钟）.mp4", "surgery"),
        ("外科34章-02断（肢）指再植（3分钟）.mp4", "surgery"),
        ("外科35章-01概论（8分钟）.mp4", "surgery"),
        ("外科35章-02上肢神经损伤（19分钟）.mp4", "surgery"),
        ("外科35章-03下肢神经损伤（14分钟）.mp4", "surgery"),
        ("外科35章-04周围神经卡压综合征（3分钟）.mp4", "surgery"),
        
        # 外科第36-41章
        ("外科36章-01运动系统慢性损伤的概论（10分钟）.mp4", "surgery"),
        ("外科36章-02棘上、棘间韧带损伤（9分钟）.mp4", "surgery"),
        ("外科36章-03疲劳骨折（6分钟）.mp4", "surgery"),
        ("外科36章-04月骨缺血性坏死（10分钟）.mp4", "surgery"),
        ("外科36章-05胫骨结节骨软骨病（8分钟）.mp4", "surgery"),
        ("外科36章-06股骨头骨软骨病（6分钟）.mp4", "surgery"),
        ("外科36章-07狭窄性腱鞘炎（19分钟）.mp4", "surgery"),
        ("外科36章-08肱骨外上髁炎（12分钟）.mp4", "surgery"),
        ("外科36章-09粘连性肩关节囊炎（20分钟）.mp4", "surgery"),
        ("外科36章-10股骨头坏死（10分钟）.mp4", "surgery"),
        ("外科37章-01颈椎间盘突出症（12分钟）.mp4", "surgery"),
        ("外科37章-02腰椎间盘突出症（67分钟）.mp4", "surgery"),
        ("外科38章-01急性血源性骨髓炎（29分钟）.mp4", "surgery"),
        ("外科38章-02慢性血源性骨髓炎（12分钟）.mp4", "surgery"),
        ("外科38章-03局限性骨脓肿（3分钟）.mp4", "surgery"),
        ("外科38章-04硬化性骨髓炎（3分钟）.mp4", "surgery"),
        ("外科38章-05创伤后骨髓炎（3分钟）.mp4", "surgery"),
        ("外科38章-06化脓性脊椎炎（2分钟）.mp4", "surgery"),
        ("外科38章-07化脓性关节炎（17分钟）.mp4", "surgery"),
        ("外科39章-01概论（23分钟）.mp4", "surgery"),
        ("外科39章-02脊柱结核（36分钟）.mp4", "surgery"),
        ("外科39章-03髋关节结核（16分钟）.mp4", "surgery"),
        ("外科39章-04膝关节结核（3分钟）.mp4", "surgery"),
        ("外科40章-01骨关节炎（25分钟）.mp4", "surgery"),
        ("外科40章-02强直性脊柱炎（24分钟）.mp4", "surgery"),
        ("外科40章-03类风湿关节炎（16分钟）.mp4", "surgery"),
        ("外科41章-01总论（15分钟）.mp4", "surgery"),
        ("外科41章-02几种常考骨肿瘤及肿瘤样病变的特点（22分钟）.mp4", "surgery"),
        
        # ===== 生理学 =====
        ("生理01章-01机体的内环境和稳态（18分钟）.mp4", "physiology"),
        ("生理01章-02机体生理功能的调节（23分钟）.mp4", "physiology"),
        ("生理01章-03人体内的自动控制系统（22分钟）.mp4", "physiology"),
        ("生理02章-01跨细胞膜的物质转运①（54分钟）.mp4", "physiology"),
        ("生理02章-02跨细胞膜的物质转运②（43分钟）.mp4", "physiology"),
        ("生理02章-03细胞的信号转导（38分钟）.mp4", "physiology"),
        ("生理02章-04细胞的电活动①（59分钟）.mp4", "physiology"),
        ("生理02章-05细胞的电活动②（46分钟）.mp4", "physiology"),
        ("生理02章-06细胞的电活动③（56分钟）.mp4", "physiology"),
        ("生理02章-07肌细胞的收缩①（41分钟）.mp4", "physiology"),
        ("生理02章-08肌细胞的收缩②（73分钟）.mp4", "physiology"),
        ("生理03章-01血液生理概述（34分钟）.mp4", "physiology"),
        ("生理03章-02血细胞生理①（45分钟）.mp4", "physiology"),
        ("生理03章-03血细胞生理②（41分钟）.mp4", "physiology"),
        ("生理03章-04生理性止血（73分钟）.mp4", "physiology"),
        ("生理03章-05血型和输血原则（35分钟）.mp4", "physiology"),
        ("生理04章-01心脏的泵血功能①（67分钟）.mp4", "physiology"),
        ("生理04章-02心脏的泵血功能②（39分钟）.mp4", "physiology"),
        ("生理04章-03各类心肌细胞的跨膜电位及其形成机制（52分钟）.mp4", "physiology"),
        ("生理04章-04心肌的生理特性（45分钟）.mp4", "physiology"),
        ("生理04章-05动脉血压（28分钟）.mp4", "physiology"),
        ("生理04章-06静脉血压（21分钟）.mp4", "physiology"),
        ("生理04章-07微循环（31分钟）.mp4", "physiology"),
        ("生理04章-08组织液（18分钟）.mp4", "physiology"),
        ("生理04章-09心血管活动的调节①（72分钟）.mp4", "physiology"),
        ("生理04章-10心血管活动的调节②（46分钟）.mp4", "physiology"),
        ("生理04章-11冠状动脉循环（14分钟）.mp4", "physiology"),
        ("生理05章-01肺通气原理（88分钟）.mp4", "physiology"),
        ("生理05章-02肺通气功能的评价（44分钟）.mp4", "physiology"),
        ("生理05章-03肺换气（53分钟）.mp4", "physiology"),
        ("生理05章-04O2和CO2在血液中的运输（70分钟）.mp4", "physiology"),
        ("生理05章-05化学感受性呼吸反射对呼吸运动的调节（50分钟）.mp4", "physiology"),
        ("生理06章-01消化生理概述（49分钟）.mp4", "physiology"),
        ("生理06章-02口腔内的消化和吞咽（16分钟）.mp4", "physiology"),
        ("生理06章-03胃内消化①（49分钟）.mp4", "physiology"),
        ("生理06章-04胃内消化②（54分钟）.mp4", "physiology"),
        ("生理06章-05小肠内的消化①（32分钟）.mp4", "physiology"),
        ("生理06章-06小肠内的消化②（53分钟）.mp4", "physiology"),
        ("生理06章-07大肠的功能（6分钟）.mp4", "physiology"),
        ("生理06章-08小肠内的物质吸收及其机制（49分钟）.mp4", "physiology"),
        ("生理07章-01能量代谢（79分钟）.mp4", "physiology"),
        ("生理07章-02体温及其调节（55分钟）.mp4", "physiology"),
        ("生理08章-01肾的功能解剖和肾血流量（51分钟）.mp4", "physiology"),
        ("生理08章-02肾小球的滤过功能（43分钟）.mp4", "physiology"),
        ("生理08章-03肾小管和集合管的物质转运功能①（88分钟）.mp4", "physiology"),
        ("生理08章-04肾小管和集合管的物质转运功能②（42分钟）.mp4", "physiology"),
        ("生理08章-05尿液的浓缩和稀释（34分钟）.mp4", "physiology"),
        ("生理08章-06尿生成的调节（53分钟）.mp4", "physiology"),
        ("生理08章-07清除率（24分钟）.mp4", "physiology"),
        ("生理08章-08排尿反射（8分钟）.mp4", "physiology"),
        ("生理09章-01感觉概述（20分钟）.mp4", "physiology"),
        ("生理09章-02视觉①（54分钟）.mp4", "physiology"),
        ("生理09章-03视觉②（84分钟）.mp4", "physiology"),
        ("生理09章-04听觉（76分钟）.mp4", "physiology"),
        ("生理09章-05平衡感觉（56分钟）.mp4", "physiology"),
        ("生理10章-01神经元和神经胶质细胞（55分钟）.mp4", "physiology"),
        ("生理10章-02突触传递（69分钟）.mp4", "physiology"),
        ("生理10章-03神经递质和受体（56分钟）.mp4", "physiology"),
        ("生理10章-04反射活动的基本规律（50分钟）.mp4", "physiology"),
        ("生理10章-05躯体与内脏感觉（48分钟）.mp4", "physiology"),
        ("生理10章-06神经系统对躯体运动的调节①（43分钟）.mp4", "physiology"),
        ("生理10章-07神经系统对躯体运动的调节②（72分钟）.mp4", "physiology"),
        ("生理10章-08神经系统对躯体运动的调节③（42分钟）.mp4", "physiology"),
        ("生理10章-09神经系统对躯体运动的调节④（57分钟）.mp4", "physiology"),
        ("生理10章-10神经系统对内脏活动、本能行为和情绪的调节（59分钟）.mp4", "physiology"),
        ("生理10章-11脑电活动及睡眠与觉醒（22分钟）.mp4", "physiology"),
        ("生理10章-12脑的高级功能（8分钟）.mp4", "physiology"),
        ("生理11章-01内分泌和激素（57分钟）.mp4", "physiology"),
        ("生理11章-02下丘脑-垂体内分泌（74分钟）.mp4", "physiology"),
        ("生理11章-03甲状腺激素（35分钟）.mp4", "physiology"),
        ("生理11章-04钙调节激素（30分钟）.mp4", "physiology"),
        ("生理11章-05胰岛素和胰高血糖素（37分钟）.mp4", "physiology"),
        ("生理11章-06糖皮质激素（32分钟）.mp4", "physiology"),
        ("生理12章-01男性生殖（41分钟）.mp4", "physiology"),
        ("生理12章-02女性生殖①（57分钟）.mp4", "physiology"),
        ("生理12章-03女性生殖②（45分钟）.mp4", "physiology"),
        
        # ===== 生物化学 =====
        ("生化01章-01蛋白质的分子组成（44分钟）.mp4", "biochemistry"),
        ("生化01章-02蛋白质的分子结构（74分钟）.mp4", "biochemistry"),
        ("生化01章-03蛋白质结构与功能的关系（36分钟）.mp4", "biochemistry"),
        ("生化01章-04蛋白质的理化性质（31分钟）.mp4", "biochemistry"),
        ("生化02章-01核酸的化学组成及一级结构（38分钟）.mp4", "biochemistry"),
        ("生化02章-02DNA的空间结构与功能（68分钟）.mp4", "biochemistry"),
        ("生化02章-03RNA的空间结构与功能（83分钟）.mp4", "biochemistry"),
        ("生化02章-04核酸的理化性质及应用（36分钟）.mp4", "biochemistry"),
        ("生化03章-01酶的分子结构与功能（72分钟）.mp4", "biochemistry"),
        ("生化03章-02酶的工作原理（49分钟）.mp4", "biochemistry"),
        ("生化03章-03酶促反应动力学（70分钟）.mp4", "biochemistry"),
        ("生化03章-04酶的调节（39分钟）.mp4", "biochemistry"),
        ("生化03章-05酶在医学上的应用（10分钟）.mp4", "biochemistry"),
        ("生化04章-01糖的无氧氧化和有氧氧化①（82分钟）.mp4", "biochemistry"),
        ("生化04章-02糖的无氧氧化和有氧氧化②（73分钟）.mp4", "biochemistry"),
        ("生化04章-03磷酸戊糖途径（31分钟）.mp4", "biochemistry"),
        ("生化04章-04糖原合成与分解（41分钟）.mp4", "biochemistry"),
        ("生化04章-05糖异生（34分钟）.mp4", "biochemistry"),
        ("生化04章-06血糖及调节（8分钟）.mp4", "biochemistry"),
        ("生化05章-01生物氧化的特点（3分钟）.mp4", "biochemistry"),
        ("生化05章-02线粒体氧化体系与呼吸链①（66分钟）.mp4", "biochemistry"),
        ("生化05章-03线粒体氧化体系与呼吸链②（48分钟）.mp4", "biochemistry"),
        ("生化05章-04氧化磷酸化与ATP生成（65分钟）.mp4", "biochemistry"),
        ("生化05章-05影响氧化磷酸化的因素（40分钟）.mp4", "biochemistry"),
        ("生化05章-06过氧化物酶体和微粒体中的酶类（2分钟）.mp4", "biochemistry"),
        ("生化06章-01酮体和胆固醇代谢①（36分钟）.mp4", "biochemistry"),
        ("生化06章-02酮体和胆固醇代谢②（20分钟）.mp4", "biochemistry"),
        ("生化06章-03甘油三脂代谢①（52分钟）.mp4", "biochemistry"),
        ("生化06章-04甘油三脂代谢②（48分钟）.mp4", "biochemistry"),
        ("生化06章-05甘油三脂代谢③（69分钟）.mp4", "biochemistry"),
        ("生化06章-06磷脂代谢（55分钟）.mp4", "biochemistry"),
        ("生化06章-07血浆脂蛋白及其代谢（42分钟）.mp4", "biochemistry"),
        ("生化07章-01蛋白质的生理功能与营养作用（30分钟）.mp4", "biochemistry"),
        ("生化07章-02氨基酸的一般代谢（71分钟）.mp4", "biochemistry"),
        ("生化07章-03氨的代谢（58分钟）.mp4", "biochemistry"),
        ("生化07章-04个别氨基酸代谢（61分钟）.mp4", "biochemistry"),
        ("生化08章-01嘌呤核苷酸的合成①（41分钟）.mp4", "biochemistry"),
        ("生化08章-02嘌呤核苷酸的合成②（51分钟）.mp4", "biochemistry"),
        ("生化08章-03嘌呤核苷酸的合成③（31分钟）.mp4", "biochemistry"),
        ("生化08章-04嘌呤核苷酸的分解（13分钟）.mp4", "biochemistry"),
        ("生化08章-05嘧啶核苷酸的合成（78分钟）.mp4", "biochemistry"),
        ("生化08章-06嘧啶核苷酸的分解（6分钟）.mp4", "biochemistry"),
        ("生化09章-01物质代谢的特点和相互联系（78分钟）.mp4", "biochemistry"),
        ("生化09章-02代谢调节的主要方式（62分钟）.mp4", "biochemistry"),
        ("生化09章-03体内重要组织和器官的代谢特点（10分钟）.mp4", "biochemistry"),
        ("生化10章-01NDA复制的基本特征（75分钟）.mp4", "biochemistry"),
        ("生化10章-02DNA复制的酶学和拓扑学①（71分钟）.mp4", "biochemistry"),
        ("生化10章-03DNA复制的酶学和拓扑学②（46分）.mp4", "biochemistry"),
        ("生化10章-04原核生物DNA复制过程（46分钟）.mp4", "biochemistry"),
        ("生化10章-05真核生物DNA复制过程（46分钟）.mp4", "biochemistry"),
        ("生化10章-06逆转录（18分钟）.mp4", "biochemistry"),
        ("生化11章-01DNA损伤（34分钟）.mp4", "biochemistry"),
        ("生化11章-02DNA损伤修复（37分钟）.mp4", "biochemistry"),
        ("生化12章-01原核生物RNA的合成①（63分钟）.mp4", "biochemistry"),
        ("生化12章-02原核生物RNA的合成②（41分钟）.mp4", "biochemistry"),
        ("生化12章-03真核生物RNA的合成①（77分钟）.mp4", "biochemistry"),
        ("生化12章-04真核生物RNA的合成②（78分钟）.mp4", "biochemistry"),
        ("生化13章-01蛋白质合成体系①（44分钟）.mp4", "biochemistry"),
        ("生化13章-02蛋白质合成体系②（57分钟）.mp4", "biochemistry"),
        ("生化13章-03氨基酸与tRNA的链接（11分钟）.mp4", "biochemistry"),
        ("生化13章-04肽链的合成过程①（45分钟）.mp4", "biochemistry"),
        ("生化13章-05肽链的合成过程②（41分钟）.mp4", "biochemistry"),
        ("生化13章-06蛋白质合成后的加工修饰（43分钟）.mp4", "biochemistry"),
        ("生化13章-07蛋白质合成的干扰宇抑制（12分钟）.mp4", "biochemistry"),
        ("生化14章-01基因表达调控的基本概念与特点（32分钟）.mp4", "biochemistry"),
        ("生化14章-02原核基因表达调控（66分钟）.mp4", "biochemistry"),
        ("生化14章-03真核基因表达调控①（56分钟）.mp4", "biochemistry"),
        ("生化14章-04真核基因表达调控②（65分钟）.mp4", "biochemistry"),
        ("生化14章-05真核基因表达调控③（34分钟）.mp4", "biochemistry"),
        ("生化15章-01细胞信号转导概述（55分钟）.mp4", "biochemistry"),
        ("生化15章-02细胞内信号转导①（66分钟）.mp4", "biochemistry"),
        ("生化15章-03细胞内信号转导②（37分钟）.mp4", "biochemistry"),
        ("生化15章-04细胞信号转导异常与疾病的关系（4分钟）.mp4", "biochemistry"),
        ("生化16章-01血浆蛋白质（35分钟）.mp4", "biochemistry"),
        ("生化16章-02血红素的合成（23分钟）.mp4", "biochemistry"),
        ("生化16章-03红细胞的代谢（27分钟）.mp4", "biochemistry"),
        ("生化16章-04肝在物质代谢中的作用（19分钟）.mp4", "biochemistry"),
        ("生化16章-05肝的生物转化作用（20分钟）.mp4", "biochemistry"),
        ("生化16章-06胆汁酸的代谢（46分钟）.mp4", "biochemistry"),
        ("生化16章-07胆色素的代谢与黄疸（49分钟）.mp4", "biochemistry"),
        ("生化17章-01脂溶性维生素（10分钟）.mp4", "biochemistry"),
        ("生化17章-02水溶性维生素（9分钟）.mp4", "biochemistry"),
        ("生化18章-01癌基因①（43分钟）.mp4", "biochemistry"),
        ("生化18章-02癌基因②（33分钟）.mp4", "biochemistry"),
        ("生化18章-03抑癌基因（48分钟）.mp4", "biochemistry"),
        ("生化18章-04基因重组与重组DNA技术①（81分钟）.mp4", "biochemistry"),
        ("生化18章-05基因重组与重组DNA技术②（48分钟）.mp4", "biochemistry"),
        ("生化18章-06常用分子生物学技术的原理及其应用（61分钟）.mp4", "biochemistry"),
        ("生化18章-07基因诊断和基因治疗（9分钟）.mp4", "biochemistry"),
        ("生化18章-08基因组学（3分钟）.mp4", "biochemistry"),
        
        # ===== 诊断学 =====
        ("诊断01章-01发热（9分钟）.mp4", "diagnostics"),
        ("诊断01章-02水肿（11分钟）.mp4", "diagnostics"),
        ("诊断01章-03咳嗽与咳痰（3分钟）.mp4", "diagnostics"),
        ("诊断01章-04咯血（4分钟）.mp4", "diagnostics"),
        ("诊断01章-05胸痛（3分钟）.mp4", "diagnostics"),
        ("诊断01章-06呼吸困难（21分钟）.mp4", "diagnostics"),
        ("诊断01章-07呕血（3分钟）.mp4", "diagnostics"),
        ("诊断01章-08便血（2分钟）.mp4", "diagnostics"),
        ("诊断01章-09腹痛（8分钟）.mp4", "diagnostics"),
        ("诊断01章-10黄疸（35分钟）.mp4", "diagnostics"),
        ("诊断01章-11血尿（6分钟）.mp4", "diagnostics"),
        ("诊断01章-12意识障碍（3分钟）.mp4", "diagnostics"),
        ("诊断02章-01全身状态检查（13分钟）.mp4", "diagnostics"),
        ("诊断02章-02皮肤（7分钟）.mp4", "diagnostics"),
        ("诊断02章-03淋巴结（1分钟）.mp4", "diagnostics"),
        ("诊断03章-01头部检查（5分钟）.mp4", "diagnostics"),
        ("诊断03章-02颈部检查（7分钟）.mp4", "diagnostics"),
        ("诊断04章-01胸部的体表标志（4分钟）.mp4", "diagnostics"),
        ("诊断04章-02胸壁、胸廓与乳房（7分钟）.mp4", "diagnostics"),
        ("诊断04章-03肺和胸膜（32分钟）.mp4", "diagnostics"),
        ("诊断04章-04呼吸系统常见疾病的主要症状和体征（13分钟）.mp4", "diagnostics"),
        ("诊断04章-05心脏检查①（47分钟）.mp4", "diagnostics"),
        ("诊断04章-06心脏检查②（62分钟）.mp4", "diagnostics"),
        ("诊断04章-07血管检查（13分钟）.mp4", "diagnostics"),
        ("诊断04章-08循环系统常见疾病的主要症状和体征（15分钟）.mp4", "diagnostics"),
        ("诊断05章-01腹部体表标志及分区（6分钟）.mp4", "diagnostics"),
        ("诊断05章-02腹部视诊（10分钟）.mp4", "diagnostics"),
        ("诊断05章-03腹部触诊（31分钟）.mp4", "diagnostics"),
        ("诊断05章-04腹部叩诊（10分钟）.mp4", "diagnostics"),
        ("诊断05章-05腹部听诊（2分钟）.mp4", "diagnostics"),
        ("诊断05章-06腹部常见病变的主要症状和体征（14分钟）.mp4", "diagnostics"),
        ("诊断06章-01脊柱检查（5分钟）.mp4", "diagnostics"),
        ("诊断06章-02四肢与关节检查（6分钟）.mp4", "diagnostics"),
        ("诊断07章-01脑神经检查（5分钟）.mp4", "diagnostics"),
        ("诊断07章-02运动功能检查（5分钟）.mp4", "diagnostics"),
        ("诊断07章-03感觉功能检查（1分钟）.mp4", "diagnostics"),
        ("诊断07章-04神经反射检查（3分钟）.mp4", "diagnostics"),
        ("诊断07章-05自主神经功能检查（1分钟）.mp4", "diagnostics"),
        ("诊断08章-01血液常规检查（13分钟）.mp4", "diagnostics"),
        ("诊断08章-02骨髓细胞学检查（5分钟）.mp4", "diagnostics"),
        ("诊断09章-01尿液检查（13分钟）.mp4", "diagnostics"),
        ("诊断09章-02粪便检测（2分钟）.mp4", "diagnostics"),
        ("诊断09章-03痰液检测（1分钟）.mp4", "diagnostics"),
        ("诊断09章-04脑脊液检测（7分钟）.mp4", "diagnostics"),
        ("诊断10章-01肾功能检查（7分钟）.mp4", "diagnostics"),
        ("诊断10章-02肝脏病常用实验室检测（2分钟）.mp4", "diagnostics"),
        ("诊断11章-01酸碱平衡失调的判断（19分钟）.mp4", "diagnostics"),
        ("诊断11章-02肺功能检查（1分钟）.mp4", "diagnostics"),
        ("诊断12章-器械检查（1分钟）.mp4", "diagnostics"),
        ("诊断13章-常用临床操作（1分钟）.mp4", "diagnostics"),
        
        # ===== 医学人文 =====
        ("医学人文-01医学职业素养（53分钟）.mp4", "medical_humanities"),
        ("医学人文-02医患关系与医患沟通（71分钟）.mp4", "medical_humanities"),
        ("医学人文-03临床伦理（55分钟）.mp4", "medical_humanities"),
        ("医学人文-04中华人民共和国医师法（29分钟）.mp4", "medical_humanities"),
        ("医学人文-05中华人民共和国民法典（18分钟）.mp4", "medical_humanities"),
        ("医学人文-06中华人民共和国药品管理法（4分钟）.mp4", "medical_humanities"),
        ("医学人文-07医疗纠纷预防和处理条例（10分钟）.mp4", "medical_humanities"),
        ("医学人文-08医疗事故处理条例（11分钟）.mp4", "medical_humanities"),
    ]
    
    # 解析所有视频
    parsed_data = []
    for filename, subject_key in all_paths:
        data = parse_filename(filename, subject_key)
        if data:
            parsed_data.append(data)
    
    # 按章节分组
    chapters = {}
    concepts = []
    
    for data in parsed_data:
        chapter_id = data["chapter_id"]
        
        if chapter_id not in chapters:
            chapters[chapter_id] = {
                "id": chapter_id,
                "book": data["subject_name"],
                "edition": "贺银成2027",
                "chapter_number": data["chapter_number"],
                "chapter_title": f"第{data['chapter_number']}章",
                "content_summary": f"贺银成西综考点精讲 - {data['subject_name']}第{data['chapter_number']}章",
                "concepts": [],
                "first_uploaded": date.today().isoformat(),
                "last_reviewed": None
            }
        
        concept = {
            "id": data["concept_id"],
            "name": data["title"],
            "sub_chapter": data["sub_chapter"]
        }
        chapters[chapter_id]["concepts"].append(concept)
        
        concepts.append({
            "concept_id": data["concept_id"],
            "chapter_id": chapter_id,
            "name": data["title"],
            "retention": 0.0,
            "understanding": 0.0,
            "application": 0.0,
            "next_review": date.today().isoformat()
        })
    
    # 插入数据库
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    chapter_count = 0
    concept_count = 0
    
    for chapter_id, chapter in chapters.items():
        try:
            cursor.execute("""
                INSERT OR IGNORE INTO chapters 
                (id, book, edition, chapter_number, chapter_title, content_summary, concepts, first_uploaded)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                chapter["id"], chapter["book"], chapter["edition"],
                chapter["chapter_number"], chapter["chapter_title"],
                chapter["content_summary"], json.dumps(chapter["concepts"], ensure_ascii=False),
                chapter["first_uploaded"]
            ))
            if cursor.rowcount > 0:
                chapter_count += 1
        except Exception as e:
            print(f"❌ 章节插入失败 {chapter_id}: {e}")
    
    for concept in concepts:
        try:
            cursor.execute("""
                INSERT OR IGNORE INTO concept_mastery 
                (concept_id, chapter_id, name, retention, understanding, application, next_review)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            """, (
                concept["concept_id"], concept["chapter_id"], concept["name"],
                concept["retention"], concept["understanding"], concept["application"],
                concept["next_review"]
            ))
            if cursor.rowcount > 0:
                concept_count += 1
        except Exception as e:
            print(f"❌ 知识点插入失败 {concept['concept_id']}: {e}")
    
    conn.commit()
    conn.close()
    
    return chapters, concepts, chapter_count, concept_count


def parse_filename(filename: str, subject_key: str) -> dict:
    """从文件名解析视频信息"""
    subject_name = SUBJECT_NAME.get(subject_key, subject_key)
    
    # 匹配：科目+章号-节号+标题
    # 如：病理01章-01细胞和组织的适应（63分钟）.mp4
    pattern = r'(?:病理|内科|外科|生化|诊断|生理|人文|医学人文)(\d+)章[-_](\d+)([^（(]+)'
    match = re.search(pattern, filename)
    
    if match:
        chapter_num = match.group(1)
        sub_chapter = match.group(2)
        title_raw = match.group(3).strip()
        
        # 清理标题
        title = re.sub(r'[（(]\d+分钟[）)]', '', title_raw).strip()
        title = re.sub(r'\.mp4$', '', title)
        
        chapter_id = f"{subject_key}_ch{chapter_num}"
        
        # 知识点ID安全化
        concept_id_safe = title.replace('（', '_').replace('）', '_').replace('(', '_').replace(')', '_')
        concept_id_safe = concept_id_safe.replace(' ', '_').replace('、', '_').replace('/', '_')
        concept_id_safe = re.sub(r'[^\w_]', '', concept_id_safe)
        concept_id = f"{chapter_id}_{sub_chapter}_{concept_id_safe[:40]}"
        
        return {
            "subject_key": subject_key,
            "subject_name": subject_name,
            "chapter_number": chapter_num,
            "sub_chapter": sub_chapter,
            "chapter_id": chapter_id,
            "concept_id": concept_id,
            "title": title
        }
    
    # 医学人文特殊处理
    if "医学人文" in filename:
        pattern2 = r'医学人文-(\d+)([^（(]+)'
        match2 = re.search(pattern2, filename)
        if match2:
            sub_chapter = match2.group(1)
            title_raw = match2.group(2).strip()
            title = re.sub(r'[（(]\d+分钟[）)]', '', title_raw).strip()
            chapter_id = "medical_humanities_ch01"
            concept_id_safe = re.sub(r'[^\w_]', '', title.replace(' ', '_'))
            concept_id = f"{chapter_id}_{sub_chapter}_{concept_id_safe[:40]}"
            return {
                "subject_key": subject_key,
                "subject_name": subject_name,
                "chapter_number": "01",
                "sub_chapter": sub_chapter,
                "chapter_id": chapter_id,
                "concept_id": concept_id,
                "title": title
            }
    
    return None


def print_stats(chapters, concepts, chapter_count, concept_count):
    """打印统计信息"""
    print("\n" + "="*70)
    print("📚 贺银成西综课程导入完成")
    print("="*70)
    
    # 按科目统计
    subject_stats = {}
    for chapter_id, chapter in chapters.items():
        subject = chapter["book"]
        if subject not in subject_stats:
            subject_stats[subject] = {"chapters": 0, "concepts": 0}
        subject_stats[subject]["chapters"] += 1
    
    for concept in concepts:
        chapter_id = concept["chapter_id"]
        if chapter_id in chapters:
            subject = chapters[chapter_id]["book"]
            subject_stats[subject]["concepts"] += 1
    
    print("\n📊 各科目统计：")
    print("-"*50)
    for subject, stats in sorted(subject_stats.items()):
        print(f"  {subject:12s} | {stats['chapters']:2d} 章 | {stats['concepts']:3d} 个知识点")
    
    print("-"*50)
    print(f"  {'总计':12s} | {len(chapters):2d} 章 | {len(concepts):3d} 个知识点")
    print("="*70)
    print(f"\n✅ 新导入: {chapter_count} 个章节, {concept_count} 个知识点")
    print(f"💡 访问 http://localhost:8000/ 查看学习系统")


def main():
    print("🚀 开始导入全部贺银成西综课程...")
    init_database()
    
    chapters, concepts, chapter_count, concept_count = import_all_courses()
    print_stats(chapters, concepts, chapter_count, concept_count)


if __name__ == "__main__":
    main()
