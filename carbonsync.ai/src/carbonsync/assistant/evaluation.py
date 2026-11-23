"""Evaluation set for the local models (Phase 4): 30 questions (15 topics × FR/EN) with known
answers, run through the real assistant (engine, tools, prefetch, figure check) to score each
model and choose the default local model.

The expected values are not written down: each one is computed from the tools at run time, so
the set stays right after every data upload. One case scores 0 to 1:

* **tools** (25 %): the assistant read one of the expected tools (prefetch included);
* **figures** (45 %): share of the expected values found in the answer (rounding allowed);
* **grounded** (20 %): no figure missing from the data read (the figure check), −25 % per figure;
* **language** (10 %): the answer is in the question's language.

An error or an empty answer scores 0. The model score is the mean × 100.

Results go to ``data/assistant/eval/`` (git-ignored). Run from the Ollama page (background) or:
``python -m carbonsync.assistant.evaluation --models qwen3.5:9b qwen3.5:4b``.
"""
from __future__ import annotations

import argparse
import json
import re
import threading
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from carbonsync.assistant import engine, grounding, tools
from carbonsync.assistant.context import get_project_root
from carbonsync.obs import get_logger

log = get_logger(__name__)

WEIGHTS = {"tools": 0.25, "figures": 0.45, "grounded": 0.20, "language": 0.10}
SPEED_MARGIN = 3.0   # a faster model within 3 points of the best one is recommended instead


@dataclass(frozen=True)
class Fact:
    """An expected value: ``tool(args)`` → ``pick(result)``. Numbers match within ``tol`` (absolute) or
    1 % (rounding to 3 significant figures; tight enough to tell 826 t from 811 t). ``pick`` may return a
    tuple: equivalent ways to state the answer, any one counts. Text matches if it appears."""
    tool: str
    args: dict[str, Any]
    pick: Callable[[dict[str, Any]], Any]
    tol: float | None = None
    words: tuple[str, ...] = ()


@dataclass(frozen=True)
class Topic:
    id: str
    fr: str
    en: str
    tools: tuple[str, ...]
    facts: tuple[Fact, ...] = ()
    words_fr: tuple[str, ...] = ()   # any of them must appear (lower case), when set
    words_en: tuple[str, ...] = ()


@dataclass
class Case:
    id: str
    lang: str
    question: str
    tools: tuple[str, ...]
    facts: tuple[Fact, ...]
    words: tuple[str, ...] = field(default_factory=tuple)


Y2025 = {"start": "2025-01-01", "end": "2025-12-31", "granularity": "year"}
Q1 = {"period_start": "2026-01-01", "period_end": "2026-03-31", "reference_start": "2025-01-01",
      "reference_end": "2025-03-31"}
_EMIS = ("query_emissions", "compare_periods", "get_overview", "analyse")

TOPICS: list[Topic] = [
    Topic("total_2025", "Quel est le total des émissions de CO2e en 2025 ?", "What were total CO2e emissions in 2025?",
          _EMIS, (Fact("query_emissions", Y2025, lambda r: r["totals"]["total_t"]),)),
    Topic("scope2_2025", "Combien le Scope 2 (électricité) a-t-il émis en 2025 ?",
          "How much did Scope 2 (electricity) emit in 2025?",
          _EMIS, (Fact("query_emissions", Y2025, lambda r: r["totals"]["scope2_t"]),)),
    Topic("last_12_months", "Combien avons-nous émis sur les 12 derniers mois, et quelle est l'évolution par rapport aux "
          "12 mois précédents ?", "How much did we emit over the last 12 months, and how does it compare with the "
          "previous 12 months?", ("get_overview", "query_emissions", "compare_periods", "analyse"),
          (Fact("get_overview", {}, lambda r: r["last_12_months"]["total_t"]),
           Fact("get_overview", {}, lambda r: r["last_12_months"]["change_pct"], tol=0.5))),
    Topic("budget", "Allons-nous respecter le plafond carbone cette année ?", "Will we stay under the carbon cap this year?",
          ("get_budget", "analyse"),
          (Fact("get_budget", {}, lambda r: r["cap_t"]), Fact("get_budget", {}, lambda r: r["full_year_estimate"]["total_t"]))),
    Topic("forecast", "Que prévoit le modèle pour les 6 prochains mois ?", "What does the forecast say for the next 6 months?",
          ("get_forecast", "analyse"), (Fact("get_forecast", {}, lambda r: r["total_t"]),)),
    Topic("forecast_range", "Quelle est la fourchette d'incertitude à 90 % de la prévision ?",
          "What is the 90% uncertainty range of the forecast?", ("get_forecast",),
          (Fact("get_forecast", {}, lambda r: r["lo90_total_t"]), Fact("get_forecast", {}, lambda r: r["hi90_total_t"]))),
    Topic("quarantine", "Combien de fichiers sont en quarantaine ?", "How many files are quarantined?",
          ("get_data_quality", "get_pipeline_activity"), (Fact("get_data_quality", {}, lambda r: r["files_quarantined"]),)),
    Topic("best_model", "Quel modèle de prévision est le plus précis ?", "Which forecasting model is the most accurate?",
          ("get_model_performance",),
          (Fact("get_model_performance", {}, lambda r: r["best_model"]),)),
    Topic("electricity_factor", "Quel facteur d'émission est utilisé pour l'électricité ?",
          "Which emission factor is used for electricity?", ("get_methodology",),
          (Fact("get_methodology", {}, lambda r: r["emission_factors"]["electricity_kgco2_per_kwh"], tol=0.001),)),
    Topic("solar_15", "Quel serait l'effet de 15 % d'électricité solaire sur les émissions de l'année ?",
          "What would 15% solar electricity do to this year's emissions?", ("run_scenario",),
          # the tonnes saved this year, or the year's total with solar (3 222 − 246.6 = 2 976 t)
          (Fact("run_scenario", {"solar_pct": 15},
                lambda r: (r["year_effect"]["saved_rest_of_year_t"], r["year_effect"]["projected_with_t"])),)),
    Topic("compare_q1", "Compare les émissions du premier trimestre 2026 à celles du premier trimestre 2025.",
          "Compare emissions in the first quarter of 2026 with the first quarter of 2025.",
          ("compare_periods", "query_emissions", "analyse"),
          (Fact("compare_periods", Q1, lambda r: r["metrics"][0]["period_total"]),
           Fact("compare_periods", Q1, lambda r: r["metrics"][0]["reference_total"]))),
    Topic("action_plan", "Fais l'analyse des 3 derniers mois et propose un plan d'action.",
          "Analyse the last 3 months and propose an action plan.", ("analyse",),
          (Fact("analyse", {"months": 3}, lambda r: r["reductions"]["gap_to_close_t"]),)),
    Topic("add_data", "Comment ajouter 3 mois de données ?", "How do I add 3 months of data?", ("explain_dashboard",),
          words_fr=("dépôt de données", "dépôt"), words_en=("data intake", "intake")),
    Topic("why_change", "Pourquoi les émissions ont-elles augmenté sur les 3 derniers mois ?",
          "Why did emissions go up over the last 3 months?", ("analyse",),
          (Fact("analyse", {"months": 3}, lambda r: r["hotspots"]["rows"][0]["change_pct"], tol=0.5),),
          words_fr=("production", "volume", "activité"), words_en=("production", "volume", "activity")),
    Topic("intensity", "Quelle est l'intensité carbone par unité produite sur les 12 derniers mois ?",
          "What is the carbon intensity per unit produced over the last 12 months?", ("get_overview", "analyse"),
          (Fact("get_overview", {}, lambda r: r["last_12_months"]["intensity_kg_per_unit"], tol=0.1),)),
]


def cases(langs: tuple[str, ...] = ("fr", "en")) -> list[Case]:
    out = []
    for t in TOPICS:
        for lg in langs:
            out.append(Case(f"{t.id}_{lg}", lg, t.fr if lg == "fr" else t.en, t.tools, t.facts,
                            t.words_fr if lg == "fr" else t.words_en))
    return out


# ---------------------------------------------------------------------------
# Scoring
# ---------------------------------------------------------------------------
_FR = {"le", "la", "les", "des", "est", "sont", "pour", "sur", "avec", "une", "dans", "émissions", "vous", "nous", "mois"}
_EN = {"the", "is", "are", "for", "on", "with", "and", "of", "emissions", "you", "we", "months", "this", "to"}
_WORD = re.compile(r"[a-zàâçéèêëîïôûùüÿœ]+")


def language_of(text: str) -> str:
    words = _WORD.findall((text or "").lower())
    fr, en = sum(w in _FR for w in words), sum(w in _EN for w in words)
    return "fr" if fr >= en else "en"


def expected(case: Case, cache: dict[str, Any] | None = None) -> list[Any]:
    """The expected values of ``case``, computed from the tools (one call per tool and arguments)."""
    cache = {} if cache is None else cache
    out = []
    for f in case.facts:
        key = f.tool + json.dumps(f.args, sort_keys=True)
        if key not in cache:
            res = tools.execute(f.tool, f.args)
            cache[key] = res.get("result") if res.get("ok") else None
        try:
            out.append(f.pick(cache[key]) if cache[key] is not None else None)
        except (KeyError, IndexError, TypeError):
            out.append(None)
    return out


REL_TOL = 0.01


def _has_number(answer: str, value: float, tol: float | None) -> bool:
    limit = tol if tol is not None else max(REL_TOL * abs(value), 0.06)
    for _tok, vals, _dec, _grouped in grounding.numbers(answer):
        if any(abs(abs(v) - abs(value)) <= limit for v in vals):
            return True
    return False


def _matches(answer: str, v: Any, tol: float | None) -> bool:
    if isinstance(v, list | tuple):   # equivalent statements of the same answer
        return any(_matches(answer, x, tol) for x in v if x is not None)
    if isinstance(v, int | float):
        return _has_number(answer, float(v), tol)
    return str(v).lower() in answer.lower()


def score(case: Case, turn: dict[str, Any], values: list[Any]) -> dict[str, Any]:
    answer = turn.get("answer") or ""
    used = [t.get("name") for t in turn.get("tools") or []]
    out: dict[str, Any] = {"id": case.id, "lang": case.lang, "tools_used": used, "seconds": turn.get("seconds", 0.0),
                           "unverified": list(turn.get("unverified") or []), "error": turn.get("error"),
                           "answer": answer[:4000]}
    if turn.get("error") or not answer.strip():
        return {**out, "tools": 0.0, "figures": 0.0, "grounded": 0.0, "language": 0.0, "score": 0.0, "found": []}
    found = []
    for f, v in zip(case.facts, values, strict=True):
        if v is None:
            continue
        found.append(_matches(answer, v, f.tol))
    if case.words:
        found.append(any(w in answer.lower() for w in case.words))
    parts = {
        "tools": 1.0 if any(u in case.tools for u in used) else 0.0,
        "figures": (sum(found) / len(found)) if found else 1.0,
        "grounded": max(0.0, 1.0 - 0.25 * len(out["unverified"])),
        "language": 1.0 if language_of(answer) == case.lang else 0.0,
    }
    return {**out, **parts, "found": found, "score": round(sum(WEIGHTS[k] * parts[k] for k in WEIGHTS), 4)}


def summarize(model: str, results: list[dict[str, Any]], total: int) -> dict[str, Any]:
    n = len(results) or 1
    secs = sorted(r["seconds"] for r in results) or [0.0]
    return {
        "model": model, "cases": len(results), "total": total, "complete": len(results) == total,
        "score": round(100 * sum(r["score"] for r in results) / n, 1),
        **{f"{k}_pct": round(100 * sum(r[k] for r in results) / n, 1) for k in WEIGHTS},
        "errors": sum(1 for r in results if r["error"]),
        "seconds_mean": round(sum(secs) / len(secs), 1), "seconds_median": round(secs[len(secs) // 2], 1),
        "finished": datetime.now().isoformat(timespec="seconds"),
    }


def recommend(summaries: dict[str, dict[str, Any]]) -> str | None:
    """Best score; a faster model wins if it is within ``SPEED_MARGIN`` points of the best one on the score
    **and** on the expected figures (speed never buys wrong numbers). Complete runs only."""
    done = [s for s in summaries.values() if s.get("complete")]
    if not done:
        return None
    top = max(done, key=lambda s: (s["score"], -s["seconds_mean"]))
    near = [s for s in done if s["score"] >= top["score"] - SPEED_MARGIN
            and s.get("figures_pct", 0.0) >= top.get("figures_pct", 0.0) - SPEED_MARGIN]
    return min(near, key=lambda s: s["seconds_mean"])["model"]


# ---------------------------------------------------------------------------
# Running
# ---------------------------------------------------------------------------
def _dir(root: Path | None = None) -> Path:
    d = (root or get_project_root()) / "data" / "assistant" / "eval"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _write(path: Path, data: Any) -> None:
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    tmp.replace(path)


def run_model(model: str, provider_factory: Callable[[str], Any], case_list: list[Case] | None = None,
              root: Path | None = None, progress: Callable[[int, int, str], None] | None = None) -> dict[str, Any]:
    """Runs every case on ``model`` (a new conversation each time, no fallback) and saves the result."""
    case_list = case_list or cases()
    cache: dict[str, Any] = {}
    results = []
    for i, case in enumerate(case_list):
        if progress:
            progress(i, len(case_list), case.id)
        values = expected(case, cache)
        provider = provider_factory(model)
        turn: dict[str, Any] = {}
        try:
            for ev in engine.ask(engine.new_chat(case.lang), case.question, provider, lang=case.lang, fallback=False):
                if ev.kind == "done":
                    turn = ev.turn
        except Exception as exc:  # noqa: BLE001 - one broken case must not stop the run
            log.warning("Eval %s on %s failed: %s", case.id, model, exc)
            turn = {"error": f"{type(exc).__name__}: {exc}"[:200]}
        r = score(case, turn, values)
        r["expected"] = values
        results.append(r)
        log.info("Eval %s · %s: %.2f (tools %s, figures %s, %.1fs)", model, case.id, r["score"], r["tools_used"],
                 r.get("found"), r["seconds"])
    summary = summarize(model, results, len(case_list))
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    _write(_dir(root) / f"{stamp}_{re.sub(r'[^A-Za-z0-9.]+', '-', model)}.json", {"summary": summary, "results": results})
    log.info("Eval %s: score %.1f/100, %.1fs per question (%d errors)", model, summary["score"],
             summary["seconds_mean"], summary["errors"])
    return summary


def latest(root: Path | None = None) -> dict[str, dict[str, Any]]:
    """The most recent complete summary of each model."""
    out: dict[str, dict[str, Any]] = {}
    for path in sorted(_dir(root).glob("2*.json")):
        try:
            s = json.loads(path.read_text(encoding="utf-8"))["summary"]
        except (ValueError, OSError, KeyError):
            continue
        if s.get("complete"):
            out[s["model"]] = s
    return out


def recommended(root: Path | None = None, installed: list[str] | None = None) -> str | None:
    s = latest(root)
    if installed is not None:
        s = {m: v for m, v in s.items() if m in installed}
    return recommend(s)


# -- background run (Ollama page) ---------------------------------------------------------------
_LOCK = threading.Lock()
_THREAD: list[threading.Thread] = []


def progress(root: Path | None = None) -> dict[str, Any]:
    path = _dir(root) / "progress.json"
    try:
        p = json.loads(path.read_text(encoding="utf-8"))
    except (ValueError, OSError):
        return {"running": False}
    if p.get("running") and not (_THREAD and _THREAD[0].is_alive()):
        p["running"] = False  # the server restarted during a run
        p["interrupted"] = True
    return p


def start_background(models: list[str], provider_factory: Callable[[str], Any], root: Path | None = None,
                     after: Callable[[], None] | None = None, case_list: list[Case] | None = None) -> bool:
    """Runs ``models`` one after the other in a background thread. False if a run is going on."""
    with _LOCK:
        if _THREAD and _THREAD[0].is_alive():
            return False
        case_list = case_list or cases()
        total = len(case_list) * len(models)
        path = _dir(root) / "progress.json"
        state = {"running": True, "models": models, "done": 0, "total": total, "model": models[0] if models else "",
                 "case": "", "started": datetime.now().isoformat(timespec="seconds"), "error": None}
        _write(path, state)

        def run() -> None:
            try:
                for k, m in enumerate(models):
                    def step(i: int, n: int, cid: str, k: int = k, m: str = m) -> None:
                        state.update(done=k * n + i, model=m, case=cid)
                        _write(path, state)

                    run_model(m, provider_factory, case_list, root, step)
                state.update(done=total)
            except Exception as exc:
                log.exception("Evaluation failed")
                state["error"] = f"{type(exc).__name__}: {exc}"[:200]
            finally:
                state.update(running=False, finished=datetime.now().isoformat(timespec="seconds"))
                _write(path, state)
                if after:
                    try:
                        after()
                    except Exception as exc:  # noqa: BLE001
                        log.warning("Evaluation follow-up failed: %s", exc)

        t = threading.Thread(target=run, name="assistant-eval", daemon=True)
        _THREAD[:] = [t]
        t.start()
        log.info("Evaluation started in the background: %s (%d questions)", ", ".join(models), total)
        return True


def main(argv: list[str] | None = None) -> None:
    from carbonsync.assistant import usage
    from carbonsync.assistant.providers import ollama_provider as olp

    ap = argparse.ArgumentParser(description="Score local models on the assistant's evaluation set.")
    ap.add_argument("--models", nargs="+", required=True)
    ap.add_argument("--lang", choices=["fr", "en"], help="only the questions in this language")
    a = ap.parse_args(argv)
    num_ctx = usage.load_settings()["ollama"]["num_ctx"]
    case_list = cases((a.lang,) if a.lang else ("fr", "en"))
    rows = [run_model(m, lambda mm: olp.OllamaProvider(mm, num_ctx=num_ctx), case_list) for m in a.models]
    print(f"\n{'model':<16} {'score':>6} {'tools':>6} {'figures':>8} {'grounded':>9} {'lang':>5} {'s/q':>5}")
    for s in rows:
        print(f"{s['model']:<16} {s['score']:>6} {s['tools_pct']:>6} {s['figures_pct']:>8} {s['grounded_pct']:>9} "
              f"{s['language_pct']:>5} {s['seconds_mean']:>5}")
    print("recommended:", recommend({s["model"]: s for s in rows}))


if __name__ == "__main__":
    main()
