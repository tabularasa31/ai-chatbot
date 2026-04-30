# Eval fixtures

`docs/` holds a small subset of the chat9 client docs (copied from
`frontend/content/docs/*.mdx` and renamed to `.md` so the chat9
upload pipeline accepts them). They form the demo bot's knowledge
base, used by `scripts/seed_eval_bot.py` and referenced in
`tests/eval/datasets/chat9_basic.yaml`.

When chat9's product docs change materially, **re-sync the fixtures**
so golden answers stay realistic:

```bash
cp frontend/content/docs/getting-started.mdx       tests/eval/datasets/_fixtures/docs/getting-started.md
cp frontend/content/docs/pricing-and-limits.mdx    tests/eval/datasets/_fixtures/docs/pricing-and-limits.md
cp frontend/content/docs/faq.mdx                   tests/eval/datasets/_fixtures/docs/faq.md
cp frontend/content/docs/api.mdx                   tests/eval/datasets/_fixtures/docs/api.md
```

Then audit `chat9_basic.yaml` for any cases whose `must_contain` /
`expected_sources` no longer match the fixture content.
