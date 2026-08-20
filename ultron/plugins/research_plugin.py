"""
research_plugin.py — Ultron Research Plugin ⭐⭐⭐⭐⭐

Provides a multi-step research pipeline:
  1. web_search         → Query DuckDuckGo and return a list of result titles + URLs
  2. research_read_url  → Fetch and extract readable text from any URL
  3. save_research      → Save a final research report to disk as a .md file
  4. list_research_reports → List all previously saved reports

The AI orchestrates these three tools automatically to:
  Search → Read docs/articles → Compare & Summarise → Save report
"""

import os
import re
import datetime
import urllib.request
import urllib.parse
import urllib.error
import html
from ultron.plugins.notification_plugin import send_toast


# ---------------------------------------------------------------------------
# 1. Web Search  (DuckDuckGo Lite — no API key required)
# ---------------------------------------------------------------------------

def web_search(query: str, max_results: int = 6) -> str:
    """Searches the web using DuckDuckGo and returns a list of result titles, URLs, and snippets.
    Use this as the FIRST step of any research task to discover relevant pages.
    Args:
        query: The search query string.
        max_results: How many results to return (default 6).
    """
    try:
        encoded_query = urllib.parse.quote_plus(query)
        url = f"https://lite.duckduckgo.com/lite/?q={encoded_query}"

        req = urllib.request.Request(
            url,
            headers={
                "User-Agent": (
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/124.0 Safari/537.36"
                )
            }
        )
        with urllib.request.urlopen(req, timeout=12) as resp:
            body = resp.read().decode("utf-8", errors="replace")

        if "Unfortunately, bots use DuckDuckGo too" in body or "captcha" in body.lower():
            return "Web search blocked by CAPTCHA."

        # Parse result links and snippets from DuckDuckGo Lite HTML
        links = re.findall(r'<a[^>]+href=[\'\"]([^\'\"]+)[\'\"][^>]*class=[\'\"]result-link[\'\"][^>]*>(.*?)</a>|<a[^>]+class=[\'\"]result-link[\'\"][^>]*href=[\'\"]([^\'\"]+)[\'\"][^>]*>(.*?)</a>', body, re.IGNORECASE)
        snippets = re.findall(r'<td[^>]*class=[\'\"]result-snippet[\'\"][^>]*>(.*?)</td>', body, re.IGNORECASE | re.DOTALL)

        results = []
        for i in range(min(max_results, len(links), len(snippets))):
            match = links[i]
            raw_url = match[0] or match[2]
            raw_title = match[1] or match[3]
            raw_snippet = snippets[i]

            # DuckDuckGo sometimes uses redirect URLs — extract the actual target
            if "uddg=" in raw_url:
                try:
                    parsed = urllib.parse.urlparse(raw_url)
                    params = urllib.parse.parse_qs(parsed.query)
                    raw_url = params.get("uddg", [raw_url])[0]
                    raw_url = urllib.parse.unquote(raw_url)
                except Exception as e:
                    print(f"[Research] could not unwrap a result URL: {e}")

            title = html.unescape(re.sub(r"<[^>]+>", "", raw_title)).strip()
            snippet = html.unescape(re.sub(r"<[^>]+>", "", raw_snippet)).strip()
            
            if title and raw_url.startswith("http"):
                results.append({"title": title, "url": raw_url, "snippet": snippet})

        if not results:
            return f"No search results found for: {query}"

        lines = [f"Search results for: '{query}'\n"]
        for i, r in enumerate(results, 1):
            lines.append(f"{i}. {r['title']}\n   Snippet: {r['snippet']}\n   URL: {r['url']}")
        return "\n".join(lines)

    except Exception as e:
        return f"Web search failed: {e}"


# ---------------------------------------------------------------------------
# 2. Read a URL / Article
# ---------------------------------------------------------------------------

def research_read_url(url: str) -> str:
    """Fetches a web page and extracts its readable text content.
    Use this to read official documentation, blog posts, or news articles found via web_search.
    Args:
        url: The full URL of the page to read.
    """
    try:
        req = urllib.request.Request(
            url,
            headers={
                "User-Agent": (
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/124.0 Safari/537.36"
                ),
                "Accept": "text/html,application/xhtml+xml",
                "Accept-Language": "en-US,en;q=0.9",
            }
        )
        with urllib.request.urlopen(req, timeout=15) as resp:
            raw = resp.read().decode("utf-8", errors="replace")

        # Remove noisy blocks
        for tag in ["script", "style", "nav", "footer", "header", "aside", "noscript"]:
            raw = re.sub(
                rf"<{tag}[^>]*>.*?</{tag}>", "", raw,
                flags=re.IGNORECASE | re.DOTALL
            )

        # Strip all remaining HTML tags
        text = re.sub(r"<[^>]+>", " ", raw)

        # Decode HTML entities
        text = html.unescape(text)

        # Collapse whitespace / blank lines
        lines = [line.strip() for line in text.splitlines() if line.strip()]
        text = "\n".join(lines)

        # Limit to avoid context window overflow
        MAX_CHARS = 8000
        if len(text) > MAX_CHARS:
            text = text[:MAX_CHARS] + f"\n\n[...content truncated at {MAX_CHARS} chars...]"

        return f"Content from: {url}\n\n{text}"

    except urllib.error.HTTPError as e:
        return f"HTTP Error {e.code} reading URL: {url}"
    except urllib.error.URLError as e:
        return f"URL Error reading {url}: {e.reason}"
    except Exception as e:
        return f"Failed to read URL {url}: {e}"


# ---------------------------------------------------------------------------
# 3. Save Research Report
# ---------------------------------------------------------------------------

def save_research(topic: str, summary: str) -> str:
    """Saves a completed research report as a Markdown file in the data/research/ folder.
    Always call this as the FINAL step after gathering and synthesising all information.
    Args:
        topic: A short title for the research topic (used as the filename).
        summary: The full, formatted research report in Markdown.
    """
    try:
        base_dir = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        research_dir = os.path.join(base_dir, "data", "research")
        os.makedirs(research_dir, exist_ok=True)

        # Sanitise filename
        safe_topic = re.sub(r"[^\w\s-]", "", topic).strip()
        safe_topic = re.sub(r"[\s]+", "_", safe_topic)[:80]
        timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        filename = f"{safe_topic}_{timestamp}.md"
        filepath = os.path.join(research_dir, filename)

        # Write the report with a header
        header = (
            f"# Research Report: {topic}\n\n"
            f"**Generated by Ultron** | "
            f"{datetime.datetime.now().strftime('%A, %d %B %Y %H:%M:%S')}\n\n"
            f"---\n\n"
        )
        with open(filepath, "w", encoding="utf-8") as f:
            f.write(header + summary)

        return (
            f"Research report saved successfully!\n"
            f"File: {filepath}\n"
            f"Topic: {topic}\n"
            f"Size: {len(summary):,} characters"
        )
    except Exception as e:
        return f"Failed to save research report: {e}"


# ---------------------------------------------------------------------------
# 4. List Saved Research Reports
# ---------------------------------------------------------------------------

def list_research_reports() -> str:
    """Lists all previously saved research reports with their topics, dates, and file paths.
    Call this when the user asks to see their saved research or past reports.
    """
    try:
        base_dir = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        research_dir = os.path.join(base_dir, "data", "research")

        if not os.path.exists(research_dir):
            return "No research reports found. The research folder does not exist yet."

        files = sorted(
            [f for f in os.listdir(research_dir) if f.endswith(".md")],
            reverse=True
        )

        if not files:
            return "No research reports saved yet."

        lines = [f"Saved Research Reports ({len(files)} total):\n"]
        for i, fname in enumerate(files, 1):
            fpath = os.path.join(research_dir, fname)
            size_kb = os.path.getsize(fpath) / 1024
            lines.append(f"{i}. {fname}  [{size_kb:.1f} KB]\n   Path: {fpath}")

        return "\n".join(lines)

    except Exception as e:
        return f"Failed to list research reports: {e}"


# ---------------------------------------------------------------------------
# 5. Background Research Task Worker
# ---------------------------------------------------------------------------

def run_background_research_task(topic: str, client=None, model: str = None,
                                output_manager=None, complete=None) -> str:
    """Helper that runs in a background thread to search, fetch, synthesize, and save a research report.
    Once complete, it announces that research is finished and saved as a .md file without reading the whole text out loud.
    """
    import threading

    def worker():
        try:
            # 1. Search the web
            search_res = web_search(topic, max_results=5)

            # 2. Extract URLs
            urls = re.findall(r'URL:\s*(https?://[^\s]+)', search_res)

            # 3. Read top URLs
            articles = []
            for url in urls[:3]:
                content = research_read_url(url)
                if not any(content.startswith(err) for err in ["HTTP Error", "URL Error", "Failed"]):
                    articles.append(content)

            combined_text = "\n\n===\n\n".join(articles) if articles else search_res

            # 4. Generate report summary using AI client (with retries & null checks)
            prompt = (
                f"Write a detailed, well-structured Markdown research report on the topic: '{topic}'.\n\n"
                f"Use the following web search and article content:\n{combined_text[:12000]}\n\n"
                f"Structure requirements:\n"
                f"- Executive Summary\n"
                f"- Key Features & Findings\n"
                f"- Pros & Cons / Comparison\n"
                f"- Final Recommendation\n"
                f"- Sources\n"
                f"Do not include conversational chatter. Output ONLY the raw Markdown report."
            )
            sys_msg = "You are an expert technical research assistant for Ultron. Generate professional Markdown reports."

            report_markdown = None
            import time
            for attempt in range(3):
                try:
                    messages = [
                        {"role": "system", "content": sys_msg},
                        {"role": "user", "content": prompt}
                    ]
                    # `complete` is the Brain's own path, so a report started
                    # before a key ran out still finishes on the next key.
                    # The client argument stays for any caller without one.
                    if complete is not None:
                        response = complete(messages=messages)
                    else:
                        response = client.chat.completions.create(
                            model=model, messages=messages)
                    if response and getattr(response, "choices", None) and len(response.choices) > 0:
                        choice = response.choices[0]
                        if choice and getattr(choice, "message", None) and choice.message.content:
                            report_markdown = choice.message.content
                            break
                except Exception as ex:
                    time.sleep(2)

            if not report_markdown or not report_markdown.strip():
                # Fallback: create structured report from search data directly if LLM API returned empty
                report_markdown = (
                    f"*(Note: Automated Web Research Report)*\n\n"
                    f"## Web Research Data\n\n"
                    f"{combined_text[:8000]}"
                )

            # 5. Save research report as .md file
            save_res = save_research(topic, report_markdown)

            # 6. Notify user concise message: research complete, saved to .md file (DO NOT READ IT OUT LOUD)
            notification = f"Sir, I have completed the research on '{topic}' and saved it as a .md file in the data/research folder."
            send_toast("Research Complete", f"Your report on '{topic}' is ready.")
            if output_manager:
                output_manager.enqueue(notification, source="system")
            else:
                print(f"\nUltron: {notification}")

        except Exception as e:
            err_msg = f"Sir, background research on '{topic}' failed: {e}"
            send_toast("Research Failed", f"Failed to research '{topic}'.")
            if output_manager:
                output_manager.enqueue(err_msg, source="system")
            else:
                print(f"\nUltron: {err_msg}")

    t = threading.Thread(target=worker, daemon=True)
    t.start()

    return f"Background research on '{topic}' has been started, sir. I will notify you once the .md file is saved."

