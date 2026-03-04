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
    base = f"""You are a legal plain-language expert.
Your job is to translate state legislation into plain English that anyone
can understand — specifically at an 8th-grade reading level as measured by
the Flesch-Kincaid Grade Level formula.

OUTPUT FORMAT RULES:
1. Start your response IMMEDIATELY with the {{ character. No intro text.
2. First, output a VALID JSON object with metadata.
3. Then, output exactly this delimiter on its own line: {DELIMITER}
4. Finally, output the full plain-English translation in clean Markdown
   (use headers, bold text, and bullet points).

READABILITY CONSTRAINT:
The translation MUST score at or below an 8th-grade reading level on the
Flesch-Kincaid scale. Break long sentences. Replace legal jargon with
everyday words. Keep the legal meaning intact."""

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


# ---------------------------------------------------------------------------
# 6. Parse Claude's response into metadata + translated text
# ---------------------------------------------------------------------------
def parse_response(raw_response):
    """Split Claude's response into metadata (JSON) and the Markdown body."""
    if DELIMITER in raw_response:
        json_part, translated_text = raw_response.split(DELIMITER, 1)
        match = re.search(r"\{.*\}", json_part, re.DOTALL)
        clean_json = match.group(0) if match else json_part.strip()
        metadata = json.loads(clean_json)
        return metadata, translated_text.strip()

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
    """Write the translated Markdown to translated_legislation/ with version."""
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
                   mode=MODE_FULL, max_iterations=1):
    """Translate a single file, score it, and optionally re-iterate."""
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

        raw_response = ask_claude_to_translate(
            client, filename, raw_text, model=model,
            mode=mode, legal_terms=legal_terms_for_prompt,
        )

        try:
            metadata, translated_text = parse_response(raw_response)
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
            print(f"\n🔄 Grade {translated_scores['flesch_kincaid_grade']} exceeds target. Re-iterating...")

    out_path = save_translation(filename, translated_text, version=version, scores=translated_scores)
    print(f"\n💾 Saved translation to: {out_path}")
    return out_path


def run_batch(model="claude-sonnet-4-20250514", mode=MODE_FULL, max_iterations=1):
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

            raw_response = ask_claude_to_translate(
                client, filename, raw_text, model=model,
                mode=mode, legal_terms=legal_terms_for_prompt,
            )

            try:
                metadata, translated_text = parse_response(raw_response)
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
                print(f"\n🔄 Grade {translated_scores['flesch_kincaid_grade']} exceeds target. Re-iterating...")

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
        default=1,
        help="Maximum translation attempts to reach the target grade level (default: 1).",
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
