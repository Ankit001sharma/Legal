# review_agent tests

## Unit (no Postgres)

```powershell
cd Legal\review\review_agent
python -m pytest -m "not integration" -q
```

## Integration (Postgres `legalai_test` on port 5435)

```powershell
$env:TEST_DATABASE_URL="postgresql://legalai:legalai@127.0.0.1:5435/legalai_test"
bash scripts/run_integration_tests.sh
```

## Load test (local, mock LLM)

Requires Postgres + document-mcp in-process (same as integration tests):

```powershell
$env:TEST_DATABASE_URL="postgresql://legalai:legalai@127.0.0.1:5435/legalai_test"
python scripts/load_test_reviews.py --concurrency 5 --reviews 10 --tenant load-test
```

Exits non-zero if error rate exceeds 10%.
