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
from utils import VideoMetaData, EventGroup
from enum import IntEnum
import os
import json
from typing import Dict, List, Tuple
from multiprocessing import RawArray
from annotation import AnnotationManager, EventGroup


class BufferItem:
    def __init__(self, frame_id, rate, frame_cnt, shm_id) -> None:
        self.frame_id = frame_id
        self.rate = rate
        self.frame_cnt = frame_cnt
        self.shm_id = shm_id
        self.cursor = 0

    def last_frame_id(self):
        return self.frame_id + (self.frame_cnt - 1) * self.rate

    def next_frame_id(self):
        return self.frame_id + self.frame_cnt * self.rate


class Thread(QThread):
    sig_update_frame = Signal(int, QImage)
    sig_open_video = Signal(VideoMetaData)

    BASE_EXTENT_PACE = 16

    def __init__(
        self, parent, q_frame: Queue, q_cmd: Queue, q_view: Queue, shm_arr: RawArray
    ):
        super().__init__(parent=parent)
        self.q_frame = q_frame  # this thread to decoder
        self.q_cmd = q_cmd  # this thread to viewer
        self.q_view = q_view  # viewer to this thread

        self.shm_arr = shm_arr

        self.view_cur_id = 0  # current frame id
        self.view_next_id = 0  # next frame to consume
        self.view_last_to_show = 0  # last frame to show(included)
        self.view_subscribed = 0
        self.view_playrate = 1

        self.buffer = []
        self.total_frames = 0

        self.last_update_t = 0

        self.stopped = False
        self.paused = False

        self.shm_cap = 1
        self.shm_mat = None

        self.v_id = 0

    def clear_buffer(self):
        for item in self.buffer:
            item: BufferItem
            self.frame_ack(
                self.v_id,
                (item.shm_id + item.cursor) % self.shm_cap,
                item.frame_cnt - item.cursor,
            )
        self.buffer = []

    def is_view_paused(self):
        return self.view_last_to_show < self.view_next_id and self.paused

    def get_playrate(self):
        return max(self.view_playrate, 1)

    def get_view_interval(self):
        if self.view_playrate < 1:
            return 1.0 / 25 / self.view_playrate
        else:
            return 1.0 / 25

    def pause(self, show_current_frame):
        if show_current_frame:
            self.view_last_to_show = self.view_next_id
        else:
            self.view_last_to_show = self.view_next_id - 1
        self.paused = True

    def open(self, path):
        self.pause(show_current_frame=False)
        self.q_cmd.put(Msg(msgtp.OPEN, self.v_id, path), block=False)

    def play(self):
        self.paused = False
        least_subscribed = (
            self.view_next_id + self.BASE_EXTENT_PACE * self.get_playrate()
        )
        sample_rate = self.get_playrate()
        if self.view_subscribed < least_subscribed:
            self.q_cmd.put(
                Msg(
                    msgtp.READ,
                    self.v_id,
                    (
                        self.view_subscribed,
                        least_subscribed - self.view_subscribed,
                        sample_rate,
                    ),
                )
            )
            self.view_subscribed = least_subscribed
        self.view_last_to_show = self.view_subscribed - 1

    def seek(self, seek_id):
        self.view_next_id = seek_id
        self.view_last_to_show = seek_id
        self.view_subscribed = seek_id + 1
        self.clear_buffer()
        sample_rate = 1 if self.paused else self.get_playrate()
        self.q_cmd.put(
            Msg(msgtp.READ, self.v_id, (seek_id, 1, sample_rate)), block=False
        )

    def change_playrate(self, rate):
        if self.view_playrate == rate:
            return
        self.view_playrate = rate
        self.seek(self.view_cur_id)
        if not self.paused:
            self.play()

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
                self.pause(show_current_frame=msg.data)
            elif msg.type == msgtp.VIEW_PLAY:
                self.play()
            elif msg.type == msgtp.VIEW_OPEN:
                self.open(msg.data)
            elif msg.type == msgtp.VIEW_TOGGLE:
                if self.paused:
                    self.play()
                else:
                    self.pause(show_current_frame=False)
            elif msg.type == msgtp.VIEW_SEEK:
                self.seek(msg.data)
            elif msg.type == msgtp.VIEW_PLAYRATE:
                self.change_playrate(msg.data)
            elif msg.type == msgtp.VIEW_NAVIGATE:
                if self.is_view_paused():
                    self.seek(msg.data)
            else:
                raise ValueError(f"Invalid type: {msg.type}")

    def read_video(self):
        try:
            msg = self.q_frame.get(block=False)

            if msg.type == msgtp.VIDEO_FRAMES:
                v_id, frame_id, rate, shm_id, frame_cnt, arr_shape, arr_type = msg.data
                accepted = False
                if v_id == self.v_id:
                    assert (
                        self.shm_mat.dtype == arr_type
                        and self.shm_mat.shape == arr_shape
                    )

                    cond_empty = len(self.buffer) == 0 and frame_id == self.view_next_id
                    cond_not_empty = (
                        len(self.buffer) > 0
                        and min(self.buffer[-1].next_frame_id(), self.total_frames - 1)
                        == frame_id
                    )
                    cond_rate = rate == self.get_playrate() or (
                        self.paused and rate == 1
                    )
                    if (cond_empty or cond_not_empty) and cond_rate:
                        accepted = True
                        self.buffer.append(
                            BufferItem(frame_id, rate, frame_cnt, shm_id)
                        )
                if not accepted:
                    self.frame_ack(v_id, shm_id, frame_cnt)

            elif msg.type == msgtp.VIDEO_OPEN_ACK:
                self.v_id, self.shm_cap, nbytes, shape, dtype, video_meta = msg.data
                shm_sliced = np.frombuffer(self.shm_arr, dtype="b")[
                    : self.shm_cap * nbytes
                ]
                self.shm_mat = np.frombuffer(shm_sliced, dtype=dtype).reshape(
                    (self.shm_cap, *shape)
                )
                self.total_frames = int(video_meta.total_frames)
                self.sig_open_video.emit(video_meta)
                self.view_cur_id = -1
                self.view_next_id = 0
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
            and cur_t - self.last_update_t >= self.get_view_interval()
            and self.view_last_to_show >= self.view_next_id
        ):
            item: BufferItem = self.buffer[0]
            frame_id = item.frame_id + item.cursor * item.rate
            frame_id = min(frame_id, self.total_frames - 1)
            shm_id = (item.shm_id + item.cursor) % self.shm_cap

            assert self.shm_mat.shape[0] == self.shm_cap
            assert frame_id == min(
                self.view_next_id, self.total_frames - 1
            ), f"get {frame_id}, expect {self.view_next_id}"

            frame_content = self.shm_mat[shm_id].copy()
            self.change_view_image(frame_id, frame_content)
            item.cursor += 1
            self.frame_ack(self.v_id, shm_id, 1)
            if item.cursor >= item.frame_cnt:
                self.buffer.pop(0)

            self.view_cur_id = self.view_next_id
            self.view_next_id += item.rate
            self.last_update_t = cur_t

            margin = self.view_subscribed - self.view_next_id
            thresh = self.BASE_EXTENT_PACE * self.get_playrate() / 2
            if not self.paused and margin < thresh:
                self.play()

    def run(self):
        while not self.stopped:
            self.read_view()
            self.read_video()
            self.update_view()
            time.sleep(0.001)


class AnnWindowManager:
    class State(IntEnum):
        IDLE = 0
        NEW = 1

    def __init__(self) -> None:
        self.video_meta = VideoMetaData("", 0, 1)
        self.breakpoints = []
        # id of the current frame shown on the screen
        self.view_frame_id = 0

        self.is_dirty = False

        self.navigate_repeat = 0
        self.playrate = 1

        event_groups = self.read_event_config()
        self.annotation_manager = AnnotationManager(event_groups)

        self.event_btn_state = {}
        for k in self.annotation_manager.get_all_events():
            self.event_btn_state[k] = (self.State.IDLE, 0)

    def get_current_time(self):
        return self.video_meta.frame_to_time(self.view_frame_id)

    def get_event_btn_state(self, event):
        return self.event_btn_state[event][0]

    def read_event_config(self):
        with open("event.json", "r", encoding="utf-8") as f:
            config = json.load(f)
        event_groups = {}
        for k, v in config.items():
            event_groups[k] = EventGroup(k, v)
        return event_groups

    def read_event_annotation_str(self, vname):
        path = os.path.join("dataset", "annotate_event", vname + ".txt")
        if os.path.exists(path):
            with open(path, "r", encoding="utf-8") as f:
                return f.read()
        else:
            with open(path, "w", encoding="utf-8") as f:
                f.write("")
            return ""

    def create_annotation(self, name, start_frame, end_frame):
        self.is_dirty = True
        self.annotation_manager.add_annotation(name, start_frame, end_frame)

    def modify_annotation(self, group_name, idx, event_name, start_frame, end_frame):
        self.annotation_manager.modify_annotation(
            group_name, idx, event_name, start_frame, end_frame
        )

    def remove_annotations(self, indexes: Dict[str, List[int]]):
        self.is_dirty = True
        for group_name, idxs in indexes.items():
            self.annotation_manager.remove_annotations(group_name, idxs)

    def valid(self):
        return len(self.video_meta.name) > 0

    def open(self, video_meta):
        self.video_meta = video_meta
        self.annotation_manager.parse_annotations(
            self.read_event_annotation_str(self.video_meta.name)
        )
        self.breakpoints = []
        self.new_start_frame_id = 0
        self.view_frame_id = 0
        self.is_dirty = False
        self.playrate = 1

    def event_button_clicked(self, event_name):
        type = self.annotation_manager.get_event_type(event_name)
        st, frame = self.event_btn_state[event_name]
        if st == self.State.IDLE:
            if type == "shot":
                self.create_annotation(
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
            self.create_annotation(event_name, frame, self.view_frame_id)
            self.event_btn_state[event_name] = self.State.IDLE, 0

    def cancel_new_event_annotation(self):
        if self.state == self.State.NEW:
            self.state = self.State.IDLE

    def save_event_annotations(self):
        path = os.path.join("dataset", "annotate_event", self.video_meta.name + ".txt")
        self.annotation_manager.save(path)
        self.is_dirty = False

    def annotations_tuple_list(self):
        return self.annotation_manager.annotations_tuple_list()

    def disabled_events(self):
        """
        有两种情况事件会被禁止使用:
        1. 事件所属group中有另一事件包含当前帧且allow_overlap为False
        2. 事件所属group中有另一事件当前被选中且allow_overlap为False
        2. 有同名事件包含当前帧
        """
        disabled_events = set()
        for group_name, anns in self.annotation_manager.annotations.items():
            group = self.annotation_manager.event_groups[group_name]
            group_conflict = False
            chosen_event = None
            for e_name in group.event_name:
                if self.event_btn_state[e_name][0] == self.State.NEW:
                    if group_conflict:
                        chosen_event = None
                    else:
                        group_conflict = True
                        chosen_event = e_name

            for ann in anns:
                if ann.f0 <= self.view_frame_id and ann.f1 >= self.view_frame_id:
                    disabled_events.add(ann.event_name)
                    group_conflict = True
                    chosen_event = None

            if group_conflict and not group.allow_overlap:
                for e_name in group.event_name:
                    if e_name != chosen_event:
                        disabled_events.add(e_name)
        return disabled_events

    def add_breakpoint(self, frame_id):
        self.breakpoints.append(frame_id)


class AnnWindow(QMainWindow):
    class AnnTableWidget(QTableWidget):
        def __init__(self, name, tables, parent=None):
            super().__init__(parent)
            self.name = name
            self.tables = tables

        def focusInEvent(self, event) -> None:
            for table in self.tables:
                if not (table is self):
                    table.clearSelection()
            return super().focusInEvent(event)

    def __init__(self, q_frame: Queue, q_cmd: Queue, shm_arr: RawArray) -> None:
        super().__init__()
        self.manager: AnnWindowManager = AnnWindowManager()
        self.setWindowTitle("Annotator")

        self.shm_arr = shm_arr

        self.btn_idl_stylesheet = r"background-color: rgb(240, 248, 255)"
        self.btn_new_stylesheet = r"background-color: rgb(3, 252, 107)"
        self.btn_overlap_stylesheet = r"background-color: palette(window)"

        self._create_tool_bar()
        self.status_bar = self.statusBar()

        self.playrates = ["1", "0.1", "0.3", "0.5", "4", "8"]
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
        for table in self.annotation_tables.values():
            table.itemDoubleClicked.connect(self.on_double_click_annotation_table_item)
            table.itemChanged.connect(self.on_annotation_table_item_changed)
        self.breakpoint_table.itemDoubleClicked.connect(
            self.on_double_click_breakpoint_table_item
        )

        self.breakpoint_btn.clicked.connect(self.on_breakpoint_btn_clicked)

        self.sort_ann_btn.clicked.connect(self.on_sort_ann_btn_clicked)
        self.remove_ann_btn.clicked.connect(self.on_remove_ann_btn_clicked)
        self.save_ann_btn.clicked.connect(self.on_save_ann_btn_clicked)
        self.edit_ann_btn.clicked.connect(self.on_edit_ann_btn_clicked)
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
        self.playrate_combobox.addItems(self.playrates)
        self.playrate_combobox.setFixedWidth(60)
        combobox_layout.addWidget(playrate_label)
        combobox_layout.addWidget(self.playrate_combobox)
        button_layout.addLayout(combobox_layout)

        self.breakpoint_btn = QPushButton("BreakPoint", self)
        button_layout.addWidget(self.breakpoint_btn)
        vlayout.addLayout(button_layout)
        return vlayout

    def _create_button_group(self):
        event_list = self.manager.annotation_manager.get_all_events()
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
        self.edit_ann_btn = QPushButton("edit", self)
        self.edit_mode = False
        button_layout.addWidget(self.sort_ann_btn)
        button_layout.addWidget(self.remove_ann_btn)
        button_layout.addWidget(self.save_ann_btn)
        button_layout.addWidget(self.edit_ann_btn)
        ann_vlayout.addLayout(button_layout)
        self.annotation_tables: Dict[str, QTableWidget] = {}

        all_ann_tables = []

        def new_ann_table(name):
            table = self.AnnTableWidget(name, all_ann_tables, self)
            self.annotation_tables[name] = table
            all_ann_tables.append(table)
            table.setColumnCount(3)
            table.setHorizontalHeaderLabels(["event", "start", "end"])
            table.setEditTriggers(QtWidgets.QAbstractItemView.NoEditTriggers)
            header = table.horizontalHeader()
            header.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
            header.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
            header.setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
            return table

        table_hlayout = QHBoxLayout()
        change_event_table = new_ann_table("变化事件")
        table_hlayout.addWidget(change_event_table)
        table_hlayout.setStretchFactor(change_event_table, 6)
        table_right_vlayout = QVBoxLayout()
        table_right_vlayout.addWidget(new_ann_table("回放"))
        table_right_vlayout.addWidget(new_ann_table("镜头情况"))
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
            self.update_ann_table(self.manager.annotations_tuple_list())

        if breakpoint_update:
            self.update_breakpoint_table(self.manager.breakpoints)

        if button_update:
            disabled_events = self.manager.disabled_events()
            for event, btn in self.event_btn_mapping.items():
                btn: QPushButton
                st = self.manager.get_event_btn_state(event)
                if st == AnnWindowManager.State.IDLE:
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
        if self.manager.video_meta.total_frames < 1:
            return
        next_frame = self.manager.view_frame_id
        next_frame -= frame
        next_frame = max(next_frame, 0)
        self.q_view.put(Msg(msgtp.VIEW_NAVIGATE, -1, next_frame))
        self.view_update_by_manager(button_update=True)

    def navigate_forward(self, frame):
        if self.manager.video_meta.total_frames < 1:
            return
        next_frame = self.manager.view_frame_id
        next_frame += frame
        next_frame = min(next_frame, int(self.manager.video_meta.total_frames) - 1)
        self.q_view.put(Msg(msgtp.VIEW_NAVIGATE, -1, next_frame))
        self.view_update_by_manager(button_update=True)

    @Slot(float)
    def on_playrate_changed(self, rate):
        rate = float(rate)
        if rate >= 1:
            rate = int(rate)
        self.manager.playrate = rate
        self.q_view.put(Msg(msgtp.VIEW_PLAYRATE, -1, rate), block=False)

    @Slot(int, QImage)
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

    def _update_table(self, table: QTableWidget, annotations):
        table.blockSignals(True)
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
        table.blockSignals(False)

    def update_ann_table(self, annotations: Dict[str, List[Tuple[str, int, int]]]):
        for k, anns in annotations.items():
            if k in self.annotation_tables:
                self._update_table(self.annotation_tables[k], anns)

    def update_breakpoint_table(self, breakpoints):
        self._update_table(self.breakpoint_table, breakpoints)

    @Slot(VideoMetaData)
    def on_open_video(self, video_meta: VideoMetaData):
        self.manager.open(video_meta)
        self.slider_change_config(video_meta.total_frames)
        self.playrate_combobox.setCurrentText("1")
        self.view_update_by_manager(ann_update=True, button_update=True)

    @Slot(QTableWidgetItem)
    def on_double_click_annotation_table_item(self, item: QTableWidgetItem):
        if item.column() == 0:
            return
        if self.edit_mode:
            item.tableWidget().editItem(item)
        else:
            frame_id = int(item.text())
            self.seek(frame_id)

    @Slot()
    def on_edit_ann_btn_clicked(self):
        self.edit_mode = not self.edit_mode
        if self.edit_mode:
            self.edit_ann_btn.setStyleSheet(self.btn_new_stylesheet)
        else:
            self.edit_ann_btn.setStyleSheet("")
        self.centralWidget().setFocus()

    @Slot(QTableWidgetItem)
    def on_annotation_table_item_changed(self, item: QTableWidgetItem):
        row = item.row()
        table = item.tableWidget()
        group_name = table.name
        event_name = table.item(row, 0).text()
        ok = True
        try:
            start_frame = int(table.item(row, 1).text())
            end_frame = int(table.item(row, 2).text())
            ok = end_frame >= start_frame
        except ValueError:
            ok = False
        if ok:
            self.manager.modify_annotation(
                group_name, row, event_name, start_frame, end_frame
            )
        self.view_update_by_manager(ann_update=True, button_update=True)

    @Slot(QTableWidgetItem)
    def on_double_click_breakpoint_table_item(self, item: QTableWidgetItem):
        frame_id = int(item.text())
        self.seek(frame_id)

    @Slot()
    def on_breakpoint_btn_clicked(self):
        self.manager.add_breakpoint(self.manager.view_frame_id)
        self.view_update_by_manager(breakpoint_update=True)

    @Slot()
    def on_sort_ann_btn_clicked(self):
        self.manager.annotation_manager.sort()
        self.view_update_by_manager(ann_update=True)

    @Slot()
    def on_remove_ann_btn_clicked(self):
        selected = {}
        for k, table in self.annotation_tables.items():
            cur_remove = [item.row() for item in table.selectedItems()]
            cur_remove = [i for i in cur_remove if i >= 0]
            selected[k] = cur_remove
        self.manager.remove_annotations(selected)
        self.view_update_by_manager(ann_update=True, button_update=True)

    @Slot()
    def on_save_ann_btn_clicked(self):
        self.manager.save_event_annotations()
        self.centralWidget().setFocus()

    @Slot(QPushButton)
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

        elif event.key() >= Qt.Key.Key_1 and event.key() <= Qt.Key.Key_9:
            index = event.key() - Qt.Key.Key_1
            if index < len(self.playrates):
                self.playrate_combobox.setCurrentIndex(index)

        return super().keyPressEvent(event)

    def keyReleaseEvent(self, event) -> None:
        if event.key() == Qt.Key.Key_Space:
            self.toggle()
        return super().keyReleaseEvent(event)
