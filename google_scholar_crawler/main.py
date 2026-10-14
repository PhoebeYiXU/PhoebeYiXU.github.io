"""Google Scholar citation crawler for PhoebeYiXU.github.io.

This script fetches the user's Google Scholar profile and writes two JSON
files to the `results/` directory:
  - gs_data.json           (full author + publications payload)
  - gs_data_shieldsio.json (small payload for the shields.io citation badge)

Failure policy
--------------
Google Scholar aggressively blocks cloud IPs (including GitHub Actions
runners) and the `scholarly` library can hang indefinitely waiting for a
response. We therefore:
  1. Run the fetch in a daemon thread with a hard timeout (default 180s).
  2. Catch every exception and exit 0 with an empty/stale placeholder,
     so the workflow is marked SUCCESS and no failure email is sent.
  3. Always write a placeholder gs_data_shieldsio.json so the badge
     doesn't render as "stale" on the homepage.
"""

import json
import os
import sys
import threading
from datetime import datetime

DEFAULT_TIMEOUT_SEC = 180  # 3 minutes; Google Scholar scrape usually < 30s when not blocked


def _fetch_scholar_data(timeout_sec: int) -> dict:
    """Fetch + fill the author profile. Raises TimeoutError or any underlying
    scholarly exception. Runs in a daemon thread so an I/O hang (e.g. a
    blocked HTTP request) is killable via join(timeout=...)."""
    result = {"author": None, "error": None}

    def worker():
        try:
            # Import inside the thread so a broken scholarly install
            # is also caught here rather than crashing the script at import.
            from scholarly import scholarly

            print("[1/2] Looking up Google Scholar author ...", file=sys.stderr)
            author = scholarly.search_author_id(os.environ["GOOGLE_SCHOLAR_ID"])
            print(
                f"[2/2] Filling details for {author.get('name', '?')} "
                f"(~{author.get('citedby', '?')} citations) ...",
                file=sys.stderr,
            )
            scholarly.fill(
                author, sections=["basics", "indices", "counts", "publications"]
            )
            result["author"] = author
        except Exception as e:  # noqa: BLE001 — we re-raise below, just want to capture
            result["error"] = e

    t = threading.Thread(target=worker, daemon=True)
    t.start()
    t.join(timeout=timeout_sec)

    if t.is_alive():
        # Daemon thread is killed automatically when the main process exits.
        raise TimeoutError(
            f"scholarly fetch did not return within {timeout_sec}s "
            f"(Google Scholar likely blocked the runner IP)"
        )
    if result["error"] is not None:
        raise result["error"]
    return result["author"]


def _write_outputs(author: dict) -> None:
    author["name"] = author.get("name", "Unknown")
    author["updated"] = str(datetime.now())
    author["publications"] = {
        v["author_pub_id"]: v for v in author.get("publications", [])
    }

    os.makedirs("results", exist_ok=True)
    with open("results/gs_data.json", "w", encoding="utf-8") as f:
        json.dump(author, f, ensure_ascii=False)

    shieldio = {
        "schemaVersion": 1,
        "label": "citations",
        "message": f"{author.get('citedby', 0)}",
    }
    with open("results/gs_data_shieldsio.json", "w", encoding="utf-8") as f:
        json.dump(shieldio, f, ensure_ascii=False)
    print(
        f"✓ Wrote results: citedby={author.get('citedby', 0)}",
        file=sys.stderr,
    )


def _ensure_placeholder() -> None:
    """Make sure the shieldsio file always exists, even on failure,
    so the homepage badge never shows a 'stale' state."""
    os.makedirs("results", exist_ok=True)
    if not os.path.exists("results/gs_data_shieldsio.json"):
        with open("results/gs_data_shieldsio.json", "w", encoding="utf-8") as f:
            json.dump(
                {"schemaVersion": 1, "label": "citations", "message": "—"},
                f,
            )
        print("  · Wrote placeholder shieldsio file", file=sys.stderr)


def main() -> int:
    timeout_sec = int(os.environ.get("SCHOLAR_TIMEOUT_SEC", DEFAULT_TIMEOUT_SEC))
    try:
        author = _fetch_scholar_data(timeout_sec)
        _write_outputs(author)
        return 0
    except TimeoutError as e:
        print(f"⚠️  TIMEOUT: {e}", file=sys.stderr)
        print(
            "   Google Scholar scraping is blocked on this runner. "
            "Exiting 0 so no failure email is sent.",
            file=sys.stderr,
        )
        _ensure_placeholder()
        return 0  # success — no email
    except Exception as e:  # noqa: BLE001
        print(
            f"⚠️  Google Scholar fetch failed: {type(e).__name__}: {e}",
            file=sys.stderr,
        )
        print(
            "   Keeping previous data (if any). Exiting 0 so no failure "
            "email is sent.",
            file=sys.stderr,
        )
        _ensure_placeholder()
        return 0  # success — no email


if __name__ == "__main__":
    sys.exit(main())
