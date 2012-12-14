"""
reqResViewer.py

Copyright 2008 Andres Riancho

This file is part of w3af, w3af.sourceforge.net .

w3af is free software; you can redistribute it and/or modify
it under the terms of the GNU General Public License as published by
the Free Software Foundation version 2 of the License.

w3af is distributed in the hope that it will be useful,
but WITHOUT ANY WARRANTY; without even the implied warranty of
MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
GNU General Public License for more details.

You should have received a copy of the GNU General Public License
along with w3af; if not, write to the Free Software
Foundation, Inc., 51 Franklin St, Fifth Floor, Boston, MA  02110-1301  USA

"""
import threading
import signal
import gtk
import gobject

import core.controllers.output_manager as om

from core.controllers.exceptions import (w3afException, w3afMustStopException,
                                         w3afMustStopOnUrlError)

from core.data.db.history import HistoryItem
from core.data.constants import severity
from core.data.parsers.HTTPRequestParser import HTTPRequestParser
from core.data.visualization.string_representation import StringRepresentation

from core.ui.gui.entries import RememberingVPaned
from core.ui.gui.entries import RememberingWindow
from core.ui.gui.entries import SemiStockButton
from core.ui.gui.httpeditor import HttpEditor
from core.ui.gui.rrviews.raw import HttpRawView
from core.ui.gui.rrviews.headers import HttpHeadersView
from core.ui.gui.rrviews.rendering import getRenderingView
from core.ui.gui.export_request import export_request
from core.ui.gui import helpers


def sigsegv_handler(signum, frame):
    print _('We caught a segmentation fault! Please report this bug to the'
            ' w3af-develop mailing list, providing details on how to reproduce'
            ' the issue in our environment.')

signal.signal(signal.SIGSEGV, sigsegv_handler)


class reqResViewer(gtk.VBox):
    '''
    A widget with the request and the response inside.

    @author: Andres Riancho (andres.riancho@gmail.com)
    @author: Facundo Batista ( facundo@taniquetil.com.ar )

    '''
    def __init__(
        self, w3af, enableWidget=None, withManual=True, withFuzzy=True,
        withCompare=True, withAudit=True, editableRequest=False,
            editableResponse=False, widgname="default", layout='Tabbed'):
        super(reqResViewer, self).__init__()
        self.w3af = w3af
        # Request
        self.request = requestPart(
            self, w3af, enableWidget, editableRequest, widgname=widgname)
        self.request.show()
        # Response
        self.response = responsePart(
            self, w3af, editableResponse, widgname=widgname)
        self.response.show()
        self.layout = layout
        if layout == 'Tabbed':
            self._initTabbedLayout()
        else:
            self._initSplittedLayout()
        # Init req toolbox
        self._initToolBox(withManual, withFuzzy, withCompare, withAudit)
        self.show()

    def _initTabbedLayout(self):
        '''Init Tabbed layout. It's more convenient for quick view.'''
        nb = gtk.Notebook()
        nb.show()
        self.nb = nb
        self.pack_start(nb, True, True)
        nb.append_page(self.request, gtk.Label(_("Request")))
        nb.append_page(self.response, gtk.Label(_("Response")))
        # Info
        self.info = HttpEditor(self.w3af)
        self.info.set_editable(False)
        #self.info.show()
        nb.append_page(self.info, gtk.Label(_("Info")))

    def _initSplittedLayout(self):
        '''Init Splitted layout. It's more convenient for intercept.'''
        self._vpaned = RememberingVPaned(self.w3af, 'trap_view')
        self._vpaned.show()
        self.pack_start(self._vpaned, True, True)
        self._vpaned.add(self.request)
        self._vpaned.add(self.response)

    def focus_response(self):
        if self.layout == 'Tabbed':
            self.nb.set_current_page(1)

    def focus_request(self):
        if self.layout == 'Tabbed':
            self.nb.set_current_page(0)

    def _initToolBox(self, withManual, withFuzzy, withCompare, withAudit):
        # Buttons

        # This import needs to be here in order to avoid an import loop
        from core.ui.gui.craftedRequests import ManualRequests, FuzzyRequests

        hbox = gtk.HBox()
        if withManual or withFuzzy or withCompare:

            if withManual:
                b = SemiStockButton(
                    "", gtk.STOCK_INDEX, _("Send Request to Manual Editor"))
                b.connect("clicked", self._send_request, ManualRequests)
                self.request.childButtons.append(b)
                b.show()
                hbox.pack_start(b, False, False, padding=2)
            if withFuzzy:
                b = SemiStockButton("", gtk.STOCK_PROPERTIES,
                                    _("Send Request to Fuzzy Editor"))
                b.connect("clicked", self._send_request, FuzzyRequests)
                self.request.childButtons.append(b)
                b.show()
                hbox.pack_start(b, False, False, padding=2)
            if withCompare:
                b = SemiStockButton("", gtk.STOCK_ZOOM_100, _(
                    "Send Request and Response to Compare Tool"))
                b.connect("clicked", self._sendReqResp)
                self.response.childButtons.append(b)
                b.show()
                hbox.pack_start(b, False, False, padding=2)
        # I always can export requests
        b = SemiStockButton("", gtk.STOCK_COPY, _("Export Request"))
        b.connect("clicked", self._send_request, export_request)
        self.request.childButtons.append(b)
        b.show()
        hbox.pack_start(b, False, False, padding=2)
        self.pack_start(hbox, False, False, padding=5)
        hbox.show()

        if withAudit:
            # Add everything I need for the audit request thing:
            # The button that shows the menu
            b = SemiStockButton(
                "", gtk.STOCK_EXECUTE, _("Audit Request with..."))
            b.connect("button-release-event", self._popupMenu)
            self.request.childButtons.append(b)
            b.show()
            hbox.pack_start(b, False, False, padding=2)

        # The throbber (hidden!)
        self.throbber = helpers.Throbber()
        hbox.pack_start(self.throbber, True, True)

        self.draw_area = helpers.DrawingAreaStringRepresentation()
        hbox.pack_end(self.draw_area, False, False)

        self.pack_start(hbox, False, False, padding=5)
        hbox.show()

    def _popupMenu(self, widget, event):
        '''Show a Audit popup menu.'''
        _time = event.time
        # Get the information about the click
        #requestId = self._lstore[path][0]
        # Create the popup menu
        gm = gtk.Menu()
        plugin_type = "audit"
        for plugin_name in sorted(self.w3af.plugins.get_plugin_list(plugin_type)):
            e = gtk.MenuItem(plugin_name)
            e.connect('activate', self._auditRequest, plugin_name, plugin_type)
            gm.append(e)
        # Add a separator
        gm.append(gtk.SeparatorMenuItem())
        # Add a special item
        e = gtk.MenuItem('All audit plugins')
        e.connect('activate', self._auditRequest, 'All audit plugins',
                  'audit_all')
        gm.append(e)
        # show
        gm.show_all()
        gm.popup(None, None, None, event.button, _time)

    def _auditRequest(self, menuItem, plugin_name, plugin_type):
        """
        Audit a request using one or more plugins.

        @param menuItem: The name of the audit plugin, or the 'All audit plugins' wildcard
        @param plugin_name: The name of the plugin
        @param plugin_type: The type of plugin
        @return: None
        """
        # We show a throbber, and start it
        self.throbber.show()
        self.throbber.running(True)
        request = self.request.get_object()
        # Now I start the analysis of this request in a new thread,
        # threading game (copied from craftedRequests)
        event = threading.Event()
        impact = ThreadedURLImpact(
            self.w3af, request, plugin_name, plugin_type, event)
        impact.start()
        gobject.timeout_add(200, self._impact_done, event, impact)

    def _impact_done(self, event, impact):
        # Keep calling this from timeout_add until isSet
        if not event.isSet():
            return True
        # We stop the throbber, and hide it
        self.throbber.hide()
        self.throbber.running(False)
        # Analyze the impact
        if impact.ok:
            #   Lets check if we found any vulnerabilities
            #
            #   TODO: I should actually show ALL THE REQUESTS generated by audit plugins...
            #               not just the ones with vulnerabilities.
            #
            for result in impact.result:

                # TODO: I'm not sure when this is None bug it appeared in Trac bug #167736
                if result.get_id() is not None:
                    for itemId in result.get_id():
                        historyItem = HistoryItem()
                        historyItem.load(itemId)
                        historyItem.update_tag(
                            historyItem.tag + result.plugin_name)
                        historyItem.info = result.get_desc()
                        historyItem.save()
        else:
            if impact.exception.__class__ == w3afException:
                msg = str(impact.exception)
            elif impact.exception.__class__ == w3afMustStopException:
                msg = "Stopped sending requests because " + \
                    str(impact.exception)
            elif impact.exception.__class__ == w3afMustStopOnUrlError:
                msg = "Not sending requests because " + str(impact.exception)
            else:
                raise impact.exception
            # We stop the throbber, and hide it
            self.throbber.hide()
            self.throbber.running(False)
            gtk.gdk.threads_enter()
            helpers.friendlyException(msg)
            gtk.gdk.threads_leave()
        return False

    def _send_request(self, widg, func):
        """Sends the texts to the manual or fuzzy request.

        @param func: where to send the request.
        """
        headers, data = self.request.get_both_texts()
        func(self.w3af, (headers, data))

    def _sendReqResp(self, widg):
        """Sends the texts to the compare tool."""
        headers, data = self.request.get_both_texts()
        self.w3af.mainwin.commCompareTool((headers, data,
                                           self.response.get_object()))

    def set_sensitive(self, how):
        """Sets the pane on/off."""
        self.request.set_sensitive(how)
        self.response.set_sensitive(how)


class requestResponsePart(gtk.Notebook):
    """Request/response common class."""

    def __init__(self, parent, w3af, enableWidget=[], editable=False, widgname="default"):
        super(requestResponsePart, self).__init__()
        self._parent = parent
        self._obj = None
        ### FIXME: REMOVE ME ###
        self._set_vals = [(False, 'default_val')]
        #######################
        self.w3af = w3af
        self.childButtons = []
        self._views = []
        self.enableWidget = enableWidget

    def add_view(self, view):
        self._views.append(view)
        self.append_page(view, gtk.Label(view.label))

    def show(self):
        for view in self._views:
            view.show()
        super(requestResponsePart, self).show()

    def set_sensitive(self, how):
        super(requestResponsePart, self).set_sensitive(how)
        for but in self.childButtons:
            but.set_sensitive(how)

    def get_view_by_id(self, viewId):
        for view in self._views:
            if view.id == viewId:
                return view
        return None

    def synchronize(self, viewId=None):
        for view in self._views:
            if view.id != viewId:
                view.initial = True
                view.show_object(self._obj)
                view.initial = False
        if self.enableWidget:
            for widg in self.enableWidget:
                widg(bool(len(self._obj.get_headers())))

    def clear_panes(self):
        self._obj = None
        ### FIXME: REMOVE ME ###
        self._set_vals.append((False, 'panes_cleared'))
        #######################
        self._parent.draw_area.clear()
        for view in self._views:
            view.initial = True
            view.clear()
            view.initial = False

    def show_error(self, text):
        print text

    def show_object(self, obj):
        self._obj = obj

        # String representation
        if hasattr(obj, 'get_body'):
            str_repr_inst = StringRepresentation(
                obj.get_body(),
                self._parent.draw_area.width,
                self._parent.draw_area.height
            )
            str_repr_dict = str_repr_inst.get_representation()
            self._parent.draw_area.set_string_representation(str_repr_dict)

        ### FIXME: REMOVE ME ###
        self._set_vals.append((True, str(self._obj)))
        #######################
        self.synchronize()

    def set_object(self, obj):
        self._obj = obj
        ### FIXME: REMOVE ME ###
        self._set_vals.append((True, str(self._obj)))
        #######################

    def get_object(self):
        return self._obj

    def highlight(self, text, sev=severity.MEDIUM):
        for view in self._views:
            view.highlight(text, sev)


class requestPart(requestResponsePart):

    def __init__(self, parent, w3af, enableWidget=[], editable=False, widgname="default"):
        requestResponsePart.__init__(
            self, parent, w3af, enableWidget, editable,
            widgname=widgname + "request")
        self.add_view(HttpRawView(w3af, self, editable))
        self.add_view(HttpHeadersView(w3af, self, editable))

    def get_both_texts(self):
        try:
            data = ''
            if self._obj.get_data():
                data = str(self._obj.get_data())
            return (self._obj.dump_request_head(), data)
        except AttributeError, ae:  # FIXME: REMOVE ME ###
            msg = ("DEBUG_EXCEPTION: %s. Actions were: %s" %
                   (ae, self._set_vals))
            raise AttributeError(msg)

    def show_raw(self, head, body):
        self._obj = HTTPRequestParser(head, body)
        ### FIXME: REMOVE ME ###
        self._set_vals.append((True, str(self._obj)))
        #######################
        self.synchronize()


class responsePart(requestResponsePart):
    def __init__(self, parent, w3af, editable, widgname="default"):
        requestResponsePart.__init__(self, parent, w3af, editable=editable,
                                     widgname=widgname + "response")
        http = HttpRawView(w3af, self, editable)
        http.is_request = False
        self.add_view(http)
        headers = HttpHeadersView(w3af, self, editable)
        headers.is_request = False
        self.add_view(headers)
        try:
            rend = getRenderingView(w3af, self)
            self.add_view(rend)
        except Exception, ex:
            print ex

    def get_both_texts(self):
        return (self._obj.dump_response_head(), str(self._obj.get_body()))


class reqResWindow(RememberingWindow):
    """
    A window to show a request/response pair.
    """
    def __init__(self, w3af, request_id, enableWidget=None, withManual=True,
                 withFuzzy=True, withCompare=True, withAudit=True, editableRequest=False,
                 editableResponse=False, widgname="default"):

        # Create the window
        RememberingWindow.__init__(self, w3af, "reqResWin",
                                   _("w3af - HTTP Request/Response"),
                                   "Browsing_the_Knowledge_Base")

        # Create the request response viewer
        rrViewer = reqResViewer(w3af, enableWidget, withManual, withFuzzy,
                                withCompare, withAudit, editableRequest,
                                editableResponse, widgname)

        # Search the id in the DB
        historyItem = HistoryItem()
        historyItem.load(request_id)
        # Set
        rrViewer.request.show_object(historyItem.request)
        rrViewer.response.show_object(historyItem.response)
        rrViewer.show()
        self.vbox.pack_start(rrViewer)

        # Show the window
        self.show()


class ThreadedURLImpact(threading.Thread):
    '''Impacts an URL in a different thread.'''
    def __init__(self, w3af, request, plugin_name, plugin_type, event):
        '''Init ThreadedURLImpact.'''
        threading.Thread.__init__(self)
        self.name = 'ThreadedURLImpact'
        self.daemon = True
        
        self.w3af = w3af
        self.request = request
        self.plugin_name = plugin_name
        self.plugin_type = plugin_type
        self.event = event
        self.result = []
        self.ok = False

    def run(self):
        '''Start the thread.'''
        try:
            # First, we check if the user choosed 'All audit plugins'
            if self.plugin_type == 'audit_all':

                #
                #   Get all the plugins and work with that list
                #
                for plugin_name in self.w3af.plugins.get_plugin_list('audit'):
                    plugin = self.w3af.plugins.get_plugin_inst(
                        'audit', plugin_name)
                    tmp_result = []
                    try:
                        tmp_result = plugin.audit_wrapper(self.request)
                        plugin.end()
                    except w3afException, e:
                        om.out.error(str(e))
                    else:
                        #
                        #   Save the plugin that found the vulnerability in the result
                        #
                        for r in tmp_result:
                            r.plugin_name = plugin_name
                        self.result.extend(tmp_result)

            else:
                #
                #   Only one plugin was enabled
                #
                plugin = self.w3af.plugins.get_plugin_inst(
                    self.plugin_type, self.plugin_name)
                try:
                    self.result = plugin.audit_wrapper(self.request)
                    plugin.end()
                except w3afException, e:
                    om.out.error(str(e))
                else:
                    #
                    #   Save the plugin that found the vulnerability in the result
                    #
                    for r in self.result:
                        r.plugin_name = self.plugin_name

            #   We got here, everything is OK!
            self.ok = True

        except Exception, e:
            self.exception = e
            #
            #   This is for debugging errors in the audit button of the reqResViewer
            #
            #import traceback
            #print traceback.format_exc()
        finally:
            self.event.set()
