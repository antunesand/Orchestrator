"""Tests for diff extraction from LLM output."""

from council.diff_extract import extract_diffs, combine_diffs, extract_and_save


class TestExtractDiffs:
    def test_fenced_diff_block(self):
        text = '''Here is the fix:

```diff
--- a/src/auth.py
+++ b/src/auth.py
@@ -10,3 +10,3 @@
-    return None
+    return user
```

Done.
'''
        diffs = extract_diffs(text)
        assert len(diffs) == 1
        assert "--- a/src/auth.py" in diffs[0]
        assert "+    return user" in diffs[0]

    def test_multiple_fenced_blocks(self):
        text = '''Fix 1:

```diff
--- a/foo.py
+++ b/foo.py
@@ -1,1 +1,1 @@
-old
+new
```

Fix 2:

```diff
--- a/bar.py
+++ b/bar.py
@@ -5,1 +5,1 @@
-broken
+fixed
```
'''
        diffs = extract_diffs(text)
        assert len(diffs) == 2
        assert "foo.py" in diffs[0]
        assert "bar.py" in diffs[1]

    def test_no_diffs(self):
        text = "This is just a regular response with no diffs."
        diffs = extract_diffs(text)
        assert diffs == []

    def test_deduplication(self):
        diff_block = """--- a/f.py
+++ b/f.py
@@ -1,1 +1,1 @@
-a
+b"""
        text = f"```diff\n{diff_block}\n```\n\nAlso:\n\n```diff\n{diff_block}\n```"
        diffs = extract_diffs(text)
        assert len(diffs) == 1

    def test_empty_diff_block(self):
        text = "```diff\n```"
        diffs = extract_diffs(text)
        assert diffs == []


class TestCombineDiffs:
    def test_combine_multiple(self):
        diffs = ["--- a/f1\n+++ b/f1\n@@ @@\n-a\n+b", "--- a/f2\n+++ b/f2\n@@ @@\n-c\n+d"]
        combined = combine_diffs(diffs)
        assert "f1" in combined
        assert "f2" in combined
        assert combined.endswith("\n")

    def test_combine_empty(self):
        assert combine_diffs([]) == ""


class TestExtractAndSave:
    def test_saves_to_file(self, tmp_path):
        text = '''```diff
--- a/x.py
+++ b/x.py
@@ -1 +1 @@
-old
+new
```'''
        out = tmp_path / "test.patch"
        result = extract_and_save(text, str(out))
        assert result is not None
        assert out.exists()
        content = out.read_text()
        assert "--- a/x.py" in content

    def test_returns_none_no_diffs(self, tmp_path):
        result = extract_and_save("no diffs here", str(tmp_path / "empty.patch"))
        assert result is None
        assert not (tmp_path / "empty.patch").exists()
