# Gunicorn configuration for Bill_Translator.
# Loaded automatically when the start command is simply: gunicorn web_app:app
#
# Why these values:
# Translating a full-length bill makes a single, long-running call to the
# Claude API. A large document can take well over a minute to generate, and
# gunicorn's default worker timeout is only 30 seconds, so the worker gets
# killed mid-request (seen as a 500 / "Internal Server Error"). Render itself
# allows responses up to 100 minutes, so the worker timeout is the only limit
# that matters here. 300 seconds gives even large bills room to finish.

# Workers silent (e.g. blocked waiting on the Claude API) longer than this many
# seconds are killed and restarted. Raised from the 30s default for long LLM calls.
timeout = 300

# One worker keeps memory within the 512 MB free instance. Each worker loads the
# whole app plus the anthropic SDK, so a single worker is the safe choice here.
# Use threads (not more workers) if you later need to serve concurrent users:
# a sync worker just waits on the API, so threads add cheap concurrency.
workers = 1
threads = 4

# Bind to the port Render provides (falls back to 10000 locally).
import os
bind = f"0.0.0.0:{os.environ.get('PORT', '10000')}"
