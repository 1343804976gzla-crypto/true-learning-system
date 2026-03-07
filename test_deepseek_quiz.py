import asyncio
from services.quiz_service_v2 import QuizService

async def run_demo():
    service = QuizService()
    concepts = [
        {'id': 'pathology_ch01_01', 'name': '萎缩'},
        {'id': 'pathology_ch01_02', 'name': '肥大'},
        {'id': 'pathology_ch01_03', 'name': '增生'},
    ]
    print('Generating questions with DeepSeek...')
    result = await service.generate_exam_paper('pathology_ch01', concepts, num_questions=3)
    print('Total:', len(result.get('questions', [])))
    for q in result.get('questions', []):
        print(f"Q{q.get('id')}: {q.get('question', '')[:40]}...")

if __name__ == "__main__":
    asyncio.run(run_demo())
