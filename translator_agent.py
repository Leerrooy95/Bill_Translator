import os
import subprocess
import json
import sys
import re
from datetime import datetime, timezone
from dotenv import load_dotenv
from openai import OpenAI

# 1. Setup & Auth
load_dotenv()
ANTHROPIC_KEY = os.getenv("ANTHROPIC_KEY")
GITHUB_TOKEN = os.getenv("GITHUB_TOKEN")

# Using your proven routing via the OpenAI client
client = OpenAI(base_url="https://api.anthropic.com/v1", api_key=ANTHROPIC_KEY)

# 2. Config for Droplet
AUTO_PR = True  
REPO_DIR = "/root/opus-daily-watcher"
INPUT_DIR = os.path.join(REPO_DIR, "raw_legislation")
DELIMITER = "===TRANSLATION_PAYLOAD_BEGINS_HERE==="

def get_github_repo():
    try:
        url = subprocess.check_output(['git', '-C', REPO_DIR, 'remote', 'get-url', 'origin']).decode('utf-8').strip()
        if 'github.com' in url:
            return url.split('github.com/')[-1].rstrip('/').replace('.git', '')
    except:
        pass
    return "Leerrooy95/Bill_Translator"

GITHUB_REPO = get_github_repo()

# 3. Fetching Raw Legal Text
def get_pending_legislation():
    if not os.path.exists(INPUT_DIR):
        os.makedirs(INPUT_DIR)
        return None, None

    files = [f for f in os.listdir(INPUT_DIR) if f.endswith('.txt') and not f.startswith('translated_')]
    if not files:
        return None, None

    target_file = files[0]
    filepath = os.path.join(INPUT_DIR, target_file)
    with open(filepath, "r") as f:
        content = f.read()
    return target_file, content

# 4. The Brain: Opus 4.6 with "Bulletproof" Header + Payload Logic
def ask_opus_to_translate(filename, raw_text, model="claude-opus-4-6"):
    system = f"""You are the Lead Legal Architect for The Regulated Friction Project.
Your directive is to translate state legislation into plain English.

OUTPUT FORMAT RULES:
1. Start your response IMMEDIATELY with the {{ character. No conversational filler or intro.
2. First, output a VALID JSON object with metadata.
3. Then, output exactly this delimiter on its own line: {DELIMITER}
4. Finally, output the full translation in clean Markdown (using headers, bolding, and bullet points).

READABILITY CONSTRAINT: 
The translation MUST score at an 8th-grade reading level. Break long sentences. Avoid legalese.

JSON Schema:
{{
  "STATUS": "SUCCESS",
  "PR_TITLE": "8th-Grade Translation: [Name]",
  "SUMMARY": "One sentence intent summary"
}}"""

    user = f"Target File: {filename}\n\nRaw Legal Text:\n{raw_text}"
    
    response = client.chat.completions.create(
        model=model,
        messages=[{"role": "system", "content": system}, {"role": "user", "content": user}],
        max_tokens=60000, 
        temperature=0.1
    )
    return response.choices[0].message.content.strip()

# 5. The Main Execution Loop
def run_translator():
    print(f"⚖️ Starting Legal Translation Pipeline — {datetime.now(timezone.utc)}")
    
    filename, raw_text = get_pending_legislation()
    if not filename:
        print("💤 No new legislation files found.")
        return

    print(f"📄 Processing: {filename}...")
    
    raw_response = ask_opus_to_translate(filename, raw_text)
    
    # PARSING: Splitting at the unique delimiter AND using Regex for safety
    try:
        if DELIMITER in raw_response:
            json_part, translated_text = raw_response.split(DELIMITER, 1)
            
            # --- THE REGEX FIX ---
            # Hunt down everything between the first { and the last }
            match = re.search(r'\{.*\}', json_part, re.DOTALL)
            if match:
                clean_json = match.group(0)
            else:
                clean_json = json_part.strip()
            # ---------------------
                
            metadata = json.loads(clean_json)
            translated_text = translated_text.strip()
        else:
            print("⚠️ Delimiter missing. Attempting to parse as pure JSON...")
            match = re.search(r'\{.*\}', raw_response, re.DOTALL)
            clean_json = match.group(0) if match else raw_response
            metadata = json.loads(clean_json)
            translated_text = "Error: Delimiter missing. Check raw logs."
            
        print(f"✅ Metadata Extracted: {metadata.get('SUMMARY')}")
    except Exception as e:
        with open("crash_log.txt", "w") as f:
            f.write(raw_response)
        print(f"❌ Critical Parsing Error: {e}. Raw response saved to crash_log.txt")
        return

    # 6. Saving and Auto-PR
    out_path = os.path.join(REPO_DIR, f"translated_{filename}")
    with open(out_path, "w") as f:
        f.write(translated_text)
    print(f"💾 Saved translation to: {out_path}")

    if AUTO_PR:
        branch = f"legis-fix-{datetime.now().strftime('%m%d-%H%M')}"
        try:
            subprocess.run(["git", "-C", REPO_DIR, "checkout", "-b", branch], check=True)
            subprocess.run(["git", "-C", REPO_DIR, "add", "."], check=True)
            subprocess.run(["git", "-C", REPO_DIR, "commit", "-m", metadata['PR_TITLE']], check=True)
            subprocess.run(["git", "-C", REPO_DIR, "push", "origin", branch], check=True)
            
            subprocess.run([
                "gh", "pr", "create", "--repo", GITHUB_REPO,
                "--title", metadata['PR_TITLE'],
                "--body", f"**Intent:** {metadata['SUMMARY']}\n\n*Translated via Opus 4.6 for 8th-grade readability compliance.*",
                "--draft", "--head", branch
            ], check=True)
            print(f"📦 PR Created Successfully!")
        except Exception as e:
            print(f"⚠️ PR failed: {e}")
            subprocess.run(["git", "-C", REPO_DIR, "checkout", "main"], capture_output=True)

    # Move processed file to archive
    archive_dir = os.path.join(INPUT_DIR, "archive")
    os.makedirs(archive_dir, exist_ok=True)
    os.rename(os.path.join(INPUT_DIR, filename), os.path.join(archive_dir, filename))
    print(f"📦 Archived raw file: {filename}")

if __name__ == "__main__":
    run_translator()
    sys.exit(0)
