###
# Copyright (c) 2020, mogad0n
# All rights reserved.
#
# Redistribution and use in source and binary forms, with or without
# modification, are permitted provided that the following conditions are met:
#
#   * Redistributions of source code must retain the above copyright notice,
#     this list of conditions, and the following disclaimer.
#   * Redistributions in binary form must reproduce the above copyright notice,
#     this list of conditions, and the following disclaimer in the
#     documentation and/or other materials provided with the distribution.
#   * Neither the name of the author of this software nor the name of
#     contributors to this software may be used to endorse or promote products
#     derived from this software without specific prior written consent.
#
# THIS SOFTWARE IS PROVIDED BY THE COPYRIGHT HOLDERS AND CONTRIBUTORS "AS IS"
# AND ANY EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT LIMITED TO, THE
# IMPLIED WARRANTIES OF MERCHANTABILITY AND FITNESS FOR A PARTICULAR PURPOSE
# ARE DISCLAIMED.  IN NO EVENT SHALL THE COPYRIGHT OWNER OR CONTRIBUTORS BE
# LIABLE FOR ANY DIRECT, INDIRECT, INCIDENTAL, SPECIAL, EXEMPLARY, OR
# CONSEQUENTIAL DAMAGES (INCLUDING, BUT NOT LIMITED TO, PROCUREMENT OF
# SUBSTITUTE GOODS OR SERVICES; LOSS OF USE, DATA, OR PROFITS; OR BUSINESS
# INTERRUPTION) HOWEVER CAUSED AND ON ANY THEORY OF LIABILITY, WHETHER IN
# CONTRACT, STRICT LIABILITY, OR TORT (INCLUDING NEGLIGENCE OR OTHERWISE)
# ARISING IN ANY WAY OUT OF THE USE OF THIS SOFTWARE, EVEN IF ADVISED OF THE
# POSSIBILITY OF SUCH DAMAGE.

###

import datetime
import json
import os
import sys
import types
import unittest

_here = os.path.dirname(os.path.abspath(__file__))

try:
    from supybot.test import *
    HAVE_SUPYBOT = True
except ImportError:
    HAVE_SUPYBOT = False

    def _stub(name, **attrs):
        mod = types.ModuleType(name)
        for k, v in attrs.items():
            setattr(mod, k, v)
        sys.modules[name] = mod
        return mod

    def _wrap(*args, **kwargs):
        # supports both @wrap([...]) and wrap(func, [...]) call styles
        if len(args) >= 2 and callable(args[0]):
            return args[0]
        return lambda f: f

    def _marker(*args, **kwargs):
        return args[0] if args else None

    def _format(fmt, *args):
        # minimal utils.str.format: %s/%i/%f positional + %T (seconds -> HH:MM:SS)
        args = list(args)

        def _td(secs):
            secs = int(secs)
            sign = '-' if secs < 0 else ''
            h, rem = divmod(abs(secs), 3600)
            m, s = divmod(rem, 60)
            return '%s%02d:%02d:%02d' % (sign, h, m, s)

        out, i, argi = '', 0, 0
        while i < len(fmt):
            if fmt[i] == '%' and i + 1 < len(fmt):
                t = fmt[i + 1]
                if t == 'T':
                    out += _td(args[argi]); argi += 1; i += 2; continue
                if t in 'sif':
                    out += str(args[argi]); argi += 1; i += 2; continue
                if t == '%':
                    out += '%'; i += 2; continue
            out += fmt[i]; i += 1
        return out

    _supybot = _stub('supybot')

    _log = _stub('supybot.log', debug=lambda *a, **k: None,
                 warning=lambda *a, **k: None, error=lambda *a, **k: None)
    _supybot.log = _log

    class _Data:
        @staticmethod
        def dirize(name):
            return name

    class _Directories:
        data = _Data()

    class _SupybotConf:
        directories = _Directories()

    _supybot.conf = _stub('supybot.conf', supybot=_SupybotConf())
    _supybot.world = _stub('supybot.world', flushers=[])
    _utils = _stub('supybot.utils')
    _utils.str = _stub('supybot.utils.str', format=_format)
    _supybot.utils = _utils
    _supybot.plugins = _stub('supybot.plugins')
    _supybot.ircutils = _stub('supybot.ircutils')

    class _Plugin:
        log = _log

        def __init__(self, irc):
            pass

    _callbacks = _stub('supybot.callbacks', Plugin=_Plugin)
    _supybot.callbacks = _callbacks
    _commands = _stub('supybot.commands', wrap=_wrap, getopts=_marker,
                      optional=_marker, many=_marker, any=_marker,
                      additional=_marker)
    _supybot.commands = _commands

    class _FakeTz(datetime.tzinfo):
        def __init__(self, name='UTC'):
            self._name = name

        def utcoffset(self, dt):
            return datetime.timedelta(0)

        def dst(self, dt):
            return datetime.timedelta(0)

        def localize(self, dt, is_dst=False):
            return dt.replace(tzinfo=self)

        def __str__(self):
            return self._name

    _stub('pytz', timezone=lambda name: _FakeTz(str(name)),
          UnknownTimeZoneError=type('UnknownTimeZoneError', (Exception,), {}))
    _stub('num2words', num2words=lambda n, **k: str(n))

sys.path.insert(0, _here)
import plugin

try:
    import pytz as _pytz
except ImportError:
    _pytz = sys.modules['pytz']

TZ = _pytz.timezone('Europe/Rome')
NOW = TZ.localize(datetime.datetime(2025, 11, 30, 18, 5, 0))

# never touch the live db, always read the repo copy of the psychonaut data
plugin.filename = os.path.join(_here, 'data', 'Tripsit.db')
plugin.psychonaut_file = os.path.join(_here, 'data', 'psychonaut.json')
plugin.psychonaut_alias_file = os.path.join(_here, 'data', 'psychonaut_alias_index.json')


class _FakeError(Exception):
    pass


class _FakeMsg:
    def __init__(self, nick='tester'):
        self.nick = nick


class _FakeIrc:
    def __init__(self):
        self.replies = []
        self.errors = []

    def reply(self, msg, **kwargs):
        self.replies.append(msg)

    def error(self, msg, Raise=False):
        self.errors.append(msg)
        if Raise:
            raise _FakeError(msg)


if HAVE_SUPYBOT:
    class TripsitTestCase(PluginTestCase):
        plugins = ('Tripsit',)


class TripsitHelpersTestCase(unittest.TestCase):
    """pure-helper tests, no IRC needed: date parsing + pw/combo lookups"""

    def setUp(self):
        self.p = plugin.Tripsit(None)

    def _pair(self, a, b):
        resolved, unknown = self.p._combo_resolve([a, b])
        self.assertEqual(unknown, [])
        return self.p._combo_line(resolved[0], resolved[1])

    # -- _parse_when (hdose backdating) --

    def test_parse_when_hhmm(self):
        dt = self.p._parse_when('1130', TZ, NOW)
        self.assertEqual((dt.year, dt.month, dt.day), (2025, 11, 30))
        self.assertEqual((dt.hour, dt.minute, dt.second), (11, 30, 0))

    def test_parse_when_hhmmss(self):
        dt = self.p._parse_when('113045', TZ, NOW)
        self.assertEqual((dt.hour, dt.minute, dt.second), (11, 30, 45))

    def test_parse_when_at_prefix(self):
        dt = self.p._parse_when('@1130', TZ, NOW)
        self.assertEqual((dt.year, dt.month, dt.day), (2025, 11, 30))
        self.assertEqual((dt.hour, dt.minute, dt.second), (11, 30, 0))
        self.assertEqual(dt, self.p._parse_when('1130', TZ, NOW))
        dt = self.p._parse_when('@113045', TZ, NOW)
        self.assertEqual((dt.hour, dt.minute, dt.second), (11, 30, 45))

    def test_parse_when_date_at(self):
        dt = self.p._parse_when('2025-11-29@1130', TZ, NOW)
        self.assertEqual((dt.year, dt.month, dt.day, dt.hour, dt.minute),
                         (2025, 11, 29, 11, 30))
        dt = self.p._parse_when('2025-11-29@113015', TZ, NOW)
        self.assertEqual(dt.second, 15)

    def test_parse_when_rejects_invalid(self):
        for spec in ('2599', '1170', '1130x', '',
                     '2025-13-01@1130', '2025-11-31@1130', '2025-11-30 1130',
                     'whenever', '250000', '@9999', '@1130x'):
            self.assertIsNone(self.p._parse_when(spec, TZ, NOW), spec)

    def test_parse_when_relative(self):
        # any digit length other than 4/6 is X hours YY minutes ago
        dt = self.p._parse_when('45', TZ, NOW)
        self.assertEqual(dt, NOW - datetime.timedelta(minutes=45))
        dt = self.p._parse_when('@45', TZ, NOW)
        self.assertEqual(dt, NOW - datetime.timedelta(minutes=45))
        dt = self.p._parse_when('5', TZ, NOW)
        self.assertEqual(dt, NOW - datetime.timedelta(minutes=5))
        # '99' used to be invalid, now it is 99 minutes ago
        dt = self.p._parse_when('99', TZ, NOW)
        self.assertEqual(dt, NOW - datetime.timedelta(minutes=99))
        dt = self.p._parse_when('100', TZ, NOW)
        self.assertEqual(dt, NOW - datetime.timedelta(hours=1))
        dt = self.p._parse_when('12345', TZ, NOW)
        self.assertEqual(dt, NOW - datetime.timedelta(hours=123, minutes=45))
        # 7 digits: 10000 hours 00 minutes ago
        dt = self.p._parse_when('1000000', TZ, NOW)
        self.assertEqual(dt, NOW - datetime.timedelta(hours=10000))

    def test_parse_when_relative_is_never_future(self):
        for spec in ('0', '45', '100', '12345', '1000000'):
            dt = self.p._parse_when(spec, TZ, NOW)
            self.assertIsNotNone(dt, spec)
            self.assertLessEqual(dt, NOW)

    def test_parse_when_future_is_detectable(self):
        # 23:30 the same day is after NOW: hdose must reject it
        dt = self.p._parse_when('2330', TZ, NOW)
        self.assertIsNotNone(dt)
        self.assertGreater(dt, NOW)
        dt = self.p._parse_when('@2330', TZ, NOW)
        self.assertIsNotNone(dt)
        self.assertGreater(dt, NOW)

    # -- pw lookup --

    def test_pw_resolve_alias(self):
        self.assertEqual(self.p._pw_resolve('ket'), ('Ketamine', None))
        self.assertEqual(self.p._pw_resolve('thc'), ('Cannabis', None))
        self.assertEqual(self.p._pw_resolve('K-E_T'), ('Ketamine', None))
        self.assertEqual(self.p._pw_resolve('LSD'), ('LSD', None))
        # alias target 'Psilocybin mushrooms' has no verbatim page, only the
        # underscore variant: must resolve through normalized title keys
        self.assertEqual(self.p._pw_resolve('shrooms'), ('Psilocybin_mushrooms', None))

    def test_pw_resolve_unknown(self):
        self.assertEqual(self.p._pw_resolve('unobtainium'), (None, None))
        self.assertEqual(self.p._pw_resolve(''), (None, None))

    def test_pw_resolve_category_fallback(self):
        # Opioids/Stimulants/Benzodiazepines have their own (dataless) pages,
        # so they resolve as pages; SSRIs has no page and stays a category
        self.assertEqual(self.p._pw_resolve('opioids'), ('Opioids', None))
        self.assertEqual(self.p._pw_resolve('benzo'), ('Benzodiazepines', None))
        self.assertEqual(self.p._pw_resolve('stimolanti'), ('Stimulants', None))
        self.assertEqual(self.p._pw_resolve('ssri'), (None, 'SSRIs'))
        self.assertEqual(self.p._pw_resolve('alcol'), ('Alcohol', None))

    def test_pw_line_ketamine_prefers_oral(self):
        line = self.p._pw_line('Ketamine')
        self.assertIn('Ketamine: oral common 100-300mg', line)
        self.assertIn('(light 50-100mg, strong 300-450mg)', line)
        self.assertIn('total 1-2 hours', line)
        self.assertIn('⚠5 dangerous, 6 uncertain', line)
        self.assertLessEqual(len(line), 400)

    def test_pw_line_lsd_sublingual(self):
        line = self.p._pw_line('LSD')
        self.assertIn('sublingual common 75-150µg', line)
        self.assertIn('total 8-12 hours', line)
        self.assertIn('unsafe', line)

    def test_pw_line_truncated(self):
        self.p.pw_pages['FakeSub'] = {'durations': {},
                                      'roas': {'Oral': {'common': '1-2 mg ' * 60}}}
        line = self.p._pw_line('FakeSub')
        self.assertLessEqual(len(line), 400)
        self.assertTrue(line.endswith('…'))

    # -- pw verbose (-v) --

    def test_pw_roa_order(self):
        self.assertEqual(self.p._pw_roa_order({}), [])
        roas = {'Smoked': {}, 'Oral': {}, 'Insufflated': {}}
        self.assertEqual(self.p._pw_roa_order(roas),
                         ['Oral', 'Insufflated', 'Smoked'])
        # no preferred ROA available: everything alphabetical
        self.assertEqual(self.p._pw_roa_order({'Zzz': {}, 'Aaa': {}}),
                         ['Aaa', 'Zzz'])

    def test_pw_verbose_lines_ketamine(self):
        lines = self.p._pw_verbose_lines('Ketamine')
        # alias 'Ketamine' equals the title and must not show up as aka
        self.assertEqual(lines[0], 'Ketamine')
        self.assertEqual(lines[1],
                         'Roa oral: threshold 50mg, light 50-100mg, '
                         'common 100-300mg, strong 300-450mg, heavy 450 mg+')
        self.assertEqual(lines[2],
                         'Roa insufflated: threshold 5mg, light 10-30mg, '
                         'common 30-75mg, strong 75-150mg, heavy 150 mg+')
        self.assertEqual(lines[3],
                         'Duration: total 1-2 hours, onset 1-3 minutes, '
                         'come up 5-15 minutes, peak 15-45 minutes, '
                         'offset 30-60 minutes, after effects 2-12 hours')
        self.assertEqual(lines[4],
                         '⚠ Dangerous: Alcohol, GHB, GBL, Opioids, Tramadol')
        self.assertEqual(lines[5],
                         '⚠ Uncertain: Amphetamines, Cocaine, '
                         'Benzodiazepines, MAOIs, Trazodone, Grapefruit')
        self.assertEqual(len(lines), 6)

    def test_pw_verbose_lines_partial_fields(self):
        self.p.pw_pages['SubV'] = {
            'aliases': ['SubV', 'Subee'],
            'roas': {'Oral': {'threshold': '2 mg', 'common': '5 - 10 mg'}},
            'durations': {'onset': '30 minutes', 'total': '4 hours'},
            'interactions': {'caution': ['SSRIs'],
                             'dangerous': ['Alcohol', 'GHB']},
        }
        lines = self.p._pw_verbose_lines('SubV')
        self.assertEqual(lines, [
            'SubV (aka: Subee)',
            'Roa oral: threshold 2mg, common 5-10mg',
            'Duration: total 4 hours, onset 30 minutes',
            '⚠ Dangerous: Alcohol, GHB',
            '⚠ Caution: SSRIs',
        ])

    def test_pw_verbose_lines_empty_page(self):
        self.p.pw_pages['Bare'] = {}
        self.assertEqual(self.p._pw_verbose_lines('Bare'), ['Bare'])

    # -- combo lookup --

    def test_combo_dangerous(self):
        self.assertEqual(self._pair('ketamine', 'alcohol'),
                         'Ketamine × Alcohol: Dangerous')

    def test_combo_uncertain_via_page_entry(self):
        # 'thc' resolves to the Cannabis page, which lists LSD as uncertain
        self.assertEqual(self._pair('thc', 'lsd'), 'Cannabis × LSD: Uncertain')

    def test_combo_unsafe(self):
        # LSD's unsafe list contains Tramadol; Tramadol's lists don't mention LSD
        self.assertEqual(self._pair('lsd', 'tramadol'), 'LSD × Tramadol: Unsafe')

    def test_combo_no_data(self):
        # MDMA's lists mention Cannabis but not LSD itself
        self.assertEqual(self._pair('lsd', 'mdma'),
                         'LSD × MDMA: no direct interaction data')

    def test_combo_category_input(self):
        self.assertEqual(self._pair('ketamine', 'opioids'),
                         'Ketamine × Opioids: Dangerous')
        # Tramadol's own interaction lists don't include the Opioids category
        self.assertEqual(self._pair('tramadol', 'opioids'),
                         'Tramadol × Opioids: no direct interaction data')

    def test_combo_severity_precedence(self):
        self.p.pw_pages['SubA'] = {'roas': {}, 'durations': {}, 'aliases': [],
                                   'interactions': {'unsafe': ['SubB']}}
        self.p.pw_pages['SubB'] = {'roas': {}, 'durations': {}, 'aliases': [],
                                   'interactions': {'dangerous': ['SubA']}}
        self.p.pw_title_keys['suba'] = 'SubA'
        self.p.pw_title_keys['subb'] = 'SubB'
        self.assertEqual(self._pair('SubA', 'SubB'), 'SubA × SubB: Dangerous')

    def test_combo_unknown_is_reported(self):
        resolved, unknown = self.p._combo_resolve(['lsd', 'unobtainium'])
        self.assertEqual(unknown, ['unobtainium'])
        self.assertEqual(resolved[0]['title'], 'LSD')


@unittest.skipIf(HAVE_SUPYBOT, 'command methods are wrapped by the supybot harness')
class TripsitCommandsTestCase(unittest.TestCase):
    """direct command calls with a simulated irc, only when running
    standalone (unwrapped methods)"""

    def setUp(self):
        self.p = plugin.Tripsit(None)

    def _logged(self, note=None):
        t = TZ.localize(datetime.datetime(2025, 11, 30, 10, 0))
        dose = {'time': t, 'dose': '20mg', 'drug': 'mph', 'method': 'oral'}
        if note:
            dose['note'] = note
        self.p.db = {'tester': {'timezone': TZ, 'doses': [dose]}}

    def test_annotate(self):
        self._logged()
        irc = _FakeIrc()
        self.p.annotate(irc, _FakeMsg('tester'), [], 'felt nothing')
        self.assertEqual(self.p.db['tester']['doses'][-1]['note'], 'felt nothing')
        self.assertTrue(irc.replies[0].startswith('annotated mph 20mg from'))
        self.assertIn('felt nothing', irc.replies[0])

    def test_annotate_no_doses(self):
        self.p.db = {'tester': {'timezone': TZ}}
        irc = _FakeIrc()
        self.p.annotate(irc, _FakeMsg('tester'), [], 'hello')
        self.assertEqual(irc.errors, ['no doses logged'])
        self.assertEqual(irc.replies, [])

    def test_lastdose_shows_note(self):
        self._logged(note='empty stomach')
        irc = _FakeIrc()
        self.p.lastdose(irc, _FakeMsg('tester'), [], None)
        self.assertIn('note: empty stomach', irc.replies[0])

    def test_tally_shows_note(self):
        self._logged(note='empty stomach')
        irc = _FakeIrc()
        self.p.tally(irc, _FakeMsg('tester'), [], 'mph')
        self.assertIn('note: empty stomach', irc.replies[0])

    def test_hdose_backdate_stores_entry(self):
        self.p.db = {'tester': {'timezone': TZ}}
        irc = _FakeIrc()
        self.p.hdose(irc, _FakeMsg('tester'), [], '0000', '20mg', 'mph', 'oral')
        dose = self.p.db['tester']['doses'][-1]
        self.assertEqual((dose['time'].hour, dose['time'].minute, dose['time'].second),
                         (0, 0, 0))
        self.assertEqual(dose['method'], 'oral')
        self.assertEqual(len(irc.replies), 1)
        self.assertIn('20mg of mph', irc.replies[0])
        self.assertIn('ago', irc.replies[0])

    def test_hdose_rejects_future(self):
        self.p.db = {'tester': {'timezone': TZ}}
        irc = _FakeIrc()
        self.assertRaises(_FakeError, self.p.hdose, irc, _FakeMsg('tester'), [],
                          '2099-01-01@1200', '20mg', 'mph', None)
        self.assertEqual(self.p.db['tester'].get('doses'), None)

    def test_hdose_rejects_bad_time(self):
        self.p.db = {'tester': {'timezone': TZ}}
        irc = _FakeIrc()
        self.assertRaises(_FakeError, self.p.hdose, irc, _FakeMsg('tester'), [],
                          '2500', '20mg', 'mph', None)
        self.assertRaises(_FakeError, self.p.hdose, irc, _FakeMsg('tester'), [],
                          'whenever', '20mg', 'mph', None)

    def test_hdose_relative_minutes(self):
        self.p.db = {'tester': {'timezone': TZ}}
        irc = _FakeIrc()
        before = datetime.datetime.now(TZ)
        self.p.hdose(irc, _FakeMsg('tester'), [], '45', '5mg', 'x', None)
        dose = self.p.db['tester']['doses'][-1]
        delta = (before - dose['time']).total_seconds()
        self.assertAlmostEqual(delta, 45 * 60, delta=5)
        self.assertIn('ago', irc.replies[0])

    def test_pw_command(self):
        irc = _FakeIrc()
        self.p.pw(irc, _FakeMsg('tester'), [], ['ket'])
        self.assertEqual(len(irc.replies), 1)
        self.assertIn('Ketamine: oral common 100-300mg', irc.replies[0])
        irc = _FakeIrc()
        self.p.pw(irc, _FakeMsg('tester'), [], ['unobtainium'])
        self.assertEqual(irc.errors, ['unknown substance (no psychonaut page)'])

    def test_pw_command_verbose(self):
        irc = _FakeIrc()
        self.p.pw(irc, _FakeMsg('tester'), [], ['-v', 'ket'])
        self.assertEqual(len(irc.replies), 6)
        self.assertEqual(irc.replies[0], 'Ketamine')
        self.assertTrue(irc.replies[1].startswith('Roa oral:'))
        self.assertTrue(irc.replies[2].startswith('Roa insufflated:'))
        self.assertTrue(irc.replies[3].startswith('Duration:'))
        self.assertTrue(irc.replies[4].startswith('⚠ Dangerous:'))
        self.assertTrue(irc.replies[5].startswith('⚠ Uncertain:'))

    def test_pw_command_verbose_multiword_name(self):
        irc = _FakeIrc()
        self.p.pw(irc, _FakeMsg('tester'), [], ['-v', 'psilocybin', 'mushrooms'])
        self.assertTrue(irc.replies[0].startswith('Psilocybin_mushrooms'))
        self.assertTrue(any(r.startswith('Roa ') for r in irc.replies))

    def test_pw_command_verbose_stays_compact_without_flag(self):
        irc = _FakeIrc()
        self.p.pw(irc, _FakeMsg('tester'), [], ['lsd'])
        self.assertEqual(len(irc.replies), 1)
        self.assertIn('LSD: sublingual common 75-150µg', irc.replies[0])

    def test_pw_command_verbose_without_substance(self):
        irc = _FakeIrc()
        self.p.pw(irc, _FakeMsg('tester'), [], ['-v'])
        self.assertEqual(irc.errors, ['usage: pw [-v] <substance>'])
        self.assertEqual(irc.replies, [])

    def test_pw_command_verbose_unknown(self):
        irc = _FakeIrc()
        self.p.pw(irc, _FakeMsg('tester'), [], ['-v', 'unobtainium'])
        self.assertEqual(irc.errors, ['unknown substance (no psychonaut page)'])
        self.assertEqual(irc.replies, [])

    def test_combo_command_replies_per_pair(self):
        irc = _FakeIrc()
        self.p.combo(irc, _FakeMsg('tester'), [], 'ketamine', ['alcohol', 'thc'])
        self.assertEqual(len(irc.replies), 3)
        self.assertEqual(irc.replies[0], 'Ketamine × Alcohol: Dangerous')
        self.assertIn('Ketamine × Cannabis', irc.replies[1])

    def test_combo_command_unknown(self):
        irc = _FakeIrc()
        self.p.combo(irc, _FakeMsg('tester'), [], 'lsd', ['unobtainium'])
        self.assertEqual(irc.errors,
                         ['unknown substance (no psychonaut page): unobtainium'])
        self.assertEqual(irc.replies, [])


if __name__ == '__main__':
    unittest.main()

# vim:set shiftwidth=4 tabstop=4 expandtab textwidth=79:
