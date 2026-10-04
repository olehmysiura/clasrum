"""Чисті функції для аналізу тексту робіт: схожість між роботами, спроби маніпуляції,
псевдоніми студентів (щоб ПІБ не потрапляли в логи і до моделі)."""
from __future__ import annotations

import hashlib
import re

_WORD = re.compile(r"[\w']+", re.U)

# Фрази, якими робота може намагатися керувати перевіркою. Вміст роботи — це дані,
# а не інструкції: збіг лише позначається у звіті для викладача.
_INJECTION_PATTERNS = [
    r"постав\w*\s+(мені\s+)?(максимальн|найвищ|відмінн|5\b|12\b|100\b)",
    r"оцін\w*\s+(цю\s+)?робот\w*\s+(на\s+)?(максимум|відмінно|5\b|12\b|100\b)",
    r"ігнору\w*\s+(всі\s+|усі\s+)?(попередн|інструкці|критері)",
    r"ти\s+(—|-)?\s*(штучний\s+інтелект|модель|ші|chatgpt|claude)",
    r"(як|для)\s+(мовн\w+\s+)?модел\w*\s*[:,]",
    r"ignore\s+(all\s+)?(previous|prior|above)\s+(instructions|prompts)",
    r"(give|assign)\s+(this|me)\s+(a\s+)?(full|max(imum)?|perfect|top)\s+(score|grade|marks)",
    r"you\s+are\s+(an?\s+)?(ai|language\s+model|assistant|grader)",
    r"system\s*prompt",
]
_INJECTION_RE = [re.compile(p, re.I | re.U) for p in _INJECTION_PATTERNS]


def find_injection_attempts(text: str, context: int = 60) -> list[str]:
    """Повертає уривки тексту, схожі на спробу дати інструкції перевіряльнику."""
    hits = []
    for rx in _INJECTION_RE:
        for m in rx.finditer(text):
            a, b = max(0, m.start() - context), min(len(text), m.end() + context)
            hits.append(text[a:b].replace("\n", " ").strip())
    return hits


def words(text: str) -> list[str]:
    return [w.lower() for w in _WORD.findall(text)]


def shingles(text: str, n: int = 5) -> set[str]:
    w = words(text)
    if len(w) < n:
        return {" ".join(w)} if w else set()
    return {" ".join(w[i:i + n]) for i in range(len(w) - n + 1)}


def jaccard(a: set, b: set) -> float:
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def longest_common_fragment(a: str, b: str, n: int = 8) -> str:
    """Найдовший спільний фрагмент (у словах, не коротший за n слів) — як приклад збігу для звіту."""
    wa, wb = words(a), words(b)
    if len(wa) < n or len(wb) < n:
        return ""
    index: dict[str, list[int]] = {}
    for j in range(len(wb) - n + 1):
        index.setdefault(" ".join(wb[j:j + n]), []).append(j)
    best = (0, 0)  # (довжина, початок у wa)
    i = 0
    while i <= len(wa) - n:
        js = index.get(" ".join(wa[i:i + n]))
        if js:
            for j in js:
                k = n
                while i + k < len(wa) and j + k < len(wb) and wa[i + k] == wb[j + k]:
                    k += 1
                if k > best[0]:
                    best = (k, i)
        i += 1
    if not best[0]:
        return ""
    frag = wa[best[1]:best[1] + best[0]]
    return " ".join(frag[:40]) + (" …" if len(frag) > 40 else "")


def similarity_pairs(texts: dict[str, str], threshold: float = 0.15) -> list[dict]:
    """Пари робіт з високою схожістю (Jaccard на 5-словних шинглах).

    texts: {packet_id: text}. Повертає [{"a", "b", "score", "example"}], від найбільшої схожості.
    """
    sh = {k: shingles(v) for k, v in texts.items()}
    ids = sorted(texts)
    out = []
    for x in range(len(ids)):
        for y in range(x + 1, len(ids)):
            a, b = ids[x], ids[y]
            s = jaccard(sh[a], sh[b])
            if s >= threshold:
                out.append({"a": a, "b": b, "score": round(s, 3),
                            "example": longest_common_fragment(texts[a], texts[b])})
    return sorted(out, key=lambda d: -d["score"])


def pseudonym(user_id: str, salt: str) -> str:
    """Стабільний псевдонім студента для логів і для моделі: «Студент-3fa9c1»."""
    return "Студент-" + hashlib.sha256(f"{salt}:{user_id}".encode()).hexdigest()[:6]


def style_stats(text: str) -> dict:
    """Прості стильові показники для порівняння з попередніми роботами студента."""
    w = words(text)
    sentences = [s for s in re.split(r"[.!?…]+", text) if s.strip()]
    return {
        "words": len(w),
        "avg_sentence_words": round(len(w) / len(sentences), 1) if sentences else 0,
        "avg_word_len": round(sum(map(len, w)) / len(w), 2) if w else 0,
        "unique_ratio": round(len(set(w)) / len(w), 3) if w else 0,
    }
