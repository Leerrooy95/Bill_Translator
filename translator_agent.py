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
    (r"\bin the event that\b", "if"),
    (r"\bin accordance with\b", "under"),
    (r"\bwith respect to\b", "about"),
    (r"\bsubsequent to\b", "after"),
    (r"\bpertaining to\b", "about"),
    (r"\bpursuant to\b", "under"),
    (r"\bprior to\b", "before"),
]

_WORD_SUBS = [
    (r"\bnotwithstanding\b", "despite"),
    (r"\baforementioned\b", "this"),
    (r"\bimplementation\b", "carrying out"),
    (r"\bconstitutional\b", "main law"),
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
    (r"\bjurisdiction\b", "power"),
    (r"\bappropriation\b", "funds"),
    (r"\bproceedings\b", "steps"),
    (r"\blegislature\b", "lawmakers"),
    (r"\blegislation\b", "law"),
    (r"\bprohibition\b", "ban"),
    (r"\bfundamental\b", "basic"),
    (r"\bnecessarily\b", ""),
    (r"\bobligation\b", "duty"),
    (r"\bregulation\b", "rule"),
    (r"\bprovisions\b", "rules"),
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
    (r"\bamendment\b", "change"),
    (r"\bshall\b", "must"),
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
    return text


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
3. ACTIVE VOICE: Write in active voice. Say "The state will do X" not "X shall be done by the state."
4. NO JARGON: Replace all legal and formal words with plain words. When a legal term has no simple replacement (like "referendum"), keep it but add a short explanation in parentheses the first time.
5. LISTS: Break complex rules into short numbered lists.
6. PRONOUNS: Use "you," "they," "the state," "the court" instead of formal titles when the meaning is clear.
7. SENTENCE SPLITTING: Every long idea MUST become two or three short sentences. Short sentences are ALWAYS better. When in doubt, split the sentence.

WORD SUBSTITUTIONS — always prefer the plain word:
  "shall" -> "will" or "must"
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
  "provisions" -> "rules" or "parts"
  "jurisdiction" -> "power" or "area of control"
  "appropriation" -> "funds set aside"
  "constitute" -> "make up" or "count as"
  "qualified elector" -> "registered voter" or "voter"
  "legislative measure" -> "proposed law"
  "municipal" -> "city or town"
  "petition" -> keep, but explain as "(a signed request)" on first use
  "referendum" -> keep, but explain as "(a public vote on a law)" on first use
  "initiative" -> keep, but explain as "(a way for people to propose new laws)" on first use
  "abeyance" -> "on hold" or "paused"
  "franchise" -> "right" or "license"
  "ministerial" -> "routine" or "basic"
  "amendment" -> "change"
  "constitutional" -> "in the state's main law" or just drop when meaning is clear
  "establishment" -> "setting up" or "creation"
  "implementation" -> "carrying out"
  "requirements" -> "rules" or "needs"
  "proceedings" -> "steps" or "actions"
  "regulation" -> "rule"
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

SELF-CHECK: Before finishing, review your translation:
  - Count the words in EVERY sentence. Is each one 12 words or fewer? If not, split it.
  - Count syllables in every word. Did you avoid ALL three-syllable words? If not, swap them.
  - Did you use simple, short words a child would know?
  - Did you write in active voice?
  - Would a 13-year-old understand every sentence on the first read?
  - Can any sentence be split into two shorter ones? If so, split it.
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
  3. Use active voice. Remove all passive constructions.
  4. Cut filler words and phrases that add no meaning.
  5. Keep the same legal meaning. Do not drop important facts.
  6. Use these word swaps on EVERY word you find:
     "shall" -> "will" or "must"
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
     "provisions" -> "rules"
     "jurisdiction" -> "power"
     "appropriation" -> "funds set aside"
     "constitute" -> "make up"
     "qualified elector" -> "voter"
     "legislative measure" -> "proposed law"
     "municipal" -> "city or town"
     "amendment" -> "change"
     "constitutional" -> "in the main law"
     "establishment" -> "setting up"
     "implementation" -> "carrying out"
     "requirements" -> "rules"
     "proceedings" -> "steps"
     "regulation" -> "rule"
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
  7. After rewriting, count the words in each sentence. If any sentence still has more than 12 words, split it again.
  8. Count syllables in every word. If any word has 3+ syllables, find a shorter word. This matters more than sentence length."""

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
