"""Offline unit tests for subtitle parsing, lemmas and CEFR data.

Run from the app root:  python -m unittest discover -s tests -v
"""
from __future__ import annotations

import unittest

from vocab import cefr, ecdict, text


SAMPLE_SRT = """1
00:00:00,400 --> 00:00:04,227
Little Giant Tommy: P-p-people come out from the suitcase?!
“小巨人”汤米：——有、有人从箱子里钻出来了？！

2
00:00:23,810 --> 00:00:25,900
Vertin: This is my suitcase.
维尔汀：这是我的箱子。
"""


class SubtitleTests(unittest.TestCase):
    def test_parse_and_speaker(self):
        cues = text.parse_srt(SAMPLE_SRT)
        self.assertEqual(len(cues), 2)
        self.assertAlmostEqual(cues[0]["start"], 0.4)
        self.assertAlmostEqual(cues[0]["end"], 4.227)
        en_line, zh_line = cues[0]["text"].split("\n")
        speaker, body = text.split_speaker(text.clean_text(en_line), "en")
        self.assertEqual(speaker, "Little Giant Tommy")
        self.assertEqual(body, "P-p-people come out from the suitcase?!")
        zh_speaker, zh_body = text.split_speaker(text.clean_text(zh_line), "zh")
        self.assertEqual(zh_speaker, "“小巨人”汤米")
        self.assertEqual(zh_body, "——有、有人从箱子里钻出来了？！")

    def test_no_false_speaker(self):
        speaker, body = text.split_speaker("It's raining.", "en")
        self.assertEqual(speaker, "")
        self.assertEqual(body, "It's raining.")

    def test_token_offsets(self):
        sentence = "Open your eyes, followers!"
        tokens = text.tokenize(sentence)
        surfaces = [sentence[s:e] for s, e, _ in tokens]
        self.assertEqual(surfaces, ["Open", "your", "eyes", "followers"])
        self.assertEqual(tokens[-1][2], "follower")

    def test_contractions_and_hyphens(self):
        sentence = "Don't run — it's a well-known trap."
        surfaces = [sentence[s:e] for s, e, _ in text.tokenize(sentence)]
        self.assertIn("Don't", surfaces)
        self.assertIn("it's", surfaces)
        self.assertIn("well-known", surfaces)


class LemmaTests(unittest.TestCase):
    def test_irregular_forms(self):
        if not ecdict.available():
            self.skipTest("offline dictionary not built")
        self.assertEqual(ecdict.lemma("went"), "go")
        self.assertEqual(ecdict.lemma("children"), "child")
        self.assertEqual(ecdict.lemma("running"), "run")

    def test_suffix_fallback(self):
        if not ecdict.available():
            self.skipTest("offline dictionary not built")
        self.assertEqual(ecdict.lemma("followers"), "follower")
        self.assertEqual(ecdict.lemma("happier"), "happy")


class CefrTests(unittest.TestCase):
    def test_dataset_available(self):
        self.assertTrue(cefr.available())

    def test_levels_and_pos(self):
        item = cefr.entry("robber")
        self.assertIsNotNone(item)
        self.assertIn(item["level"], ("A1", "A2", "B1", "B2", "C1", "C2"))
        self.assertTrue(item["pos_levels"])
        self.assertEqual(cefr.level("robber"), item["level"])


if __name__ == "__main__":
    unittest.main()
