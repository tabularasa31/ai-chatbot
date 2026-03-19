# CURSOR PROMPT TEMPLATE

⚠️ **CRITICAL: YOU MUST FOLLOW THE SETUP EXACTLY AS WRITTEN. NO SHORTCUTS.**

---

## SETUP

```bash
cd ~/Projects/ai-chatbot
git checkout main
git pull origin main
git checkout -b feature/FEATURE_NAME
```

**IMPORTANT:** Follow these commands in EXACT ORDER:
1. Checkout main branch
2. Pull latest from origin/main (ensure you have latest code)
3. Create NEW branch from main (do not reuse old branches)

**DO NOT:**
- Skip `git pull origin main` — this ensures you see latest changes
- Reuse branches from previous attempts
- Assume your local main is up-to-date
- Work on any branch other than the newly created feature branch

---

## CODE DISCIPLINE

**Scope (you MAY modify):**
- [List files you can change]

**Do NOT touch:**
- [List files/areas that are off-limits]
- migrations
- Other modules

**If you think something outside Scope must be changed, STOP and describe it in a comment instead of editing code.**

---

## CONTEXT

[Problem description, current state, why this fix matters]

---

## WHAT TO DO

[Step-by-step implementation instructions]

---

## TESTING

Before pushing:
- [ ] [Test point 1]
- [ ] [Test point 2]
- [ ] [Test point 3]

---

## GIT PUSH

```bash
git add [modified files]
git commit -m "feat/fix: [description] (FI-XXX)"
git push origin feature/FEATURE_NAME
```

**STRICT ORDER:**
1. Add files
2. Commit with message
3. Push to origin
4. Do NOT skip any step

---

## NOTES

- [Important implementation detail]
- [Edge case to watch for]
- [Security consideration if relevant]

---

## PR DESCRIPTION

After completing the implementation, provide the Pull Request description in English (Markdown format):

```markdown
## Summary
[1-2 sentences: what was changed and why]

## Changes
- [file.py] — [what changed]
- [file.py] — [what changed]

## Testing
- [ ] Tests pass (pytest)
- [ ] Manual test: [specific scenario]

## Notes
[Any important context, limitations, or follow-up work]
```

