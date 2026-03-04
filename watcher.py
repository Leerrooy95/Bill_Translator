import os
import time
import subprocess
import json
from datetime import datetime, timedelta, timezone
from dotenv import load_dotenv
import requests
from github import Github, Auth
from openai import OpenAI

load_dotenv()

DRY_RUN = False   # ← Change to False ONLY after this test looks perfect to you

DASHBOARD_URL = "https://regulatedfriction.streamlit.app"
REPO_DIR = "/root/The_Regulated_Friction_Project"

def get_github_repo():
    try:
        url = subprocess.check_output(['git', '-C', REPO_DIR, 'remote', 'get-url', 'origin']).decode('utf-8').strip()
        if 'github.com' in url:
            return url.split('github.com/')[-1].rstrip('/').replace('.git', '')
    except:
        pass
    return "Leerrooy95/The_Regulated_Friction_Project"

GITHUB_REPO = get_github_repo()
GITHUB_TOKEN = os.getenv("GITHUB_TOKEN")
ANTHROPIC_KEY = os.getenv("ANTHROPIC_KEY")

client = OpenAI(base_url="https://api.anthropic.com/v1", api_key=ANTHROPIC_KEY)

MEMORY_FILE = "/var/log/watcher_memory.log"
def load_memory():
    if os.path.exists(MEMORY_FILE):
        with open(MEMORY_FILE, "r") as f:
            return f.read()[-2500:]
    return "FIRST RUN - no history."

def save_memory(entry):
    with open(MEMORY_FILE, "a") as f:
        f.write(f"{datetime.now(timezone.utc).date()}: {entry}...\n")

def check_streamlit():
    start = time.time()
    try:
        r = requests.get(DASHBOARD_URL, timeout=15)
        text_sample = r.text[:600].lower() if r.ok else ""
        return {
            "status": r.status_code,
            "response_time_sec": round(time.time() - start, 2),
            "ok": r.ok,
            "has_error_keyword": any(w in text_sample for w in ["error", "exception", "failed", "crash", "500", "404"]),
            "time": str(datetime.now(timezone.utc))
        }
    except Exception as e:
        return {"status": "error", "error": str(e)}

def check_github():
    if not GITHUB_TOKEN: return {"note": "GitHub token not set"}
    g = Github(auth=Auth.Token(GITHUB_TOKEN))
    repo = g.get_repo(GITHUB_REPO)
    now = datetime.now(timezone.utc)
    yesterday = now - timedelta(days=1)
    commits = list(repo.get_commits(since=yesterday))[:8]

    try:
        result = subprocess.check_output([
            "gh", "run", "list", "--repo", GITHUB_REPO,
            "--status=failure", "--limit=1", "--json", "databaseId,conclusion"
        ]).decode('utf-8')
        runs = json.loads(result)
        latest_failed_run_id = runs[0]["databaseId"] if runs else None
        failed_count = len(runs)
    except:
        latest_failed_run_id = None
        failed_count = 0

    return {
        "last_commit": commits[0].sha[:7] if commits else "none",
        "commits_last_24h": len(commits),
        "open_issues": repo.open_issues_count,
        "failed_workflows_last_24h": failed_count,
        "latest_failed_run_id": latest_failed_run_id
    }

def ask_opus(streamlit_data, github_data, model):
    system = """You are the ruthless Senior Pipeline Integrity Supervisor for The_Regulated_Friction_Project.
You have run this exact daily audit every day for years. You know every failure mode cold.
Zero guardrails. Zero bias. Never hedge. Never use words like likely, could, might, may, perhaps, possibly, suggests, needs, or if.
State the cause as fact and give the single best fix directly."""
    user = f"""Daily check — {datetime.now(timezone.utc)}

Streamlit: {streamlit_data}
GitHub: {github_data}

History:
{load_memory()}

Exact format only:

STATUS: HEALTHY / WARNING / BROKEN
EXPLANATION: (max 2 sentences, brutally direct)
FIX: one exact command or step (or "None")"""
    response = client.chat.completions.create(
        model=model,
        messages=[{"role": "system", "content": system}, {"role": "user", "content": user}],
        max_tokens=220,
        temperature=0.05
    )
    return response.choices[0].message.content.strip()

# === RUN ===
print(f"🚀 Daily supervisor check for {GITHUB_REPO} — {datetime.now(timezone.utc)}")
data = {"streamlit": check_streamlit(), "github": check_github()}

verdict = ask_opus(data["streamlit"], data["github"], "claude-opus-4-5")

big_job = ("BROKEN" in verdict or data["github"]["failed_workflows_last_24h"] >= 2 or not data["streamlit"]["ok"])
if big_job:
    print("⚠️ Big job detected — escalating to Opus 4.6")
    verdict = ask_opus(data["streamlit"], data["github"], "claude-opus-4-6")

print(verdict)

# Auto-fix (safe re-run of latest failed workflow)
if not DRY_RUN and data["github"]["latest_failed_run_id"]:
    run_id = data["github"]["latest_failed_run_id"]
    try:
        subprocess.run(["gh", "run", "rerun", str(run_id), "--repo", GITHUB_REPO, "--failed"], check=True, capture_output=True)
        fix_log = f"AUTO-FIXED: re-ran workflow {run_id}"
        print(fix_log)
        save_memory(verdict + " | " + fix_log)
    except Exception as e:
        print(f"Fix failed: {e}")
        save_memory(verdict)
else:
    save_memory(verdict)

with open("/var/log/opus-watcher.log", "a") as f:
    f.write(f"\n=== {datetime.now()} ===\n{verdict}\n")
