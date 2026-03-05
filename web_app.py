"""
Bill Translator — Web Interface

A simple Flask app that lets users upload a bill, translate it to an
8th-grade reading level, and review the before/after side-by-side.

Run:
    python web_app.py

Then open http://localhost:5000 in your browser.
"""

import os
import re
import uuid
from datetime import datetime, timezone

from flask import (Flask, render_template, request, redirect, url_for,
                   flash, session, jsonify)
from werkzeug.utils import secure_filename

from translator_agent import (
    get_client, ask_claude_to_translate, ask_claude_to_refine,
    ask_claude_targeted_refine, identify_hard_sentences,
    parse_response, apply_word_substitutions, split_long_sentences,
    score_readability, extract_legal_terms, compare_legal_terms,
    save_translation, read_file,
    MODE_FULL, MODE_PRESERVE_LEGAL, MODE_JARGON_ONLY,
    FK_TARGET_GRADE, SCRIPT_DIR, OUTPUT_DIR,
)

app = Flask(__name__)
app.secret_key = os.environ.get("FLASK_SECRET_KEY", os.urandom(32).hex())
# Note: The fallback random key means sessions won't survive app restarts.
# For production, set FLASK_SECRET_KEY in your .env file.

UPLOAD_DIR = os.path.join(SCRIPT_DIR, "uploads")
os.makedirs(UPLOAD_DIR, exist_ok=True)

ALLOWED_EXTENSIONS = {".txt"}
MAX_CONTENT_LENGTH = 2 * 1024 * 1024  # 2 MB
app.config["MAX_CONTENT_LENGTH"] = MAX_CONTENT_LENGTH

# In-memory store for translation sessions (keyed by session_id)
# In production, this would use a database.
translations = {}


def allowed_file(filename):
    """Check if the file extension is allowed."""
    return os.path.splitext(filename)[1].lower() in ALLOWED_EXTENSIONS


@app.route("/")
def index():
    """Landing page with upload form."""
    return render_template("index.html")


@app.route("/upload", methods=["POST"])
def upload():
    """Handle file upload or pasted text, translate, and show results."""
    # Get text from file upload OR from the text area
    raw_text = None
    filename = "pasted_text.txt"

    if "file" in request.files and request.files["file"].filename:
        file = request.files["file"]
        if not allowed_file(file.filename):
            flash("Only .txt files are allowed.", "error")
            return redirect(url_for("index"))
        filename = secure_filename(file.filename)
        raw_text = file.read().decode("utf-8", errors="replace")
    elif request.form.get("bill_text", "").strip():
        raw_text = request.form["bill_text"].strip()
    else:
        flash("Please upload a .txt file or paste bill text.", "error")
        return redirect(url_for("index"))

    if not raw_text.strip():
        flash("The uploaded file is empty.", "error")
        return redirect(url_for("index"))

    # Get translation mode
    mode_str = request.form.get("mode", "full")
    mode = {"full": MODE_FULL, "preserve_legal": MODE_PRESERVE_LEGAL,
            "jargon_only": MODE_JARGON_ONLY}.get(mode_str, MODE_FULL)

    # Score the original text
    original_scores = score_readability(raw_text)
    original_legal_terms = extract_legal_terms(raw_text)
    legal_terms_for_prompt = original_legal_terms if mode == MODE_PRESERVE_LEGAL else None

    # Translate
    try:
        client = get_client()
    except SystemExit:
        flash("ANTHROPIC_API_KEY is not configured. See .env.example.", "error")
        return redirect(url_for("index"))

    model = request.form.get("model", "claude-sonnet-4-20250514")
    # Basic validation: model name must be alphanumeric with hyphens/dots/underscores
    if not re.match(r"^[a-zA-Z0-9\-_.]+$", model):
        flash("Invalid model name.", "error")
        return redirect(url_for("index"))

    try:
        raw_response = ask_claude_to_translate(
            client, filename, raw_text, model=model,
            mode=mode, legal_terms=legal_terms_for_prompt,
        )
        metadata, translated_text = parse_response(raw_response)
        translated_text = apply_word_substitutions(translated_text)
        translated_text = split_long_sentences(translated_text)
    except Exception as e:
        flash(f"Translation failed: {e}", "error")
        return redirect(url_for("index"))

    # Score the translation
    translated_scores = score_readability(translated_text)

    # Drift detection
    translated_legal_terms = extract_legal_terms(translated_text)
    missing_terms = compare_legal_terms(original_legal_terms, translated_legal_terms)

    # Create a session record
    session_id = uuid.uuid4().hex[:12]
    translations[session_id] = {
        "filename": filename,
        "original_text": raw_text,
        "translated_text": translated_text,
        "original_scores": original_scores,
        "translated_scores": translated_scores,
        "metadata": metadata,
        "missing_terms": missing_terms,
        "original_legal_terms": original_legal_terms,
        "mode": mode_str,
        "model": model,
        "version": 1,
        "created": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
    }

    return redirect(url_for("results", session_id=session_id))


@app.route("/results/<session_id>")
def results(session_id):
    """Show side-by-side comparison of original and translated bill."""
    data = translations.get(session_id)
    if not data:
        flash("Session not found. Please upload a new bill.", "error")
        return redirect(url_for("index"))

    return render_template("results.html", data=data, session_id=session_id,
                           fk_target=FK_TARGET_GRADE)


@app.route("/re-iterate/<session_id>", methods=["POST"])
def re_iterate(session_id):
    """Re-translate the bill with an additional iteration."""
    data = translations.get(session_id)
    if not data:
        flash("Session not found. Please upload a new bill.", "error")
        return redirect(url_for("index"))

    mode = {"full": MODE_FULL, "preserve_legal": MODE_PRESERVE_LEGAL,
            "jargon_only": MODE_JARGON_ONLY}.get(data["mode"], MODE_FULL)

    legal_terms_for_prompt = data["original_legal_terms"] if mode == MODE_PRESERVE_LEGAL else None

    try:
        client = get_client()
        # Use refinement: feed back the previous translation and its FK score
        fk_grade = data["translated_scores"]["flesch_kincaid_grade"]
        legal_terms_for_refine = data["original_legal_terms"] if mode == MODE_PRESERVE_LEGAL else None

        raw_response = ask_claude_to_refine(
            client, data["translated_text"], fk_grade,
            model=data["model"],
            legal_terms=legal_terms_for_refine,
        )
        metadata, translated_text = parse_response(raw_response)
        translated_text = apply_word_substitutions(translated_text)
        translated_text = split_long_sentences(translated_text)
    except Exception as e:
        flash(f"Re-iteration failed: {e}", "error")
        return redirect(url_for("results", session_id=session_id))

    translated_scores = score_readability(translated_text)
    translated_legal_terms = extract_legal_terms(translated_text)
    missing_terms = compare_legal_terms(data["original_legal_terms"],
                                        translated_legal_terms)

    data["translated_text"] = translated_text
    data["translated_scores"] = translated_scores
    data["metadata"] = metadata
    data["missing_terms"] = missing_terms
    data["version"] += 1

    flash(f"Re-iteration complete (version {data['version']}).", "success")
    return redirect(url_for("results", session_id=session_id))


@app.route("/accept/<session_id>", methods=["POST"])
def accept(session_id):
    """Accept the translation and save it to disk."""
    data = translations.get(session_id)
    if not data:
        flash("Session not found.", "error")
        return redirect(url_for("index"))

    out_path = save_translation(
        data["filename"], data["translated_text"],
        version=data["version"], scores=data["translated_scores"],
    )

    abs_path = os.path.abspath(out_path)
    flash(
        f"Translation saved! File: {os.path.basename(out_path)} — "
        f"Location: {abs_path}",
        "success",
    )
    return redirect(url_for("results", session_id=session_id))


@app.route("/score-only", methods=["POST"])
def score_only():
    """Score text without translating."""
    raw_text = None
    filename = "pasted_text.txt"

    if "file" in request.files and request.files["file"].filename:
        file = request.files["file"]
        if not allowed_file(file.filename):
            return jsonify({"error": "Only .txt files are allowed."}), 400
        filename = secure_filename(file.filename)
        raw_text = file.read().decode("utf-8", errors="replace")
    elif request.form.get("bill_text", "").strip():
        raw_text = request.form["bill_text"].strip()
    else:
        return jsonify({"error": "No text provided."}), 400

    scores = score_readability(raw_text)
    return jsonify({"filename": filename, "scores": scores})


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    debug = os.environ.get("FLASK_DEBUG", "").lower() in ("1", "true", "yes")
    print(f"\n📜 Bill Translator — Web UI")
    print(f"   Open http://localhost:{port} in your browser.\n")
    app.run(host="0.0.0.0", port=port, debug=debug)
