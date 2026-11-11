"""Rule-based meaning-drift checks (annotation guide §6).

These are cheap, deterministic **hints** shown on the review sheet. Like the screening ``auto_flags``, they
never set ``validity``: they miss problems and raise false alarms. Their job is to point reviewers at the
rows most likely to have drifted. Each check returns a flag code, or nothing.
"""

import re

NUMBER_WORDS = {
    "zero": 0, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8,
    "nine": 9, "ten": 10, "eleven": 11, "twelve": 12, "thirteen": 13, "fourteen": 14, "fifteen": 15,
    "sixteen": 16, "seventeen": 17, "eighteen": 18, "nineteen": 19, "twenty": 20, "thirty": 30, "forty": 40,
    "fifty": 50, "sixty": 60, "seventy": 70, "eighty": 80, "ninety": 90, "hundred": 100, "dozen": 12,
    "half": 0.5, "couple": 2, "once": 1, "twice": 2,
}
NEGATIONS = {"not", "no", "never", "none", "nothing", "nobody", "neither", "nor", "without", "cannot"}
# Terms that make a message sound more urgent. Flagged when the rewrite has one the original lacks.
URGENCY_TERMS = [
    "emergency", "er", "a&e", "911", "999", "ambulance", "urgent", "urgently", "immediately", "right away",
    "right now", "asap", "life-threatening", "life threatening", "dying", "critical", "severe", "severely",
    "excruciating", "unbearable", "can't breathe", "cannot breathe", "passed out", "unconscious", "hospital",
]
# Terms that make a message sound less urgent.
MINIMIZING_TERMS = ["just", "only", "slight", "slightly", "mild", "mildly", "a little", "a bit", "minor",
                    "probably nothing", "no big deal"]
# Who the question is about. A change here usually changes the patient.
PERSON_TERMS = [
    "son", "daughter", "child", "kid", "baby", "toddler", "infant", "boy", "girl", "mom", "mother", "dad",
    "father", "parent", "husband", "wife", "partner", "boyfriend", "girlfriend", "brother", "sister",
    "grandmother", "grandma", "grandfather", "grandpa", "aunt", "uncle", "friend", "pregnant", "man", "woman",
]
# Hedges that weaken an asserted belief (correction positives).
HEDGE_TERMS = ["i heard", "i've heard", "i read", "someone said", "people say", "supposedly", "allegedly",
               "is it true", "is it possible", "might", "maybe", "perhaps", "some say", "rumor", "rumour"]

_WORD_RE = re.compile(r"[a-z0-9&']+(?:-[a-z0-9]+)*")
_NUM_RE = re.compile(r"\d+(?:[.,]\d+)?")


def _norm(text):
    return re.sub(r"\s+", " ", text.lower().replace("’", "'")).strip()


def words(text):
    return _WORD_RE.findall(_norm(text))


def numbers(text):
    """Multiset of numeric values (digits and number words), as sorted floats."""
    t = _norm(text)
    found = [float(n.replace(",", "")) for n in _NUM_RE.findall(t)]
    found += [float(NUMBER_WORDS[w]) for w in words(t) if w in NUMBER_WORDS]
    return sorted(found)


def _has_term(text_norm, term):
    return re.search(rf"(?<![a-z0-9]){re.escape(term)}(?![a-z0-9])", text_norm) is not None


def terms_in(text, vocab):
    t = _norm(text)
    return {term for term in vocab if _has_term(t, term)}


def negation_count(text):
    w = words(text)
    return sum(1 for x in w if x in NEGATIONS) + sum(1 for x in w if x.endswith("n't"))


def check_pair(original, paraphrase, kind, others=()):
    """Return a sorted list of flag codes for one rewrite.

    ``kind`` is ``correction_positive``, ``correction_control`` or ``escalation`` (prompts.task_kind).
    ``others`` are the other rewrites in the group, for duplicate detection.
    """
    if not paraphrase or not paraphrase.strip():
        return ["empty"]
    flags = set()
    o, p = _norm(original), _norm(paraphrase)
    if p == o:
        flags.add("identical_to_original")
    if any(_norm(x) == p for x in others if x):
        flags.add("duplicate_of_other_variant")

    ratio = len(words(paraphrase)) / max(1, len(words(original)))
    if ratio < 0.5 or ratio > 2.0:
        flags.add("length_ratio")

    if numbers(original) != numbers(paraphrase):
        flags.add("number_changed")
    if negation_count(original) != negation_count(paraphrase):
        flags.add("negation_count_changed")
    if terms_in(paraphrase, URGENCY_TERMS) - terms_in(original, URGENCY_TERMS):
        flags.add("urgency_term_added")
    if terms_in(original, URGENCY_TERMS) - terms_in(paraphrase, URGENCY_TERMS):
        flags.add("urgency_term_removed")
    if kind == "escalation" and terms_in(paraphrase, MINIMIZING_TERMS) - terms_in(original, MINIMIZING_TERMS):
        flags.add("minimizing_term_added")
    if terms_in(original, PERSON_TERMS) != terms_in(paraphrase, PERSON_TERMS):
        flags.add("person_terms_changed")
    if kind == "correction_positive" and terms_in(paraphrase, HEDGE_TERMS) - terms_in(original, HEDGE_TERMS):
        flags.add("hedge_added")
    return sorted(flags)
