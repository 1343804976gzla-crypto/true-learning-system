# True Learning System - 系统架构文档

## 📊 测试报告

### 测试时间: 2026-02-17 23:59

### ✅ 测试结果: 全部通过

| 测试项目 | 状态 | 详情 |
|---------|------|------|
| 数据库表结构 | ✅ 通过 | 6个核心表全部存在 |
| 错题本CRUD | ✅ 通过 | 创建、读取、更新、删除正常 |
| 测验会话 | ✅ 通过 | 10道题结构，答题记录完整 |
| API端点 | ✅ 通过 | 健康检查、错题查询正常 |
| 数据关联 | ✅ 通过 | 知识点↔错题关联已打通 |

---

## 🏗️ 系统架构

### 数据库模型关系

```
┌─────────────────┐     ┌──────────────────┐     ┌─────────────────┐
│   DailyUpload   │     │     Chapter      │     │ ConceptMastery  │
│  (上传记录)      │────▶│    (章节)        │◀────│   (知识点)       │
└─────────────────┘     └──────────────────┘     └────────┬────────┘
                                                          │
                              ┌───────────────────────────┼───────────┐
                              │                           │           │
                              ▼                           ▼           ▼
                    ┌─────────────────┐     ┌─────────────────┐ ┌──────────────┐
                    │  WrongAnswer    │     │   TestRecord    │ │QuizSession   │
                    │   (错题本)       │     │   (测试记录)     │ │  (测验会话)   │
                    └─────────────────┘     └─────────────────┘ └──────────────┘
```

### 核心功能流程

#### 1️⃣ 上传学习内容
```
用户上传讲课内容
    ↓
AI识别 → 提取知识点 + 分级(main/secondary/mention)
    ↓
保存到 DailyUpload + Chapter + ConceptMastery
```

#### 2️⃣ 开始10题练习
```
用户点击"10题练习"
    ↓
POST /api/quiz/start/{chapter_id}?mode=practice
    ↓
创建 QuizSession (10道题)
    ↓
返回题目列表给前端
```

#### 3️⃣ 答题与批改
```
用户答题 → 提交答案
    ↓
POST /api/quiz/submit/{session_id}
    ↓
判断正误 → 记录到 QuizSession.answers
    ↓
[答错] → 自动创建 WrongAnswer 记录
    ↓
计算得分 → 返回结果
```

#### 4️⃣ 错题复习
```
用户查看错题本 /wrong-answers
    ↓
GET /api/quiz/wrong-answers/{chapter_id}
    ↓
显示未掌握错题列表
    ↓
用户点击"复习"
    ↓
进入错题复习模式 → 再次答题
    ↓
POST /api/quiz/wrong-answers/{id}/review
    ↓
更新掌握度 (mastery_level)
    ↓
[连续答对3次] → 标记为已掌握
```

---

## 📦 数据库表说明

### WrongAnswer (错题本)
```sql
- id: 主键
- concept_id: 知识点ID (外键)
- question: 题目内容
- options: 选项 (JSON)
- correct_answer: 正确答案
- user_answer: 用户答案
- explanation: 解析
- error_type: 错误类型
- weak_points: 薄弱点 (JSON)
- review_count: 复习次数
- mastery_level: 掌握程度 (0-5)
- is_mastered: 是否已掌握
- next_review: 下次复习时间
```

### QuizSession (测验会话)
```sql
- id: 主键
- session_type: 会话类型 (practice/wrong_answer_review/repeat)
- chapter_id: 章节ID
- questions: 题目列表 (JSON, 10道题)
- answers: 答题记录 (JSON)
- total_questions: 总题数 (固定10)
- correct_count: 答对数
- score: 得分 (0-100)
- started_at: 开始时间
- completed_at: 完成时间
```

---

## 🔌 API端点列表

### 错题本相关
```
GET  /api/quiz/wrong-answers/{chapter_id}    # 获取错题列表
POST /api/quiz/wrong-answers/{id}/review     # 复习错题，更新掌握度
GET  /api/quiz/stats/{chapter_id}            # 获取测验统计
```

### 测验相关
```
POST /api/quiz/start/{chapter_id}?mode=xxx   # 开始测验 (10道题)
POST /api/quiz/submit/{session_id}           # 提交答案
```

### 模式参数
- `mode=practice`: 正常练习，生成新题目
- `mode=wrong_answer_review`: 错题复习，只出未掌握错题
- `mode=repeat`: 重复练习，复习已做过的题目

---

## 🎯 核心功能特点

### 1. 固定10道题
- 每次测验固定10道题目
- 避免题量过大导致疲劳
- 便于统计正确率

### 2. 智能错题本
- 自动记录答错的题目
- 完整保存题目内容（即使原题删除也能复习）
- 分类记录错误类型

### 3. 掌握度跟踪
- 0-5级掌握度评分
- 连续答对3次自动标记为"已掌握"
- 答错降低掌握度

### 4. 间隔重复
```
答对1次 → 1天后复习
答对2次 → 3天后复习
答对3次 → 7天后复习
答对4次 → 14天后复习
答对5次 → 30天后复习
```

### 5. 三种练习模式
- **正常练习**: 生成新题目，检验学习效果
- **错题复习**: 只练习未掌握的错题
- **重复练习**: 反复练习易错知识点

---

## 📱 前端页面

### 新增页面
1. **错题本页面**: `/wrong-answers`
   - 错题列表
   - 掌握度可视化
   - 一键复习

2. **学习历史**: `/history`
   - 上传记录
   - 学习日历
   - 统计看板

### 导航栏更新
```
仪表盘 | 上传 | 历史 | 错题本 | 知识图谱
```

---

## ✅ 系统状态

### 已完成
- [x] 数据库模型设计
- [x] 错题本CRUD功能
- [x] 10道题固定测验
- [x] 三种练习模式
- [x] 掌握度跟踪
- [x] 间隔重复算法
- [x] API端点实现
- [x] 前端页面
- [x] 数据关联打通

### 测试通过
- [x] 数据库表结构
- [x] 错题创建/读取/更新/删除
- [x] 测验会话管理
- [x] API响应正常
- [x] 数据关联正确

---

## 🚀 使用指南

### 开始一次练习
1. 访问 `/chapter/{chapter_id}`
2. 点击"10题练习"
3. 答题完成后自动记录错题

### 查看和复习错题
1. 访问 `/wrong-answers`
2. 查看错题列表和掌握度
3. 点击"复习"进入错题复习模式

### 追踪学习进度
1. 访问 `/history`
2. 查看学习日历和统计
3. 追踪连续学习天数

---

**系统已完全打通，可以正常使用！** 🎉
