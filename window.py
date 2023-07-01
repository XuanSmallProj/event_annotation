from PySide6.QtWidgets import (
    QPushButton,
    QMainWindow,
    QLabel,
    QWidget,
    QToolBar,
    QHBoxLayout,
    QVBoxLayout,
    QTableWidget,
    QTableWidgetItem,
    QSlider,
    QFileDialog,
    QComboBox,
    QTabWidget,
    QMessageBox,
    QButtonGroup,
    QHeaderView,
)
from PySide6 import QtWidgets
from PySide6.QtCore import Signal, Slot, QThread, Qt
from PySide6.QtGui import QImage, QPixmap, QAction
from multiprocessing import Queue
from msg import Msg, MsgType as msgtp
import numpy as np
import time
import queue
from utils import VideoMetaData, sort_events, Event, EventGroup
from enum import IntEnum
import os
import json
from typing import Dict, Union, List, Tuple
from multiprocessing import RawArray


class BufferItem:
    def __init__(self, frame_id, rate, frame_cnt, shm_id) -> None:
        self.frame_id = frame_id
        self.rate = rate
        self.frame_cnt = frame_cnt
        self.shm_id = shm_id
        self.cursor = 0

    def last_frame_id(self):
        return self.frame_id + (self.frame_cnt - 1) * self.rate

    def expect_next_frame_id(self):
        return self.frame_id + self.frame_cnt * self.rate


class CircularBuffer:
    def __init__(self, cap) -> None:
        self.cap = cap
        self.buffer = []


class Thread(QThread):
    sig_update_frame = Signal(int, QImage)
    sig_open_video = Signal(VideoMetaData)

    BASE_EXTENT_PACE = 6

    def __init__(
        self, parent, q_frame: Queue, q_cmd: Queue, q_view: Queue, shm_arr: RawArray
    ):
        super().__init__(parent=parent)
        self.q_frame = q_frame  # this thread to decoder
        self.q_cmd = q_cmd  # this thread to viewer
        self.q_view = q_view  # viewer to this thread

        self.shm_arr = shm_arr

        self.view_frame_id = 0  # next frame to consume
        self.view_last_to_show = 0  # last frame to show(included)
        self.view_subscribed = 0
        self.view_playrate = 1

        self.buffer = []

        self.last_update_t = 0

        self.stopped = False
        self.paused = False

        self.shm_cap = 1
        self.shm_mat = None

        self.v_id = 0

    def is_paused(self):
        return self.view_last_to_show < self.view_frame_id and self.paused

    def pause(self, show_current_frame):
        if show_current_frame:
            self.view_last_to_show = self.view_frame_id
        else:
            self.view_last_to_show = self.view_frame_id - 1
        self.paused = True

    def open(self, path):
        self.pause(show_current_frame=False)
        self.q_cmd.put(Msg(msgtp.OPEN, self.v_id, path), block=False)

    def play(self):
        least_subscribed = (
            self.view_frame_id + self.BASE_EXTENT_PACE * self.view_playrate
        )
        if self.view_subscribed < least_subscribed:
            self.q_cmd.put(
                Msg(
                    msgtp.READ,
                    self.v_id,
                    (self.view_subscribed, least_subscribed - self.view_subscribed),
                )
            )
            self.view_subscribed = least_subscribed
        self.view_last_to_show = self.view_subscribed - 1
        self.paused = False

    def seek(self, seek_id):
        self.view_frame_id = seek_id
        self.view_last_to_show = seek_id
        self.view_subscribed = seek_id + 1
        for item in self.buffer:
            item: BufferItem
            self.frame_ack(
                self.v_id, item.shm_id + item.cursor, item.frame_cnt - item.cursor
            )
        self.buffer = []
        self.q_cmd.put(Msg(msgtp.READ, self.v_id, (seek_id, 1)), block=False)

    def playrate(self, rate):
        self.view_playrate = rate
        self.q_cmd.put(Msg(msgtp.PLAYRATE, self.v_id, rate), block=False)

    def stop(self) -> None:
        # GIL to protect it
        self.stopped = True
        self.q_cmd.put(Msg(msgtp.CLOSE, self.v_id, None), block=False)

    def frame_ack(self, v_id, shm_start, shm_len):
        self.q_cmd.put(Msg(msgtp.FRAME_ACK, v_id, (shm_start, shm_len)), block=False)

    def open_ack(self, v_id):
        self.q_cmd.put(Msg(msgtp.OPEN_ACK, v_id, None), block=False)

    def change_view_image(self, frame_id, frame):
        h, w, ch = frame.shape
        img = QImage(frame.data, w, h, ch * w, QImage.Format_RGB888)
        scaled_img = img.scaled(640, 480, Qt.KeepAspectRatio)
        self.sig_update_frame.emit(frame_id, scaled_img)

    def read_view(self):
        while True:
            if self.q_view.empty():
                break
            msg = self.q_view.get(block=False)
            if msg.type == msgtp.VIEW_PAUSE:
                self.pause(msg.data)
            elif msg.type == msgtp.VIEW_PLAY:
                self.play()
            elif msg.type == msgtp.VIEW_OPEN:
                self.open(msg.data)
            elif msg.type == msgtp.VIEW_TOGGLE:
                if self.is_paused():
                    self.play()
                else:
                    self.pause(show_current_frame=False)
            elif msg.type == msgtp.VIEW_SEEK:
                self.seek(msg.data)
            elif msg.type == msgtp.VIEW_PLAYRATE:
                self.playrate(msg.data)
            elif msg.type == msgtp.VIEW_NAVIGATE:
                if self.is_paused():
                    self.seek(msg.data)

    def read_video(self):
        try:
            msg = self.q_frame.get(block=False)

            if msg.type == msgtp.VIDEO_FRAMES:
                v_id, frame_id, rate, shm_id, frame_cnt, arr_shape, arr_type = msg.data
                if v_id == self.v_id:
                    assert (
                        self.shm_mat.dtype == arr_type
                        and self.shm_mat.shape == arr_shape
                    )
                    if (len(self.buffer) == 0 and frame_id == self.view_frame_id) or (
                        len(self.buffer) > 0
                        and (self.buffer[-1].expect_next_frame_id() == frame_id)
                    ):
                        self.buffer.append(
                            BufferItem(frame_id, rate, frame_cnt, shm_id)
                        )
                else:
                    self.frame_ack(v_id, shm_id, frame_cnt)

            elif msg.type == msgtp.VIDEO_OPEN_ACK:
                self.v_id, self.shm_cap, nbytes, shape, dtype, video_meta = msg.data
                shm_sliced = np.frombuffer(self.shm_arr, dtype="b")[
                    : self.shm_cap * nbytes
                ]
                self.shm_mat = np.frombuffer(shm_sliced, dtype=dtype).reshape(
                    (self.shm_cap, *shape)
                )
                self.sig_open_video.emit(video_meta)
                self.view_frame_id = 0
                self.view_last_to_show = 0
                self.view_playrate = 1
                self.seek(0)
                self.open_ack(self.v_id)

        except queue.Empty:
            pass

    def update_view(self):
        cur_t = time.time()
        if (
            self.buffer
            and cur_t - self.last_update_t >= 1.0 / 25
            and self.view_last_to_show >= self.view_frame_id
        ):
            item: BufferItem = self.buffer[0]
            frame_id = item.frame_id + item.cursor
            shm_id = (item.shm_id + item.cursor) % self.shm_cap
            assert self.shm_mat.shape[0] == self.shm_cap
            frame_content = self.shm_mat[shm_id].copy()
            self.change_view_image(frame_id, frame_content)
            item.cursor += 1
            self.frame_ack(self.v_id, shm_id, 1)
            if item.cursor >= item.frame_cnt:
                self.buffer.pop(0)

            self.view_frame_id += item.rate
            self.last_update_t = cur_t

            margin = self.view_subscribed - self.view_frame_id
            thresh = self.BASE_EXTENT_PACE * self.view_playrate / 2
            if not self.paused and margin < thresh:
                self.play()

    def run(self):
        while not self.stopped:
            self.read_view()
            self.read_video()
            self.update_view()
            time.sleep(0.001)


class AnnManager:
    class State(IntEnum):
        IDLE = 0
        NEW = 1

    def __init__(self) -> None:
        self.video_meta = VideoMetaData("", 0, 1)
        self.extract_origin_name = ""
        self.extract_index = 0
        self.event_annotations: List[Event] = []  # Event
        self.event_annotations_by_group = {}
        self.breakpoints = []
        # id of the current frame shown on the screen
        self.view_frame_id = 0

        self.is_dirty = False

        self.navigate_repeat = 0
        self.playrate = 1

        self.event_groups: Dict[str, EventGroup]
        self.event_groups, self.event_max_table_id = self.read_event_meta()

        self.event_btn_state = {}
        for k in self.get_event_list():
            self.event_btn_state[k] = (self.State.IDLE, 0)

    def get_current_time(self):
        return self.video_meta.frame_to_time(self.view_frame_id)

    def get_event_list(self):
        result = []
        for group in self.event_groups.values():
            result.extend(group.event_name)
        return result

    def _get_event_group_type(self, event: Union[Event, str]) -> Tuple[EventGroup, str]:
        if isinstance(event, str):
            name = event
        else:
            name = event.name
        for _, v in self.event_groups.items():
            tp = v.get_type(name)
            if tp:
                return v, tp
        raise ValueError(f"invalid event: {name}")

    def is_event_allow_overlap(self, event):
        group, _ = self._get_event_group_type(event)
        return group.overlap

    def check_event_overlap_conflict(self):
        for i, e in enumerate(self.event_annotations):
            group_e, _ = self._get_event_group_type(e)
            if not group_e.overlap:
                for j in range(i + 1, len(self.event_annotations)):
                    e2 = self.event_annotations[j]
                    group_e2, _ = self._get_event_group_type(e2)
                    if group_e2.group_name == group_e.group_name:
                        if e.f1 >= e2.f0 and e.f1 <= e2.f1:
                            return True
                        if e2.f1 >= e.f0 and e2.f1 <= e.f1:
                            return True
        return False

    def get_disabled_events(self):
        """
        有两种情况事件会被禁止使用:
        1. 事件所属group中有另一事件包含当前帧
        2. 有同名事件包含当前帧
        """
        disabled_events = set()
        for e in self.event_annotations:
            group, _ = self._get_event_group_type(e)
            if e.f0 <= self.view_frame_id and e.f1 >= self.view_frame_id:
                disabled_events.add(e.name)
                if not group.overlap:
                    for e_in_group in group.event_name:
                        disabled_events.add(e_in_group)
        return disabled_events

    def get_event_btn_state(self, event):
        return self.event_btn_state[event][0]

    def read_event_meta(self) -> Dict[str, EventGroup]:
        with open("event.json", "r", encoding="utf-8") as f:
            config = json.load(f)
        event_groups = {}
        max_table_id = 0
        for k, v in config.items():
            event_groups[k] = EventGroup(k, v)
            max_table_id = max(max_table_id, event_groups[k].table_id)
        return event_groups, max_table_id

    def read_event_annotation_str(self, vname):
        path = os.path.join("dataset", "annotate_event", vname + ".txt")
        if os.path.exists(path):
            with open(path, "r", encoding="utf-8") as f:
                return f.read()
        else:
            with open(path, "w", encoding="utf-8") as f:
                f.write("")
            return ""

    def parse_event_annotation_str(self, s: str):
        res = []
        events_set = set(self.get_event_list())
        for line in s.split("\n"):
            if line:
                e = Event.parse(line)
                if e.name in events_set:
                    _, tp = self._get_event_group_type(e)
                    e.type = tp
                    res.append(e)
        return res

    def create_event_annotations(self, name, start_id, end_id):
        self.is_dirty = True
        _, type = self._get_event_group_type(name)
        e = Event(name, type)
        e.f0, e.f1 = start_id, end_id
        self.event_annotations.append(e)

    def remove_event_annotations(self, indexes):
        # TODO: remove logic is very complicate, simplify this.
        self.is_dirty = True
        to_remove = set()
        rear0 = 0
        index_rear = [0] * len(indexes)
        cnts = [0] * len(indexes)
        for i, table_indexes in enumerate(indexes):
            indexes[i] = sorted(list(set(table_indexes)))

        while rear0 < len(self.event_annotations):
            group, _ = self._get_event_group_type(self.event_annotations[rear0])

            cur_rear = index_rear[group.table_id]
            if cur_rear < len(indexes[group.table_id]):
                if cnts[group.table_id] == indexes[group.table_id][cur_rear]:
                    index_rear[group.table_id] += 1
                    to_remove.add(rear0)

            rear0 += 1
            cnts[group.table_id] += 1

        self.event_annotations = [
            ann for i, ann in enumerate(self.event_annotations) if i not in to_remove
        ]

    def sort_event_annotations(self):
        self.event_annotations = sort_events(self.event_annotations)

    def valid(self):
        return len(self.video_meta.name) > 0

    def open(self, meta_data):
        self.video_meta = meta_data
        self.event_annotations = self.parse_event_annotation_str(
            self.read_event_annotation_str(self.video_meta.name)
        )
        self.breakpoints = []
        self.new_start_frame_id = 0
        self.view_frame_id = 0
        self.is_dirty = False
        self.playrate = 1

    def event_button_clicked(self, event_name):
        _, type = self._get_event_group_type(event_name)
        st, frame = self.event_btn_state[event_name]
        if st == self.State.IDLE:
            if type == "shot":
                self.create_event_annotations(
                    event_name, self.view_frame_id, self.view_frame_id
                )
            elif type == "interval":
                self.event_btn_state[event_name] = (
                    self.State.NEW,
                    self.view_frame_id,
                )
            else:
                raise ValueError(f"{type} not implemented")
        elif st == self.State.NEW:
            self.create_event_annotations(event_name, frame, self.view_frame_id)
            self.event_btn_state[event_name] = self.State.IDLE, 0

    def cancel_new_event_annotation(self):
        if self.state == self.State.NEW:
            self.state = self.State.IDLE

    def save_event_annotations(self):
        path = os.path.join("dataset", "annotate_event", self.video_meta.name + ".txt")
        sorted_annotations = sort_events(self.event_annotations)
        content = "\n".join([str(e) for e in sorted_annotations])
        assert not self.check_event_overlap_conflict()
        with open(path, "w", encoding="utf-8") as f:
            f.write(content)
        self.is_dirty = False

    def event_annotations_tuple_list(self):
        tables = [[] for _ in range(self.event_max_table_id + 1)]
        for e in self.event_annotations:
            group, _ = self._get_event_group_type(e.name)
            tables[group.table_id].append((e.name, e.f0, e.f1))
        return tables

    def add_breakpoint(self, frame_id):
        self.breakpoints.append(frame_id)


class AnnWindow(QMainWindow):
    class AnnTableWidget(QTableWidget):
        def __init__(self, tables, parent=None):
            super().__init__(parent)
            self.tables = tables

        def focusInEvent(self, event) -> None:
            for table in self.tables:
                if not (table is self):
                    table.clearSelection()
            return super().focusInEvent(event)

    def __init__(self, q_frame: Queue, q_cmd: Queue, shm_arr: RawArray) -> None:
        super().__init__()
        self.manager: AnnManager = AnnManager()
        self.setWindowTitle("Annotator")

        self.shm_arr = shm_arr

        self.btn_idl_stylesheet = r"background-color: rgb(240, 248, 255)"
        self.btn_new_stylesheet = r"background-color: rgb(3, 252, 107)"
        self.btn_overlap_stylesheet = r"background-color: palette(window)"

        self._create_tool_bar()
        self.status_bar = self.statusBar()

        top_hlayout = QHBoxLayout()
        top_hlayout.addLayout(self._create_image_viewer())
        top_hlayout.addLayout(self._create_button_group())
        top_hlayout.addLayout(self._create_control_panel())

        central_widget = QWidget(self)
        central_widget.setLayout(top_hlayout)
        self.setCentralWidget(central_widget)

        self.view_update_by_manager(ann_update=True, button_update=True)
        self.q_view = Queue()
        self.th = Thread(self, q_frame, q_cmd, self.q_view, self.shm_arr)

        self.setup_connection()
        self.th.start(self.th.Priority.NormalPriority)

    def setup_connection(self):
        self.slider.sliderReleased.connect(self.slider_released)
        self.slider.sliderPressed.connect(self.slider_pressed)
        for table in self.annotation_tables:
            table.itemDoubleClicked.connect(self.on_double_click_table_item)
        self.breakpoint_table.itemDoubleClicked.connect(self.on_double_click_table_item)

        self.breakpoint_btn.clicked.connect(self.on_breakpoint_btn_clicked)

        self.sort_ann_btn.clicked.connect(self.on_sort_ann_btn_clicked)
        self.remove_ann_btn.clicked.connect(self.on_remove_ann_btn_clicked)
        self.save_ann_btn.clicked.connect(self.on_save_ann_btn_clicked)
        self.playrate_combobox.currentTextChanged.connect(self.on_playrate_changed)
        self.btn_group.buttonClicked.connect(self.on_event_btn_clicked)

        self.th.sig_update_frame.connect(self.set_frame)
        self.th.sig_open_video.connect(self.on_open_video)

    def _create_image_viewer(self):
        vlayout = QVBoxLayout()
        self.img_label = QLabel(self)
        self.img_label.setFixedSize(640, 480)
        vlayout.addWidget(self.img_label)
        self.slider = QSlider(Qt.Horizontal)
        self.slider.setFixedWidth(640)
        vlayout.addWidget(self.slider)

        button_layout = QHBoxLayout()

        combobox_layout = QHBoxLayout()
        combobox_layout.setSpacing(0)
        playrate_label = QLabel("Play rate:", self)
        playrate_label.setAlignment(
            Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
        )
        playrate_label.setFixedWidth(60)
        self.playrate_combobox = QComboBox(self)
        self.playrate_combobox.setEditable(False)
        self.playrate_combobox.addItems(["1", "2", "4", "8", "16", "32", "64"])
        self.playrate_combobox.setFixedWidth(60)
        combobox_layout.addWidget(playrate_label)
        combobox_layout.addWidget(self.playrate_combobox)
        button_layout.addLayout(combobox_layout)

        self.breakpoint_btn = QPushButton("BreakPoint", self)
        button_layout.addWidget(self.breakpoint_btn)
        vlayout.addLayout(button_layout)
        return vlayout

    def _create_button_group(self):
        event_list = self.manager.get_event_list()
        v_layout = QVBoxLayout()
        self.btn_group = QButtonGroup(self)
        self.event_btn_mapping = {}
        v_layout.setContentsMargins(0, 10, 0, 10)
        v_layout.setSpacing(20)
        for event in event_list:
            button = QPushButton(event, self)
            button.setStyleSheet(self.btn_idl_stylesheet)
            button.setFixedHeight(40)
            self.event_btn_mapping[event] = button
            self.btn_group.addButton(button)
            v_layout.addWidget(button)
        v_layout.addStretch()
        return v_layout

    def _create_tool_bar(self):
        toolbar = QToolBar("top tool bar")
        self.addToolBar(toolbar)
        button_action = QAction("Open", self)
        button_action.setStatusTip("Open Video")
        button_action.triggered.connect(self.view_open_video)
        toolbar.addAction(button_action)

    def _create_control_panel(self):
        table_width = 300

        control_vlayout = QVBoxLayout()
        control_tab = QTabWidget(self)

        ann_page = QWidget(control_tab)
        ann_vlayout = QVBoxLayout()
        button_layout = QHBoxLayout()
        self.sort_ann_btn = QPushButton("sort", self)
        self.remove_ann_btn = QPushButton("delete", self)
        self.save_ann_btn = QPushButton("save", self)
        button_layout.addWidget(self.sort_ann_btn)
        button_layout.addWidget(self.remove_ann_btn)
        button_layout.addWidget(self.save_ann_btn)
        ann_vlayout.addLayout(button_layout)
        self.annotation_tables = []

        def new_ann_table():
            table = self.AnnTableWidget(self.annotation_tables, self)
            self.annotation_tables.append(table)
            table.setColumnCount(3)
            table.setHorizontalHeaderLabels(["event", "start", "end"])
            table.setEditTriggers(QtWidgets.QAbstractItemView.NoEditTriggers)
            header = table.horizontalHeader()
            header.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
            header.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
            header.setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
            return table

        table_hlayout = QHBoxLayout()
        change_event_table = new_ann_table()
        table_hlayout.addWidget(change_event_table)
        table_hlayout.setStretchFactor(change_event_table, 6)
        table_right_vlayout = QVBoxLayout()
        table_right_vlayout.addWidget(new_ann_table())
        table_right_vlayout.addWidget(new_ann_table())
        table_hlayout.addLayout(table_right_vlayout)
        table_hlayout.setStretchFactor(table_right_vlayout, 4)
        ann_vlayout.addLayout(table_hlayout)
        ann_page.setLayout(ann_vlayout)
        control_tab.addTab(ann_page, "Annotations")

        breakpoint_page = QWidget(control_tab)
        breakpoint_layout = QVBoxLayout()
        self.breakpoint_table = QTableWidget(self)
        self.breakpoint_table.setFixedWidth(table_width)
        breakpoint_layout.addWidget(self.breakpoint_table)
        self.breakpoint_table.setColumnCount(1)
        self.breakpoint_table.setHorizontalHeaderLabels(["time"])
        self.breakpoint_table.setEditTriggers(
            QtWidgets.QAbstractItemView.NoEditTriggers
        )
        breakpoint_page.setLayout(breakpoint_layout)
        control_tab.addTab(breakpoint_page, "Breakpoint")

        control_vlayout.addWidget(control_tab)
        return control_vlayout

    def _show_save_dialog(self):
        dialog = QMessageBox(self)
        dialog.setText("Annotation not saved, save it now?")
        dialog.setStandardButtons(
            QMessageBox.StandardButton.Save
            | QMessageBox.StandardButton.Cancel
            | QMessageBox.StandardButton.Discard
        )
        return dialog.exec()

    def show_save_dialog(self):
        if self.manager.valid() and self.manager.is_dirty:
            ret = self._show_save_dialog()
            if ret == QMessageBox.StandardButton.Save:
                self.manager.save_event_annotations()
            elif ret == QMessageBox.StandardButton.Cancel:
                return -1
        return 0

    def view_update_by_manager(
        self,
        status_update=True,
        ann_update=False,
        button_update=False,
        breakpoint_update=False,
    ):
        if status_update:
            msg = f"{self.manager.video_meta.name} {self.manager.view_frame_id} {self.manager.get_current_time()}"
            self.status_bar.showMessage(msg)

        if ann_update:
            self.update_ann_table(self.manager.event_annotations_tuple_list())

        if breakpoint_update:
            self.update_breakpoint_table(self.manager.breakpoints)

        if button_update:
            disabled_events = self.manager.get_disabled_events()
            for event, btn in self.event_btn_mapping.items():
                btn: QPushButton
                st = self.manager.get_event_btn_state(event)
                if st == AnnManager.State.IDLE:
                    btn.setStyleSheet(self.btn_idl_stylesheet)
                else:
                    btn.setStyleSheet(self.btn_new_stylesheet)
                btn.setEnabled(True)

                if event in disabled_events:
                    btn.setStyleSheet(self.btn_overlap_stylesheet)
                    btn.setEnabled(False)

        # set focus to centralwidget(otherwise the keyboard won't work)
        # TODO: figure out why
        self.centralWidget().setFocus()

    def pause(self, lag):
        self.q_view.put(Msg(msgtp.VIEW_PAUSE, -1, lag), block=False)

    def toggle(self):
        self.q_view.put(Msg(msgtp.VIEW_TOGGLE, -1, None), block=False)

    def play(self):
        self.q_view.put(Msg(msgtp.VIEW_PLAY, -1, None), block=False)

    def seek(self, frame_id):
        self.q_view.put(Msg(msgtp.VIEW_SEEK, -1, frame_id), block=False)

    @Slot()
    def view_open_video(self):
        img_path, _ = QFileDialog.getOpenFileName()
        if not img_path:
            return
        if self.show_save_dialog() < 0:
            return
        self.q_view.put(Msg(msgtp.VIEW_OPEN, -1, img_path), block=False)

    def navigate_back(self, frame):
        if self.manager.video_meta.total_frame < 1:
            return
        next_frame = self.manager.view_frame_id
        next_frame -= frame
        next_frame = max(next_frame, 0)
        self.q_view.put(Msg(msgtp.VIEW_NAVIGATE, -1, next_frame))
        self.view_update_by_manager(button_update=True)

    def navigate_forward(self, frame):
        if self.manager.video_meta.total_frame < 1:
            return
        next_frame = self.manager.view_frame_id
        next_frame += frame
        next_frame = min(next_frame, int(self.manager.video_meta.total_frame) - 1)
        self.q_view.put(Msg(msgtp.VIEW_NAVIGATE, -1, next_frame))
        self.view_update_by_manager(button_update=True)

    @Slot()
    def on_playrate_changed(self, rate):
        rate = int(rate)
        self.manager.playrate = rate
        self.q_view.put(Msg(msgtp.VIEW_PLAYRATE, -1, rate), block=False)

    @Slot(QImage)
    def set_frame(self, frame_id, image):
        self.manager.view_frame_id = frame_id
        self.slider.setValue(frame_id)
        self.img_label.setPixmap(QPixmap.fromImage(image))
        self.view_update_by_manager(button_update=True)

    @Slot()
    def slider_pressed(self):
        self.pause(lag=False)

    @Slot()
    def slider_released(self):
        self.seek(self.slider.value())
        self.pause(lag=True)

    def slider_change_config(self, total):
        self.slider.setMaximum(total)

    def _update_table(self, table, annotations):
        while table.rowCount():
            table.removeRow(0)
        for i, ann in enumerate(annotations):
            items = []
            if isinstance(ann, (list, tuple)):
                for sub_ann in ann:
                    items.append(QTableWidgetItem(str(sub_ann)))
            else:
                items = [QTableWidgetItem(str(ann))]
            table.insertRow(i)
            for j, item in enumerate(items):
                table.setItem(i, j, item)

    def update_ann_table(self, annotations: List[List[Tuple[str, int, int]]]):
        for i, anns in enumerate(annotations):
            self._update_table(self.annotation_tables[i], anns)

    def update_breakpoint_table(self, breakpoints):
        self._update_table(self.breakpoint_table, breakpoints)

    @Slot(VideoMetaData, list)
    def on_open_video(self, meta_data: VideoMetaData):
        self.manager.open(meta_data)
        self.slider_change_config(meta_data.total_frame)
        self.playrate_combobox.setCurrentText("1")
        self.view_update_by_manager(ann_update=True, button_update=True)

    @Slot(QTableWidgetItem)
    def on_double_click_table_item(self, item: QTableWidgetItem):
        try:
            frame_id = int(item.text())
            self.seek(frame_id)
        except:
            pass

    @Slot()
    def on_breakpoint_btn_clicked(self):
        self.manager.add_breakpoint(self.manager.view_frame_id)
        self.view_update_by_manager(breakpoint_update=True)

    @Slot()
    def on_sort_ann_btn_clicked(self):
        self.manager.sort_event_annotations()
        self.view_update_by_manager(ann_update=True)

    @Slot()
    def on_remove_ann_btn_clicked(self):
        selected = []
        for table in self.annotation_tables:
            cur_remove = [item.row() for item in table.selectedItems()]
            cur_remove = [i for i in cur_remove if i >= 0]
            selected.append(cur_remove)
        self.manager.remove_event_annotations(selected)
        self.view_update_by_manager(ann_update=True, button_update=True)

    @Slot()
    def on_save_ann_btn_clicked(self):
        self.manager.save_event_annotations()

    @Slot()
    def on_event_btn_clicked(self, btn: QPushButton):
        self.manager.event_button_clicked(btn.text())
        self.view_update_by_manager(button_update=True, ann_update=True)

    def closeEvent(self, event) -> None:
        if self.show_save_dialog() < 0:
            event.ignore()
            return
        self.th.stop()
        self.th.quit()
        self.th.wait()
        return super().closeEvent(event)

    def keyPressEvent(self, event):
        if event.key() == Qt.Key.Key_A:
            if event.isAutoRepeat():
                self.manager.navigate_repeat = min(4, self.manager.navigate_repeat + 1)
            else:
                self.manager.navigate_repeat = 0
            self.navigate_back(1 * 2 ** (self.manager.navigate_repeat))

        elif event.key() == Qt.Key.Key_D:
            if event.isAutoRepeat():
                self.manager.navigate_repeat = min(4, self.manager.navigate_repeat + 1)
            else:
                self.manager.navigate_repeat = 0
            self.navigate_forward(1 * 2 ** (self.manager.navigate_repeat))

        return super().keyPressEvent(event)

    def keyReleaseEvent(self, event) -> None:
        if event.key() == Qt.Key.Key_Space:
            self.toggle()
        return super().keyReleaseEvent(event)
