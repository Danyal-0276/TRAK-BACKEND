"""Smoke test: HF Spaces + fact checker + summarizer."""
from __future__ import annotations

import os
import sys

import django

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "TRAK_Backend.settings")
django.setup()

from django.conf import settings

from news.credibility.inference import predict_credibility, preload_credibility_model
from news.factcheck.service import preload_fact_checker, verify_claim
from news.summarization.inference import preload_summarizer_model, summarize_text


def main() -> int:
    print("=== ENV (from Django settings) ===")
    keys = [
        "SUMMARIZER_SPACE_ID",
        "FAKE_DETECTION_SPACE_ID",
        "SUMMARIZER_ENABLED",
        "FACT_CHECKER_ENABLED",
        "FACT_CHECKER_PROVIDERS",
        "FACT_CHECKER_PROVIDER",
        "HF_TOKEN",
        "CREDIBILITY_CONFIDENCE_THRESHOLD",
    ]
    for key in keys:
        val = getattr(settings, key, None)
        display = val if val else "(unset)"
        if key == "HF_TOKEN" and val:
            display = val[:8] + "..."
        print(f"  {key}={display}")

    print("\n=== PRELOAD ===")
    sum_info = preload_summarizer_model()
    cred_info = preload_credibility_model()
    fc_info = preload_fact_checker()
    print("  Summarizer:", sum_info)
    print("  Credibility:", cred_info)
    print("  Fact checker:", fc_info)

    sample = (
        "Scientists discovered that drinking eight glasses of water cures all diseases overnight, "
        "doctors say!"
    )
    title = "Miracle water cure goes viral"

    print("\n=== FAKE DETECTION + FACT CHECK ===")
    cred = predict_credibility(sample, title=title)
    for key in (
        "fake_detection_model_id",
        "fake_detection_label",
        "fake_detection_max_prob",
        "fact_check_provider",
        "fact_check_providers_used",
        "fact_check_verdict",
        "fact_check_support_count",
        "fact_check_contradict_count",
        "fact_check_results",
        "fact_check_error",
        "credibility_label",
        "credibility_model_id",
    ):
        print(f"  {key}: {cred.get(key)}")

    print("\n=== SUMMARIZER ===")
    s = summarize_text(sample, title=title)
    print("  summarizer_mode:", s.get("summarizer_mode"))
    print("  summarizer_model_id:", s.get("summarizer_model_id"))
    summary = (s.get("summary") or "")[:250]
    print("  summary:", summary or "(empty)")

    ok = True
    if not sum_info.get("loaded") and sum_info.get("mode") != "bart-space":
        print("\n[WARN] Summarizer Space not loaded")
        ok = False
    if cred_info.get("mode") not in ("space",) or not cred_info.get("loaded"):
        print("\n[WARN] Fake detection Space not loaded")
        ok = False
    if fc_info.get("mode") == "disabled" or not fc_info.get("loaded"):
        print("\n[WARN] Fact checker not loaded")
        ok = False
    if cred.get("fact_check_verdict") == "api_error":
        print("\n[WARN] Fact checker API error:", cred.get("fact_check_error"))
        ok = False
    if cred.get("fake_detection_model_id", "").startswith("stub"):
        print("\n[WARN] Fake detection fell back to stub (Space call failed)")
        ok = False
    if s.get("summarizer_mode") not in ("bart-space", "bart"):
        print("\n[WARN] Summarizer fell back to extractive")
        ok = False

    print("\n=== RESULT ===", "OK" if ok else "ISSUES FOUND")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
