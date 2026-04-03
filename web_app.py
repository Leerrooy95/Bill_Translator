"""
Bill Translator — Web Interface

A Flask app that lets users upload a bill (txt, pdf, or md), translate it to
an 8th-grade reading level, and review the before/after side-by-side.  Also
supports fact-checking claims in bills via Brave Search + Claude.

Run:
    python web_app.py

Then open http://localhost:5000 in your browser.
"""

import io
import os
import re
import uuid
from datetime import datetime, timezone

from flask import (Flask, render_template, request, redirect, url_for,
                   flash, session, jsonify, send_file)
from werkzeug.utils import secure_filename

from config import Config
from translator_agent import (
    get_client, ask_claude_to_translate, ask_claude_to_refine,
    ask_claude_targeted_refine, identify_hard_sentences,
    parse_response, apply_word_substitutions, split_long_sentences,
    score_readability, extract_legal_terms, compare_legal_terms,
    save_translation, read_file,
    MODE_FULL, MODE_PRESERVE_LEGAL, MODE_JARGON_ONLY,
    FK_TARGET_GRADE, SCRIPT_DIR, OUTPUT_DIR,
)
import document_processor
import fact_checker

app = Flask(__name__)
app.config.from_object(Config)

UPLOAD_DIR = Config.UPLOAD_DIR
os.makedirs(UPLOAD_DIR, exist_ok=True)

ALLOWED_EXTENSIONS = {".txt", ".pdf", ".md"}


# ─── Security Headers ────────────────────────────────────────────────────────
@app.after_request
def set_security_headers(response):
    """Add security headers to every response."""
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["X-XSS-Protection"] = "1; mode=block"
    response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    # Note: 'unsafe-inline' is required for Bootstrap's JS and the existing
    # inline <script> blocks in templates. In a future refactor, move all
    # inline scripts to external files and use nonce-based CSP instead.
    response.headers["Content-Security-Policy"] = (
        "default-src 'self'; "
        "script-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net; "
        "style-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net; "
        "img-src 'self' data:; "
        "font-src 'self'; "
        "connect-src 'self'"
    )
    return response

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
            flash("Only .txt, .pdf, and .md files are allowed.", "error")
            return redirect(url_for("index"))
        filename = secure_filename(file.filename)
        ext = os.path.splitext(filename)[1].lower()
        # Use document_processor for PDF and MD files
        if ext in (".pdf", ".md"):
            save_path, original_name = document_processor.save_uploaded_file(
                file, UPLOAD_DIR
            )
            try:
                result = document_processor.process_file(save_path, filename)
                raw_text = result["raw_text"]
            except Exception as e:
                flash(f"File processing failed: {e}", "error")
                return redirect(url_for("index"))
            finally:
                # Clean up saved file
                if os.path.exists(save_path):
                    os.remove(save_path)
        else:
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

    # Capture the user-supplied API key (BYOK)
    user_api_key = request.form.get("api_key", "").strip() or None
    if user_api_key:
        session["api_key"] = user_api_key

    # Translate
    try:
        client = get_client(api_key=user_api_key or session.get("api_key"))
    except SystemExit:
        flash("No API key provided. Enter your Anthropic API key below, "
              "or set ANTHROPIC_API_KEY on the server.", "error")
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
        client = get_client(api_key=session.get("api_key"))
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
    """Accept the translation and return it as a downloadable file."""
    data = translations.get(session_id)
    if not data:
        flash("Session not found.", "error")
        return redirect(url_for("index"))

    out_path = save_translation(
        data["filename"], data["translated_text"],
        version=data["version"], scores=data["translated_scores"],
    )

    abs_path = os.path.abspath(out_path)
    download_name = os.path.basename(out_path)

    # Read into memory and remove the temp file to avoid disk accumulation
    with open(abs_path, "rb") as f:
        content = io.BytesIO(f.read())
    os.remove(abs_path)

    return send_file(
        content,
        as_attachment=True,
        download_name=download_name,
        mimetype="text/markdown",
    )


@app.route("/score-only", methods=["POST"])
def score_only():
    """Score text without translating."""
    raw_text = None
    filename = "pasted_text.txt"

    if "file" in request.files and request.files["file"].filename:
        file = request.files["file"]
        if not allowed_file(file.filename):
            return jsonify({"error": "Only .txt, .pdf, and .md files are allowed."}), 400
        filename = secure_filename(file.filename)
        ext = os.path.splitext(filename)[1].lower()
        if ext in (".pdf", ".md"):
            save_path, original_name = document_processor.save_uploaded_file(
                file, UPLOAD_DIR
            )
            try:
                result = document_processor.process_file(save_path, filename)
                raw_text = result["raw_text"]
            except Exception as e:
                return jsonify({"error": f"File processing failed: {e}"}), 400
            finally:
                if os.path.exists(save_path):
                    os.remove(save_path)
        else:
            raw_text = file.read().decode("utf-8", errors="replace")
    elif request.form.get("bill_text", "").strip():
        raw_text = request.form["bill_text"].strip()
    else:
        return jsonify({"error": "No text provided."}), 400

    scores = score_readability(raw_text)
    return jsonify({"filename": filename, "scores": scores})


@app.route("/fact-check", methods=["POST"])
def fact_check():
    """Verify a claim from a bill against web sources."""
    claim = request.form.get("claim", "").strip()
    if not claim:
        return jsonify({"error": "No claim provided."}), 400

    brave_key = request.form.get("brave_key", "").strip() or session.get("brave_key")
    if not brave_key:
        return jsonify({"error": "Brave Search API key is required for fact-checking."}), 400

    user_api_key = request.form.get("api_key", "").strip() or session.get("api_key")
    if not user_api_key:
        return jsonify({"error": "Anthropic API key is required."}), 400

    # Store keys in session for convenience
    session["brave_key"] = brave_key
    session["api_key"] = user_api_key

    model = request.form.get("model", Config.ANTHROPIC_MODEL)
    if not re.match(r"^[a-zA-Z0-9\-_.]+$", model):
        return jsonify({"error": "Invalid model name."}), 400

    result = fact_checker.verify_claim(claim, user_api_key, brave_key, model=model)
    return jsonify(result)


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    debug = os.environ.get("FLASK_DEBUG", "").lower() in ("1", "true", "yes")
    print(f"\n📜 Bill Translator — Web UI")
    print(f"   Open http://localhost:{port} in your browser.\n")
    app.run(host="0.0.0.0", port=port, debug=debug)
