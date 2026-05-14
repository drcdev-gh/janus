import os

# Must run before any module import — pocket.py, outline.py, and ssh.py all
# read env vars at import time and raise if they are missing.
os.environ.setdefault("POCKETID_API_URL", "https://pocket.test")
os.environ.setdefault("POCKETID_API_KEY", "pocket-key")
os.environ.setdefault("OUTLINE_API_URL", "https://outline.test")
os.environ.setdefault("OUTLINE_API_KEY", "outline-key")
os.environ.setdefault("SSH_ALLOWED_GROUP", "SSH Users")
os.environ.setdefault("API_KEY", "test-api-key")
