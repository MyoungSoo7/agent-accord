#!/usr/bin/env python3
"""Check that every number quoted in README.md also appears in results.md.

    .venv/bin/python -I check_report.py      # exit 1 and list offenders on mismatch

Ignored: fenced/inline code, link targets, the references section, citation
years such as "(Jackson & Mudholkar, 1979)", and integers with |value| <= 20
(section numbers, '5 fold', '2회' ...). Dates,
times, sha256 digests and scientific-notation p-values are matched whole.
Numbers must be written exactly as results.md formats them.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent

HEX = re.compile(r"\b[0-9a-f]{64}\b")
DATE = re.compile(r"\b\d{4}-\d{2}-\d{2}\b")
TIME = re.compile(r"\b\d{2}:\d{2}(?::\d{2})?\b")
SCI = re.compile(r"(?<![A-Za-z0-9_.])\d+(?:\.\d+)?e[-+]\d+")
NUM = re.compile(r"(?<![A-Za-z0-9_.])[-+−]?\d+(?:\.\d+)?(?![0-9]|\.\d|[A-Za-z_])")
SMALL_INT = 20
CITATION_YEAR = re.compile(r"(?<![\d.])(?:19\d\d|20[0-3]\d)[a-z]?(?=\))")


def _strip_markdown(text: str) -> str:
    text = re.sub(r"```.*?```", " ", text, flags=re.S)
    text = CITATION_YEAR.sub(" ", text)  # "(Jackson & Mudholkar, 1979)"
    text = re.sub(r"`[^`\n]*`", " ", text)
    text = re.sub(r"\]\([^)]*\)", "]", text)  # link / image targets
    return text


def _references_cut(text: str) -> str:
    m = re.search(r"^##\s*참고문헌.*$", text, flags=re.M)
    return text[: m.start()] if m else text


def tokens(text: str) -> dict[str, set[str]]:
    out = {"hex": set(HEX.findall(text))}
    text = HEX.sub(" ", text)
    out["date"] = set(DATE.findall(text))
    text = DATE.sub(" ", text)
    out["time"] = set(TIME.findall(text))
    text = TIME.sub(" ", text)
    out["sci"] = set(SCI.findall(text))
    text = SCI.sub(" ", text)
    nums = set()
    for tok in NUM.findall(text):
        tok = tok.replace("−", "-").lstrip("+")
        nums.add(tok)
    out["num"] = nums
    return out


def check(readme: Path, results_md: Path) -> list[str]:
    rd_raw = _references_cut(readme.read_text(encoding="utf-8"))
    rd = tokens(_strip_markdown(rd_raw))
    rd["hex"] |= set(HEX.findall(rd_raw))  # digests usually sit in inline code
    rs_text = results_md.read_text(encoding="utf-8")
    rs = tokens(rs_text)
    # a hash in results.md sits inside inline code; collect those directly
    rs["hex"] |= set(HEX.findall(rs_text))
    missing = []
    for kind in ("hex", "date", "time", "sci"):
        missing += [f"{kind}:{t}" for t in sorted(rd[kind] - rs[kind])]
    for t in sorted(rd["num"]):
        if "." not in t and abs(int(t)) <= SMALL_INT:
            continue
        if t not in rs["num"]:
            missing.append(f"num:{t}")
    return missing


def main() -> int:
    missing = check(HERE / "README.md", HERE / "results.md")
    if missing:
        print("README.md numbers not found in results.md:")
        for m in missing:
            print("  ", m)
        return 1
    print("OK: every number in README.md appears in results.md")
    return 0


if __name__ == "__main__":
    sys.exit(main())
