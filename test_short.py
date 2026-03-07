from services.content_parser import ContentParser
import asyncio

async def test():
    parser = ContentParser()
    content = '小肠内物质的吸收及其机制。主要讲解吸收部位和吸收途径，包括水、钠离子、铁、钙的吸收机制。'
    print('Testing with short content...')
    result = await parser.parse_content(content)
    print('Book:', result.get('book'))
    print('Chapter:', result.get('chapter_title'))
    print('Concepts:', len(result.get('concepts', [])))

asyncio.run(test())
