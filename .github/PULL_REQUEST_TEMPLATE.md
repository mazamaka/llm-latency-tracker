<!-- Keep PRs focused — one logical change each. -->

## What & why

<!-- What does this change, and why? -->

## Checklist

- [ ] `python3 -m pytest -q` passes
- [ ] `ruff check .` is clean
- [ ] If adding a provider: the host resolves and the endpoint responds; `name` matches `^[a-z0-9][a-z0-9_-]{0,63}$`
- [ ] No fabricated data; honest about limitations
