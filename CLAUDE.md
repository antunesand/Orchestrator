# Project Rules

## PR Link Sharing

When the user asks for a PR link, always generate a pre-filled GitHub PR creation URL using `urllib.parse.quote` to encode the title and body. The URL format is:

```
https://github.com/{owner}/{repo}/compare/{base}...{branch}?expand=1&title={encoded_title}&body={encoded_body}
```

- Extract `{owner}/{repo}` from `git remote -v`
- Use the current branch as `{branch}` and `main` as `{base}`
- Build the title from the branch work (short, descriptive)
- Build the body with a `## Summary` section (bullet points of changes) and a `## Test plan` section (checklist)
- Share the final URL as a clickable markdown link
