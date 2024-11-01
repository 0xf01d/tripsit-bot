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
import pickle
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

    def __init__(self, irc):
        self.__parent = super(Tripsit, self)
        self.__parent.__init__(irc)
        self.db = {}
        self._loadDb()
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
                self.db[nick] = {'timezone': timezone}
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
            if 'ago' in opts and len(opts['ago']) == 4:
                ago = opts['ago']
                dose_td = datetime.timedelta(hours=int(ago[0:2]), minutes=int(ago[2:4]))
                dose_td_s = dose_td.total_seconds()
                time = time - dose_td
            doseLog = {'time': time, 'dose': dose, 'drug': name, 'method': method}
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
            if 'ago' in opts and len(opts['ago']) == 4:
                ago = opts['ago']
                dose_td = datetime.timedelta(hours=int(ago[0:2]), minutes=int(ago[2:4]))
                dose_td_s = dose_td.total_seconds()
                time = time - dose_td
            doseLog = {'time': time, 'dose': dose, 'drug': name, 'method': method}
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
#        re=":-( This is currently not available, sorry. Exception ID T0T4LLYFCK3D."
        irc.reply(re)

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
            time = datetime.datetime.now(tz = tz)
            since_dose= time - dose_time
            since_dose_seconds = since_dose.total_seconds()
            if history:
                history = num2words(history, to='ordinal')
                re = utils.str.format("Your %i last dose was %s of %s via %s at %s %s, %T ago", history, dose, drug, method, dose_time.strftime("%c"), timezone, since_dose_seconds)
            else:
                re = utils.str.format("You last dosed %s of %s via %s at %s %s, %T ago", dose, drug, method, dose_time.strftime("%c"), timezone, since_dose_seconds)
            irc.reply(re)
        else:
            irc.error(f'No doses saved for {nick}')

    @wrap(['positiveInt'])
    def listdose(self, irc, msg, args, history):
        """<n>

        retrieves your <n> last logged doses
        """
        if history > 20:
            irc.error("you can't retrieve more than 20 doses")
            return
        nick = msg.nick
        if nick in self.db:
            try:
                rangecheck = self.db[nick]['doses'][-int(history)]
                irc.reply(f"Your last {history} doses logged are:", private=True)
                for number in range(history, 0, -1):
                    lastdose = self.db[nick]['doses'][-int(number)]
                    dose = lastdose['dose']
                    drug = lastdose['drug']
                    method = lastdose['method']
                    dose_time = lastdose['time']
                    timezone = self.db[nick]['timezone']
                    tz = pytz.timezone(str(timezone))
                    time = datetime.datetime.now(tz=tz)
                    since_dose = time - dose_time
                    since_dose_seconds = since_dose.total_seconds()
                    if number == 1:
                        number = "The"
                    else:
                        number = num2words(number, to='ordinal')
                    re = utils.str.format("::> %i last dose: Amount: %s of \x02%s\x0F via %s | datetime: %s %s | timedelta %T ", number, dose, drug, method, dose_time.strftime("%c"), timezone, since_dose_seconds)
                    irc.reply(re, private=True)
            except IndexError:
                irc.error("You haven't logged that many doses")
                return
        else:
            irc.error(f'No doses saved for {nick}')

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
                    for i, c in enumerate(doselog["dose"]):
                        if not c.isdigit():
                            break 
                    num += int(doselog["dose"][:i])
                    unit = doselog["dose"][i:].lstrip()
            irc.reply(f"You have dosed a total of {num}{unit} amount of {drug}")
        else:
            irc.error(f"No doses saved for {nick}")
 
Class = Tripsit


# vim:set shiftwidth=4 softtabstop=4 expandtab textwidth=79:
