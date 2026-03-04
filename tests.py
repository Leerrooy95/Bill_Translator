"""
Tests for the Arkansas Bill Translator.

Covers:
  - Flesch-Kincaid scoring
  - Legal term extraction
  - Legal term drift comparison
  - Response parsing
  - CLI argument parsing
  - Web app routes (upload page, score-only endpoint)
"""

import json
import os
import sys
import tempfile
import unittest

# Ensure the project root is on the path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from translator_agent import (
    score_readability,
    extract_legal_terms,
    compare_legal_terms,
    parse_response,
    save_translation,
    build_system_prompt,
    build_refinement_prompt,
    strip_markdown,
    apply_word_substitutions,
    DELIMITER,
    FK_TARGET_GRADE,
    MODE_FULL,
    MODE_PRESERVE_LEGAL,
    MODE_JARGON_ONLY,
)


# ---------------------------------------------------------------------------
# Readability scoring tests
# ---------------------------------------------------------------------------
class TestScoreReadability(unittest.TestCase):
    """Tests for the Flesch-Kincaid readability scorer."""

    def test_simple_text_scores_low(self):
        text = "The cat sat on the mat. It was a good cat. The mat was red."
        scores = score_readability(text)
        self.assertLessEqual(scores["flesch_kincaid_grade"], 5.0)
        self.assertTrue(scores["passes_act602"])

    def test_complex_text_scores_high(self):
        text = (
            "Notwithstanding the aforementioned provisions of the constitutional "
            "amendment pertaining to the establishment of jurisdictional "
            "boundaries and the adjudication of administrative proceedings "
            "thereunder, the legislature shall promulgate regulations."
        )
        scores = score_readability(text)
        self.assertGreater(scores["flesch_kincaid_grade"], 12.0)
        self.assertFalse(scores["passes_act602"])

    def test_scores_contain_expected_keys(self):
        scores = score_readability("Hello world.")
        self.assertIn("flesch_kincaid_grade", scores)
        self.assertIn("flesch_reading_ease", scores)
        self.assertIn("word_count", scores)
        self.assertIn("sentence_count", scores)
        self.assertIn("passes_act602", scores)

    def test_word_count_accuracy(self):
        text = "One two three four five."
        scores = score_readability(text)
        self.assertEqual(scores["word_count"], 5)

    def test_target_grade_is_eight(self):
        self.assertEqual(FK_TARGET_GRADE, 8.0)


# ---------------------------------------------------------------------------
# Legal term extraction tests
# ---------------------------------------------------------------------------
class TestExtractLegalTerms(unittest.TestCase):
    """Tests for legal term extraction."""

    def test_section_references(self):
        text = "See Section 7-9-107 for details."
        terms = extract_legal_terms(text)
        self.assertTrue(any("7-9-107" in t for t in terms))

    def test_quoted_terms(self):
        text = 'The term "ballot title" is defined in the statute.'
        terms = extract_legal_terms(text)
        self.assertIn("ballot title", terms)

    def test_common_legal_phrases(self):
        text = "The Attorney General shall review all submissions."
        terms = extract_legal_terms(text)
        self.assertIn("Attorney General", terms)

    def test_empty_text(self):
        terms = extract_legal_terms("")
        self.assertEqual(terms, [])

    def test_due_process(self):
        text = "This violates due process protections."
        terms = extract_legal_terms(text)
        self.assertIn("due process", terms)


# ---------------------------------------------------------------------------
# Legal term drift comparison tests
# ---------------------------------------------------------------------------
class TestCompareLegalTerms(unittest.TestCase):
    """Tests for comparing legal terms between original and translation."""

    def test_no_drift(self):
        original = ["Section 7-9-107", "Attorney General", "ballot title"]
        translated = ["section 7-9-107", "attorney general", "ballot title"]
        missing = compare_legal_terms(original, translated)
        self.assertEqual(missing, [])

    def test_missing_term(self):
        original = ["Section 7-9-107", "Attorney General"]
        translated = ["Attorney General"]
        missing = compare_legal_terms(original, translated)
        self.assertTrue(any("7-9-107" in t for t in missing))

    def test_all_caps_filtered_out(self):
        original = ["SECTION ONE", "Attorney General"]
        translated = ["Attorney General"]
        missing = compare_legal_terms(original, translated)
        # ALL-CAPS headings should be filtered
        self.assertFalse(any(t.isupper() for t in missing))


# ---------------------------------------------------------------------------
# Response parsing tests
# ---------------------------------------------------------------------------
class TestParseResponse(unittest.TestCase):
    """Tests for parsing Claude's response."""

    def test_valid_response(self):
        response = (
            '{"STATUS": "SUCCESS", "TITLE": "Test Bill", "SUMMARY": "A test."}\n'
            + DELIMITER + "\n"
            + "# Translated Bill\n\nThis is the translation."
        )
        metadata, text = parse_response(response)
        self.assertEqual(metadata["STATUS"], "SUCCESS")
        self.assertIn("Translated Bill", text)
        # Markdown headers should be stripped
        self.assertNotIn("#", text)

    def test_markdown_stripped_from_response(self):
        response = (
            '{"STATUS": "SUCCESS", "TITLE": "Test", "SUMMARY": "A test."}\n'
            + DELIMITER + "\n"
            + "## Section One\n\n**Bold text** and *italic text*.\n\n"
            + "- Bullet one\n- Bullet two\n"
        )
        metadata, text = parse_response(response)
        self.assertNotIn("#", text)
        self.assertNotIn("**", text)
        self.assertNotIn("*", text)
        self.assertIn("Bold text", text)
        self.assertIn("italic text", text)
        self.assertIn("Bullet one", text)

    def test_missing_delimiter_fallback(self):
        response = '{"STATUS": "SUCCESS", "TITLE": "Test"}'
        metadata, text = parse_response(response)
        self.assertEqual(metadata["STATUS"], "SUCCESS")


# ---------------------------------------------------------------------------
# System prompt builder tests
# ---------------------------------------------------------------------------
class TestBuildSystemPrompt(unittest.TestCase):
    """Tests for the system prompt builder."""

    def test_full_mode(self):
        prompt = build_system_prompt(mode=MODE_FULL)
        self.assertIn("8th-grade reading level", prompt)
        self.assertIn("PLAIN TEXT", prompt)
        self.assertIn("Do NOT use any Markdown", prompt)
        self.assertNotIn("PRESERVE LEGAL TERMS", prompt)
        self.assertNotIn("SIMPLIFY JARGON ONLY", prompt)

    def test_prompt_contains_sentence_length_guidance(self):
        prompt = build_system_prompt(mode=MODE_FULL)
        # Must mention keeping sentences short (10-12 words)
        self.assertIn("12 words", prompt)

    def test_prompt_contains_syllable_guidance(self):
        prompt = build_system_prompt(mode=MODE_FULL)
        # Must mention using words with fewer syllables
        self.assertIn("syllable", prompt.lower())

    def test_prompt_contains_word_substitutions(self):
        prompt = build_system_prompt(mode=MODE_FULL)
        # Must include concrete word substitution examples
        self.assertIn('"shall"', prompt)
        self.assertIn('"notwithstanding"', prompt)
        self.assertIn('"commence"', prompt)

    def test_prompt_contains_active_voice_guidance(self):
        prompt = build_system_prompt(mode=MODE_FULL)
        self.assertIn("active voice", prompt.lower())

    def test_prompt_contains_self_check(self):
        prompt = build_system_prompt(mode=MODE_FULL)
        self.assertIn("SELF-CHECK", prompt)

    def test_prompt_contains_fk_formula(self):
        prompt = build_system_prompt(mode=MODE_FULL)
        # Must explain the FK formula so the model understands what drives the score
        self.assertIn("0.39", prompt)
        self.assertIn("11.8", prompt)

    def test_preserve_legal_mode(self):
        terms = ["Section 7-9-107", "ballot title"]
        prompt = build_system_prompt(mode=MODE_PRESERVE_LEGAL, legal_terms=terms)
        self.assertIn("PRESERVE LEGAL TERMS", prompt)
        self.assertIn("Section 7-9-107", prompt)

    def test_jargon_only_mode(self):
        prompt = build_system_prompt(mode=MODE_JARGON_ONLY)
        self.assertIn("SIMPLIFY JARGON ONLY", prompt)


# ---------------------------------------------------------------------------
# Refinement prompt tests
# ---------------------------------------------------------------------------
class TestBuildRefinementPrompt(unittest.TestCase):
    """Tests for the refinement prompt builder."""

    def test_includes_current_fk_grade(self):
        prompt = build_refinement_prompt("Some text here.", 12.3)
        self.assertIn("12.3", prompt)

    def test_includes_target_grade(self):
        prompt = build_refinement_prompt("Some text here.", 12.3)
        self.assertIn(str(FK_TARGET_GRADE), prompt)

    def test_includes_text_to_simplify(self):
        prompt = build_refinement_prompt("The cat sat on the mat.", 10.0)
        self.assertIn("The cat sat on the mat.", prompt)

    def test_includes_fk_formula(self):
        prompt = build_refinement_prompt("Text.", 9.5)
        self.assertIn("0.39", prompt)
        self.assertIn("11.8", prompt)

    def test_includes_sentence_splitting_guidance(self):
        prompt = build_refinement_prompt("Text.", 9.5)
        self.assertIn("12 words", prompt)

    def test_includes_syllable_guidance(self):
        prompt = build_refinement_prompt("Text.", 9.5)
        self.assertIn("syllable", prompt.lower())

    def test_includes_delimiter(self):
        prompt = build_refinement_prompt("Text.", 9.5)
        self.assertIn(DELIMITER, prompt)

    def test_includes_legal_terms_when_provided(self):
        terms = ["Section 7-9-107", "ballot title"]
        prompt = build_refinement_prompt("Text.", 10.0, legal_terms=terms)
        self.assertIn("Section 7-9-107", prompt)
        self.assertIn("ballot title", prompt)
        self.assertIn("MUST be kept exactly", prompt)

    def test_no_legal_terms_section_when_none(self):
        prompt = build_refinement_prompt("Text.", 10.0, legal_terms=None)
        self.assertNotIn("MUST be kept exactly", prompt)

    def test_refinement_includes_word_substitutions(self):
        prompt = build_refinement_prompt("Text.", 10.0)
        # Refinement prompt should now include word swap guidance
        self.assertIn("requirements", prompt)
        self.assertIn("provisions", prompt)
        self.assertIn("rules", prompt)

    def test_refinement_aggressive_sentence_target(self):
        prompt = build_refinement_prompt("Text.", 10.0)
        # Should target 8-10 words per sentence
        self.assertIn("12 words", prompt)


# ---------------------------------------------------------------------------
# Word substitution post-processing tests
# ---------------------------------------------------------------------------
class TestApplyWordSubstitutions(unittest.TestCase):
    """Tests for the programmatic word substitution post-processor."""

    def test_replaces_notwithstanding(self):
        result = apply_word_substitutions("Notwithstanding the above rules.")
        self.assertNotIn("Notwithstanding", result)
        self.assertIn("despite", result.lower())

    def test_replaces_multi_word_phrase(self):
        result = apply_word_substitutions("This is pursuant to the law.")
        self.assertNotIn("pursuant to", result.lower())
        self.assertIn("under", result.lower())

    def test_replaces_prior_to(self):
        result = apply_word_substitutions("Prior to the vote, we must act.")
        self.assertNotIn("Prior to", result)
        self.assertIn("before", result.lower())

    def test_replaces_legislation(self):
        result = apply_word_substitutions("The legislation was passed.")
        self.assertNotIn("legislation", result.lower())
        self.assertIn("law", result.lower())

    def test_cleans_up_double_spaces(self):
        result = apply_word_substitutions("We specifically need this.")
        self.assertNotIn("  ", result)

    def test_plain_text_unchanged(self):
        text = "The cat sat on the mat."
        result = apply_word_substitutions(text)
        self.assertEqual(result, text)

    def test_lowers_fk_score(self):
        """Word substitutions should lower the FK grade of complex text."""
        complex_text = (
            "Notwithstanding the aforementioned provisions regarding the "
            "establishment of regulatory requirements, the implementation "
            "of this legislation shall commence immediately."
        )
        original_score = score_readability(complex_text)["flesch_kincaid_grade"]
        simplified = apply_word_substitutions(complex_text)
        new_score = score_readability(simplified)["flesch_kincaid_grade"]
        self.assertLess(new_score, original_score)

    def test_system_prompt_sentence_splitting_rule(self):
        prompt = build_system_prompt(mode=MODE_FULL)
        # Should have aggressive sentence splitting guidance
        self.assertIn("12 words", prompt)

    def test_system_prompt_extra_substitutions(self):
        prompt = build_system_prompt(mode=MODE_FULL)
        # Should include newer word substitutions
        self.assertIn('"amendment"', prompt)
        self.assertIn('"regulation"', prompt)
        self.assertIn('"pertaining to"', prompt)


# ---------------------------------------------------------------------------
# Save translation tests
# ---------------------------------------------------------------------------
class TestSaveTranslation(unittest.TestCase):
    """Tests for saving translations with versioning."""

    def test_versioned_filename(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            # Temporarily override OUTPUT_DIR
            import translator_agent
            orig_dir = translator_agent.OUTPUT_DIR
            translator_agent.OUTPUT_DIR = tmpdir
            try:
                path = save_translation("bill.txt", "Test content", version=2)
                self.assertIn("_v2.md", path)
                self.assertTrue(os.path.exists(path))
                with open(path) as f:
                    content = f.read()
                self.assertIn("Test content", content)
            finally:
                translator_agent.OUTPUT_DIR = orig_dir

    def test_scores_in_header(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            import translator_agent
            orig_dir = translator_agent.OUTPUT_DIR
            translator_agent.OUTPUT_DIR = tmpdir
            try:
                scores = {
                    "flesch_kincaid_grade": 6.5,
                    "flesch_reading_ease": 72.0,
                    "passes_act602": True,
                }
                path = save_translation("bill.txt", "Content", version=1, scores=scores)
                with open(path) as f:
                    content = f.read()
                self.assertIn("Flesch-Kincaid Grade: 6.5", content)
                self.assertIn("Act 602 Compliant:    Yes", content)
            finally:
                translator_agent.OUTPUT_DIR = orig_dir


# ---------------------------------------------------------------------------
# Markdown stripping tests
# ---------------------------------------------------------------------------
class TestStripMarkdown(unittest.TestCase):
    """Tests for the strip_markdown utility."""

    def test_removes_headers(self):
        text = "# Title\n## Subtitle\n### Sub-sub"
        result = strip_markdown(text)
        self.assertNotIn("#", result)
        self.assertIn("Title", result)
        self.assertIn("Subtitle", result)

    def test_removes_bold_and_italic(self):
        text = "This is **bold** and *italic* text."
        result = strip_markdown(text)
        self.assertNotIn("**", result)
        self.assertNotIn("*", result)
        self.assertIn("bold", result)
        self.assertIn("italic", result)

    def test_removes_bullet_markers(self):
        text = "- Item one\n- Item two\n* Item three"
        result = strip_markdown(text)
        self.assertIn("Item one", result)
        self.assertIn("Item two", result)
        self.assertIn("Item three", result)
        # Lines should not start with - or *
        for line in result.strip().split("\n"):
            stripped = line.strip()
            if stripped:
                self.assertFalse(stripped.startswith("- "))
                self.assertFalse(stripped.startswith("* "))

    def test_plain_text_unchanged(self):
        text = "This is plain text. No formatting here."
        result = strip_markdown(text)
        self.assertEqual(result, text)

    def test_collapses_blank_lines(self):
        text = "Line one.\n\n\n\nLine two."
        result = strip_markdown(text)
        self.assertNotIn("\n\n\n", result)
        self.assertIn("Line one.", result)
        self.assertIn("Line two.", result)


# ---------------------------------------------------------------------------
# Web app tests
# ---------------------------------------------------------------------------
class TestWebApp(unittest.TestCase):
    """Tests for the Flask web interface."""

    def setUp(self):
        from web_app import app
        app.config["TESTING"] = True
        self.client = app.test_client()

    def test_index_page_loads(self):
        response = self.client.get("/")
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"Arkansas Bill Translator", response.data)

    def test_upload_no_file_no_text(self):
        response = self.client.post("/upload", data={},
                                     follow_redirects=True)
        self.assertEqual(response.status_code, 200)

    def test_score_only_no_text(self):
        response = self.client.post("/score-only", data={})
        self.assertEqual(response.status_code, 400)

    def test_score_only_with_text(self):
        response = self.client.post("/score-only",
                                     data={"bill_text": "The cat sat on the mat."})
        self.assertEqual(response.status_code, 200)
        data = json.loads(response.data)
        self.assertIn("scores", data)
        self.assertIn("flesch_kincaid_grade", data["scores"])

    def test_results_invalid_session(self):
        response = self.client.get("/results/nonexistent",
                                    follow_redirects=True)
        self.assertEqual(response.status_code, 200)


if __name__ == "__main__":
    unittest.main()
