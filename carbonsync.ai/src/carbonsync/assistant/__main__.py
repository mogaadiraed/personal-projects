"""Terminal entry point: prints the carbon analysis, or runs one assistant tool.

    python -m carbonsync.assistant                     # analysis, French, last 3 months
    python -m carbonsync.assistant --lang en --months 6 --cap 2900
    python -m carbonsync.assistant --tool get_budget --args '{"year": 2025}'
    python -m carbonsync.assistant --list-tools
"""
from __future__ import annotations

import argparse
import json
import sys

from carbonsync.obs import setup_console_logging


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m carbonsync.assistant",
                                     description="CarbonSync assistant: carbon analysis and read-only data tools.")
    parser.add_argument("--lang", default="fr", choices=["fr", "en"])
    parser.add_argument("--months", type=int, default=3, help="months studied and short-term action window (1-12)")
    parser.add_argument("--cap", type=float, default=None, help="annual cap override in tCO2e")
    parser.add_argument("--json", action="store_true", help="print the analysis as JSON (the 'analyse' tool output)")
    parser.add_argument("--tool", help="run one tool and print its JSON result")
    parser.add_argument("--args", default="{}", help="tool arguments as JSON")
    parser.add_argument("--privacy", default="full", choices=["full", "aggregated"])
    parser.add_argument("--list-tools", action="store_true")
    ns = parser.parse_args(argv)
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")
    root = setup_console_logging()
    if ns.tool or ns.json:  # keep stdout pure JSON
        for h in root.handlers:
            if getattr(h, "_carbonsync_console", False):
                h.setStream(sys.stderr)

    from carbonsync.assistant import tools

    if ns.list_tools:
        for spec in tools.specs():
            print(f"{spec['name']}: {spec['description']}")
        return 0
    tctx = tools.ToolContext(lang=ns.lang, privacy=ns.privacy)
    if ns.tool or ns.json:
        name = ns.tool or "analyse"
        args = json.loads(ns.args) if ns.tool else {"months": ns.months, **({"cap_t": ns.cap} if ns.cap else {})}
        out = tools.execute(name, args, tctx)
        print(json.dumps(out, ensure_ascii=False, indent=2))
        return 0 if out["ok"] else 1

    from carbonsync.assistant.analyst import analyse, render_text

    print(render_text(analyse(ns.lang, months=ns.months, cap=ns.cap)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
