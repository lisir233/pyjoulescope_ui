# Copyright 2018-2023 Jetperch LLC
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

# https://stackoverflow.com/questions/11874767/real-time-plotting-in-while-loop-with-matplotlib
# https://wiki.qt.io/Gallery_of_Qt_CSS_Based_Styles

from joulescope_ui import pubsub_singleton, N_, get_topic_name, tooltip_format, CAPABILITIES, Metadata
from joulescope_ui.widgets import *   # registers all built-in widgets
from joulescope_ui.logging_util import logging_preconfig, logging_config
from joulescope_ui.styles.manager import style_settings
from joulescope_ui.process_monitor import ProcessMonitor
from PySide6 import QtCore, QtGui, QtWidgets
import PySide6QtAds as QtAds
from .error_window import ErrorWindow
from .help_ui import HelpHtmlMessageBox
from .exporter import ExporterDialog   # register the exporter
from .jls_source import JlsSource      # register the source
from .resources import load_resources, load_fonts
from joulescope_ui.devices.jsdrv.jsdrv_wrapper import JsdrvWrapper
from .styles import StyleManager
from .app import App
# from .mem_leak_debugger import MemLeakDebugger
from .paths import Paths
from .view import View  # registers the view manager
import appnope
import logging


_CPU_UTILIZATION_TOOLTIP = tooltip_format(
    N_('CPU utilization'),
    N_("""\
    Display the CPU utilization by this application and
    the total CPU utilization by all applications.
    The value is displayed in percent."""))


_MEMORY_UTILIZATION_TOOLTIP = tooltip_format(
    N_('Memory utilization'),
    N_("""\
    Display the memory (RAM) utilization by this application
    and by all applications.
    The value is displayed in percent."""))


class QResyncEvent(QtCore.QEvent):
    """An event containing a request for pubsub.process."""
    EVENT_TYPE = QtCore.QEvent.Type(QtCore.QEvent.registerEventType())

    def __init__(self):
        QtCore.QEvent.__init__(self, self.EVENT_TYPE)

    def __str__(self):
        return 'QResyncEvent()'

    def __len__(self):
        return 0


def _menu_setup(parent, d):
    def _publish_factory(value):
        return lambda: pubsub_singleton.publish(*value)

    k = {}
    for name_safe, name, value in d:
        if name_safe.endswith('_menu'):
            menu = QtWidgets.QMenu(parent)
            menu.setTitle(name)
            parent.addAction(menu.menuAction())
            w = [menu, _menu_setup(menu, value)]
        else:
            action = QtGui.QAction(parent)
            action.setText(name)
            if callable(value):
                action.triggered.connect(value)
            else:
                action.triggered.connect(_publish_factory(value))
            parent.addAction(action)
            w = [action, None]
        k[name_safe] = w
    return k


def _device_factory_add():
    jsdrv = JsdrvWrapper()
    pubsub_singleton.register(jsdrv, 'jsdrv')
    topic = get_topic_name(jsdrv)
    pubsub_singleton.topic_remove('registry/JsdrvStreamBuffer:001')
    pubsub_singleton.process()
    pubsub_singleton.publish(f'{topic}/actions/mem/!add', 1)  # use singleton memory buffer
    pubsub_singleton.process()


def _device_factory_finalize():
    factories = pubsub_singleton.query(f'registry_manager/capabilities/{CAPABILITIES.DEVICE_FACTORY}/list')
    for factory in factories:
        topic = f'registry/{factory}/actions/!finalize'
        pubsub_singleton.publish(topic, None)


class MainWindow(QtWidgets.QMainWindow):

    EVENTS = {
        'blink_slow': Metadata('bool', 'Periodic slow blink signal (0.5 Hz).', flags=['ro', 'skip_undo']),
        'blink_medium': Metadata('bool', 'Periodic medium blink signal (1 Hz).', flags=['ro', 'skip_undo']),
        'blink_fast': Metadata('bool', 'Periodic fast blink signal (2 Hz).', flags=['ro', 'skip_undo']),
    }

    def __init__(self):
        self._log = logging.getLogger(__name__)
        super(MainWindow, self).__init__()
        self._dialog = None
        self._pubsub = pubsub_singleton
        self.SETTINGS = style_settings(N_('UI'))
        self._pubsub.register(self, 'ui', parent=None)
        self._app = App().register()
        self._paths = Paths().register()
        self.resize(800, 600)
        self._icon = QtGui.QIcon()
        self._icon.addFile(u":/icon_64x64.ico", QtCore.QSize(), QtGui.QIcon.Normal, QtGui.QIcon.Off)
        self.setWindowIcon(self._icon)
        self._style_manager = StyleManager(self._pubsub)
        self._pubsub.register(self._style_manager, 'StyleManager:0')

        self._blink_count = 0
        self._blink_timer = QtCore.QTimer()
        self._blink_timer.timeout.connect(self._on_blink_timer)
        self._blink_timer.start(250)

        # Create the central widget with horizontal layout
        self._central_widget = QtWidgets.QWidget(self)
        self._central_widget.setObjectName('central_widget')
        self.setCentralWidget(self._central_widget)
        size_policy_xx = QtWidgets.QSizePolicy(QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Expanding)
        #self._central_widget.setSizePolicy(size_policy_xx)
        self._central_layout = QtWidgets.QHBoxLayout()
        self._central_layout.setObjectName('central_layout')
        self._central_layout.setSpacing(0)
        self._central_layout.setContentsMargins(0, 0, 0, 0)
        self._central_widget.setLayout(self._central_layout)

        self._dock_widget = QtWidgets.QWidget(self._central_widget)
        self._dock_widget.setObjectName('main_widget')
        self._dock_widget.setSizePolicy(size_policy_xx)

        self._status_bar = QtWidgets.QStatusBar(self)
        self._status_bar.setObjectName('status_bar')
        self.setStatusBar(self._status_bar)

        self._dock_layout = QtWidgets.QVBoxLayout(self._dock_widget)
        self._dock_layout.setContentsMargins(0, 0, 0, 0)
        QtAds.CDockManager.setConfigFlags(
            0
            | QtAds.CDockManager.DockAreaHasCloseButton
            | QtAds.CDockManager.DockAreaHasUndockButton
            | QtAds.CDockManager.DockAreaHasTabsMenuButton
            | QtAds.CDockManager.ActiveTabHasCloseButton
            | QtAds.CDockManager.FloatingContainerHasWidgetTitle
            | QtAds.CDockManager.XmlAutoFormattingEnabled
            | QtAds.CDockManager.OpaqueSplitterResize
            | QtAds.CDockManager.OpaqueUndocking
            | QtAds.CDockManager.FocusHighlighting
            | QtAds.CDockManager.MiddleMouseButtonClosesTab
        )
        self._dock_manager = QtAds.CDockManager(self._dock_widget)
        self._dock_manager.setStyleSheet("")
        self._dock_layout.addWidget(self._dock_manager)

        self._pubsub.publish('registry/view/actions/!ui_connect', {
            'ui': self,
            'dock_manager': self._dock_manager,
        })

        self._pubsub.publish('registry/view/actions/!add', 'view:multimeter')
        self._pubsub.publish('registry/view:multimeter/settings/name', N_('Multimeter'))
        self._pubsub.publish('registry/view/actions/!add', 'view:oscilloscope')
        self._pubsub.publish('registry/view:oscilloscope/settings/name', N_('Oscilloscope'))

        # Create the singleton sidebar widget
        self._side_bar = SideBar(self._central_widget)
        self._side_bar.register()
        self._central_layout.addWidget(self._side_bar)
        self._central_layout.addWidget(self._dock_widget)

        self._menu_bar = QtWidgets.QMenuBar(self)
        self._menu_items = _menu_setup(self._menu_bar, [
            ['file_menu', N_('&File'), [
                ['open', N_('Open'), ['registry/ui/actions/!file_open', '']],
                # 'Open &Recent': {},  # dynamically populated from MRU
                # '&Preferences': self.on_preferences,
                ['exit', N_('Exit'), ['registry/ui/actions/!close', '']],
            ]],
            ['view_menu', N_('View'), []],     # dynamically populated from available views
            ['widgets_menu', N_('Widgets'), []],  # dynamically populated from available widgets
            # '&Tools': {
            #     '&Clear Accumulator': self._on_accumulators_clear,
            #     '&Record Statistics': self._on_record_statistics,
            # },
            ['help_menu', N_('&Help'), [
                ['getting_started', N_('Getting Started'), ['registry/help_html/actions/!show', 'getting_started']],
                #'JS220 User\'s Guide': self._help_js220_users_guide,
                #'JS110 User\'s Guide': self._help_js110_users_guide,
                #'&View logs...': self._view_logs,
                ['changelog', N_('Changelog'), ['registry/help_html/actions/!show', 'changelog']],
                ['credits', N_('Credits'), ['registry/help_html/actions/!show', 'credits']],
                ['about', N_('About'), ['registry/help_html/actions/!show', 'about']],
            ]],
        ])
        self._view_menu_group = QtGui.QActionGroup(self._menu_items['view_menu'][0])
        self._view_menu_group.setExclusive(True)
        self.setMenuBar(self._menu_bar)

        _device_factory_add()
        self._pubsub.subscribe('registry_manager/capabilities/view.object/list',
                                   self._on_change_views, flags=['pub', 'retain'])
        self._pubsub.subscribe('registry_manager/capabilities/widget.class/list',
                                   self._on_change_widgets, flags=['pub', 'retain'])

        # todo restore view
        #self._pubsub.publish('registry/view/actions/!widget_open', 'ExampleWidget')
        #self._pubsub.publish('registry/view/actions/!widget_open', 'ExampleWidget')
        #self._pubsub.publish('registry/view/actions/!widget_open', 'MultimeterWidget')
        #self._pubsub.publish('registry/view/actions/!widget_open', 'MultimeterWidget')
        self._pubsub.publish('registry/view/actions/!widget_open', 'WaveformWidget')
        self._pubsub.publish('registry/StyleManager:0/actions/!render', None)

        self._pubsub.process()

        self._process_monitor = ProcessMonitor(self)
        self._process_monitor.update.connect(self._on_process_monitor)
        self._cpu_utilization = QtWidgets.QLabel(self._status_bar)
        self._cpu_utilization.setToolTip(_CPU_UTILIZATION_TOOLTIP)
        self._status_bar.addPermanentWidget(self._cpu_utilization)
        self._mem_utilization = QtWidgets.QLabel(self._status_bar)
        self._mem_utilization.setToolTip(_MEMORY_UTILIZATION_TOOLTIP)
        self._status_bar.addPermanentWidget(self._mem_utilization)
        self.show()
        # self._mem_leak_debugger = MemLeakDebugger(self)
        # self._side_bar.on_cmd_show(1)

    def _on_process_monitor(self, obj):
        x1 = obj['cpu_utilization']['self']
        x2 = obj['cpu_utilization']['all']
        self._cpu_utilization.setText(f'CPU: {x1:.1f}%, {x2:.1f}%')
        x1 = obj['memory_utilization']['self'] / obj['memory_utilization']['total'] * 100
        x2 = obj['memory_utilization']['all'] / obj['memory_utilization']['total'] * 100
        self._mem_utilization.setText(f'Mem: {x1:.1f}%, {x2:.1f}%')

    def _on_blink_timer(self):
        topic = get_topic_name(self)
        self._blink_count = (self._blink_count + 1) & 0x07
        self._pubsub.publish(f'{topic}/events/blink_fast', (self._blink_count & 1) != 0)
        if (self._blink_count & 1) == 0:
            self._pubsub.publish(f'{topic}/events/blink_medium', (self._blink_count & 2) != 0)
        if (self._blink_count & 3) == 0:
            self._pubsub.publish(f'{topic}/events/blink_slow', (self._blink_count & 4) != 0)

    def _on_change_views(self, value):
        active_view = self._pubsub.query('registry/view/settings/active', default=None)
        menu, k = self._menu_items['view_menu']
        for action, _ in k.values():
            self._view_menu_group.removeAction(action)
        menu.clear()

        menu_items = []
        for unique_id in value:
            name = self._pubsub.query(f'registry/{unique_id}/settings/name', default=unique_id)
            menu_items.append([unique_id, name, ['registry/view/settings/active', unique_id]])
        self._menu_items['view_menu'] = _menu_setup(menu, menu_items)

        k = self._menu_items['widgets_menu'][1]  # map of children
        for view_unique_id, (action, _) in k.items():
            action.setCheckable()
            if view_unique_id == active_view:
                action.setChecked()
            self._view_menu_group.addAction(action)

    def _on_change_widgets(self, value):
        menu, _ = self._menu_items['widgets_menu']
        menu.clear()
        menu_items = []
        for unique_id in value:
            name = self._pubsub.query(f'registry/{unique_id}/settings/name', default=unique_id)
            menu_items.append([unique_id, name, ['registry/view/actions/!widget_open', unique_id]])
        self._menu_items['widgets_menu'] = _menu_setup(menu, menu_items)

    def event(self, event: QtCore.QEvent):
        if event.type() == QResyncEvent.EVENT_TYPE:
            event.accept()
            self._pubsub.process()
            return True
        else:
            return super(MainWindow, self).event(event)

    def resync_request(self):
        # safely resynchronize pubsub processing to the main Qt event thread
        event = QResyncEvent()
        QtCore.QCoreApplication.postEvent(self, event)

    def on_action_file_open(self):
        self._log.info('file_open')
        path = pubsub_singleton.query('registry/paths/settings/save_path')
        self._dialog = QtWidgets.QFileDialog(self, N_('Select file to open'), path)
        self._dialog.setNameFilter('Joulescope Data (*.jls)')
        self._dialog.setFileMode(QtWidgets.QFileDialog.ExistingFile)
        self._dialog.updateGeometry()
        self._dialog.open()
        self._dialog.finished.connect(self._on_file_open_dialog_finished)

    def _on_file_open_dialog_finished(self, result):
        if result == QtWidgets.QDialog.DialogCode.Accepted:
            files = self._dialog.selectedFiles()
            if files and len(files) == 1:
                path = files[0]
                self._log.info('file_open %s', path)
                self._pubsub.publish(f'registry/JlsSource/actions/!open', path)
            else:
                self._log.info('file_open invalid files: %s', files)
        else:
            self._log.info('file_open cancelled')

    def closeEvent(self, event):
        self._log.info('closeEvent()')
        _device_factory_finalize()
        self._pubsub.publish('registry/JlsSource/actions/!finalize', None)
        # todo pubsub save
        return super(MainWindow, self).closeEvent(event)

    def on_action_close(self, value):
        self.close()


def run(log_level=None, file_log_level=None, filename=None):
    """Run the Joulescope UI application.

    :param log_level: The logging level for the stdout console stream log.
        The allowed levels are in :data:`joulescope_ui.logging_util.LEVELS`.
        None (default) disables console logging.
    :param file_log_level: The logging level for the file log.
        The allowed levels are in :data:`joulescope_ui.logging_util.LEVELS`.
        None (default) uses the configuration value.
    :param filename: The optional filename to display immediately.

    :return: 0 on success or error code on failure.
    """
    app = None
    try:
        logging_preconfig()
        pubsub_singleton.register(HelpHtmlMessageBox, 'help_html')
        # pubsub_singleton.publish(PUBSUB_TOPICS.PUBSUB_APP_NAME, N_('Joulescope UI'))
        log_path = pubsub_singleton.query('common/settings/paths/log')
        logging_config(log_path, stream_log_level=log_level, file_log_level=file_log_level)
        pubsub_singleton.process()
        app = QtWidgets.QApplication([])
        resources = load_resources()
        fonts = load_fonts()
        appnope.nope()
        ui = MainWindow()
        pubsub_singleton.notify_fn = ui.resync_request
        rc = app.exec_()
        del ui
        return rc
    except Exception:
        if app is None:
            app = QtWidgets.QApplication([])
        w = ErrorWindow()
        return app.exec_()
