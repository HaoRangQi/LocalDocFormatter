from __future__ import annotations

from pathlib import Path
import csv
import json


BUILTIN_LEXICON = [
    ("open ai", "OpenAI"),
    ("欧喷AI", "OpenAI"),
    ("在见", "再见"),
    ("帐号", "账号"),
    ("登陆", "登录"),
    ("做用", "作用"),
    ("因该", "应该"),
]

SYSTEM_PROMPT = """你是中文语音转写文稿的保守纠错工具。
只允许修正明显错别字、同音误转、ASR 转译错误、专有名词误识别和必要标点。
禁止润色、总结、扩写、缩写、改写表达风格、改变语气、重排段落或删除信息。
保持原有段落结构、换行、编号和说话内容。
只输出修正后的全文，不要解释，不要列修改清单。"""


def parse_user_lexicon(text: str) -> list[tuple[str, str]]:
    pairs: list[tuple[str, str]] = []
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or "=>" not in line:
            continue
        wrong, correct = line.split("=>", 1)
        wrong = wrong.strip()
        correct = correct.strip()
        if wrong and correct:
            pairs.append((wrong, correct))
    return pairs


def normalize_lexicon_entries(entries: list[dict] | None) -> list[tuple[str, str]]:
    pairs: list[tuple[str, str]] = []
    for entry in entries or []:
        if not isinstance(entry, dict):
            continue
        wrong = str(entry.get("wrong") or entry.get("key") or "").strip()
        correct = str(entry.get("correct") or entry.get("value") or "").strip()
        if wrong and correct:
            pairs.append((wrong, correct))
    return pairs


def load_lexicon_file(path: Path) -> list[tuple[str, str]]:
    suffix = path.suffix.lower()
    text = path.read_text(encoding="utf-8")
    if suffix == ".json":
        data = json.loads(text or "[]")
        if isinstance(data, dict):
            return [(str(key).strip(), str(value).strip()) for key, value in data.items() if str(key).strip() and str(value).strip()]
        if isinstance(data, list):
            return normalize_lexicon_entries(data)
        return []
    if suffix in {".csv", ".tsv"}:
        delimiter = "\t" if suffix == ".tsv" else ","
        rows = list(csv.reader(text.splitlines(), delimiter=delimiter))
        pairs: list[tuple[str, str]] = []
        for index, row in enumerate(rows):
            if len(row) < 2:
                continue
            wrong = row[0].strip()
            correct = row[1].strip()
            if index == 0 and wrong in {"错误词", "wrong", "key"} and correct in {"正确词", "correct", "value"}:
                continue
            if wrong and correct:
                pairs.append((wrong, correct))
        return pairs
    return parse_user_lexicon(text)


def build_correction_messages(
    text: str,
    user_lexicon: list[tuple[str, str]],
    system_prompt: str | None = None,
) -> list[dict[str, str]]:
    lexicon = BUILTIN_LEXICON + user_lexicon
    lexicon_text = "\n".join(f"{wrong} => {correct}" for wrong, correct in lexicon)
    user_prompt = f"""请谨慎修正下面的语音转写文稿。

常见错词和专名提示：
{lexicon_text}

    待修正文稿：
{text}"""
    return [
        {"role": "system", "content": (system_prompt or SYSTEM_PROMPT).strip() or SYSTEM_PROMPT},
        {"role": "user", "content": user_prompt},
    ]


def chunk_text(text: str, max_chars: int = 6000) -> list[str]:
    if len(text) <= max_chars:
        return [text]
    chunks: list[str] = []
    current = ""
    paragraphs = text.splitlines(keepends=True)
    for paragraph in paragraphs:
        if len(current) + len(paragraph) > max_chars and current:
            chunks.append(current)
            current = ""
        if len(paragraph) > max_chars:
            for index in range(0, len(paragraph), max_chars):
                part = paragraph[index : index + max_chars]
                if current:
                    chunks.append(current)
                    current = ""
                chunks.append(part)
        else:
            current += paragraph
    if current:
        chunks.append(current)
    return chunks


def correct_text_with_client(
    client,
    model: str,
    text: str,
    user_lexicon: list[tuple[str, str]],
    system_prompt: str | None = None,
) -> str:
    corrected_chunks = []
    for chunk in chunk_text(text):
        messages = build_correction_messages(chunk, user_lexicon, system_prompt=system_prompt)
        corrected_chunks.append(client.chat_completion(model, messages))
    return "".join(corrected_chunks)


def correct_srt_text(text: str, correct_func) -> str:
    blocks = text.split("\n\n")
    corrected_blocks = []
    for block in blocks:
        lines = block.splitlines()
        text_indexes = [
            index
            for index, line in enumerate(lines)
            if line.strip() and not line.strip().isdigit() and "-->" not in line
        ]
        if text_indexes:
            text_body = "\n".join(lines[index] for index in text_indexes)
            corrected = correct_func(text_body).splitlines()
            if len(corrected) == len(text_indexes):
                for target_index, corrected_line in zip(text_indexes, corrected):
                    lines[target_index] = corrected_line
            else:
                lines[text_indexes[0]] = "\n".join(corrected)
                for target_index in reversed(text_indexes[1:]):
                    del lines[target_index]
        corrected_blocks.append("\n".join(lines))
    return "\n\n".join(corrected_blocks)


def unique_corrected_path(source: Path) -> Path:
    candidate = source.with_name(f"{source.stem}.corrected{source.suffix}")
    index = 1
    while candidate.exists():
        candidate = source.with_name(f"{source.stem}.corrected ({index}){source.suffix}")
        index += 1
    return candidate


def is_supported_text_file(path: Path) -> bool:
    return path.suffix.lower() in {".txt", ".md", ".srt"}
