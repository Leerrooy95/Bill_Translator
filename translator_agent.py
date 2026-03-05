import os
import json
import sys
import re
import argparse
from datetime import datetime, timezone
from dotenv import load_dotenv
import anthropic
import textstat

# ---------------------------------------------------------------------------
# 1. Setup & Auth
# ---------------------------------------------------------------------------
load_dotenv()

DELIMITER = "===TRANSLATION_PAYLOAD_BEGINS_HERE==="
FK_TARGET_GRADE = 8.0

# Translation modes
MODE_FULL = "full"
MODE_PRESERVE_LEGAL = "preserve_legal"
MODE_JARGON_ONLY = "jargon_only"

# Directories (relative to where you run the script)
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
INPUT_DIR = os.path.join(SCRIPT_DIR, "raw_legislation")
OUTPUT_DIR = os.path.join(SCRIPT_DIR, "translated_legislation")


def get_client():
    """Create and return an Anthropic client using the API key from .env."""
    api_key = os.getenv("ANTHROPIC_API_KEY")
    if not api_key:
        print("❌ ERROR: ANTHROPIC_API_KEY not found.")
        print("   Create a .env file with your key. See .env.example for help.")
        sys.exit(1)
    return anthropic.Anthropic(api_key=api_key)


# ---------------------------------------------------------------------------
# 2. Readability Scoring
# ---------------------------------------------------------------------------
def score_readability(text):
    """Compute readability metrics for the given text using textstat."""
    return {
        "flesch_kincaid_grade": round(textstat.flesch_kincaid_grade(text), 1),
        "flesch_reading_ease": round(textstat.flesch_reading_ease(text), 1),
        "word_count": textstat.lexicon_count(text, removepunct=True),
        "sentence_count": textstat.sentence_count(text),
        "passes_act602": textstat.flesch_kincaid_grade(text) <= FK_TARGET_GRADE,
    }


def print_readability_report(label, scores):
    """Print a formatted readability report."""
    grade = scores["flesch_kincaid_grade"]
    status = "✅ PASS" if scores["passes_act602"] else "❌ FAIL"
    print(f"\n📊 {label} Readability:")
    print(f"   Flesch-Kincaid Grade Level: {grade} ({status} — target ≤ {FK_TARGET_GRADE})")
    print(f"   Flesch Reading Ease:        {scores['flesch_reading_ease']}")
    print(f"   Word Count:                 {scores['word_count']}")
    print(f"   Sentence Count:             {scores['sentence_count']}")


def score_sentences(text):
    """Score individual sentences and return a list of (sentence, fk_grade) tuples.

    Sentences shorter than 3 words are returned with a grade of 0.0 since
    the FK formula is unreliable for very short fragments.
    """
    # Split on sentence-ending punctuation followed by whitespace or end-of-string
    raw_sentences = re.split(r'(?<=[.!?])\s+', text.strip())
    results = []
    for sent in raw_sentences:
        sent = sent.strip()
        if not sent:
            continue
        word_count = textstat.lexicon_count(sent, removepunct=True)
        if word_count < 3:
            results.append((sent, 0.0))
        else:
            grade = round(textstat.flesch_kincaid_grade(sent), 1)
            results.append((sent, grade))
    return results


def identify_hard_sentences(text, threshold=12.0):
    """Return sentences that score at or above *threshold* on the FK scale.

    Each item is a (sentence, fk_grade) tuple.  Sentences below the
    threshold are considered "already easy" and excluded.
    """
    return [(s, g) for s, g in score_sentences(text) if g >= threshold]


def extract_legal_terms(text):
    """Extract capitalised legal terms, defined terms, and section references."""
    terms = set()

    # Section/Article references  (e.g. "Section 12-3-401")
    terms.update(re.findall(r"(?:Section|Article|Chapter|Title|Act)\s+[\d\-\.]+(?:\([a-z]\))?", text, re.IGNORECASE))

    # Quoted defined terms  (e.g. "ballot title")
    terms.update(re.findall(r'"([^"]{3,60})"', text))

    # ALL-CAPS phrases (often legal headings)
    terms.update(re.findall(r"\b[A-Z][A-Z\s]{4,40}\b", text))

    # Common legal phrases that should be preserved
    legal_phrases = [
        "due process", "equal protection", "habeas corpus", "ex post facto",
        "ballot title", "popular name", "constitutional amendment",
        "Attorney General", "Secretary of State", "General Assembly",
        "initiative", "referendum", "appropriation", "eminent domain",
    ]
    for phrase in legal_phrases:
        if phrase.lower() in text.lower():
            terms.add(phrase)

    return sorted(terms)


def compare_legal_terms(original_terms, translated_terms):
    """Compare legal terms between original and translation, flag missing ones."""
    original_lower = {t.lower().strip() for t in original_terms}
    translated_lower = {t.lower().strip() for t in translated_terms}

    missing = original_lower - translated_lower
    # Filter out ALL-CAPS headings (≥3 chars) that are expected to change
    missing = {t for t in missing if not (t.isupper() and len(t) >= 3)}

    return sorted(missing)


def strip_markdown(text):
    """Remove common Markdown formatting so readability scores reflect plain text."""
    # Remove headers (e.g. # Title, ## Subtitle)
    text = re.sub(r"^#{1,6}\s+", "", text, flags=re.MULTILINE)
    # Remove bold/italic markers (**, __, *, _)
    text = re.sub(r"\*{1,3}(.*?)\*{1,3}", r"\1", text)
    text = re.sub(r"_{1,3}(.*?)_{1,3}", r"\1", text)
    # Remove Markdown bullet markers at start of line (- or *)
    text = re.sub(r"^[\-\*]\s+", "", text, flags=re.MULTILINE)
    # Collapse multiple blank lines into one
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


# ---------------------------------------------------------------------------
# Post-processing: programmatic word substitutions
# ---------------------------------------------------------------------------
# Multi-word phrases first (longer matches before shorter), then single words.
# Each tuple is (pattern, replacement).  Replacements are applied case-
# insensitively on whole-word boundaries so they won't break legal terms that
# happen to contain a substring match.
_PHRASE_SUBS = [
    (r"\bclear and convincing evidence\b", "strong proof"),
    (r"\bqualified electors\b", "voters"),
    (r"\bqualified elector\b", "voter"),
    (r"\blegislative measures\b", "proposed laws"),
    (r"\blegislative measure\b", "proposed law"),
    (r"\bin the event that\b", "if"),
    (r"\bin accordance with\b", "under"),
    (r"\bwith respect to\b", "about"),
    (r"\bfor the purpose of\b", "to"),
    (r"\bin order to\b", "to"),
    (r"\bwith regard to\b", "about"),
    (r"\bon behalf of\b", "for"),
    (r"\bat such time as\b", "when"),
    (r"\bby means of\b", "by"),
    (r"\bin the absence of\b", "without"),
    (r"\bin lieu of\b", "instead of"),
    (r"\bin excess of\b", "more than"),
    (r"\bon the condition that\b", "if"),
    (r"\bsubsequent to\b", "after"),
    (r"\bpertaining to\b", "about"),
    (r"\bpursuant to\b", "under"),
    (r"\bprior to\b", "before"),
]

_WORD_SUBS = [
    (r"\bnotwithstanding\b", "despite"),
    (r"\baforementioned\b", "this"),
    (r"\bimplementation\b", "carrying out"),
    (r"\bapproximately\b", "about"),
    (r"\badditionally\b", "also"),
    (r"\bestablishment\b", "setting up"),
    (r"\bdetermination\b", "decision"),
    (r"\bauthorization\b", "approval"),
    (r"\bcertification\b", "proof"),
    (r"\bnotification\b", "notice"),
    (r"\bmodification\b", "change"),
    (r"\bcompensation\b", "pay"),
    (r"\bfurthermore\b", "also"),
    (r"\bsignificantly\b", "a lot"),
    (r"\brequirements\b", "rules"),
    (r"\bmunicipalities\b", "cities"),
    (r"\bspecifically\b", ""),
    (r"\badjudication\b", "ruling"),
    (r"\blegislature\b", "lawmakers"),
    (r"\blegislation\b", "law"),
    (r"\bprohibition\b", "ban"),
    (r"\bfundamental\b", "basic"),
    (r"\bnecessarily\b", ""),
    (r"\bsubsequently\b", "then"),
    (r"\bsubstantially\b", "mostly"),
    (r"\bindependently\b", "on its own"),
    (r"\bpredominantly\b", "mainly"),
    (r"\bconsecutive\b", "in a row"),
    (r"\bexpenditure\b", "spending"),
    (r"\bacquisition\b", "purchase"),
    (r"\bdesignation\b", "naming"),
    (r"\bdistribution\b", "sharing"),
    (r"\bconsolidation\b", "merging"),
    (r"\bhereinafter\b", "from now on"),
    (r"\bthereupon\b", "then"),
    (r"\binasmuch as\b", "since"),
    (r"\bobligation\b", "duty"),
    (r"\bmunicipal\b", "city"),
    (r"\bpromulgate\b", "issue"),
    (r"\bconstitute\b", "make up"),
    (r"\bcommence\b", "start"),
    (r"\bterminate\b", "end"),
    (r"\butilize\b", "use"),
    (r"\bwhereas\b", "since"),
    (r"\benacted\b", "passed"),
    (r"\btherefore\b", "so"),
    (r"\bhowever\b", "but"),
    (r"\bimmediately\b", "at once"),
    (r"\bregarding\b", "about"),
    (r"\bshall\b", "must"),
    (r"\bministerial\b", "basic"),
    (r"\bindividuals\b", "people"),
    (r"\bindividual\b", "person"),
    (r"\bnecessary\b", "needed"),
    (r"\btemporary\b", "short-term"),
    (r"\badditional\b", "more"),
    (r"\bauthority\b", "power"),
    (r"\bviolation\b", "breach"),
    (r"\bpreservation\b", "saving"),
    (r"\baffirmative\b", "yes"),
    (r"\bprocedures\b", "steps"),
    (r"\bprocedure\b", "step"),
    (r"\benumerated\b", "listed"),
    (r"\bdeclarations\b", "statements"),
    (r"\bdeclaration\b", "statement"),
    (r"\belectors\b", "voters"),
    (r"\belector\b", "voter"),
    (r"\bcanvassers\b", "workers"),
    (r"\bcanvasser\b", "worker"),
    (r"\bconveyance\b", "transfer"),
    (r"\baffidavit\b", "sworn statement"),
    (r"\bcriminals\b", "crooks"),
    (r"\bdefraud\b", "cheat"),
    (r"\bclarifies\b", "makes clear"),
    (r"\bperjury\b", "false oath"),
]


def apply_word_substitutions(text):
    """Apply programmatic word substitutions to lower syllable counts.

    This runs after each Claude iteration as a safety net to catch any
    high-syllable words the model missed.  Multi-word phrases are replaced
    first to avoid partial matches.
    """
    for pattern, replacement in _PHRASE_SUBS:
        text = re.sub(pattern, replacement, text, flags=re.IGNORECASE)
    for pattern, replacement in _WORD_SUBS:
        text = re.sub(pattern, replacement, text, flags=re.IGNORECASE)
    # Clean up double spaces that may result from dropped words
    text = re.sub(r"  +", " ", text)
    # Clean up space before punctuation
    text = re.sub(r" +([.,;:!?])", r"\1", text)
    # Fix article-noun agreement after substitutions
    # "an" before a consonant sound → "a"
    text = re.sub(r"\ban (?=[bcdfghjklmnpqrstvwxyzBCDFGHJKLMNPQRSTVWXYZ])", "a ", text)
    text = re.sub(r"\bAn (?=[bcdfghjklmnpqrstvwxyzBCDFGHJKLMNPQRSTVWXYZ])", "A ", text)
    return text


def _split_if_long(sent, max_words):
    """Try to split a single sentence at natural break points if too long.

    Returns a list of one or more sentence strings.  Only splits at
    semicolons and coordinating conjunctions preceded by a comma so
    the resulting pieces remain grammatically correct.
    """
    if len(sent.split()) <= max_words:
        return [sent]

    # Split points in priority order: (regex, prefix for right part)
    split_points = [
        (r";\s+", ""),            # semicolons
        (r",\s+and\s+", ""),      # ", and"
        (r",\s+but\s+", "But "),  # ", but"
        (r",\s+or\s+", "Or "),    # ", or"
        (r",\s+so\s+", "So "),    # ", so"
    ]

    for pattern, prefix in split_points:
        match = re.search(pattern, sent, re.IGNORECASE)
        if match:
            left = sent[:match.start()].rstrip()
            right = sent[match.end():].strip()

            # Both parts need at least 3 words
            if len(left.split()) < 3 or len(right.split()) < 3:
                continue

            # Ensure left ends with punctuation
            if not left.endswith((".", "!", "?")):
                left += "."

            # Capitalize right part
            if prefix:
                right = prefix + right
            elif right and right[0].islower():
                right = right[0].upper() + right[1:]

            return _split_if_long(left, max_words) + _split_if_long(right, max_words)

    return [sent]  # can't split further


def split_long_sentences(text, max_words=12):
    """Split sentences longer than *max_words* at natural break points.

    Processes each line independently to preserve paragraph structure.
    Only splits at semicolons and coordinating conjunctions (", and",
    ", but", ", or", ", so") to keep results grammatically correct.
    """
    lines = text.split("\n")
    result_lines = []
    for line in lines:
        line = line.strip()
        if not line:
            result_lines.append("")
            continue
        raw_sentences = re.split(r"(?<=[.!?])\s+", line)
        line_result = []
        for sent in raw_sentences:
            sent = sent.strip()
            if not sent:
                continue
            line_result.extend(_split_if_long(sent, max_words))
        result_lines.append(" ".join(line_result))
    return "\n".join(result_lines)


# ---------------------------------------------------------------------------
# 3. Fetching Raw Legal Text
# ---------------------------------------------------------------------------
def get_pending_legislation():
    """Return the first unprocessed .txt file from raw_legislation/."""
    if not os.path.exists(INPUT_DIR):
        os.makedirs(INPUT_DIR)
        return None, None

    files = [
        f for f in os.listdir(INPUT_DIR)
        if f.endswith(".txt") and not f.startswith("translated_")
    ]
    if not files:
        return None, None

    target_file = files[0]
    filepath = os.path.join(INPUT_DIR, target_file)
    with open(filepath, "r", encoding="utf-8") as f:
        content = f.read()
    return target_file, content


def read_file(filepath):
    """Read and return the contents of a single file."""
    with open(filepath, "r", encoding="utf-8") as f:
        return f.read()


# ---------------------------------------------------------------------------
# 5. The Brain: Claude translates legislation to 8th-grade reading level
# ---------------------------------------------------------------------------
def build_system_prompt(mode=MODE_FULL, legal_terms=None):
    """Build the system prompt based on the translation mode."""
    base = f"""You are a legal plain-language expert who writes at an 8th-grade reading level.
Your job is to translate state legislation into plain English that a 13-year-old can read and understand easily.

The Flesch-Kincaid Grade Level formula is:
  FK = 0.39 x (words per sentence) + 11.8 x (syllables per word) - 15.59
To score at or below grade 8, you MUST keep BOTH factors low:
  - Average sentence length: aim for 10 words. Never exceed 12 words in any sentence.
  - Average syllables per word: close to 1.0. Use one-syllable words as much as possible. Two-syllable words only when no one-syllable word exists. NEVER use a three-syllable word — always find a shorter way to say it.

OUTPUT FORMAT RULES:
1. Start your response IMMEDIATELY with the {{ character. No intro text.
2. First, output a VALID JSON object with metadata.
3. Then, output exactly this delimiter on its own line: {DELIMITER}
4. Finally, output the full plain-English translation in PLAIN TEXT only.
   Do NOT use any Markdown formatting — no #, **, *, -, or bullet symbols.
   Use plain numbered lists (1. 2. 3.) and blank lines to separate sections.
   Markdown formatting inflates readability scores and must be avoided.

STRICT PLAIN-LANGUAGE RULES:
1. SENTENCES: Target 10 words per sentence. Never go above 12 words. If a sentence has more than 10 words, split it into two sentences. Every idea gets its own short sentence.
2. WORDS: Use the shortest, most common words. One-syllable words are best. Two-syllable words only when needed. NEVER use three-syllable words — find a shorter way. Use words a child would know.
3. ACTIVE VOICE ENFORCEMENT: You MUST write every sentence in active voice. NEVER use passive voice.
   Passive voice inflates word count and grade level. Find the doer and make them the subject.
   FORBIDDEN PATTERNS — rewrite ALL of these:
     "shall be [verb]ed by" -> flip to "[doer] must [verb]"
     "is required by" -> "[doer] requires"
     "was enacted by" -> "[doer] passed"
     "are collected by" -> "[doer] collects"
     "will be reviewed by" -> "[doer] will review"
     "may be amended by" -> "[doer] may change"
   Example: "Signatures shall be collected by the sponsor" -> "The sponsor collects signatures"
   Example: "The power shall be vested in the court" -> "The court holds the power"
   If you cannot find the doer, use "the state," "the court," "the people," or "they."
4. NO JARGON: Replace all legal and formal words with plain words.
5. EXPLAIN THEN SUBSTITUTE: When you must keep a legal term with 3+ syllables (like "referendum," "initiative," or "appropriation"), follow this rule:
   a. On FIRST use, write the term followed by a short definition in parentheses.
   b. After that, use ONLY a short 1-syllable nickname for the rest of the text.
   Example: "This is a referendum (a vote to cancel a law). If the vote passes..."
   Example: "This is an appropriation (funds set aside). These funds will go to..."
   This lowers the average syllable count across the whole document.
6. LISTS: Break complex rules into short numbered lists.
7. PRONOUNS: Use "you," "they," "the state," "the court" instead of formal titles when the meaning is clear.
8. SENTENCE SPLITTING: Every long idea MUST become two or three short sentences. Short sentences are ALWAYS better. When in doubt, split the sentence.
9. SYLLABLE-PRIORITY SPLITTING: If a sentence contains a word with 4 or more syllables that you cannot replace, that sentence MUST be 8 words or fewer. The heavy word eats the syllable budget, so the sentence must be extra short to compensate.

LEGAL STRICTNESS PRESERVATION — CRITICAL:
You are simplifying the LANGUAGE, not the LAW. The translation must carry the exact same legal force as the original. A reader must be able to rely on the translation as if it were the law itself. Do NOT let simplification weaken, soften, or leave any rule open to interpretation.
  1. MANDATORY LANGUAGE: "shall" and "must" create legal duties. Always translate "shall" to "must" — NEVER to "should," "can," or "may." The word "must" keeps the same legal force.
  2. PROHIBITIONS: Keep all "shall not," "may not," "is prohibited" language strict. Translate "shall not" to "must not." NEVER soften to "should not" or "might not."
  3. CONDITIONS AND EXCEPTIONS: Every "if," "unless," "except," "provided that," and "notwithstanding" creates a legal condition. When you split a sentence, keep the condition and its result in back-to-back sentences. Use "If so," or "Then," to link them. NEVER separate a condition from its consequence by more than one sentence.
  4. NUMBERS, DATES, AND DEADLINES: Copy ALL numbers, dollar amounts, dates, deadlines, and time limits EXACTLY as written. "within 30 days" must stay "within 30 days." "$500" must stay "$500." Do not round or approximate.
  5. PENALTIES AND CONSEQUENCES: Keep all penalty amounts, fine ranges, and punishment descriptions EXACTLY as stated. Do not summarize or paraphrase penalties.
  6. DEFINED TERMS: If the original law defines a term in quotes (e.g., "qualified elector"), keep the defined term on first use and explain it. The reader must know the exact legal term.
  7. SCOPE WORDS: Words like "all," "any," "every," "no," "none," "only," and "exclusively" set the scope of a law. Copy them exactly. "All persons" is different from "some persons." Never drop or change scope words.
  8. CROSS-REFERENCES: Keep all section numbers, article numbers, and statutory references EXACTLY as written (e.g., "Section 7-9-107").
  9. FINE PRINT: Every exception, qualifier, limitation, and carve-out in the original MUST appear in the translation. If the original says "except in cases of fraud," your translation must say "except in fraud cases." Do not drop exceptions.
  10. LEGAL-PRECISION TERMS: Some words have exact legal meaning that plain synonyms do not capture. For these, use EXPLAIN THEN SUBSTITUTE (rule 5 above) — NEVER silently replace them with a vague word. These include: "jurisdiction" (area of legal power), "amendment" (formal legal change), "provision" (a specific part of a law), "regulation" (an official rule with force of law), "proceeding" (a formal legal action), "appropriation" (money set aside by law), "statute" (a written law).

AMBIGUITY PREVENTION — CRITICAL:
Simplifying language MUST NOT create new ambiguity. Every sentence must have exactly one clear meaning. If a reader could understand a simplified sentence two different ways, rewrite it until only one reading is possible.
  1. QUANTIFIER PRECISION: Keep "at least," "at most," "no more than," "no fewer than," and "exactly" intact. Never combine two quantity rules into one sentence.
     BAD: "Collect from 15 counties" (could mean exactly 15 or at least 15).
     GOOD: "Collect from at least 15 counties."
  2. PRONOUN CLARITY: Every "they," "it," "this," and "that" must point to ONE clear noun. If two groups appear nearby, name them instead of using a pronoun.
     BAD: "The board reviews the plan. They must approve it." (Who is "they"?)
     GOOD: "The board reviews the plan. The board must approve it."
  3. ONE RULE PER SENTENCE: Never merge two distinct legal duties, rights, or conditions into one sentence. Each requirement gets its own sentence.
  4. WHO-DOES-WHAT: Every sentence must name WHO acts and WHAT they do. Never use vague subjects like "one" or "parties." Name the actor: "the voter," "the state," "the court."
  5. COMPLETE LISTS: If the original lists specific items (types, categories, steps), keep EVERY item. Never summarize with "and others," "such as," or "including but not limited to" if the original gives a closed list. A closed list must stay closed.
  6. CONDITION ANCHORING: When splitting an "if/then" or "unless/then" rule, the condition and result MUST be in the same sentence or in two back-to-back sentences joined by "If so," "Then," or "In that case." Never let another idea come between a condition and its result.
  7. TIME AND SEQUENCE: Keep all "before," "after," "during," "within," and "until" time markers. When splitting a time-bound rule, repeat the time marker in each new sentence.
     BAD: "File within 30 days. Include the fee." (Is the fee also within 30 days?)
     GOOD: "File within 30 days. Pay the fee with the filing."
  8. PARALLEL STRUCTURE FOR LISTS: When listing duties, rights, or steps, use the same sentence pattern for each item. Parallel structure prevents confusion about which words apply to which item.

WORD SUBSTITUTIONS — always prefer the plain word:
  "shall" -> "must" (NEVER use "should," "can," or "may" — "must" keeps the legal force)
  "pursuant to" -> "under" or "by"
  "notwithstanding" -> "even if" or "despite"
  "herein" / "thereof" / "therein" / "hereby" -> drop or use "in this" / "of that" / "by this"
  "prior to" -> "before"
  "subsequent to" -> "after"
  "commence" -> "start" or "begin"
  "terminate" -> "end" or "stop"
  "utilize" -> "use"
  "in the event that" -> "if"
  "in accordance with" -> "under" or "by"
  "with respect to" -> "about" or "for"
  "whereas" -> "since" or "because"
  "aforementioned" -> "this" or "that"
  "promulgate" -> "make" or "issue"
  "adjudication" -> "ruling" or "decision"
  "enacted" -> "passed" or "made into law"
  "provisions" -> keep, but explain as "(specific parts of a law)" on first use, then just "parts"
  "jurisdiction" -> keep, but explain as "(area of legal power)" on first use, then just "power"
  "appropriation" -> keep, but explain as "(money set aside by law)" on first use, then just "funds"
  "constitute" -> "make up" or "count as"
  "qualified elector" -> "registered voter" or "voter"
  "legislative measure" -> "proposed law"
  "municipal" -> "city or town"
  "petition" -> keep, but explain as "(a signed request)" on first use, then just "request"
  "referendum" -> keep, but explain as "(a public vote on a law)" on first use, then just "vote"
  "initiative" -> keep, but explain as "(a way for people to propose new laws)" on first use, then just "plan"
  "abeyance" -> "on hold" or "paused"
  "franchise" -> "right" or "license"
  "ministerial" -> "routine" or "basic"
  "amendment" -> keep, but explain as "(a formal change to a law)" on first use, then just "change"
  "constitutional" -> keep, but explain as "(from the state's main law)" on first use, then just "main-law"
  "establishment" -> "setting up" or "creation"
  "implementation" -> "carrying out"
  "requirements" -> "rules" or "needs"
  "proceedings" -> keep, but explain as "(formal legal steps)" on first use, then just "steps"
  "regulation" -> keep, but explain as "(an official rule)" on first use, then just "rule"
  "authority" -> "power" or "right"
  "compensation" -> "pay" or "payment"
  "determination" -> "decision" or "choice"
  "obligation" -> "duty" or "must-do"
  "prohibition" -> "ban" or "rule against"
  "certification" -> "proof" or "approval"
  "notification" -> "notice" or "alert"
  "authorization" -> "approval" or "okay"
  "modification" -> "change"
  "legislation" -> "law"
  "specifically" -> "namely" or just drop
  "pertaining to" -> "about"
  "regarding" -> "about"
  "therefore" -> "so"
  "however" -> "but"
  "furthermore" -> "also"
  "additionally" -> "also"
  "approximately" -> "about" or "near"
  "immediately" -> "right away" or "at once"
  "necessarily" -> "must" or just drop
  "significantly" -> "a lot" or "greatly"
  "for the purpose of" -> "to"
  "in order to" -> "to"
  "with regard to" -> "about"
  "on behalf of" -> "for"
  "at such time as" -> "when"
  "by means of" -> "by" or "with"
  "in the absence of" -> "without"
  "in lieu of" -> "instead of"
  "in excess of" -> "more than"
  "on the condition that" -> "if"
  "subsequently" -> "then" or "next"
  "substantially" -> "mostly"
  "independently" -> "on its own"
  "predominantly" -> "mainly"
  "consecutive" -> "in a row"
  "expenditure" -> "spending"
  "consolidation" -> "merging"
  "hereinafter" -> "from now on" or drop
  "thereupon" -> "then"

SELF-CHECK: Before finishing, review your translation:
  - Count the words in EVERY sentence. Is each one 12 words or fewer? If not, split it.
  - Does any sentence contain a 4-syllable word? If so, is that sentence 8 words or fewer? If not, split it.
  - Count syllables in every word. Did you avoid ALL three-syllable words? If not, swap them.
  - Did you use simple, short words a child would know?
  - Is EVERY sentence in active voice? Search for "by the," "shall be," "is required," "was [verb]ed by," "are [verb]ed by." If you find any, rewrite in active voice now.
  - For each legal term you kept, did you define it on first use and then switch to a short nickname?
  - Would a 13-year-old understand every sentence on the first read?
  - Can any sentence be split into two shorter ones? If so, split it.
  STRICTNESS CHECK — do this last:
  - Did you keep every "must" and "must not"? Did any become "should" or "may"? Fix them.
  - Are all numbers, dates, deadlines, and dollar amounts EXACTLY the same as the original?
  - Does every condition ("if," "unless," "except") still connect to its result? Are they in back-to-back sentences?
  - Did you keep every exception and qualifier from the original? Compare section by section.
  - Are all scope words ("all," "any," "every," "no," "none," "only") still present and correct?
  - Would a lawyer agree that your translation has the same legal force as the original?
  AMBIGUITY CHECK:
  - Read each sentence. Could it be understood two ways? If so, rewrite it.
  - Does every pronoun ("they," "it," "this") point to one clear noun? If not, replace it with the noun.
  - Did you keep "at least," "at most," "no more than," "no fewer than" exactly? If a quantity word is missing, add it back.
  - Does every sentence name who acts and what they do? If the actor is unclear, name them.
  - Did you merge two rules into one sentence? If so, split them apart.
  If any sentence fails these checks, rewrite it right now before finishing."""

    if mode == MODE_PRESERVE_LEGAL:
        terms_list = "\n".join(f"  - {t}" for t in (legal_terms or []))
        base += f"""

PRESERVE LEGAL TERMS MODE:
The following legal terms and references MUST be kept exactly as written.
Do NOT replace, rephrase, or remove them. Simplify everything AROUND them.
{terms_list}"""

    elif mode == MODE_JARGON_ONLY:
        base += """

SIMPLIFY JARGON ONLY MODE:
Only replace legal jargon and complex vocabulary with simpler words.
Do NOT restructure sentences, reorder sections, or change the document
layout. Keep the original structure intact; only swap hard words for
easy ones."""

    base += f"""

JSON Schema:
{{
  "STATUS": "SUCCESS",
  "TITLE": "8th-Grade Translation: [Short Name of Bill]",
  "SUMMARY": "One sentence describing the bill's intent",
  "KEY_LEGAL_TERMS": ["list", "of", "important", "legal", "terms", "preserved"]
}}"""

    return base


def build_refinement_prompt(previous_text, fk_grade, legal_terms=None):
    """Build a refinement prompt that asks Claude to simplify an already-translated text further.

    This is used in subsequent iterations when the first translation did not
    reach the target 8th-grade level.
    """
    prompt = f"""You are a plain-language editor. Your ONLY job is to make the text below easier to read.

The current Flesch-Kincaid Grade Level is {fk_grade}. The target is {FK_TARGET_GRADE} or lower.

The FK formula is: 0.39 x (words per sentence) + 11.8 x (syllables per word) - 15.59
To lower the score, you MUST do ALL of the following:
  1. SPLIT EVERY SENTENCE that is longer than 10 words into two shorter sentences. Aim for 8-10 words per sentence. NEVER exceed 12 words.
  2. Replace EVERY word of three or more syllables with a shorter word (one or two syllables). Use words a child would know. This is the most important step.
  3. ACTIVE VOICE: Rewrite EVERY passive sentence in active voice. Search for "by the," "shall be," "is required," "was," "are [verb]ed." If you find any, flip them so the doer is the subject.
     Example: "Signatures shall be collected by the sponsor" -> "The sponsor collects signatures."
  4. Cut filler words and phrases that add no meaning.
  5. Keep the same legal meaning. Do not drop important facts.
  6. EXPLAIN THEN SUBSTITUTE: If you must keep a word with 3+ syllables (like "referendum"), define it in parentheses on first use, then use a short nickname (1 syllable) for the rest of the text. Example: "referendum (a vote to cancel a law)" then just "vote" after that.
  7. SYLLABLE-PRIORITY SPLITTING: If a sentence has a word with 4+ syllables that cannot be replaced, that sentence MUST be 8 words or fewer. The heavy word eats the syllable budget.
  8. LEGAL STRICTNESS: You are simplifying LANGUAGE, not the LAW. Never soften mandatory language — keep every "must" and "must not." Keep all numbers, dates, deadlines, and dollar amounts exactly. Keep all conditions ("if," "unless," "except") connected to their results in back-to-back sentences. Keep all scope words ("all," "any," "every," "no," "none," "only"). Keep every exception and fine-print qualifier from the original.
  9. Use these word swaps on EVERY word you find:
     "shall" -> "must" (NEVER "should," "can," or "may")
     "pursuant to" -> "under"
     "notwithstanding" -> "even if" or "despite"
     "herein" / "thereof" / "therein" / "hereby" -> drop or use "in this" / "of that"
     "prior to" -> "before"
     "subsequent to" -> "after"
     "commence" -> "start"
     "terminate" -> "end"
     "utilize" -> "use"
     "in the event that" -> "if"
     "in accordance with" -> "under"
     "with respect to" -> "about"
     "whereas" -> "since"
     "aforementioned" -> "this"
     "promulgate" -> "make" or "issue"
     "adjudication" -> "ruling"
     "enacted" -> "passed"
     "constitute" -> "make up"
     "qualified elector" -> "voter"
     "legislative measure" -> "proposed law"
     "municipal" -> "city or town"
     "establishment" -> "setting up"
     "implementation" -> "carrying out"
     "requirements" -> "rules"
     "authority" -> "power"
     "compensation" -> "pay"
     "determination" -> "decision"
     "obligation" -> "duty"
     "prohibition" -> "ban"
     "certification" -> "proof"
     "notification" -> "notice"
     "authorization" -> "approval"
     "modification" -> "change"
     "legislation" -> "law"
     "specifically" -> drop it
     "pertaining to" -> "about"
     "regarding" -> "about"
     "therefore" -> "so"
     "however" -> "but"
     "furthermore" -> "also"
     "additionally" -> "also"
     "approximately" -> "about"
     "immediately" -> "at once"
     "necessarily" -> drop it
     "significantly" -> "a lot"
     For LEGAL-PRECISION terms, use EXPLAIN THEN SUBSTITUTE (define on first use, then nickname):
     "provisions" -> explain as "(specific parts of a law)," then "parts"
     "jurisdiction" -> explain as "(area of legal power)," then "power"
     "appropriation" -> explain as "(money set aside by law)," then "funds"
     "amendment" -> explain as "(a formal change to a law)," then "change"
     "constitutional" -> explain as "(from the state's main law)," then "main-law"
     "proceedings" -> explain as "(formal legal steps)," then "steps"
     "regulation" -> explain as "(an official rule)," then "rule"
  10. After rewriting, count the words in each sentence. If any sentence still has more than 12 words, split it again.
  11. Count syllables in every word. If any word has 3+ syllables, find a shorter word. This matters more than sentence length.
  12. AMBIGUITY PREVENTION: Every simplified sentence must have exactly one clear meaning. Keep all quantity words ("at least," "no more than"). Every pronoun must point to one clear noun — if unclear, use the noun instead. Never merge two rules into one sentence. Name the actor in every sentence — no vague "one" or "parties." Keep all time markers ("within," "before," "after") when splitting sentences."""

    if legal_terms:
        terms_list = "\n".join(f"  - {t}" for t in legal_terms)
        prompt += f"""

IMPORTANT: The following legal terms and references MUST be kept exactly as
written. Do NOT replace, rephrase, or remove them. Simplify everything else.
{terms_list}"""

    prompt += f"""

OUTPUT FORMAT RULES:
1. Start your response IMMEDIATELY with the {{ character. No intro text.
2. First, output a VALID JSON object with metadata.
3. Then, output exactly this delimiter on its own line: {DELIMITER}
4. Finally, output the simplified text in PLAIN TEXT only.
   No Markdown. No #, **, *, or - bullet symbols.

JSON Schema:
{{
  "STATUS": "SUCCESS",
  "TITLE": "8th-Grade Translation: [Short Name of Bill]",
  "SUMMARY": "One sentence describing the bill's intent",
  "KEY_LEGAL_TERMS": ["list", "of", "important", "legal", "terms", "preserved"]
}}

TEXT TO SIMPLIFY:
{previous_text}"""

    return prompt


def build_targeted_refinement_prompt(full_text, hard_sentences, fk_grade,
                                     legal_terms=None):
    """Build a prompt that targets only the hardest sentences for rewriting.

    Instead of asking Claude to rewrite the entire document (which risks
    making already-simple sentences more complex), this prompt lists the
    specific sentences that scored Grade 12+ and asks Claude to rewrite
    only those while leaving the rest untouched.
    """
    numbered = "\n".join(
        f"  {i+1}. (Grade {g}) {s}" for i, (s, g) in enumerate(hard_sentences)
    )
    prompt = f"""You are a plain-language editor. The text below scores {fk_grade} on the Flesch-Kincaid scale. The target is {FK_TARGET_GRADE} or lower.

COMPLEXITY HEATMAP — these sentences score Grade 12 or above and need rewriting:
{numbered}

REWRITING RULES for the sentences above:
1. Split each one into two or more sentences of 8 words or fewer.
2. Replace every word of 3+ syllables with a 1- or 2-syllable word.
3. Use active voice. No passive constructions.
4. If you must keep a word with 3+ syllables, define it in parentheses on first use, then use a short nickname after.
5. Keep the same meaning. Do not drop facts.
6. LEGAL STRICTNESS: You are simplifying LANGUAGE, not the LAW. Never soften mandatory language — keep every "must" and "must not." Keep all numbers, dates, deadlines, and dollar amounts exactly. Keep all conditions ("if," "unless," "except") connected to their results in back-to-back sentences. Keep all scope words ("all," "any," "every," "no," "none," "only"). Keep every exception and fine-print qualifier from the original.
7. AMBIGUITY PREVENTION: Every simplified sentence must have exactly one clear meaning. Keep all quantity words ("at least," "no more than"). Every pronoun must point to one clear noun — if unclear, use the noun instead. Never merge two rules into one sentence. Name the actor in every sentence.

IMPORTANT: Output the FULL document. ONLY rewrite the numbered sentences from the COMPLEXITY HEATMAP above. Every other sentence must be copied exactly as written, word-for-word, with no changes."""

    if legal_terms:
        terms_list = "\n".join(f"  - {t}" for t in legal_terms)
        prompt += f"""

The following legal terms MUST be kept exactly as written:
{terms_list}"""

    prompt += f"""

OUTPUT FORMAT RULES:
1. Start your response IMMEDIATELY with the {{ character. No intro text.
2. First, output a VALID JSON object with metadata.
3. Then, output exactly this delimiter on its own line: {DELIMITER}
4. Finally, output the full text in PLAIN TEXT only.
   No Markdown. No #, **, *, or - bullet symbols.

JSON Schema:
{{
  "STATUS": "SUCCESS",
  "TITLE": "8th-Grade Translation: [Short Name of Bill]",
  "SUMMARY": "One sentence describing the bill's intent",
  "KEY_LEGAL_TERMS": ["list", "of", "important", "legal", "terms", "preserved"]
}}

FULL TEXT (rewrite ONLY the hard sentences, copy the rest):
{full_text}"""

    return prompt


def ask_claude_to_translate(client, filename, raw_text,
                            model="claude-sonnet-4-20250514",
                            mode=MODE_FULL, legal_terms=None):
    """Send the raw bill text to Claude and return the translated response."""
    system = build_system_prompt(mode=mode, legal_terms=legal_terms)

    user = f"Target File: {filename}\n\nRaw Legal Text:\n{raw_text}"

    response = client.messages.create(
        model=model,
        max_tokens=16000,
        temperature=0.1,
        system=system,
        messages=[{"role": "user", "content": user}],
    )
    return response.content[0].text.strip()


def ask_claude_to_refine(client, previous_text, fk_grade,
                         model="claude-sonnet-4-20250514",
                         legal_terms=None):
    """Ask Claude to simplify an already-translated text to lower its FK score."""
    user_prompt = build_refinement_prompt(previous_text, fk_grade,
                                         legal_terms=legal_terms)

    response = client.messages.create(
        model=model,
        max_tokens=16000,
        temperature=0.1,
        messages=[{"role": "user", "content": user_prompt}],
    )
    return response.content[0].text.strip()


def ask_claude_targeted_refine(client, full_text, hard_sentences, fk_grade,
                               model="claude-sonnet-4-20250514",
                               legal_terms=None):
    """Ask Claude to rewrite only the hardest sentences in the text.

    This is used when the overall score is above target but many sentences
    are already simple.  By targeting only Grade 12+ sentences we avoid
    accidentally making easy sentences harder.
    """
    user_prompt = build_targeted_refinement_prompt(
        full_text, hard_sentences, fk_grade, legal_terms=legal_terms,
    )

    response = client.messages.create(
        model=model,
        max_tokens=16000,
        temperature=0.1,
        messages=[{"role": "user", "content": user_prompt}],
    )
    return response.content[0].text.strip()


# ---------------------------------------------------------------------------
# 6. Parse Claude's response into metadata + translated text
# ---------------------------------------------------------------------------
def parse_response(raw_response):
    """Split Claude's response into metadata (JSON) and the plain-text body."""
    if DELIMITER in raw_response:
        json_part, translated_text = raw_response.split(DELIMITER, 1)
        match = re.search(r"\{.*\}", json_part, re.DOTALL)
        clean_json = match.group(0) if match else json_part.strip()
        metadata = json.loads(clean_json)
        return metadata, strip_markdown(translated_text)

    # Fallback: no delimiter found
    print("⚠️  Delimiter missing — attempting to parse as pure JSON...")
    match = re.search(r"\{.*\}", raw_response, re.DOTALL)
    clean_json = match.group(0) if match else raw_response
    metadata = json.loads(clean_json)
    return metadata, "Error: Delimiter missing. Check crash_log.txt."


# ---------------------------------------------------------------------------
# 7. Save the translated output (with versioning)
# ---------------------------------------------------------------------------
def save_translation(filename, translated_text, version=1, scores=None):
    """Write the translated text to translated_legislation/ with version."""
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    base = filename.rsplit(".", 1)[0] if "." in filename else filename
    out_name = f"translated_{base}_v{version}.md"
    out_path = os.path.join(OUTPUT_DIR, out_name)

    # Prepend readability scores as a metadata block
    header = ""
    if scores:
        header = f"""<!-- Readability Scores (auto-generated)
     Flesch-Kincaid Grade: {scores['flesch_kincaid_grade']}
     Flesch Reading Ease:  {scores['flesch_reading_ease']}
     Act 602 Compliant:    {'Yes' if scores['passes_act602'] else 'No'}
     Version:              {version}
     Generated:            {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}
-->\n\n"""

    with open(out_path, "w", encoding="utf-8") as f:
        f.write(header + translated_text)
    return out_path


# ---------------------------------------------------------------------------
# 8. Archive the original raw file after processing
# ---------------------------------------------------------------------------
def archive_raw_file(filename):
    """Move the processed raw file into raw_legislation/archive/."""
    archive_dir = os.path.join(INPUT_DIR, "archive")
    os.makedirs(archive_dir, exist_ok=True)
    src = os.path.join(INPUT_DIR, filename)
    dst = os.path.join(archive_dir, filename)
    if os.path.exists(src):
        os.rename(src, dst)
        print(f"📦 Archived raw file: {filename}")


# ---------------------------------------------------------------------------
# 9. Main entry point
# ---------------------------------------------------------------------------
def translate_file(filepath, model="claude-sonnet-4-20250514",
                   mode=MODE_FULL, max_iterations=7):
    """Translate a single file, score it, and optionally re-iterate.

    The first iteration translates from the original text.  Subsequent
    iterations use a refinement prompt that feeds the previous translation
    and its FK score back to Claude so it can target specific problems.
    """
    client = get_client()
    filename = os.path.basename(filepath)
    raw_text = read_file(filepath)

    # Score the original
    original_scores = score_readability(raw_text)
    print_readability_report("ORIGINAL", original_scores)

    # Extract legal terms for drift detection
    original_legal_terms = extract_legal_terms(raw_text)
    legal_terms_for_prompt = original_legal_terms if mode == MODE_PRESERVE_LEGAL else None

    version = 0
    translated_text = None
    translated_scores = None
    metadata = None

    for iteration in range(1, max_iterations + 1):
        version = iteration
        print(f"\n📄 Processing: {filename} (iteration {iteration}/{max_iterations}) ...")

        if iteration == 1:
            # First pass: translate from the original text
            raw_response = ask_claude_to_translate(
                client, filename, raw_text, model=model,
                mode=mode, legal_terms=legal_terms_for_prompt,
            )
        else:
            # Subsequent passes: refine the previous translation
            fk_grade = translated_scores["flesch_kincaid_grade"]
            raw_response = ask_claude_to_refine(
                client, translated_text, fk_grade, model=model,
                legal_terms=legal_terms_for_prompt,
            )

        try:
            metadata, translated_text = parse_response(raw_response)
            translated_text = apply_word_substitutions(translated_text)
            translated_text = split_long_sentences(translated_text)
            print(f"✅ {metadata.get('TITLE', 'Translation complete')}")
            print(f"   Summary: {metadata.get('SUMMARY', 'N/A')}")
        except Exception as e:
            crash_path = os.path.join(SCRIPT_DIR, "crash_log.txt")
            with open(crash_path, "w", encoding="utf-8") as f:
                f.write(raw_response)
            print(f"❌ Parsing error: {e}. Raw response saved to crash_log.txt")
            return None

        # Score the translation
        translated_scores = score_readability(translated_text)
        print_readability_report("TRANSLATED", translated_scores)

        # Check for legal term drift
        translated_legal_terms = extract_legal_terms(translated_text)
        missing_terms = compare_legal_terms(original_legal_terms, translated_legal_terms)
        if missing_terms:
            print(f"\n⚠️  Potential meaning drift — {len(missing_terms)} legal term(s) not found in translation:")
            for term in missing_terms[:10]:
                print(f"   • {term}")

        # If it passes, stop iterating
        if translated_scores["passes_act602"]:
            break

        if iteration < max_iterations:
            print(f"\n🔄 Grade {translated_scores['flesch_kincaid_grade']} exceeds target. Refining...")

    out_path = save_translation(filename, translated_text, version=version, scores=translated_scores)
    print(f"\n💾 Saved translation to: {out_path}")
    return out_path


def run_batch(model="claude-sonnet-4-20250514", mode=MODE_FULL, max_iterations=7):
    """Process all .txt files waiting in the raw_legislation/ folder."""
    client = get_client()
    print(f"⚖️  Arkansas Bill Translator — {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}")

    filename, raw_text = get_pending_legislation()
    if not filename:
        print("💤 No new legislation files found in raw_legislation/.")
        print("   Drop .txt files there, or pass a file directly:")
        print("   python translator_agent.py path/to/bill.txt")
        return

    while filename:
        filepath = os.path.join(INPUT_DIR, filename)

        # Score original
        original_scores = score_readability(raw_text)
        print_readability_report("ORIGINAL", original_scores)

        original_legal_terms = extract_legal_terms(raw_text)
        legal_terms_for_prompt = original_legal_terms if mode == MODE_PRESERVE_LEGAL else None

        version = 0
        translated_text = None
        translated_scores = None

        for iteration in range(1, max_iterations + 1):
            version = iteration
            print(f"\n📄 Processing: {filename} (iteration {iteration}/{max_iterations}) ...")

            if iteration == 1:
                raw_response = ask_claude_to_translate(
                    client, filename, raw_text, model=model,
                    mode=mode, legal_terms=legal_terms_for_prompt,
                )
            else:
                fk_grade = translated_scores["flesch_kincaid_grade"]
                raw_response = ask_claude_to_refine(
                    client, translated_text, fk_grade, model=model,
                    legal_terms=legal_terms_for_prompt,
                )

            try:
                metadata, translated_text = parse_response(raw_response)
                translated_text = apply_word_substitutions(translated_text)
                translated_text = split_long_sentences(translated_text)
                print(f"✅ {metadata.get('TITLE', 'Translation complete')}")
                print(f"   Summary: {metadata.get('SUMMARY', 'N/A')}")
            except Exception as e:
                crash_path = os.path.join(SCRIPT_DIR, "crash_log.txt")
                with open(crash_path, "w", encoding="utf-8") as f:
                    f.write(raw_response)
                print(f"❌ Parsing error: {e}. Raw response saved to crash_log.txt")
                break

            translated_scores = score_readability(translated_text)
            print_readability_report("TRANSLATED", translated_scores)

            translated_legal_terms = extract_legal_terms(translated_text)
            missing_terms = compare_legal_terms(original_legal_terms, translated_legal_terms)
            if missing_terms:
                print(f"\n⚠️  Potential meaning drift — {len(missing_terms)} term(s) not in translation:")
                for term in missing_terms[:10]:
                    print(f"   • {term}")

            if translated_scores["passes_act602"]:
                break

            if iteration < max_iterations:
                print(f"\n🔄 Grade {translated_scores['flesch_kincaid_grade']} exceeds target. Refining...")

        if translated_text and translated_scores:
            out_path = save_translation(filename, translated_text, version=version, scores=translated_scores)
            print(f"💾 Saved translation to: {out_path}")
            archive_raw_file(filename)

        filename, raw_text = get_pending_legislation()

    print("\n🏁 All done.")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def main():
    parser = argparse.ArgumentParser(
        description="Translate Arkansas legislation to an 8th-grade reading level.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python translator_agent.py bill.txt                       # Translate one file
  python translator_agent.py bill.txt --preserve-legal-terms # Keep legal terms intact
  python translator_agent.py bill.txt --simplify-jargon-only # Only swap hard words
  python translator_agent.py bill.txt --max-iterations 3     # Re-try up to 3 times
  python translator_agent.py                                 # Batch mode
  python translator_agent.py --score-only bill.txt           # Just score, no translation
""",
    )
    parser.add_argument(
        "file",
        nargs="?",
        help="Path to a .txt file containing the bill text. "
             "If omitted, processes all files in raw_legislation/.",
    )
    parser.add_argument(
        "--model",
        default="claude-sonnet-4-20250514",
        help="Claude model to use (default: claude-sonnet-4-20250514).",
    )
    parser.add_argument(
        "--preserve-legal-terms",
        action="store_true",
        help="Keep legal terms and references exactly as written; "
             "simplify only the surrounding language.",
    )
    parser.add_argument(
        "--simplify-jargon-only",
        action="store_true",
        help="Only replace jargon/complex vocabulary with simpler words. "
             "Do not restructure sentences or reorder sections.",
    )
    parser.add_argument(
        "--max-iterations",
        type=int,
        default=7,
        help="Maximum translation attempts to reach the target grade level (default: 7).",
    )
    parser.add_argument(
        "--score-only",
        action="store_true",
        help="Only compute the Flesch-Kincaid score of the input file — no translation.",
    )
    args = parser.parse_args()

    # Determine translation mode
    if args.preserve_legal_terms and args.simplify_jargon_only:
        print("❌ Cannot use both --preserve-legal-terms and --simplify-jargon-only.")
        sys.exit(1)
    elif args.preserve_legal_terms:
        mode = MODE_PRESERVE_LEGAL
    elif args.simplify_jargon_only:
        mode = MODE_JARGON_ONLY
    else:
        mode = MODE_FULL

    # Score-only mode
    if args.score_only:
        if not args.file:
            print("❌ --score-only requires a file path.")
            sys.exit(1)
        if not os.path.isfile(args.file):
            print(f"❌ File not found: {args.file}")
            sys.exit(1)
        text = read_file(args.file)
        scores = score_readability(text)
        print_readability_report(os.path.basename(args.file), scores)
        sys.exit(0)

    if args.file:
        if not os.path.isfile(args.file):
            print(f"❌ File not found: {args.file}")
            sys.exit(1)
        translate_file(args.file, model=args.model, mode=mode,
                       max_iterations=args.max_iterations)
    else:
        run_batch(model=args.model, mode=mode,
                  max_iterations=args.max_iterations)


if __name__ == "__main__":
    main()
