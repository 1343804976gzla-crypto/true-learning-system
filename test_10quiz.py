import urllib.request
import json
import time

BASE = "http://localhost:8000"

print("=" * 60)
print("10道题批量测验测试 (DeepSeek)")
print("=" * 60)

# 1. 生成试卷
print("\n1. 生成10道题试卷...")
start = time.time()
try:
    req = urllib.request.Request(
        BASE + "/api/quiz/batch/generate/pathology_ch01",
        data=json.dumps({}).encode('utf-8'),
        headers={'Content-Type': 'application/json'},
        method='POST'
    )
    resp = urllib.request.urlopen(req, timeout=180)
    data = json.loads(resp.read().decode('utf-8'))
    duration = time.time() - start
    
    session_id = data['session_id']
    questions = data['questions']
    summary = data['summary']
    
    print(f"✅ 生成成功! 耗时: {duration:.1f}s")
    print(f"   Session ID: {session_id[:20]}...")
    print(f"   总题数: {summary['total']}")
    print(f"   题型分布: A1={summary['by_type'].get('A1',0)}, A2={summary['by_type'].get('A2',0)}, A3={summary['by_type'].get('A3',0)}, X={summary['by_type'].get('X',0)}")
    
    # 显示前3题
    print("\n2. 题目预览 (前3题):")
    for i, q in enumerate(questions[:3], 1):
        print(f"\n第{i}题 [{q['type']}] {q['concept_name']}")
        print(f"Q: {q['question'][:80]}...")
        print(f"   A. {q['options']['A'][:40]}...")
        print(f"   B. {q['options']['B'][:40]}...")
        if q['type'] == 'X':
            print(f"   (多选题 - 可能多个正确答案)")
    
    # 模拟答题
    print("\n3. 模拟答题并提交...")
    answers = ["A", "B", "C", "D", "E", "AB", "AC", "AD", "AE", "BC"]
    
    start = time.time()
    req = urllib.request.Request(
        BASE + f"/api/quiz/batch/submit/{session_id}",
        data=json.dumps(answers).encode('utf-8'),
        headers={'Content-Type': 'application/json'},
        method='POST'
    )
    resp = urllib.request.urlopen(req, timeout=30)
    result = json.loads(resp.read().decode('utf-8'))
    duration = time.time() - start
    
    print(f"✅ 提交成功! 耗时: {duration:.2f}s")
    print(f"\n4. 成绩:")
    print(f"   得分: {result['score']}分")
    print(f"   正确: {result['correct_count']}/{result['total']}")
    print(f"   正确率: {result['correct_count']*100//result['total']}%")
    
    if result.get('weak_concepts'):
        print(f"\n   薄弱知识点: {', '.join(result['weak_concepts'][:3])}")
    
    # 显示第1题解析
    print("\n5. 答案解析 (第1题):")
    d = result['details'][0]
    q = questions[0]
    print(f"   你的答案: {d['user_answer']}")
    print(f"   正确答案: {d['correct_answer']}")
    print(f"   结果: {'✅ 正确' if d['is_correct'] else '❌ 错误'}")
    print(f"   解析: {d['explanation'][:100]}...")
    
except Exception as e:
    print(f"❌ 错误: {e}")
    import traceback
    traceback.print_exc()

print("\n" + "=" * 60)
print("测试完成")
print("=" * 60)
