# Category assignment and admin recategorization

## Source of truth

Browse pages filter by **`primary_category`** (one slug per article). Secondary ML labels in `categories[]` do not affect browse when `CATEGORY_BROWSE_PRIMARY_ONLY=true`.

## Backfill missing categories

After changing classifier settings or importing articles:

```bash
cd TRAK-BACKEND
python manage.py run_category_backfill
```

## Fix mislabeled articles

1. Open **Admin → Articles** and locate the article.
2. Review the assigned `primary_category` in the article detail/review modal.
3. Override `primary_category` to the correct taxonomy slug before approving or via edit.
4. Run `python manage.py run_category_backfill --force` if bulk reclassification is needed (see command help).

## Tuning

- `CATEGORY_PRIMARY_MIN_CONFIDENCE` — raise to reduce low-confidence mislabels (articles below threshold should map to uncategorized rather than a wrong chip).
- `CATEGORY_RULE_FALLBACK_ENABLED` — disable if rule-based guessing causes false matches.
