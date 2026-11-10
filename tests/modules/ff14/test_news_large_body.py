"""Full major-patch HTML participates in the existing article identity check."""

import unittest

from ygl_test_subject.modules.ff14.features.news import (
    MAX_BODY_CHARS,
    article,
    body_excerpt,
    content_facts,
)


class LargeNewsTests(unittest.TestCase):
    def test_major_patch_tail_changes_version_without_growing_saved_excerpt(self):
        body = "<p>本次更新。</p>" + "<p>条目内容。</p>" * 20000
        row = {
            "Id": 123,
            "Title": "大型版本更新笔记",
            "CategoryCode": "8325",
            "Content": body + "<p>原尾部。</p>",
        }
        before = article(row)
        after = article({**row, "Content": body + "<p>修正尾部。</p>"})
        self.assertGreater(len(body), 100000)
        self.assertNotEqual(before["version"], after["version"])
        self.assertLessEqual(len(after["summary"]), 200)
        self.assertLessEqual(len(after["body_snapshot"]), 1024)
        self.assertTrue(after["body_truncated"])

    def test_body_budget_remains_finite(self):
        for call in (content_facts, lambda body: body_excerpt(body, "title")):
            with self.assertRaisesRegex(ValueError, "news_text_invalid"):
                call("x" * (MAX_BODY_CHARS + 1))
