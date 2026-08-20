"""What Ultron's tools actually cost, and which ones earn it.

Run:  venv\\Scripts\\python.exe tool_report.py

Deliberately a script rather than a tool. Adding a ninetieth tool to a system
whose problem is having eighty-nine would be a strange way to fix it, and
this is a thing you read while pruning, not something to ask for out loud.
"""

import sys

sys.path.insert(0, __file__.rsplit("\\", 1)[0].rsplit("/", 1)[0])

from ultron import tool_usage


def main():
    data = tool_usage.report()
    if not data["total_tools"]:
        print("No tally yet. Start Ultron once and the file appears at:")
        print(f"  {tool_usage.USAGE_PATH}")
        return

    print(f"\n{data['total_tools']} tools registered, "
          f"{data['total_calls']} calls recorded.\n")

    if data["used"]:
        print("USED")
        for name, row in data["used"]:
            errors = row.get("errors", 0)
            note = f"   ({errors} failed)" if errors else ""
            last = (row.get("last_used") or "")[:16].replace("T", " ")
            print(f"  {row['calls']:>5}x  {name:<34}{last}{note}")

    if data["always_failing"]:
        print("\nCALLED BUT NEVER SUCCEEDED  (broken, not popular)")
        for name, row in data["always_failing"]:
            print(f"  {row['calls']:>5}x  {name}")

    # Said plainly, because an undercount that looks like a total is worse
    # than no count: it reads as "nothing is broken".
    print("\n  Note: failures counted here are raises, timeouts and returns "
          "beginning 'Error'.\n  Tools that fail with 'Failed to ...' or "
          "'Could not ...' are counted as successes,\n  so this list is a "
          "floor rather than the full picture.")

    never = data["never_used"]
    if never:
        print(f"\nNEVER USED  ({len(never)} of {data['total_tools']})")
        for name in never:
            print(f"         -  {name}")

    # The point of the exercise: what removing them would actually save.
    try:
        from ultron.brain import Brain
        share = len(never) / data["total_tools"] if data["total_tools"] else 0
        print(f"\nThose {len(never)} are roughly {share:.0%} of the tool "
              f"surface. The full description block measured 2,690 tokens "
              f"locally and 7,445 as cloud schemas, so cutting them would "
              f"save on the order of {int(2690 * share)} and "
              f"{int(7445 * share)} tokens per request respectively.")
        print("An estimate: tool descriptions differ in length, so the real "
              "saving depends on which ones go.")
    except Exception:
        pass

    print(f"\nSource: {tool_usage.USAGE_PATH}")


if __name__ == "__main__":
    main()
