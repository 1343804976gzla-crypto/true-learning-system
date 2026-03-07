import requests

BASE = "http://localhost:8000"

def run_basic_smoke():
    print("=" * 60)
    print("基础功能测试")
    print("=" * 60)

    # 健康检查
    r = requests.get(BASE + "/health", timeout=5)
    data = r.json()
    print("1. Health:", data.get("status"))

    # 页面测试
    pages = ["/", "/upload", "/history", "/wrong-answers", "/graph"]
    for i, path in enumerate(pages, 2):
        r = requests.get(BASE + path, timeout=5)
        print(str(i) + ". " + path + ":", r.status_code)

    # API测试
    r = requests.get(BASE + "/api/chapters", timeout=5)
    chapters_payload = r.json()
    if isinstance(chapters_payload, list):
        chapters = chapters_payload
    else:
        chapters = chapters_payload.get("chapters", [])
    print(str(len(pages) + 2) + ". Chapters API:", len(chapters), "chapters")

    r = requests.get(BASE + "/api/stats", timeout=5)
    stats = r.json()
    print(str(len(pages) + 3) + ". Stats API:", stats.get("total_concepts", 0), "concepts")

    print("=" * 60)
    print("基础测试完成！")


if __name__ == "__main__":
    run_basic_smoke()
