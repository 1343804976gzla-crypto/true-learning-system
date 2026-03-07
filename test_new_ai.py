import sys
sys.path.insert(0, r'C:\Users\35456\true-learning-system')

import asyncio
from services.ai_client import get_ai_client

async def test():
    print("Testing new AI API...")
    print("API: https://api.qingyuntop.top/v1")
    print("Model: gemini-3-flash-preview")
    print()
    
    try:
        client = get_ai_client()
        
        # Test simple generation
        result = await client.generate_content(
            "What is 2+2? Answer in one word.",
            max_tokens=50
        )
        print(f"Test result: {result}")
        print()
        print("✅ New AI API is working!")
        
    except Exception as e:
        print(f"❌ Error: {e}")
        import traceback
        traceback.print_exc()

asyncio.run(test())
