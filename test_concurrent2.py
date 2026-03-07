import requests

BASE = 'http://localhost:8000'

# Test with physiology_ch10 which has 12 concepts
chapter_id = 'physiology_ch10'

print('Testing with chapter:', chapter_id)
print('Starting concurrent quiz generation...')

r = requests.post(BASE + '/api/quiz-v2/start/' + chapter_id, timeout=90)
print('Status:', r.status_code)

if r.status_code == 200:
    data = r.json()
    print('Session ID:', data.get('session_id'))
    print('Questions:', len(data.get('questions', [])))
    print('Method:', data.get('generation_method'))
    
    if data.get('questions'):
        q = data['questions'][0]
        print('Q1:', q.get('question', '')[:50])
        print('Concept:', q.get('concept_name'))
        print('OK - Concurrent generation working!')
    else:
        print('No questions generated')
else:
    print('Error:', r.status_code, r.text[:200])
