"""Tests for lyrics_flow - the whole-song sheet behind the Lyrics Flow display mode.

Standard library only (unittest), so they run without the app's dependencies:
    python scripts/test_lyrics_flow.py -v
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))  # repo root

import lyrics_flow as lf  # noqa: E402

SONG = [(1.0, "First line"), (5.0, "Second line"), (9.0, "Third line")]


class SheetRows(unittest.TestCase):
    def test_rows_wrap_the_song_in_intro_and_end(self):
        self.assertEqual(lf.sheet_rows(SONG), ["Intro", "First line", "Second line", "Third line", "End"])

    def test_empty_line_shows_a_note_like_the_six_line_window(self):
        self.assertEqual(lf.sheet_rows([(1.0, "a"), (2.0, ""), (3.0, "b")])[2], "♪")

    def test_no_sheet_without_lyrics(self):
        self.assertIsNone(lf.sheet_rows(None))
        self.assertIsNone(lf.sheet_rows([]))

    def test_no_sheet_for_the_searching_placeholder(self):
        # Covers AE7: the placeholder is not a sheet; the real lyrics arrive as one later.
        self.assertIsNone(lf.sheet_rows([(0, "Searching lyrics...")]))

    def test_no_sheet_for_a_single_line_instrumental_placeholder(self):
        # The nine placeholders lyrics.py and server.py listed inline before they shared INSTRUMENTAL_TEXTS
        for text in ("Instrumental", "music only", "No Lyrics", "non-lyrical", "♪", "♫", "♬",
                     "(Instrumental)", "[instrumental]"):
            with self.subTest(text=text):
                self.assertIsNone(lf.sheet_rows([(0, text)]))

    def test_a_real_single_line_song_is_a_sheet(self):
        self.assertEqual(lf.sheet_rows([(0, "Only line")]), ["Intro", "Only line", "End"])


class SheetId(unittest.TestCase):
    def test_same_lines_same_id(self):
        self.assertEqual(lf.sheet_id(SONG), lf.sheet_id(list(SONG)))

    def test_changed_text_or_timestamp_changes_the_id(self):
        self.assertNotEqual(lf.sheet_id(SONG), lf.sheet_id([(1.0, "First line!")] + SONG[1:]))
        self.assertNotEqual(lf.sheet_id(SONG), lf.sheet_id([(1.5, "First line")] + SONG[1:]))

    def test_id_is_short(self):
        self.assertLessEqual(len(lf.sheet_id(SONG)), 16)


class RowForIndex(unittest.TestCase):
    def test_line_k_is_row_k_plus_one(self):
        self.assertEqual(lf.row_for_index(0, 3), 1)
        self.assertEqual(lf.row_for_index(2, 3), 3)

    def test_intro_and_unknown_map_to_the_leading_row(self):
        self.assertEqual(lf.row_for_index(-2, 3), 0)
        self.assertEqual(lf.row_for_index(-1, 3), 0)

    def test_outro_maps_to_the_trailing_row(self):
        self.assertEqual(lf.row_for_index(-3, 3), 4)

    def test_repeated_lines_keep_their_own_rows(self):
        chorus = [(1.0, "La la"), (5.0, "Verse"), (9.0, "La la")]
        rows = lf.sheet_rows(chorus)
        self.assertEqual((rows[lf.row_for_index(0, 3)], rows[lf.row_for_index(2, 3)]), ("La la", "La la"))
        self.assertNotEqual(lf.row_for_index(0, 3), lf.row_for_index(2, 3))


class FlowFragment(unittest.TestCase):
    def test_sheet_sent_when_the_page_has_none_or_another(self):
        for known in (None, "", "stale"):
            with self.subTest(known=known):
                frag = lf.flow_fragment(SONG, 1, known)
                self.assertEqual(frag["sheet"], lf.sheet_rows(SONG))
                self.assertEqual((frag["sheet_id"], frag["row"]), (lf.sheet_id(SONG), 2))

    def test_sheet_omitted_when_the_page_already_has_it(self):
        frag = lf.flow_fragment(SONG, 1, lf.sheet_id(SONG))
        self.assertNotIn("sheet", frag)
        self.assertEqual(frag["row"], 2)

    def test_no_sheet_states_carry_no_id(self):
        for lines in (None, [], [(0, "Searching lyrics...")], [(0, "Instrumental")]):
            with self.subTest(lines=lines):
                self.assertEqual(lf.flow_fragment(lines, -1, "x"), {"sheet_id": None, "row": None})


if __name__ == "__main__":
    unittest.main()
