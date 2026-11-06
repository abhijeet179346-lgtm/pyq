from django.test import SimpleTestCase

from apps.content.math_text import repair_text


class MathTextTests(SimpleTestCase):
    def test_repairs_broken_symbols_without_changing_latex(self):
        self.assertEqual(repair_text('Î±â\x88\x88R and $x^2$'), 'α∈R and $x^2$')
        self.assertEqual(repair_text('UseÂ\xa0CaCO3Â'), 'Use\xa0CaCO3')

    def test_wraps_bare_table_but_preserves_existing_delimiters(self):
        table = r'\begin{array}{cc}a&b\\c&d\end{array}'
        self.assertEqual(repair_text('Match: ' + table), 'Match: \\[' + table + '\\]')
        self.assertEqual(repair_text('$' + table + '$'), '$' + table + '$')
        nested = r'\begin{array}{c}\begin{array}{c}a\end{array}\end{array}'
        self.assertEqual(repair_text(nested), '\\[' + nested + '\\]')
