from __future__ import annotations

import argparse
import csv
import json
import re
from collections import defaultdict
from datetime import datetime
from pathlib import Path


TREE_NODE_RE = re.compile(r"^(?P<indent>(?:│   |    )*)(?:├──|└──)\s*(?P<name>.+?)\s*$")
NUMBER_PREFIX_RE = re.compile(r"^(?P<number>\d{1,3})\.\s*(?P<title>.+?)\s*$")
EXT_RE = re.compile(r"\.(mp4|pdf|docx?|pptx?|png|jpe?g)$", re.IGNORECASE)
SCREENSHOT_RE = re.compile(r"^\d{4}-\d{2}-\d{2}\s+\d{6}$")

BOOK_ALIASES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("内科学", ("内科含诊断（含部分外科）", "内科含诊断(含部分外科)", "内科含诊断+部分外科", "内科含诊断", "内科学", "内科")),
    ("外科学", ("外科学", "外科")),
    ("生理学", ("生理学", "生理")),
    ("生物化学", ("生物化学", "生化")),
    ("病理学", ("病理学", "病理")),
    ("医学人文", ("医学人文", "人文")),
)

SECTION_TYPES: tuple[tuple[str, str], ...] = (
    ("核心-真题-串联【课程】", "core_course"),
    ("思维导图【课程】", "mindmap_course"),
    ("讲义", "notes_archive"),
    ("导图", "notes_archive"),
)

MEDIA_PRIORITY = {
    "mp4": 0,
    "pdf": 1,
    "docx": 2,
    "doc": 2,
    "pptx": 3,
    "png": 4,
    "jpg": 4,
    "jpeg": 4,
    "folder": 9,
}


def detect_book(raw: str) -> tuple[str, str]:
    text = str(raw or "").strip()
    for canonical, aliases in BOOK_ALIASES:
        for alias in aliases:
            if text.startswith(alias):
                remainder = text[len(alias):].strip(" -_、，,")
                return canonical, remainder
    return "", text


def detect_section_type(raw: str) -> str:
    text = str(raw or "").strip()
    for marker, section_type in SECTION_TYPES:
        if marker in text:
            return section_type
    return ""


def normalize_title(raw: str) -> str:
    text = str(raw or "").strip()
    text = EXT_RE.sub("", text).strip()
    text = re.sub(r"\s*(?:天天师兄.*|tt师兄.*)$", "", text, flags=re.IGNORECASE).strip()
    text = re.sub(r"\s*第\d+段$", "", text)
    text = re.sub(r"\s+", " ", text)
    return text.strip(" -_")


def parse_section(node_name: str) -> dict[str, object] | None:
    numbered = NUMBER_PREFIX_RE.match(node_name)
    if not numbered:
        return None

    raw_title = numbered.group("title").strip()
    section_type = detect_section_type(raw_title)
    if not section_type:
        return None

    book, remainder = detect_book(raw_title)
    if not book:
        return None

    return {
        "section_order": int(numbered.group("number")),
        "book": book,
        "section_type": section_type,
        "raw_header": node_name,
        "section_title": normalize_title(remainder) or raw_title,
        "items": [],
    }


def detect_media_type(node_name: str) -> str:
    match = EXT_RE.search(node_name.strip())
    if not match:
        return "folder"
    return match.group(1).lower()


def parse_item(node_name: str, section: dict[str, object], depth: int, line_no: int) -> dict[str, object] | None:
    media_type = detect_media_type(node_name)
    if media_type == "folder":
        return None

    numbered = NUMBER_PREFIX_RE.match(node_name.strip())
    lesson_number = numbered.group("number") if numbered else ""
    raw_title = numbered.group("title") if numbered else node_name.strip()

    book, remainder = detect_book(raw_title)
    normalized_book = book or str(section["book"])
    normalized_title = normalize_title(remainder if book else raw_title)

    if not normalized_title or SCREENSHOT_RE.match(normalized_title):
        return None

    return {
        "line_no": line_no,
        "depth": depth,
        "book": normalized_book,
        "section_type": section["section_type"],
        "section_title": section["section_title"],
        "lesson_number": lesson_number,
        "title": normalized_title,
        "raw_name": node_name.strip(),
        "media_type": media_type,
    }


def parse_catalog(path: Path) -> dict[str, object]:
    lines = path.read_text(encoding="utf-8").splitlines()
    sections: list[dict[str, object]] = []
    active_sections: dict[int, dict[str, object]] = {}

    for line_no, line in enumerate(lines, start=1):
        match = TREE_NODE_RE.match(line)
        if not match:
            continue

        depth = len(match.group("indent")) // 4
        node_name = match.group("name").strip()
        active_sections = {d: section for d, section in active_sections.items() if d < depth}

        section = parse_section(node_name)
        if section is not None:
            section["depth"] = depth
            sections.append(section)
            active_sections[depth] = section
            continue

        parent_depths = [d for d in active_sections.keys() if d < depth]
        if not parent_depths:
            continue

        parent = active_sections[max(parent_depths)]
        item = parse_item(node_name, parent, depth, line_no)
        if item is not None:
            parent["items"].append(item)

    canonical_candidates: list[dict[str, object]] = []
    grouped_candidates: dict[tuple[str, str], list[dict[str, object]]] = defaultdict(list)
    for section in sections:
        if section["section_type"] != "core_course":
            continue
        for item in section["items"]:
            lesson_number = str(item["lesson_number"] or "").strip()
            if not lesson_number:
                continue
            grouped_candidates[(str(item["book"]), lesson_number)].append(item)

    for (book, lesson_number), items in sorted(grouped_candidates.items(), key=lambda entry: (entry[0][0], int(entry[0][1]))):
        ranked = sorted(
            items,
            key=lambda item: (
                MEDIA_PRIORITY.get(str(item["media_type"]), 9),
                len(str(item["title"])),
                str(item["title"]),
            ),
        )
        canonical_candidates.append({
            "book": book,
            "lesson_number": lesson_number,
            "normalized_title": ranked[0]["title"],
            "source_count": len(items),
            "variants": [
                {
                    "title": item["title"],
                    "media_type": item["media_type"],
                    "line_no": item["line_no"],
                }
                for item in ranked
            ],
        })

    subject_summary: dict[str, dict[str, int]] = defaultdict(lambda: {
        "sections": 0,
        "items": 0,
        "core_items": 0,
        "mindmap_items": 0,
        "notes_items": 0,
        "canonical_candidates": 0,
    })
    for section in sections:
        book = str(section["book"])
        subject_summary[book]["sections"] += 1
        subject_summary[book]["items"] += len(section["items"])
        if section["section_type"] == "core_course":
            subject_summary[book]["core_items"] += len(section["items"])
        elif section["section_type"] == "mindmap_course":
            subject_summary[book]["mindmap_items"] += len(section["items"])
        else:
            subject_summary[book]["notes_items"] += len(section["items"])

    for candidate in canonical_candidates:
        subject_summary[str(candidate["book"])]["canonical_candidates"] += 1

    return {
        "source_path": str(path),
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "section_count": len(sections),
        "sections": sections,
        "canonical_candidates": canonical_candidates,
        "subject_summary": dict(sorted(subject_summary.items())),
    }


def write_json(path: Path, payload: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def write_csv(path: Path, payload: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=[
                "book",
                "section_type",
                "section_title",
                "lesson_number",
                "title",
                "media_type",
                "line_no",
                "raw_name",
            ],
        )
        writer.writeheader()
        for section in payload["sections"]:
            for item in section["items"]:
                writer.writerow({
                    "book": item["book"],
                    "section_type": item["section_type"],
                    "section_title": item["section_title"],
                    "lesson_number": item["lesson_number"],
                    "title": item["title"],
                    "media_type": item["media_type"],
                    "line_no": item["line_no"],
                    "raw_name": item["raw_name"],
                })


def build_default_paths(project_dir: Path) -> tuple[Path, Path]:
    data_dir = project_dir / "data"
    return data_dir / "course_catalog.cleaned.json", data_dir / "course_catalog.cleaned.csv"


def main() -> None:
    project_dir = Path(__file__).resolve().parents[1]
    json_out, csv_out = build_default_paths(project_dir)

    parser = argparse.ArgumentParser()
    parser.add_argument("--input", required=True, help="Path to the exported tree txt file")
    parser.add_argument("--json-out", default=str(json_out), help="Output JSON path")
    parser.add_argument("--csv-out", default=str(csv_out), help="Output CSV path")
    args = parser.parse_args()

    source_path = Path(args.input).expanduser().resolve()
    payload = parse_catalog(source_path)
    json_path = Path(args.json_out).expanduser().resolve()
    csv_path = Path(args.csv_out).expanduser().resolve()
    write_json(json_path, payload)
    write_csv(csv_path, payload)

    print(f"[source] {source_path}")
    print(f"[json]   {json_path}")
    print(f"[csv]    {csv_path}")
    print(f"[sections] {payload['section_count']}")
    print(f"[canonical_candidates] {len(payload['canonical_candidates'])}")
    for book, summary in payload["subject_summary"].items():
        print(
            f"[book] {book}: sections={summary['sections']} items={summary['items']} "
            f"core={summary['core_items']} mindmap={summary['mindmap_items']} "
            f"notes={summary['notes_items']} canonical={summary['canonical_candidates']}"
        )


if __name__ == "__main__":
    main()
