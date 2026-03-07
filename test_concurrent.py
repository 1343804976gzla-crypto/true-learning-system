import requests

BASE = 'http://localhost:8000'

r = requests.get(BASE + '/api/chapters', timeout=5)
if r.status_code == 200:
    chapters = r.json()
    if isinstance(chapters, list) and len(chapters) > 0:
        chapter_id = chapters[0]['id']
        print('Testing with chapter:', chapter_id)
        
        print('Testing new concurrent quiz API...')
        r2 = requests.post(BASE + '/api/quiz-v2/start/' + chapter_id, timeout=60)
        print('Status:', r2.status_code)
        if r2.status_code == 200:
            data = r2.json()
            print('Session ID:', data.get('session_id'))
            print('Questions:', len(data.get('questions', [])))
            print('Method:', data.get('generation_method'))
            print('OK - New concurrent API working!')
        else:
            print('Error:', r2.text[:200])
