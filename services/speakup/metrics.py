"""Count transcript edits and compute word error rates for speech evaluation.
The study runner pools edits across clips using these pure functions.
Empty references with inserted words have an infinite rate, not zero error.
"""
import re
import unicodedata

_TOKEN = re.compile(r"[a-z0-9']+")


def normalize_words(text: str) -> list[str]:
    """Lowercase, strip accents and punctuation; keeps digits and apostrophes."""
    folded = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode().lower()
    return _TOKEN.findall(folded)


def word_edits(reference: str, hypothesis: str) -> dict:
    """Count substitutions, deletions and insertions from reference to hypothesis."""
    ref, hyp = normalize_words(reference), normalize_words(hypothesis)
    # Each cell holds (cost, substitutions, deletions, insertions); ties prefer fewer substitutions.
    prev = [(j, 0, 0, j) for j in range(len(hyp) + 1)]
    for i in range(1, len(ref) + 1):
        cur = [(i, 0, i, 0)]
        for j in range(1, len(hyp) + 1):
            if ref[i - 1] == hyp[j - 1]:
                cur.append(prev[j - 1])
                continue
            sub, dele, ins = prev[j - 1], prev[j], cur[j - 1]
            cur.append(min((sub[0] + 1, sub[1] + 1, sub[2], sub[3]),
                           (dele[0] + 1, dele[1], dele[2] + 1, dele[3]),
                           (ins[0] + 1, ins[1], ins[2], ins[3] + 1)))
        prev = cur
    cost, s, d, n = prev[-1]
    return {"reference_words": len(ref), "edits": cost, "substitutions": s, "deletions": d, "insertions": n}


def word_error_rate(reference: str, hypothesis: str) -> float:
    edits = word_edits(reference, hypothesis)
    if edits["reference_words"] == 0:
        return 0.0 if edits["edits"] == 0 else float("inf")
    return edits["edits"] / edits["reference_words"]


def corpus_wer(pairs) -> dict:
    """Pooled WER over (reference, hypothesis) pairs: total edits / total reference words."""
    totals = {"reference_words": 0, "edits": 0, "substitutions": 0, "deletions": 0, "insertions": 0, "utterances": 0}
    for reference, hypothesis in pairs:
        result = word_edits(reference, hypothesis)
        for key in ("reference_words", "edits", "substitutions", "deletions", "insertions"):
            totals[key] += result[key]
        totals["utterances"] += 1
    totals["wer"] = (totals["edits"] / totals["reference_words"] if totals["reference_words"]
                     else (float("inf") if totals["edits"] else 0.0))
    return totals
