###
# Copyright (c) 2020, mogad0n
# All rights reserved.
#
# Redistribution and use in source and binary forms, with or wthout
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

from supybot import utils, plugins, ircutils, callbacks, world, conf, log
from supybot.commands import *


from num2words import num2words
import json
import pickle
import re
import datetime
import pytz

try:
    from supybot.i18n import PluginInternationalization
    _ = PluginInternationalization('Tripsit')
except ImportError:
    # Placeholder that allows to run the plugin on a bot
    # without the i18n module
    _ = lambda x: x

filename = conf.supybot.directories.data.dirize("Tripsit.db")
psychonaut_file = conf.supybot.directories.data.dirize("psychonaut.json")
psychonaut_alias_file = conf.supybot.directories.data.dirize("psychonaut_alias_index.json")

insufflated = ["Insufflation", "Insufflation-IR", "Insufflation-XR"]

METHODS = {
    "iv": ["IV"],
    "shot": ["IV"],

    "im": ["IM"],

    "oral": ["Oral", "Oral-IR", "Oral-XR"],

    "insufflated": insufflated,
    "snorted": insufflated,

    "smoked": ["Smoked"]
}


class Tripsit(callbacks.Plugin):
    """Harm-Reduction tools from tripsit's tripbot and the tripsitwiki"""
    threaded = True

    DEFAULT_TZ = 'Europe/Rome'

    PW_ROA_PREF = ['oral', 'sublingual', 'insufflated', 'smoked', 'vaporized', 'rectal']
    # ordered by decreasing severity: index 0 = worst
    PW_LEVELS = ['dangerous', 'unsafe', 'uncertain', 'caution']
    PW_LEVEL_LABEL = {'dangerous': 'Dangerous', 'unsafe': 'Unsafe',
                      'uncertain': 'Uncertain', 'caution': 'Caution'}
    # minimal fallback mapping user input -> PsychonautWiki interaction category
    PW_CATEGORY_INPUTS = {
        'cannabis': 'Cannabis', 'weed': 'Cannabis',
        'alcol': 'Alcohol', 'alcohol': 'Alcohol',
        'ssri': 'SSRIs', 'ssris': 'SSRIs',
        'maoi': 'MAOIs', 'maois': 'MAOIs',
        'opioid': 'Opioids', 'opioids': 'Opioids', 'oppiacei': 'Opioids',
        'oppioidi': 'Opioids', 'oppioide': 'Opioids',
        'benzo': 'Benzodiazepines', 'benzos': 'Benzodiazepines',
        'stimolante': 'Stimulants', 'stimolanti': 'Stimulants',
        'stimulant': 'Stimulants', 'stimulants': 'Stimulants',
        'depressante': 'Depressants', 'depressanti': 'Depressants',
        'depressant': 'Depressants', 'depressants': 'Depressants',
    }
    # plural-sounding entries in interaction lists are treated as categories
    # (Stimulants, SSRIs, ...); everything else could be a bare substance
    # without its own page (Lithium, Nitrous, ...) and must not expand
    PW_CLASS_STOPWORDS = frozenset(('nitrous',))
    PW_ROA_FIELDS = ('threshold', 'light', 'common', 'strong', 'heavy')
    PW_DURATION_ORDER = ('total', 'onset', 'come up', 'peak', 'offset',
                         'after effects')

    def __init__(self, irc):
        self.__parent = super(Tripsit, self)
        self.__parent.__init__(irc)
        self.db = {}
        self._loadDb()
        self._loadPw()
        world.flushers.append(self._flushDb)

    def _loadDb(self):
        """Loads the (flatfile) database mapping nicks to doses."""

        try:
            with open(filename, "rb") as f:
                self.db = pickle.load(f)
        except Exception as e:
            self.log.debug("Tripsit: Unable to load pickled database: %s", e)

    def _flushDb(self):
        """Flushes the (flatfile) database mapping nicks to doses."""

        try:
            with open(filename, "wb") as f:
                pickle.dump(self.db, f, 2)
        except Exception as e:
            self.log.warning("Tripsit: Unable to write pickled database: %s", e)

    def _loadPw(self):
        """Loads the PsychonautWiki data (pages + alias index) and derives
        the category -> substances map from the interaction lists."""
        self.pw_pages = {}
        self.pw_alias = {}
        self.pw_title_keys = {}
        self.pw_categories = {}
        try:
            with open(psychonaut_file) as f:
                self.pw_pages = json.load(f)
            with open(psychonaut_alias_file) as f:
                self.pw_alias = json.load(f)
        except Exception as e:
            self.log.warning("Tripsit: Unable to load psychonaut data: %s", e)
            return
        for title in self.pw_pages:
            self.pw_title_keys[self._pw_key(title)] = title
        for title, page in self.pw_pages.items():
            for entries in (page.get('interactions') or {}).values():
                if not isinstance(entries, list):
                    continue
                for entry in entries:
                    key = self._pw_key(entry)
                    if key and key not in self.pw_title_keys and self._pw_is_class_key(key):
                        cat = self.pw_categories.setdefault(key, {'display': str(entry), 'keys': set()})
                        cat['keys'].add(self._pw_key(title))

    @classmethod
    def _pw_is_class_key(cls, key):
        # NB: do not use builtin any() here -- "from supybot.commands import *"
        # shadows it with a truthy return value (same in real supybot)
        if key in cls.PW_CLASS_STOPWORDS:
            return False
        if key.endswith('s'):
            return True
        for w in ('inhibitor', 'substrate', 'releaser', 'antagonist',
                  'antidepressant', 'control'):
            if w in key:
                return True
        return False

    def die(self):
        self._flushDb()
        world.flushers.remove(self._flushDb)
        self.__parent.die()

    def set(self, irc, msg, args, timezone):
        """<timezone>

        Sets location for your current nick to <timezone>
        for eg. America/Chicago
        """
        nick = msg.nick
        try:
            timezone = pytz.timezone(timezone)
            if nick in self.db:
                self.db[nick]['timezone'] = timezone
            else:
                self.db[nick] = {'timezone': timezone }
            irc.replySuccess()
        except pytz.UnknownTimeZoneError:
            irc.error(_('Unknown timezone'), Raise=True)
    set = wrap(set, ["something"])

    @wrap([getopts({'ago': 'something'}), "something", "something", optional("something")])
    def idose(self, irc, msg, args, opts, dose, name, method):
        """[--ago <HHMM>] <amount> <drug> [<method/ROA>]

        logs a dose for your <nick>, eg. @idose --ago 0100 20mg mph oral
        would log that dose as if it was taken an hour ago
        [--ago] and [ROA] fields are optional
        """
        opts = dict(opts)
        found_method = False
        onset = None
        methods = []
        if method:
            methods = [method.lower()]
            methods = METHODS.get(methods[0], methods)
        drug_and_method = name
        if method:
            if not found_method:
                method = method
            drug_and_method = "%s via %s" % (drug_and_method, method)
        else:
            method = 'Undefined'

        nick = msg.nick
        if nick in self.db:
            timezone = self.db[nick].get('timezone', 'UTC')
            tz = pytz.timezone(str(timezone))
            time = datetime.datetime.now(tz=tz)
            dose_td = 0
            if 'ago' in opts:
                ago = opts['ago'].lstrip('0') or '0'
                if len(ago) <= 2:
                    hours, mins = 0, int(ago or '0')
                else:
                    hours = int(ago[:-2])
                    mins = int(ago[-2:])
                dose_td = datetime.timedelta(hours=hours, minutes=mins)
                dose_td_s = dose_td.total_seconds()
                time = time - dose_td
            doseLog = {'time': time, 'dose': dose, 'drug': name, 'method': method }
            doses = self.db[nick].get('doses')
            if doses:
                doses.append(doseLog)
            else:
                doses = [doseLog]
            self.db[nick]['doses'] = doses
        else:
            timezone = 'UTC'
            tz = pytz.timezone(timezone)
            time = datetime.datetime.now(tz=tz)
            dose_td = 0
            if 'ago' in opts:
                ago = opts['ago'].lstrip('0') or '0'
                if len(ago) <= 2:
                    hours, mins = 0, int(ago or '0')
                else:
                    hours = int(ago[:-2])
                    mins = int(ago[-2:])
                dose_td = datetime.timedelta(hours=hours, minutes=mins)
                dose_td_s = dose_td.total_seconds()
                time = time - dose_td
            doseLog = {'time': time, 'dose': dose, 'drug': name, 'method': method }
            doses = [doseLog]
            self.db[nick] = {'timezone': timezone, 'doses': doses}

        if dose_td == 0:
            re = utils.str.format("You dosed %s of %s at %s, %s", dose, drug_and_method, time.strftime("%c"), timezone)
            if onset is not None:
                re += utils.str.format(". You should start feeling effects %s from now", onset)
        else:
            re = utils.str.format("You dosed %s of %s at %s, %s ; %T ago", dose, drug_and_method, time.strftime("%c"), timezone, dose_td.total_seconds())
            if onset is not None:
                re += utils.str.format(". You should have/will start feeling effects %s from/after dosing", onset)
        irc.reply(re)


    def _parse_when(self, spec, tz, now=None):
        """Parses [@]HHMM | [@]HHMMSS | YYYY-MM-DD@HHMM | YYYY-MM-DD@HHMMSS
        (a leading @ on time-only specs is accepted and ignored) into an
        aware datetime in <tz> (today for time-only specs), or None if
        invalid.  All-digit specs of any other length are relative: the
        last two digits are minutes, the digits before are hours, so
        '45' is 45 minutes ago and '1000000' is 10000 hours ago."""
        spec = spec.strip()
        now = now or datetime.datetime.now(tz)
        if re.match(r'^@?\d+$', spec):
            digits = spec.lstrip('@')
            if len(digits) not in (4, 6):
                if len(digits) <= 2:
                    hours, mins = 0, int(digits)
                else:
                    hours, mins = int(digits[:-2]), int(digits[-2:])
                return now - datetime.timedelta(hours=hours, minutes=mins)
            m = re.match(r'^(\d{2})(\d{2})(\d{2})?$', digits)
            y, mo, d = now.year, now.month, now.day
            hh, mi, ss = int(m.group(1)), int(m.group(2)), int(m.group(3) or 0)
        else:
            m = re.match(r'^(\d{4})-(\d{2})-(\d{2})@(\d{2})(\d{2})(\d{2})?$', spec)
            if not m:
                return None
            y, mo, d, hh, mi = (int(g) for g in m.groups()[:5])
            ss = int(m.group(6) or 0)
        if hh > 23 or mi > 59 or ss > 59:
            return None
        try:
            return tz.localize(datetime.datetime(y, mo, d, hh, mi, ss))
        except ValueError:
            return None

    @wrap(["something", "something", "something", optional("something")])
    def hdose(self, irc, msg, args, when, dose, name, method):
        """<[@]HHMM[SS]|YYYY-MM-DD@HHMM[SS]|digits> <amount> <drug> [<method/ROA>]

        logs a dose taken at <when> (past times only), eg.
        @hdose 1130 20mg mph oral logs today at 11:30, @hdose @1130 20mg mph
        oral is the same (a leading @ on the time is allowed), @hdose 113045
        20mg mph oral also sets seconds, @hdose 2025-11-30@1130 20mg mph oral
        sets an explicit date.  A digit-only time of any other length is
        relative: the last two digits are minutes, the rest hours, so
        @hdose 45 20mg mph oral logs 45 minutes ago and @hdose 1000000
        logs 10000 hours ago. Times are in your local timezone.
        """
        nick = msg.nick
        tzname = str(self.db.get(nick, {}).get('timezone', self.DEFAULT_TZ))
        tz = pytz.timezone(tzname)
        now = datetime.datetime.now(tz)
        when_dt = self._parse_when(when, tz, now)
        if when_dt is None:
            irc.error("Bad time %s, use [@]HHMM, [@]HHMMSS, digits for XhMMm ago or YYYY-MM-DD@HHMM[SS]" % when, Raise=True)
        if when_dt > now:
            irc.error("That time is in the future, only past times are allowed", Raise=True)
        if method:
            drug_and_method = "%s via %s" % (name, method)
        else:
            method = 'Undefined'
            drug_and_method = name
        nick_db = self.db.setdefault(nick, {})
        nick_db.setdefault('timezone', tz)
        nick_db.setdefault('doses', []).append(
            {'time': when_dt, 'dose': dose, 'drug': name, 'method': method})
        since = now - when_dt
        resp = utils.str.format("You dosed %s of %s at %s, %s ; %T ago",
                                dose, drug_and_method, when_dt.strftime("%c"), tzname,
                                since.total_seconds())
        irc.reply(resp)

    @wrap([optional('positiveInt')])
    def undose(self, irc, msg, args, entry):
        """<n>
        
        removes your last dose entry, if <n> is provided then
        deletes the nth last dose
        """
        nick = msg.nick
        if nick in self.db:
            nick_dose_log = self.db[nick]['doses']
            if entry:
                try:
                    del nick_dose_log[-int(entry)]
                    entry = num2words(entry, to='ordinal')
                    irc.replySuccess(f"Deleted the {entry} last dose logged for {nick} ")
                except IndexError:
                    irc.error("The dose entry doesn't exist")
                return
            else:
                del nick_dose_log[-1]
            irc.replySuccess(f"Deleted the last dose logged for {nick} ")
        else:
            irc.error(f'No doses saved for {nick}')        

    def doseslogged(self, irc, msg, args):
        """
        This command takes no arguments.
        
        Retrieves the number of doses logged for a given nick
        """
        nick = msg.nick
        if nick in self.db:
            try:
                nick_dose_log_count = len(self.db[nick]['doses'])
                nick_dose_log_since = self.db[nick]['doses'][0]["time"]
                nick_dose_log_since_string = nick_dose_log_since.strftime("%c")
                irc.reply(f"{nick} has logged {nick_dose_log_count} doses since {nick_dose_log_since_string}")
            except IndexError:
                irc.error("Can't seem to do math, check logs")
        else:
            irc.error(f"No doses saved for {nick}")

    doseslogged = wrap(doseslogged)

    @wrap([optional('positiveInt')])
    def lastdose(self, irc, msg, args, history):
        """<n>

        retrieves your <n>th last logged dose
        """
        nick = msg.nick
        if nick in self.db:
            if history:
                try:
                    lastdose = self.db[nick]['doses'][-int(history)]
                except IndexError:
                    irc.error("You haven't logged that many doses")
                    return
            else:
                lastdose = self.db[nick]['doses'][-1]
            dose = lastdose['dose']
            drug = lastdose['drug']
            method = lastdose['method']
            dose_time = lastdose['time']
            timezone = self.db[nick]['timezone']
            tz = pytz.timezone(str(timezone))
            time = datetime.datetime.now(tz=tz)
            since_dose = time - dose_time
            since_dose_seconds = since_dose.total_seconds()
            if history:
                history = num2words(history, to='ordinal')
                re = utils.str.format("Your %i last dose was %s of %s via %s at %s %s, %T ago", history, dose, drug, method, dose_time.strftime("%c"), timezone, since_dose_seconds)
            else:
                re = utils.str.format("You last dosed %s of %s via %s at %s %s, %T ago", dose, drug, method, dose_time.strftime("%c"), timezone, since_dose_seconds)
            note = lastdose.get('note')
            if note:
                re += " | note: %s" % note
            irc.reply(re)
        else:
            irc.error(f'No doses saved for {nick}')

    @wrap(['text'])
    def annotate(self, irc, msg, args, note):
        """<note>

        annotates your last logged dose with <note>, eg.
        @annotate felt slightly nauseous
        """
        nick = msg.nick
        doses = self.db.get(nick, {}).get('doses')
        if not doses:
            irc.error('no doses logged')
            return
        last = doses[-1]
        last['note'] = note
        irc.reply("annotated %s %s from %s: %s" %
                  (last['drug'], last['dose'], last['time'].strftime("%c"), note))

    @wrap([getopts({'drug': 'something'}), 'positiveInt'])
    def listdose(self, irc, msg, args, opts, history):
        """[--drug <drug>] <n>

        Retrieves your <n> last logged doses, optionally filtered by drug.
        """
        if history > 20:
            irc.error("You can't retrieve more than 20 doses.")
            return

        opts = dict(opts)
        drug_filter = opts.get('drug')
        nick = msg.nick

        if nick in self.db:
            doses = self.db[nick]['doses']
            if drug_filter:
                doses = [dose for dose in doses if dose['drug'].lower() == drug_filter.lower()]
            if len(doses) == 0:
                irc.error(f"No doses found for drug '{drug_filter}'.")
                return
            if drug_filter:
                irc.reply(f"Here are your last {history} dose(s) for drug '{drug_filter}':", private=True)
            else:
                irc.reply(f"Here are your last {history} dose(s):", private=True)
            try:
                for number in range(0, history):
                    lastdose = doses[-(number + 1)]
                    dose = lastdose['dose']
                    drug = lastdose['drug']
                    method = lastdose['method']
                    dose_time = lastdose['time']
                    timezone = self.db[nick]['timezone']
                    tz = pytz.timezone(str(timezone))
                    time = datetime.datetime.now(tz=tz)
                    since_dose = time - dose_time
                    since_dose_seconds = since_dose.total_seconds()
                    hours, remainder = divmod(since_dose_seconds, 3600)
                    minutes, seconds = divmod(remainder, 60)
                    timedelta_str = f"{int(hours)} hours, {int(minutes)} minutes" if hours > 0 else f"{int(minutes)} minutes, {int(seconds)} seconds"
                    number_str = "The" if number == 0 else num2words(number + 1, to='ordinal')
                    re = utils.str.format(
                        "✧ %s last dose: \x02Amount:\x0F %s of \x02%s\x0F \x02via\x0F %s | \x02Datetime:\x0F %s %s | \x02Time Since Last Dose:\x0F %s",
                        number_str, dose, drug, method, dose_time.strftime("%Y-%m-%d %H:%M:%S"), timezone, timedelta_str
                    )
                    irc.reply(re, private=True)
            except IndexError:
                irc.error("You haven't logged that many doses.")
                return
        else:
            irc.error(f"No doses saved for {nick}.")

    @wrap(["something"])
    def grepdose(self, irc, msg, args, drug):
        """<drug>

        pulls most recent dose for drug
        """
        nick = msg.nick
        if nick in self.db:
            doselogs = self.db[nick]['doses']
            found = False
            for doselog in reversed(doselogs):
                if doselog['drug'] == drug:
                    timezone = self.db[nick]['timezone']
                    tz = pytz.timezone(str(timezone))
                    now = datetime.datetime.now(tz=tz)
                    since_dose = now - doselog['time']
                    re = utils.str.format("You last dosed %s of %s via %s at %s %s, %T ago", doselog["dose"], doselog["drug"], doselog["method"], doselog["time"].strftime("%c"), timezone, since_dose.total_seconds())
                    irc.reply(re)
                    found = True
                    break
            if not found:
                irc.error(f"No doses saved for {drug}")
        else:
            irc.error(f"No doses saved for {nick}")

    @wrap(["something"])
    def amountdosed(self, irc, msg, args, drug):
        """<drug>

        shows Aggregate amount in "mg" for <drug> ever logged
        """
        num = 0
        unit = ""
        nick = msg.nick
        if nick in self.db:
            doselogs = self.db[nick]['doses']
            for doselog in doselogs:
                if doselog["drug"] == drug:
                    for i,c in enumerate(doselog["dose"]):
                        if not c.isdigit():
                            break 
                    num += int(doselog["dose"][:i])
                    unit = doselog["dose"][i:].lstrip()
            irc.reply(f"You have dosed a total of {num}{unit} amount of {drug}")
        else:
            irc.error(f"No doses saved for {nick}")


    UNIT_TO_BASE = {'mg': ('mg', 1.0), 'mcg': ('mg', 0.001), 'ug': ('mg', 0.001),
                    'g': ('mg', 1000.0), 'ml': ('ml', 1.0), 'cl': ('ml', 10.0),
                    'l': ('ml', 1000.0)}

    def _norm_amount(self, dose):
        """returns (base_unit, amount) or None"""
        m = re.match(r'^([\d.]+)\s*([a-zA-Z]*)$', str(dose))
        if not m:
            return None
        amount = float(m.group(1))
        unit = m.group(2).lower() or 'units'
        if unit in self.UNIT_TO_BASE:
            base, factor = self.UNIT_TO_BASE[unit]
            return base, amount * factor
        return unit, amount

    @wrap([getopts({'since': 'something'}), "something"])
    def doseamt(self, irc, msg, args, opts, drug):
        """[--since <YYYY-MM-DD>] <drug>

        shows cumulative dosed amount for <drug>, grouped by unit
        """
        nick = msg.nick
        if nick not in self.db:
            irc.error(f"No doses saved for {nick}")
            return
        cutoff = None
        opts = dict(opts)
        if 'since' in opts:
            try:
                cutoff = datetime.datetime.fromisoformat(opts['since'])
            except ValueError:
                irc.error("Bad --since date, use YYYY-MM-DD")
                return
        tz = pytz.timezone(str(self.db[nick]['timezone']))
        if cutoff is not None:
            cutoff = tz.localize(cutoff)
        totals = {}
        last = None
        for doselog in self.db[nick]['doses']:
            if doselog["drug"] != drug:
                continue
            if cutoff is not None and doselog["time"] < cutoff:
                continue
            norm = self._norm_amount(doselog["dose"])
            if not norm:
                label = str(doselog["dose"]).strip().lower()
                totals[label] = totals.get(label, 0) + 1
                continue
            unit, amount = norm
            totals[unit] = totals.get(unit, 0) + amount
            last = doselog
        if not totals:
            irc.error(f"No doses of {drug} logged")
            return
        parts = [f"{v:g}{u}" if u in ('mg', 'ml', 'x') else f"{v:g} {u}" for u, v in sorted(totals.items())]
        resp = utils.str.format("%s: %s", drug, ", ".join(parts))
        if cutoff is not None:
            resp += f" since {opts['since']}"
        if last is not None:
            since = datetime.datetime.now(tz) - last['time']
            resp += utils.str.format(" (last: %s, %T ago)", last['dose'], since.total_seconds())
        irc.reply(resp)

    @wrap(["something"])
    def tally(self, irc, msg, args, drug):
        """<drug>

        shows dose count, total amount, average amount and last dose for <drug>
        """
        nick = msg.nick
        if nick not in self.db:
            irc.error(f"No doses saved for {nick}")
            return
        logs = [d for d in self.db[nick]['doses'] if d["drug"] == drug]
        if not logs:
            irc.error(f"No doses of {drug} logged")
            return
        tz = pytz.timezone(str(self.db[nick]['timezone']))
        counts = {}
        sums = {}
        types = {}
        for d in logs:
            types[str(d["dose"]).strip().lower()] = types.get(str(d["dose"]).strip().lower(), 0) + 1
            norm = self._norm_amount(d["dose"])
            if norm:
                unit, amount = norm
                counts[unit] = counts.get(unit, 0) + 1
                sums[unit] = sums.get(unit, 0) + amount
        last = logs[-1]
        since = datetime.datetime.now(tz) - last['time']
        parts = []
        for u in sorted(sums):
            n, tot = counts[u], sums[u]
            avg = tot / n
            parts.append(f"{n}x, {tot:g}{u} total, {avg:g}{u} avg")
        for t in sorted(types):
            if self._norm_amount(t) is None:
                parts.append(f"{types[t]}x {t}")
        resp = utils.str.format("%s: %s | last: %s via %s, %T ago",
                              drug, " | ".join(parts), last['dose'], last['method'],
                              since.total_seconds())
        note = last.get('note')
        if note:
            resp += " | note: %s" % note
        irc.reply(resp)

    @staticmethod
    def _pw_key(name):
        return re.sub(r'[\s\-_]+', '', str(name).lower())

    def _pw_resolve(self, name):
        """returns (page_title, category): one of the two is set, or (None, None)
        if nothing matches the alias index, the page titles, the manual category
        fallback or the interaction-derived categories."""
        key = self._pw_key(name)
        if not key:
            return None, None
        title = self.pw_alias.get(key)
        if title:
            # some alias targets don't match page keys verbatim (space vs
            # underscore variants), normalize through the title keys too
            resolved = self.pw_title_keys.get(self._pw_key(title))
            if resolved:
                return resolved, None
        title = self.pw_title_keys.get(key)
        if title:
            return title, None
        cat = self.PW_CATEGORY_INPUTS.get(key)
        if cat:
            title = self.pw_title_keys.get(self._pw_key(cat))
            if title:
                return title, None
            return None, cat
        if key in self.pw_categories:
            return None, self.pw_categories[key]['display']
        return None, None

    @staticmethod
    def _pw_range(value):
        """normalizes a dose/duration string: strips [n] citation refs,
        collapses '80 - 120 mg' to '80-120 mg'"""
        if not value:
            return None
        s = re.sub(r'\[\d+\]', '', str(value)).strip()
        s = s.replace(' - ', '-').replace(' +', '+')
        return s or None

    @staticmethod
    def _pw_dose(value):
        """like _pw_range but also attaches the unit to the number: '80-120 mg' -> '80-120mg'"""
        s = Tripsit._pw_range(value)
        if not s:
            return None
        return re.sub(r'(\d)\s+([a-zA-Zµ]+)$', r'\1\2', s)

    def _pw_pick_roa(self, roas):
        """returns (roa_name, roa_data) following PW_ROA_PREF order,
        or the first available one"""
        if not roas:
            return None, None
        for pref in self.PW_ROA_PREF:
            for rk in roas:
                if rk.lower() == pref:
                    return rk, roas[rk]
        rk = sorted(roas)[0]
        return rk, roas[rk]

    @staticmethod
    def _irc_truncate(resp, limit=390):
        if len(resp) <= limit:
            return resp
        return resp[:limit - 1].rsplit(' ', 1)[0] + ' …'

    def _pw_line(self, title):
        page = self.pw_pages[title]
        parts = [title]
        roas = page.get('roas') or {}
        roa_name, roa = self._pw_pick_roa(roas)
        if roa:
            seg = "%s common %s" % (roa_name.lower(), self._pw_dose(roa.get('common')) or '?')
            extras = []
            for label in ('light', 'strong'):
                rng = self._pw_dose(roa.get(label))
                if rng:
                    extras.append("%s %s" % (label, rng))
            if extras:
                seg += " (%s)" % ", ".join(extras)
            parts.append(seg)
        total = self._pw_range((page.get('durations') or {}).get('total'))
        if total:
            parts.append("total %s" % total)
        inter = page.get('interactions') or {}
        counts = ["%d %s" % (len(inter.get(level) or []), level)
                  for level in self.PW_LEVELS if inter.get(level)]
        if counts:
            parts.append("⚠" + ", ".join(counts))
        if len(parts) == 1:
            return parts[0]
        return self._irc_truncate("%s: %s" % (parts[0], ", ".join(parts[1:])))

    def _pw_roa_order(self, roas):
        """ROA names with _pw_pick_roa's preferred one first, rest sorted"""
        if not roas:
            return []
        first, _roa = self._pw_pick_roa(roas)
        return [first] + sorted(k for k in roas if k != first)

    def _pw_verbose_lines(self, title):
        """full detail as one IRC line per item: title+aliases, one line per
        ROA, durations, one line per non-empty interaction level"""
        page = self.pw_pages[title]
        head = title
        aliases = [str(a) for a in (page.get('aliases') or [])
                   if self._pw_key(a) != self._pw_key(title)]
        if aliases:
            head = "%s (aka: %s)" % (title, ", ".join(aliases))
        lines = [head]
        roas = page.get('roas') or {}
        for rk in self._pw_roa_order(roas):
            roa = roas[rk]
            segs = ["%s %s" % (field, self._pw_dose(roa.get(field)))
                    for field in self.PW_ROA_FIELDS
                    if self._pw_dose(roa.get(field))]
            if segs:
                lines.append("Roa %s: %s" % (rk.lower(), ", ".join(segs)))
        durations = page.get('durations') or {}
        segs = ["%s %s" % (field, self._pw_range(durations.get(field)))
                for field in self.PW_DURATION_ORDER
                if self._pw_range(durations.get(field))]
        if segs:
            lines.append("Duration: %s" % ", ".join(segs))
        inter = page.get('interactions') or {}
        for level in self.PW_LEVELS:
            entries = inter.get(level)
            if entries:
                lines.append("⚠ %s: %s" % (self.PW_LEVEL_LABEL[level],
                                           ", ".join(str(e) for e in entries)))
        return lines

    @wrap([many('something')])
    def pw(self, irc, msg, args, words):
        """[-v] <substance>

        looks up <substance> on PsychonautWiki: dosage of the most relevant
        ROA, total duration and interaction counts. With -v prints one line
        per ROA with all dose ranges, full durations and the interaction
        lists per level, eg. @pw -v ketamine
        """
        verbose = False
        if words and words[0] == '-v':
            verbose = True
            words = words[1:]
        if not words:
            irc.error('usage: pw [-v] <substance>')
            return
        title, _cat = self._pw_resolve(' '.join(words))
        if not title:
            irc.error('unknown substance (no psychonaut page)')
            return
        if verbose:
            for line in self._pw_verbose_lines(title):
                irc.reply(line)
        else:
            irc.reply(self._pw_line(title))

    def _combo_resolve(self, names):
        """resolves substance names to lookup dicts; returns (resolved, unknown)"""
        resolved, unknown = [], []
        for name in names:
            title, cat = self._pw_resolve(name)
            if title:
                keys = {self._pw_key(title)}
                keys.update(self._pw_key(a) for a in (self.pw_pages[title].get('aliases') or []))
                resolved.append({'label': title, 'title': title, 'cat': None, 'keys': keys})
            elif cat:
                resolved.append({'label': cat, 'title': None, 'cat': cat,
                                 'keys': {self._pw_key(cat)}})
            else:
                unknown.append(name)
        return resolved, unknown

    def _combo_entry_matches(self, entry, target):
        en = self._pw_key(entry)
        if not en:
            return False
        tn = self._pw_key(target['title'] or target['cat'])
        if tn and (en in tn or tn in en):
            return True
        cat = self.pw_categories.get(en)
        return bool(cat and (target['keys'] & cat['keys']))

    def _combo_match(self, page_title, target):
        """strongest interaction level in <page_title>'s list matching target, or None"""
        best = None
        inter = self.pw_pages[page_title].get('interactions') or {}
        for level, entries in inter.items():
            if not isinstance(entries, list):
                continue
            for entry in entries:
                if self._combo_entry_matches(entry, target):
                    if best is None or self.PW_LEVELS.index(level) < self.PW_LEVELS.index(best):
                        best = level
                    break
        return best

    def _combo_line(self, a, b):
        level = None
        if a['title']:
            level = self._combo_match(a['title'], b)
        if b['title']:
            other = self._combo_match(b['title'], a)
            if other and (level is None or
                          self.PW_LEVELS.index(other) < self.PW_LEVELS.index(level)):
                level = other
        label = self.PW_LEVEL_LABEL.get(level) if level else None
        return "%s × %s: %s" % (a['label'], b['label'],
                                label or "no direct interaction data")

    @wrap(['something', many('something')])
    def combo(self, irc, msg, args, first, rest):
        """<sub1> <sub2> [<sub3> ...]

        checks pairwise substance interactions using PsychonautWiki data,
        eg. @combo lsd tramadol
        """
        resolved, unknown = self._combo_resolve([first] + list(rest))
        if unknown:
            irc.error("unknown substance (no psychonaut page): %s" % ", ".join(unknown))
            return
        for i, a in enumerate(resolved):
            for b in resolved[i + 1:]:
                irc.reply(self._combo_line(a, b))

Class = Tripsit


# vim:set shiftwidth=4 softtabstop=4 expandtab textwidth=79:
