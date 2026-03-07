import asyncio
from services.content_parser import ContentParser

async def test():
    parser = ContentParser()
    content = '''病理学第一章：细胞和组织的适应与损伤。主要讲解萎缩、肥大、增生、化生四种适应形式，以及细胞损伤的原因和机制。'''
    try:
        result = await asyncio.wait_for(parser.parse_content(content), timeout=25)
        print('=== 解析结果 ===')
        print('Book:', result.get('book'))
        print('Chapter:', result.get('chapter_title'))
        print('Concepts:', len(result.get('concepts', [])))
        for c in result.get('concepts', [])[:3]:
            print('  -', c.get('name'), f"({c.get('importance')})")
    except asyncio.TimeoutError:
        print('ERROR: 解析超时')
    except Exception as e:
        print('ERROR:', e)
        import traceback
        traceback.print_exc()

asyncio.run(test())
