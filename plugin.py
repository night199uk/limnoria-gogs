###
# Copyright (c) 2015, Moritz Lipp
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

import json

from supybot.commands import wrap
import supybot.ircdb as ircdb
import supybot.ircmsgs as ircmsgs
import supybot.callbacks as callbacks
import supybot.log as log
import supybot.httpserver as httpserver
try:
    from supybot.i18n import PluginInternationalization
    from supybot.i18n import internationalizeDocstring
    _ = PluginInternationalization('Gogs')
except ImportError:
    # Placeholder that allows to run the plugin on a bot
    # without the i18n module
    def _(x):
        return x

    def internationalizeDocstring(x):
        return x


class GogsHandler(object):

    """Handle Gogs messages"""

    def __init__(self, plugin, irc):
        self.irc = irc
        self.plugin = plugin
        self.log = log.getPluginLogger('Gogs')

    def handle_payload(self, headers, payload):
        print(str(headers))
        print(str(payload))
        if 'X-Gogs-Event' not in headers:
            self.log.info('Invalid header: Missing X-Gogs-Event entry')
            return

        event_type = headers['X-Gogs-Event']
        if event_type not in ['push', 'create']:
            self.log.info('Unsupported X-Gogs-Event type')
            return

        # Check if any channel has subscribed to this project
        for channel in self.irc.state.channels.keys():
            projects = self.plugin._load_projects(channel)
            for slug, url in projects.items():
                # Parse project url
                if event_type == 'push' or event_type == 'create':
                    if url != payload['repository']['url']:
                        continue
                else:
                    continue

                # Handle types
                if event_type == 'push':
                    self._push_hook(channel, payload)
                elif event_type == 'create':
                    self._tag_push_hook(channel, payload)

    def _push_hook(self, channel, payload):
        # Send general message
        payload['total_commits_count'] = len(payload['commits'])
        msg = self._build_message(channel, 'push', payload)
        self._send_message(channel, msg)

        # Send commits
        for commit in payload['commits']:
            commit['repository'] = {
                'id': payload['repository']['id'],
                'name': payload['repository']['name'],
                'url': payload['repository']['url']
            }
            commit['short_message'] = commit['message'].splitlines()[0]
            commit['short_id'] = commit['id'][0:10]

            msg = self._build_message(channel, 'commit', commit)
            self._send_message(channel, msg)

    def _build_message(self, channel, format_string_identifier, args):
        format_string = str(
            self.plugin.registryValue(
                'format.' +
                format_string_identifier,
                channel))
        msg = format_string.format(**args)
        return msg

    def _send_message(self, channel, msg):
        priv_msg = ircmsgs.privmsg(channel, msg)
        self.irc.queueMsg(priv_msg)


class GogsWebHookService(httpserver.SupyHTTPServerCallback):
    """https://gitlab.com/gitlab-org/gitlab-ce/blob/master/doc/web_hooks/web_hooks.md"""

    name = "GogsWebHookService"
    defaultResponse = """This plugin handles only POST request, please don't use other requests."""

    def __init__(self, plugin, irc):
        self.log = log.getPluginLogger('Gogs')
        self.gogs = GogsHandler(plugin, irc)
        self.plugin = plugin
        self.irc = irc

    def _send_error(self, handler, message):
        handler.send_response(403)
        handler.send_header('Content-type', 'text/plain')
        handler.end_headers()
        handler.wfile.write(message.encode('utf-8'))

    def _send_ok(self, handler):
        handler.send_response(200)
        handler.send_header('Content-type', 'text/plain')
        handler.end_headers()
        handler.wfile.write(bytes('OK', 'utf-8'))

    def doPost(self, handler, path, form):
        headers = dict(self.headers)

        network = None
        channel = None

        try:
            information = path.split('/')[1:]
            network = information[0]
            channel = '#' + information[1]
        except IndexError:
            self._send_error(handler, _("""Error: You need to provide the
                                        network name and the channel in
                                        url."""))
            return

        if self.irc.network != network or channel not in self.irc.state.channels:
            return

        # Handle payload
        payload = None
        try:
            payload = json.JSONDecoder().decode(form.decode('utf-8'))
        except Exception as e:
            self.log.info(e)
            self._send_error(handler, _('Error: Invalid JSON data sent.'))
            return

        try:
            self.gogs.handle_payload(headers, payload)
        except Exception as e:
            self.log.info(e)
            self._send_error(handler, _('Error: Invalid data sent.'))
            return

        # Return OK
        self._send_ok(handler)


class Gogs(callbacks.Plugin):
    """Plugin for communication and notifications of a Gogs project
    management tool instance"""
    threaded = True

    def __init__(self, irc):
        global instance
        super(Gogs, self).__init__(irc)
        instance = self

        callback = GogsWebHookService(self, irc)
        httpserver.hook('gogs', callback)

    def die(self):
        httpserver.unhook('gogs')

        super(Gogs, self).die()

    def _load_projects(self, channel):
        projects = self.registryValue('projects', channel)
        if projects is None:
            return {}
        else:
            return projects

    def _save_projects(self, projects, channel):
        self.setRegistryValue('projects', value=projects, channel=channel)

    def _check_capability(self, irc, msg):
        if ircdb.checkCapability(msg.prefix, 'admin'):
            return True
        else:
            irc.errorNoCapability('admin')
            return False

    class gogs(callbacks.Commands):
        """Gogs commands"""

        class project(callbacks.Commands):
            """Project commands"""

            @internationalizeDocstring
            def add(self, irc, msg, args, channel, project_slug, project_url):
                """[<channel>] <project-slug> <project-url>

                Announces the changes of the project with the slug
                <project-slug> and the url <project-url> to <channel>.
                """
                if not instance._check_capability(irc, msg):
                    return

                projects = instance._load_projects(channel)
                if project_slug in projects:
                    irc.error(
                        _('This project is already announced to this channel.'))
                    return

                # Save new project mapping
                projects[project_slug] = project_url
                instance._save_projects(projects, channel)

                irc.replySuccess()

            add = wrap(add, ['channel', 'somethingWithoutSpaces', 'httpUrl'])

            @internationalizeDocstring
            def remove(self, irc, msg, args, channel, project_slug):
                """[<channel>] <project-slug>

                Stops announcing the changes of the project slug <project-slug>
                to <channel>.
                """
                if not instance._check_capability(irc, msg):
                    return

                projects = instance._load_projects(channel)
                if project_slug not in projects:
                    irc.error(
                        _('This project is not registered to this channel.'))
                    return

                # Remove project mapping
                del projects[project_slug]
                instance._save_projects(projects, channel)

                irc.replySuccess()

            remove = wrap(remove, ['channel', 'somethingWithoutSpaces'])

            @internationalizeDocstring
            def list(self, irc, msg, args, channel):
                """[<channel>]

                Lists the registered projects in <channel>.
                """
                if not instance._check_capability(irc, msg):
                    return

                projects = instance._load_projects(channel)
                if projects is None or len(projects) == 0:
                    irc.error(_('This channel has no registered projects.'))
                    return

                for project_slug, project_url in projects.items():
                    irc.reply("%s: %s" % (project_slug, project_url))

            list = wrap(list, ['channel'])


Class = Gogs


# vim:set shiftwidth=4 softtabstop=4 expandtab textwidth=79:
