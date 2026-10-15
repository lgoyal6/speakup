"""Check transcript edit counts and pooled error rates on hand-counted pairs.
The speech study uses these functions without contacting a provider.
Silent reference clips must not hide inserted words as zero error.
"""
import unittest

from speakup.metrics import corpus_wer, normalize_words, word_edits, word_error_rate


class MetricTests(unittest.TestCase):
    def test_normalization_ignores_case_punctuation_and_accents(self):
        self.assertEqual(normalize_words("Café, it's  DONE!"), ["cafe", "it's", "done"])

    def test_hand_counted_edit_types(self):
        # ref: the cat sat on the mat ; hyp: the bat sat on mat today
        result = word_edits("the cat sat on the mat", "The bat sat on mat today")
        self.assertEqual((result["substitutions"], result["deletions"], result["insertions"], result["edits"]), (1, 1, 1, 3))
        self.assertAlmostEqual(word_error_rate("the cat sat on the mat", "the bat sat on mat today"), 0.5)

    def test_identity_empty_and_all_wrong(self):
        self.assertEqual(word_error_rate("hello world", "hello, world."), 0.0)
        self.assertEqual(word_error_rate("hello world", ""), 1.0)
        self.assertEqual(word_error_rate("", ""), 0.0)
        self.assertEqual(word_error_rate("", "noise"), float("inf"))

    def test_corpus_wer_pools_words_not_utterance_rates(self):
        pooled = corpus_wer([("a b c d", "a b c d"), ("x", "y")])
        self.assertEqual((pooled["edits"], pooled["reference_words"]), (1, 5))
        self.assertAlmostEqual(pooled["wer"], 0.2)

    def test_all_silent_references_do_not_hide_insertions(self):
        self.assertEqual(corpus_wer([])["wer"], 0.0)
        self.assertEqual(corpus_wer([("", "")])["wer"], 0.0)
        silent = corpus_wer([("", "invented words"), ("", "")])
        self.assertEqual(silent["edits"], 2)
        self.assertEqual(silent["wer"], float("inf"))


if __name__ == "__main__":
    unittest.main()
