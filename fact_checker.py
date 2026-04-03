"""
fact_checker.py — Claim Verification Engine
=============================================
Verifies claims from bills or translations against web sources using
Brave Search + Claude cross-reference analysis.

Adapted from the Accountability Agent's fact_checker.py.
"""

import logging

import requests

logger = logging.getLogger(__name__)

_BRAVE_SEARCH_URL = "https://api.search.brave.com/res/v1/web/search"


def _brave_search(query, api_key, count=5):
    """Search Brave for evidence related to a claim."""
    headers = {
        "Accept": "application/json",
        "Accept-Encoding": "gzip",
        "X-Subscription-Token": api_key,
    }
    params = {"q": query, "count": count}
    try:
        resp = requests.get(
            _BRAVE_SEARCH_URL, headers=headers, params=params, timeout=15
        )
        resp.raise_for_status()
        data = resp.json()
        return [
            {
                "title": item.get("title", ""),
                "url": item.get("url", ""),
                "description": item.get("description", ""),
            }
            for item in data.get("web", {}).get("results", [])
        ]
    except requests.RequestException as e:
        logger.warning("Fact check search failed: %s", e)
        return []


def verify_claim(claim, anthropic_key, brave_key, model="claude-sonnet-4-20250514"):
    """
    Verify a single claim using Brave Search + Claude analysis.

    Returns a dict:
      - claim: the original claim text
      - verdict: VERIFIED | UNVERIFIED | CONTRADICTED | INSUFFICIENT_DATA
      - confidence: HIGH | MEDIUM | LOW
      - evidence: list of supporting/contradicting web sources
      - explanation: Claude's analysis text
    """
    import anthropic

    # Search for evidence
    evidence = _brave_search(claim, brave_key)

    if not evidence:
        return {
            "claim": claim,
            "verdict": "INSUFFICIENT_DATA",
            "confidence": "LOW",
            "evidence": [],
            "explanation": "No web sources found to verify this claim.",
        }

    # Format evidence for Claude
    evidence_text = "\n\n".join(
        f"Source: {e['title']}\nURL: {e['url']}\n{e['description']}"
        for e in evidence
    )

    prompt = (
        f"FACT VERIFICATION TASK\n\n"
        f"CLAIM TO VERIFY: {claim}\n\n"
        f"WEB EVIDENCE:\n{evidence_text}\n\n"
        f"Based on the web evidence above:\n"
        f"1. Is this claim VERIFIED, UNVERIFIED, or CONTRADICTED?\n"
        f"2. What is your confidence? (HIGH / MEDIUM / LOW)\n"
        f"3. Cite the specific sources that support or contradict the claim.\n"
        f"4. Provide a brief explanation.\n\n"
        f"Respond in this exact format:\n"
        f"VERDICT: [VERIFIED/UNVERIFIED/CONTRADICTED]\n"
        f"CONFIDENCE: [HIGH/MEDIUM/LOW]\n"
        f"EXPLANATION: [your explanation]\n"
        f"SOURCES: [list of relevant source URLs]"
    )

    try:
        client = anthropic.Anthropic(api_key=anthropic_key)
        response = client.messages.create(
            model=model,
            max_tokens=1024,
            messages=[{"role": "user", "content": prompt}],
        )
        analysis = response.content[0].text

        # Parse verdict from response
        verdict = "UNVERIFIED"
        confidence = "MEDIUM"
        for line in analysis.split("\n"):
            line_upper = line.strip().upper()
            if line_upper.startswith("VERDICT:"):
                v = line_upper.replace("VERDICT:", "").strip()
                if v in ("VERIFIED", "UNVERIFIED", "CONTRADICTED"):
                    verdict = v
            elif line_upper.startswith("CONFIDENCE:"):
                c = line_upper.replace("CONFIDENCE:", "").strip()
                if c in ("HIGH", "MEDIUM", "LOW"):
                    confidence = c

        return {
            "claim": claim,
            "verdict": verdict,
            "confidence": confidence,
            "evidence": evidence,
            "explanation": analysis,
        }

    except Exception:
        logger.exception("Fact verification failed")
        return {
            "claim": claim,
            "verdict": "INSUFFICIENT_DATA",
            "confidence": "LOW",
            "evidence": evidence,
            "explanation": "Verification failed due to an internal error.",
        }
