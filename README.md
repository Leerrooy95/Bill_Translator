# 📜 Arkansas Bill Translator

**Instantly rewrite any bill so it reads at an 8th-grade level — the new legal requirement in Arkansas.**

Arkansas **Act 602** (signed April 2025) says the Attorney General **cannot certify** a proposed ballot title if it reads above an 8th-grade level on the Flesch-Kincaid Grade Level scale. Bills that fail this test get rejected.

This tool takes a bill written in dense legal language, sends it to an AI model (Claude by Anthropic), and produces a plain-English version that keeps the original legal meaning while meeting the 8th-grade readability standard.

---

## Features

| Feature | Description |
|---|---|
| **Built-in Flesch-Kincaid Scorer** | Locally scores both original and translated text — no guessing |
| **Meaning Drift Detection** | Compares legal terms between original and translation to flag potential intent shifts |
| **Translation Versioning** | Every iteration is numbered (v1, v2, …) so you can track changes |
| **Web UI** | Upload a bill → see before/after side-by-side → accept or reject → re-iterate |
| **Three Translation Modes** | Full simplification, preserve legal terms, or jargon-only replacement |
| **Auto Re-iteration** | Set `--max-iterations` to automatically retry until the target grade is reached |
| **Score-Only Mode** | Check any file's readability grade without translating |
| **Batch Mode** | Process multiple bills at once from the `raw_legislation/` folder |

---

## How It Works (The Short Version)

1. You put your bill text in a file (plain `.txt`).
2. You run one command (or use the web UI).
3. The tool sends the text to Claude, which rewrites it in plain English.
4. You see the Flesch-Kincaid scores for both versions and any drift warnings.
5. You get a clean Markdown file with the translated bill ready to review.

That's it.

---

## What You Need

| Requirement | Details |
|---|---|
| **Computer** | Windows, Mac, or Linux — any will work |
| **Python 3.8+** | Free — see install steps below |
| **Anthropic API key** | Takes 2 minutes to get at [console.anthropic.com](https://console.anthropic.com/) |

### How Much Does It Cost?

The tool uses Claude Sonnet 4 by default. Here's what a typical bill costs:

| Bill Length | Approximate Cost |
|---|---|
| 5 pages | ~$0.05 – $0.10 |
| 20 pages | ~$0.15 – $0.40 |
| 50 pages | ~$0.40 – $1.00 |

> **A 20-page bill costs roughly a quarter.** You only pay for what you use — there's no monthly fee from Anthropic beyond the per-use API charges. You load credits onto your account and they're spent as you translate bills.

---

## Setup (One-Time, ~5 Minutes)

### Step 1 — Install Python

If you don't have Python yet:

- **Windows:** Download from [python.org/downloads](https://www.python.org/downloads/). During install, **check the box that says "Add Python to PATH"**.
- **Mac:** Open Terminal and run: `brew install python3` (or download from python.org).
- **Linux:** Run: `sudo apt install python3 python3-pip`

Verify it works by opening a terminal / command prompt and typing:

```
python3 --version
```

You should see something like `Python 3.12.3`.

### Step 2 — Download This Repository

Click the green **Code** button at the top of this page, then **Download ZIP**. Unzip it anywhere you like.

Or, if you use Git:

```
git clone https://github.com/Leerrooy95/Bill_Translator.git
cd Bill_Translator
```

### Step 3 — Install Dependencies

Open a terminal in the project folder and run:

```
pip install -r requirements.txt
```

### Step 4 — Add Your API Key

1. Go to [console.anthropic.com/settings/keys](https://console.anthropic.com/settings/keys) and create an API key.
2. Copy the file `.env.example` to `.env`:
   ```
   cp .env.example .env
   ```
3. Open `.env` in any text editor (type **nano .env**) and paste your key:
   ```
   ANTHROPIC_API_KEY=sk-ant-your-actual-key-here
   ```
4. Save the file (Control+X, Y to save, then press enter). **Never share this key or commit the `.env` file.**

---

## Usage

### Option 1: Web UI (Recommended for Most Users)

Start the web interface:

```
python3 web_app.py
```

Then open **http://localhost:5000** (or similar URL found at the bottom of the output) in your browser. You'll see a simple page where you can:

1. **Upload a .txt file** or **paste bill text** directly
2. **Choose a translation mode:**
   - **Full Simplification** — Rewrite everything at an 8th-grade level
   - **Preserve Legal Terms** — Keep legal terms exactly as written, simplify surrounding language
   - **Jargon Only** — Only swap complex words for simpler ones, keep structure intact
3. **Review the results** side-by-side with Flesch-Kincaid scores for both versions
4. **Accept** the translation (saves to disk), **Re-iterate** (try again), or **Reject** and start over

### Option 2: Command Line

#### Translate a Single Bill

```
python3 translator_agent.py path/to/your_bill.txt
```

#### Check Readability Score Only (No Translation)

```
python3 translator_agent.py --score-only path/to/your_bill.txt
```

Output:
```
📊 your_bill.txt Readability:
   Flesch-Kincaid Grade Level: 16.6 (❌ FAIL — target ≤ 8.0)
   Flesch Reading Ease:        23.6
   Word Count:                 187
   Sentence Count:             7
```

#### Translate with Preserved Legal Terms

```
python3 translator_agent.py bill.txt --preserve-legal-terms
```

#### Translate Jargon Only (Keep Structure)

```
python3 translator_agent.py bill.txt --simplify-jargon-only
```

#### Auto Re-iterate Until Target Grade is Met

```
python3 translator_agent.py bill.txt --max-iterations 3
```

#### Batch Mode (Multiple Bills)

1. Drop all your `.txt` bill files into the `raw_legislation/` folder.
2. Run:
   ```
   python3 translator_agent.py
   ```
3. The tool processes each file one by one. Finished originals are moved to `raw_legislation/archive/`.

You can combine flags in batch mode too:
```
python3 translator_agent.py --preserve-legal-terms --max-iterations 2
```

### Example Output

```
$ python3 translator_agent.py my_bill.txt --max-iterations 2

📊 ORIGINAL Readability:
   Flesch-Kincaid Grade Level: 16.6 (❌ FAIL — target ≤ 8.0)
   Flesch Reading Ease:        23.6
   Word Count:                 187
   Sentence Count:             7

📄 Processing: my_bill.txt (iteration 1/2) ...
✅ 8th-Grade Translation: Arkansas Ballot Title Readability Act
   Summary: This bill requires ballot titles to be written at an 8th-grade reading level.

📊 TRANSLATED Readability:
   Flesch-Kincaid Grade Level: 6.8 (✅ PASS — target ≤ 8.0)
   Flesch Reading Ease:        72.3
   Word Count:                 145
   Sentence Count:             12

💾 Saved translation to: translated_legislation/translated_my_bill_v1.md
```

---

## Folder Structure

```
Bill_Translator/
├── translator_agent.py       ← CLI script for translating bills
├── web_app.py                ← Web UI (run this for the browser interface)
├── templates/                ← HTML templates for the web UI
│   ├── index.html            ← Upload page
│   └── results.html          ← Side-by-side comparison page
├── tests.py                  ← Automated tests
├── requirements.txt          ← Python packages (installed once)
├── .env.example              ← Template for your API key
├── .env                      ← Your actual API key (never shared)
├── raw_legislation/          ← Drop bill .txt files here for batch mode
│   └── archive/              ← Processed originals move here
└── translated_legislation/   ← Translated output appears here
```

---

## Meaning Drift Detection

Legal text is unforgiving — even small word changes can shift intent. The translator includes automatic drift detection:

1. **Legal Term Extraction** — Before translating, the tool identifies section references, defined terms, official titles, and common legal phrases in the original.
2. **Comparison** — After translating, it checks whether those terms appear in the output.
3. **Drift Warnings** — If key terms are missing, you'll see a warning like:

```
⚠️  Potential meaning drift — 2 legal term(s) not found in translation:
   • Section 7-9-107
   • ballot title
```

This doesn't mean the translation is wrong — it means you should double-check those areas. The web UI shows these warnings prominently on the results page.

---

## Running Tests

```
python3 -m unittest tests -v
```

This runs 25 automated tests covering readability scoring, legal term extraction, drift detection, response parsing, and the web interface.

---

## Frequently Asked Questions

**Q: What is the Flesch-Kincaid Grade Level?**
It's a formula that measures how hard text is to read. It looks at sentence length and word complexity. A score of 8.0 means an average 8th-grader can understand it. Arkansas Act 602 requires ballot titles to score at or below 8th grade.

**Q: Will the translation change the legal meaning of my bill?**
The AI is instructed to keep the legal meaning intact while simplifying the language. The tool also runs automatic drift detection to flag potential issues. **You should always review the output** to make sure nothing was lost or changed. This is a drafting aid, not a replacement for legal review.

**Q: What's the difference between the three translation modes?**
- **Full Simplification** — Rewrites everything. Best for getting the lowest grade level.
- **Preserve Legal Terms** — Keeps terms like "Section 7-9-107", "Attorney General", and defined terms exactly as written. Simplifies only the surrounding language.
- **Jargon Only** — Only swaps complex vocabulary for simpler words. Does not restructure sentences or reorder anything. Most conservative option.

**Q: Can I use a different Claude model?**
Yes. Pass the `--model` flag:
```
python3 translator_agent.py my_bill.txt --model claude-opus-4-6
```

**Q: What format should my bill file be in?**
Plain text (`.txt`). Just copy-paste the bill text into a text file.

**Q: Something went wrong — where do I look?**
If the AI response can't be parsed, the raw output is saved to `crash_log.txt` in the project folder. Open it to see what happened.

**Q: Can I use the web UI on a server?**
Yes. Run `python3 web_app.py` on your server and access it at `http://your-server-ip:5000`. For production use, consider putting it behind a reverse proxy (like Nginx) with a proper WSGI server (like Gunicorn).

---

## Background: Arkansas Act 602

In April 2025, Arkansas passed **Act 602** (HB 1713), which added a readability requirement to the ballot initiative process. The Attorney General must now reject any proposed ballot title that scores above an 8th-grade reading level on the Flesch-Kincaid Grade Level formula.

This has already led to multiple ballot measure rejections. For example, in June 2025, a proposed constitutional amendment on direct democracy was rejected because its ballot title scored at grade 11.5 — well above the 8th-grade limit.

This tool helps lawmakers, advocates, and drafters rewrite their proposals in plain language so they pass the readability test while keeping the legal substance intact.

---

## License

This project is provided as-is for public use. No warranty is expressed or implied.
