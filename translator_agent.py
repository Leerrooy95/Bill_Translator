import os
import json
import sys
import re
import argparse
from datetime import datetime, timezone
from dotenv import load_dotenv
import anthropic

# ---------------------------------------------------------------------------
# 1. Setup & Auth
# ---------------------------------------------------------------------------
load_dotenv()

DELIMITER = "===TRANSLATION_PAYLOAD_BEGINS_HERE==="

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
# 2. Fetching Raw Legal Text
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
# 3. The Brain: Claude translates legislation to 8th-grade reading level
# ---------------------------------------------------------------------------
def ask_claude_to_translate(client, filename, raw_text, model="claude-sonnet-4-20250514"):
    """Send the raw bill text to Claude and return the translated response."""
    system = f"""You are a legal plain-language expert.
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
everyday words. Keep the legal meaning intact.

JSON Schema:
{{
  "STATUS": "SUCCESS",
  "TITLE": "8th-Grade Translation: [Short Name of Bill]",
  "SUMMARY": "One sentence describing the bill's intent"
}}"""

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
# 4. Parse Claude's response into metadata + translated text
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
# 5. Save the translated output
# ---------------------------------------------------------------------------
def save_translation(filename, translated_text):
    """Write the translated Markdown to translated_legislation/."""
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    out_name = f"translated_{filename}"
    if not out_name.endswith(".md"):
        out_name = out_name.rsplit(".", 1)[0] + ".md"
    out_path = os.path.join(OUTPUT_DIR, out_name)
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(translated_text)
    return out_path


# ---------------------------------------------------------------------------
# 6. Archive the original raw file after processing
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
# 7. Main entry point
# ---------------------------------------------------------------------------
def translate_file(filepath, model="claude-sonnet-4-20250514"):
    """Translate a single file and save the result."""
    client = get_client()
    filename = os.path.basename(filepath)
    raw_text = read_file(filepath)

    print(f"📄 Processing: {filename} ...")
    raw_response = ask_claude_to_translate(client, filename, raw_text, model=model)

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

    out_path = save_translation(filename, translated_text)
    print(f"💾 Saved translation to: {out_path}")
    return out_path


def run_batch():
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
        print(f"\n📄 Processing: {filename} ...")
        raw_response = ask_claude_to_translate(client, filename, raw_text)

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

        out_path = save_translation(filename, translated_text)
        print(f"💾 Saved translation to: {out_path}")
        archive_raw_file(filename)

        filename, raw_text = get_pending_legislation()

    print("\n🏁 All done.")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def main():
    parser = argparse.ArgumentParser(
        description="Translate Arkansas legislation to an 8th-grade reading level."
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
    args = parser.parse_args()

    if args.file:
        if not os.path.isfile(args.file):
            print(f"❌ File not found: {args.file}")
            sys.exit(1)
        translate_file(args.file, model=args.model)
    else:
        run_batch()


if __name__ == "__main__":
    main()
