# Copyright 2023 Jetperch LLC
#
# Licensed under the Apache License, Version 2.0 (the 'License');
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an 'AS IS' BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

from PySide6 import QtWidgets, QtGui, QtCore
from joulescope_ui import N_, register, CAPABILITIES, pubsub_singleton, get_topic_name
from joulescope_ui.source_selector import SourceSelector
from joulescope_ui.widget_tools import settings_action_create
from joulescope_ui.styles import styled_widget
from joulescope_ui.units import UNITS_SETTING, convert_units, unit_prefix, elapsed_time_formatter
import logging


SETTINGS = {
    'statistics_stream_source': {
        'dtype': 'str',
        'brief': N_('The statistics data stream source.'),
        'default': None,
    },
    'field': {
        'dtype': 'str',
        'brief': N_('The signal to display.'),
        'options': [
            ['charge', N_('charge')],
            ['energy', N_('energy')],
        ],
        'default': 'charge',
    },
    'show_titles': {
        'dtype': 'bool',
        'brief': N_('Show the statistics section title for each signal.'),
        'default': True,
    },
    'units': UNITS_SETTING,
}

@register
@styled_widget(N_('Accumulator'))
class AccumulatorWidget(QtWidgets.QWidget):
    CAPABILITIES = ['widget@', CAPABILITIES.STATISTIC_STREAM_SINK]
    SETTINGS = SETTINGS

    def __init__(self, parent=None):
        self._log = logging.getLogger(__name__)
        self._menu = None
        self._clipboard = None
        self._statistics = None
        self._on_statistics_fn = self._on_statistics
        self._devices = ['default']
        super().__init__(parent=parent)
        self.setObjectName('accumulator_widget')

        self.source_selector = SourceSelector(self, 'statistics_stream')
        self.source_selector.source_changed.connect(self._on_source_changed)
        self.source_selector.resolved_changed.connect(self._on_resolved_changed)

        self._layout = QtWidgets.QVBoxLayout()
        self._layout.setContentsMargins(0, 0, 0, 0)
        self._layout.setSpacing(0)
        self.setLayout(self._layout)
        self._hold_global = False

        self._accum_label = QtWidgets.QLabel(parent=self)
        self._accum_label.setObjectName('accum')
        self._layout.addWidget(self._accum_label)

        self._subscribers = [
            ['registry/app/settings/statistics_stream_enable',
             self._on_global_statistics_stream_enable],
        ]

    def on_pubsub_register(self):
        topic = f'{get_topic_name(self)}/settings/statistics_stream_source'
        self.source_selector.settings_topic = topic
        self.source_selector.on_pubsub_register()

        for topic, fn in self._subscribers:
            pubsub_singleton.subscribe(topic, fn, ['pub', 'retain'])

    def on_pubsub_unregister(self):
        self.source_selector.on_pubsub_unregister()
        self._disconnect()
        self._statistics = None
        for topic, fn in self._subscribers:
            pubsub_singleton.unsubscribe(topic, fn)

    def _disconnect(self):
        pubsub_singleton.unsubscribe_all(self._on_statistics_fn)
        self.repaint()

    def _connect(self):
        self._disconnect()
        source = self.source_selector.resolved()
        if source is not None:
            topic = get_topic_name(source)
            pubsub_singleton.subscribe(f'{topic}/events/statistics/!data', self._on_statistics_fn, ['pub'])
        self.repaint()

    def _on_source_changed(self, value):
        self.repaint()

    def _on_resolved_changed(self, value):
        self._connect()

    def _on_global_statistics_stream_enable(self, value):
        self._hold_global = not bool(value)

    def _on_statistics(self, pubsub, topic, value):
        if self._hold_global:
            return
        self._statistics = value
        signal = value['accumulators'][self.field]
        signal_value, signal_units = convert_units(signal['value'], signal['units'], self.units)
        _, prefix, scale = unit_prefix(signal_value)
        v_str = ('%+6f' % (signal_value / scale))[:8]

        a_start, a_end = self._statistics['time']['accum_samples']['value']
        sample_freq = self._statistics['time']['sample_freq']['value']
        duration = (a_end - a_start) / sample_freq
        duration_txt, duration_units = elapsed_time_formatter(duration, fmt='standard', precision=3)
        if duration_units == 's':
            duration_units = ' s'
        else:
            duration_units = ''
        s = f'{v_str} {prefix}{signal_units} in {duration_txt}{duration_units}'
        self._accum_label.setText(s)

    def _on_field(self, value):
        self.field = value
        self.repaint()

    def _on_units(self, value):
        self.units = value
        self.repaint()

    def mousePressEvent(self, event):
        if event.button() == QtCore.Qt.LeftButton:
            self._clipboard = self._accum_label.text()
            self._log.info('copy value to clipboard: %s', self._clipboard)
            QtWidgets.QApplication.clipboard().setText(self._clipboard)
        else:
            menu = QtWidgets.QMenu(self)
            if self.field == 'energy':
                toggle_field = 'charge'
                action = N_('Show charge')
            else:
                toggle_field = 'energy'
                action = N_('Show energy')
            field_toggle = QtGui.QAction(action)
            menu.addAction(field_toggle)
            field_toggle.triggered.connect(lambda checked=False: self._on_field(toggle_field))

            if self.units == 'SI':
                toggle_units = 'Xh'
                action_units = 'Wh' if self.field == 'energy' else 'Ah'
            else:
                toggle_units = 'SI'
                action_units = 'J' if self.field == 'energy' else 'C'
            units_toggle = QtGui.QAction(N_('Set units') + ': ' + action_units)
            menu.addAction(units_toggle)
            units_toggle.triggered.connect(lambda checked=False: self._on_units(toggle_units))

            source_menu, source_menu_items = self.source_selector.submenu_factory(menu)

            style_action = settings_action_create(self, menu)
            menu.popup(event.globalPosition().toPoint())
            self._menu = [
                menu, field_toggle, units_toggle,
                source_menu, source_menu_items,
                style_action]
            event.accept()

    def _construct_source_action(self, source):
        def fn():
            self.statistics_stream_source = source
        return fn
