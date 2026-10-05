"""Probe definitions: real public-API calls into real stdlib programs.

A probe is a source snippet `def __regpt_probe__(_m): ...` returning plain
data (str/int/bool/list/tuple/dict). The executor binds the *program variant*
to `_m`, so the probe exercises mutated code, and returns only deterministic
values -- no object reprs, so observations are byte-stable across runs.

`cost` is the declared price of the observation, standing in for the real
per-probe runtime. A method is judged on the total declared cost of the
observations it selects, so keeping the informative probes and dropping the
expensive ones is a win on its own.
"""

from __future__ import annotations

DIFFLIB = [
    ("ratio_4", 4, "SequenceMatcher(None,'abcd','bcde').ratio()"),
    ("ratio_ident", 2, "SequenceMatcher(None,'abc','abc').ratio()"),
    ("quick_ratio_40", 5, "SequenceMatcher(None,'x'*40,'x'*20+'y'*20).quick_ratio()"),
    ("real_quick_40", 4, "SequenceMatcher(None,'x'*40,'x'*20+'y'*20).real_quick_ratio()"),
    ("matching_blocks", 4,
     "SequenceMatcher(None,'qjhvhtzxz','qjhvhtzxz').get_matching_blocks()"),
    ("opcodes_40", 7, "SequenceMatcher(None,list(range(40)),list(range(40))).get_opcodes()"),
    ("opcodes_shift", 7,
     "SequenceMatcher(None,list(range(40)),list(range(20,60))).get_opcodes()"),
    ("grouped_opcodes", 5,
     "SequenceMatcher(None,'abcdefg','acdfg').get_grouped_opcodes(3)"),
    ("autojunk_off", 6,
     "SequenceMatcher(None,list(range(40)),list(range(40)),autojunk=False).ratio()"),
    ("ndif_small", 4, "ndif('qjrwpit','abcde')"),
    ("ndif_long", 5, "ndif('abcdefghij','jihgfedcba')"),
    ("unified_diff", 5,
     "list(unified_diff('a\\nb\\nc\\nd\\n'.splitlines(1),'a\\nc\\ne\\nd\\n'.splitlines(1)))"),
    ("context_diff", 5,
     "list(context_diff('a\\nb\\nc\\nd\\n'.splitlines(1),'a\\nb\\nd\\n'.splitlines(1)))"),
    ("html_table", 8, "HtmlDiff().make_table(['a\\n','b\\n'],['a\\n','c\\n'])"),
    ("sq_repr_pairs", 3,
     "SequenceMatcher(None,'abcdef','abed').get_opcodes()"),
    ("find_longest", 4,
     "SequenceMatcher(None,list('abcdefghi'),list('xcdefghiy')).find_longest_match(0,9,0,9)"),
]

TEXTWRAP = [
    ("wrap_20", 3, "wrap('The quick brown fox jumps over the lazy dog',20)"),
    ("wrap_indent", 4,
     "wrap('a b c d e f g h i j k',10,initial_indent='>> ',subsequent_indent='   ')"),
    ("fill_40", 4, "fill('word '*40,40)"),
    ("wrap_long_word", 3, "wrap('supercalifragilisticexpialidocious',10)"),
    ("wrap_hyphen", 3, "wrap('well-known hyphenated words go here',12,break_on_hyphens=True)"),
    ("wrap_nohyphen", 3,
     "wrap('well-known hyphenated words go here',12,break_on_hyphens=False)"),
    ("wrap_tabs", 3, "wrap('a\\tb\\tc\\td',8,tabsize=4,expand_tabs=True)"),
    ("fill_nows", 3, "fill('keep  double  spaces',30,replace_whitespace=False)"),
    ("textwrapper_25", 6,
     "TextWrapper(width=25).wrap(' '.join(str(i) for i in range(60)))"),
    ("wrap_exact_width", 2, "wrap('exactlyten',11)"),
    ("wrap_narrow", 2, "wrap('word',2)"),
    ("wrap_empty", 1, "wrap('')"),
    ("wrap_single_space", 1, "wrap(' ',10)"),
    ("wrap_newlines", 3, "wrap('a\\nb\\nc',20,replace_whitespace=False)"),
    ("wrap_trailing_ws", 3, "fill('trailing space   ',30)"),
    ("wrap_unicode", 3, "wrap('مرحبا بالعالم كيف حالك',12)"),
]

FRACTIONS = [
    ("add", 3, "Fraction(7,3)+Fraction(5,6)"),
    ("mul_div", 4, "Fraction(3,4)*Fraction(8,9)/Fraction(2)"),
    ("floor_div", 3, "Fraction(7,2)//Fraction(3,2)"),
    ("modulo", 3, "Fraction(7,2)%Fraction(3,2)"),
    ("pow_neg", 3, "Fraction(2,3)**-2"),
    ("from_float", 4, "Fraction(0.1)"),
    ("from_float_pi", 5, "Fraction(3.14159265358979)"),
    ("from_decimal", 3, "Fraction(Decimal('0.1'))"),
    ("from_string", 2, "Fraction('3/7')"),
    ("limit_denom_13", 3, "Fraction(3.14159265358979).limit_denominator(13)"),
    ("limit_denom_7", 3, "Fraction(2.718281828459045).limit_denominator(7)"),
    ("float_roundtrip", 2, "float(Fraction(22,7))"),
    ("sort_mixed", 3,
     "[str(x) for x in sorted([Fraction(3,4),Fraction(1,3),Fraction(2,1),Fraction(5,8)])]"),
    ("sum_harmonic", 4, "sum([Fraction(1,n) for n in range(1,8)])"),
    ("mixed_int", 2, "Fraction(7,2)+3"),
    ("mixed_float", 3, "Fraction(1,3)+0.5"),
    ("negate_abs", 2, "(-Fraction(5,8)).numerator"),
    ("denominator_gcd", 3, "Fraction(100,10).denominator"),
    ("is_integer", 2, "Fraction(8,4).is_integer()"),
    ("as_integer_ratio", 2, "Fraction(-7,9).as_integer_ratio()"),
    ("pow_pos", 3, "Fraction(2,3)**4"),
    ("conjugate", 2, "Fraction(5,6).conjugate()"),
]

SHLEX = [
    ("split_basic", 2, "split(\"a b 'c d' e\")"),
    ("split_empty", 1, "split('')"),
    ("split_noposix", 3, "split('a \"b c\" d\\\\ e',posix=False)"),
    ("split_quotes", 2, "split(\"'one two' \\\"three four\\\" five\")"),
    ("split_comment", 2, "split('cmd arg1 # trailing comment')"),
    ("split_escapes", 3, "split('a\\\\ b c\\\\\"d e')"),
    ("split_multi_ws", 2, "split('a    b\\tc')"),
    ("split_trailing_bs", 2, "split('a b \\\\')"),
    ("stream_ws", 4, "_stream(_m,\"a 'b c' d\\n\")"),
    ("punctuation", 4, "_punct(_m,'x=1; y+=2;; z')"),
    ("commenters", 3, "_commenters(_m,'a b ; comment c')"),
    ("quotes_custom", 3, "split('a <b c> d')"),
    ("split_unicode", 2, "split('مرحبا بالعالم')"),
    ("split_unbalanced", 2, "split(\"a 'b\")"),
    ("split_only_space", 1, "split('    ')"),
    ("split_dquote_ws", 3, "split('a \"  b  \" c')"),
]

CONFIGPARSER = [
    ("read_basic", 3, "(_read(_m), sorted(_m.ConfigParser().sections()))"),
    ("getint", 2, "_c(_m)['s'].getint('k')"),
    ("getfloat", 2, "_c(_m)['s'].getfloat('x')"),
    ("getboolean", 2, "_c(_m)['s'].getboolean('flag')"),
    ("sections", 2, "_c(_m).sections()"),
    ("options", 2, "_c(_m).options('s')"),
    ("defaults", 2, "dict(_c(_m).defaults())"),
    ("items", 2, "dict(_c(_m)['s'].items())"),
    ("interp_basic", 2, "_c(_m)['s'].get('b_n', fallback=99)"),
    ("interp_interp", 2, "_c(_m)['s'].get('%(base)s_n', fallback=1)"),
    ("set_get", 2, "_setget(_m)"),
    ("remove_opt", 2, "(_c(_m).remove_option('s','k'), _c(_m).options('s'))"),
    ("add_section", 2, "(_c(_m).add_section('b'), _c(_m).sections())"),
    ("no_section", 2, "_c(_m).has_section('nope')"),
    ("converters", 2, "_c(_m).get('s','k', raw=True)"),
    ("multiline", 2, "_c(_m)['s'].get('multi')"),
    ("no_interp_val", 2, "_c(_m).get('s','pct', raw=True)"),
    ("spaces", 2, "_c(_m)['s'].get('k')"),
]

CONFIGPARSER_HELPERS = '''
def _c(_m):
    c = _m.ConfigParser()
    c.read_string(
        "[s]\\n"
        "k = 1\\n"
        "x = 2.5\\n"
        "flag = yes\\n"
        "base = b\\n"
        "b_n = 7\\n"
        "multi = one\\n"
        "    two\\n"
        "pct = 50%%\\n"
    )
    return c


def _read(_m):
    c = _m.ConfigParser()
    c.read_string("[a]\\nk = 1\\n")
    return sorted(c.sections())


def _setget(_m):
    c = _c(_m)
    c.set('s', 'k2', 'v')
    return c.get('s', 'k2')
'''

FNMATCH = [
    ("fnmatch_star", 2, "_m.fnmatch('a.txt', '*.txt')"),
    ("fnmatch_multi", 2, "_m.fnmatch('abc.def', '*.???')"),
    ("fnmatch_no", 2, "_m.fnmatch('a.py', '*.txt')"),
    ("fnmatch_q", 2, "_m.fnmatch('a?c', 'a?c')"),
    ("filter_basic", 2, "sorted(_m.filter(['a.txt','b.py','c.txt'], '*.txt'))"),
    ("translate_star", 2, "_m.translate('*.txt')"),
    ("translate_q", 2, "_m.translate('a?c')"),
    ("fnmatchcase_star", 2, "_m.fnmatchcase('a.txt', '*.TXT')"),
    ("fnmatchcase_no", 2, "_m.fnmatchcase('a.txt', '*.py')"),
    ("translate_brackets", 2, "_m.translate('[a-c]x')"),
    ("filtercase", 2, "sorted(_m.filter(['A.TXT','b.txt'], '*.txt'))"),
    ("star_nostar", 2, "_m.fnmatch('noext', '*')"),
    ("empty_pat", 2, "_m.fnmatch('x', '')")]

FNMATCH_HELPERS = ''

HELPERS = '''
def _stream(_m, s):
    lex = _m.shlex(s, posix=True)
    lex.whitespace_split = True
    return list(lex)


def _punct(_m, s):
    lex = _m.shlex(s, posix=True, punctuation_chars=True)
    lex.whitespace_split = True
    return list(lex)


def _commenters(_m, s):
    lex = _m.shlex(s, posix=True, commenters=';', whitespace_split=True)
    return list(lex)
'''

NAMES = {
    "difflib": "    SequenceMatcher = _m.SequenceMatcher\n    ndiff = _m.ndiff\n"
               "    unified_diff = _m.unified_diff\n    context_diff = _m.context_diff\n"
               "    HtmlDiff = _m.HtmlDiff\n",
    "textwrap": "    wrap = _m.wrap\n    fill = _m.fill\n    TextWrapper = _m.TextWrapper\n",
    "fractions": "    from decimal import Decimal\n    Fraction = _m.Fraction\n",
    "shlex": "    split = _m.split\n",
    "configparser": "",
    "fnmatch": "",
}

PROGRAMS = ("difflib", "textwrap", "fractions", "shlex", "configparser", "fnmatch")


PREAMBLE = {
    "shlex": HELPERS,
    "configparser": CONFIGPARSER_HELPERS,
    "fnmatch": FNMATCH_HELPERS,
}


def probe_source(module: str, body: str) -> str:
    """Full probe function source for one module, bound to variant `_m`.

    The names are bound *inside* the function: `_m` only exists there, and the
    helpers take it as an argument for the same reason.
    """
    return (
        PREAMBLE.get(module, "")
        + "\ndef __regpt_probe__(_m):\n"
        + NAMES[module]
        + "\n    return "
        + body
        + "\n"
    )
