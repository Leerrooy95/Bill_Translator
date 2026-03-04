# 📜 Arkansas Bill Translator

**Instantly rewrite any bill so it reads at an 8th-grade level — the new legal requirement in Arkansas.**

Arkansas **Act 602** (signed April 2025) says the Attorney General **cannot certify** a proposed ballot title if it reads above an 8th-grade level on the Flesch-Kincaid Grade Level scale. Bills that fail this test get rejected.

This tool takes a bill written in dense legal language, sends it to an AI model (Claude by Anthropic), and produces a plain-English version that keeps the original legal meaning while meeting the 8th-grade readability standard.

---

## How It Works (The Short Version)

1. You put your bill text in a file (plain `.txt`).
2. You run one command.
3. The tool sends the text to Claude, which rewrites it in plain English.
4. You get a clean Markdown file with the translated bill ready to review.

That's it.

---

## What You Need

| Requirement | Details |
|---|---|
| **Computer** | Windows, Mac, or Linux — any will work |
| **Python 3.8+** | Free — see install steps below |
| **Anthropic API key** | Takes 2 minutes to get at [console.anthropic.com](https://console.anthropic.com/) |

### How Much Does It Cost?

The tool uses Claude Sonnet 4.6 by default. Here's what a typical bill costs:

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
3. Open `.env` in any text editor and paste your key:
   ```
   ANTHROPIC_API_KEY=sk-ant-your-actual-key-here
   ```
4. Save the file. **Never share this key or commit the `.env` file.**

---

## Usage

### Translate a Single Bill

```
python3 translator_agent.py path/to/your_bill.txt
```

The translated file will be saved in the `translated_legislation/` folder.

### Translate Multiple Bills at Once (Batch Mode)

1. Drop all your `.txt` bill files into the `raw_legislation/` folder.
2. Run:
   ```
   python3 translator_agent.py
   ```
3. The tool will process each file one by one. Finished originals are moved to `raw_legislation/archive/`.

### Example

```
$ python3 translator_agent.py my_bill.txt

📄 Processing: my_bill.txt ...
✅ 8th-Grade Translation: Arkansas Clean Water Act Amendment
   Summary: This bill updates water quality standards for public water systems.
💾 Saved translation to: translated_legislation/translated_my_bill.md
```

---

## Folder Structure

```
Bill_Translator/
├── translator_agent.py       ← The main script you run
├── requirements.txt          ← Python packages (installed once)
├── .env.example              ← Template for your API key
├── .env                      ← Your actual API key (never shared)
├── raw_legislation/          ← Drop bill .txt files here for batch mode
│   └── archive/              ← Processed originals move here
└── translated_legislation/   ← Translated output appears here
```

---

## Frequently Asked Questions

**Q: What is the Flesch-Kincaid Grade Level?**
It's a formula that measures how hard text is to read. It looks at sentence length and word complexity. A score of 8.0 means an average 8th-grader can understand it. Arkansas Act 602 requires ballot titles to score at or below 8th grade.

**Q: Will the translation change the legal meaning of my bill?**
The AI is instructed to keep the legal meaning intact while simplifying the language. **You should always review the output** to make sure nothing was lost or changed. This is a drafting aid, not a replacement for legal review.

**Q: Can I use a different Claude model?**
Yes. Pass the `--model` flag:
```
python3 translator_agent.py my_bill.txt --model claude-opus-4-6
```
Claude Opus 4.6 is more capable but costs more ($5 / $25 per million input/output tokens vs. $3 / $15 for Sonnet). For most bills, Sonnet works great.

**Q: What format should my bill file be in?**
Plain text (`.txt`). Just copy-paste the bill text into a text file.

**Q: Something went wrong — where do I look?**
If the AI response can't be parsed, the raw output is saved to `crash_log.txt` in the project folder. Open it to see what happened.

---

## Background: Arkansas Act 602

In April 2025, Arkansas passed **Act 602** (HB 1713), which added a readability requirement to the ballot initiative process. The Attorney General must now reject any proposed ballot title that scores above an 8th-grade reading level on the Flesch-Kincaid Grade Level formula.

This has already led to multiple ballot measure rejections. For example, in June 2025, a proposed constitutional amendment on direct democracy was rejected because its ballot title scored at grade 11.5 — well above the 8th-grade limit.

This tool helps lawmakers, advocates, and drafters rewrite their proposals in plain language so they pass the readability test while keeping the legal substance intact.

---

## License

This project is provided as-is for public use. No warranty is expressed or implied.
