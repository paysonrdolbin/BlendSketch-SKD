"""Launch BlendSketch from source:  .venv/bin/python run.py [playlist.bsplaylist]

This is also the packaged app's entry point; bootstrap.py lets it run updated app code.
"""

import bootstrap

if __name__ == "__main__":
    bootstrap.start()
