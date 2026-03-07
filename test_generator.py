import sys
sys.path.insert(0, r'C:\Users\35456\true-learning-system')

import asyncio
from services.concurrent_quiz import get_concurrent_generator

async def test():
    print("Testing concurrent quiz generator...")
    
    generator = get_concurrent_generator()
    
    # Test with 2 concepts first
    concept_names = ["神经递质和受体", "突触传递"]
    
    print(f"Generating quizzes for: {concept_names}")
    print("This may take 20-40 seconds...")
    
    try:
        quizzes = await generator.generate_quiz_batch(concept_names)
        print(f"Generated {len(quizzes)} quizzes")
        
        for i, quiz in enumerate(quizzes):
            print(f"\nQ{i+1}: {quiz.get('question', 'N/A')[:50]}...")
            print(f"   Answer: {quiz.get('correct_answer')}")
            
    except Exception as e:
        print(f"Error: {e}")
        import traceback
        traceback.print_exc()

asyncio.run(test())
