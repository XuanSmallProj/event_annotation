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
)
from PySide6 import QtWidgets
from PySide6.QtCore import Signal, Slot, QThread, Qt, QObject, QEvent
from PySide6.QtGui import QImage, QPixmap, QAction
from multiprocessing import Queue, shared_memory
from msg import Msg, MsgType as msgtp
import numpy as np
import time
import queue
from utils import VideoMetaData, sort_events, Event
from enum import IntEnum
from typing import *
import os
import math
from extract import get_extract_meta, extract_name_split
import json

class BufferItem:
    def __init__(self, init_id, rate, frames, shm) -> None:
        self.init_id = init_id
        self.rate = rate
        self.shm = shm
        self.shm_name = shm.name
        self.frames = frames
        self.cursor = 0

    def last_frame_id(self):
        return self.init_id + (len(self.frames) - 1) * self.rate

    def expect_next_frame_id(self):
        return self.init_id + len(self.frames) * self.rate


class Thread(QThread):
    sig_update_frame = Signal(int, QImage)
    sig_open_video = Signal(VideoMetaData)

    BASE_EXTENT_PACE = 6

    def __init__(self, parent, q_frame: Queue, q_cmd: Queue, q_view: Queue):
        super().__init__(parent=parent)
        self.q_frame = q_frame
        self.q_cmd = q_cmd

        self.q_view = q_view

        self.view_frame_id = 0  # next frame to consume
        self.view_last_to_show = 0  # last frame to show(included)
        self.view_playrate = 1

        self.buffer = []

        self.last_update_t = 0

        self.stopped = False
        self.paused = False

    def is_paused(self):
        return self.view_last_to_show < self.view_frame_id and self.paused

    def open(self, path):
        self.pause()
        self.q_cmd.put(Msg(msgtp.OPEN, path), block=False)

    def pause(self):
        self.view_last_to_show = self.view_frame_id - 2
        self.paused = True

    def play(self):
        self.view_last_to_show = (
            self.view_frame_id + self.BASE_EXTENT_PACE * self.view_playrate
        )
        self.paused = False
        self.q_cmd.put(Msg(msgtp.EXTENT, self.view_last_to_show), block=False)

    def seek(self, seek_id):
        self.view_frame_id = seek_id
        self.view_last_to_show = seek_id
        self.buffer = []
        self.q_cmd.put(Msg(msgtp.SEEK, seek_id), block=False)

    def playrate(self, rate):
        self.view_playrate = rate
        self.q_cmd.put(Msg(msgtp.PLAYRATE, rate), block=False)

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
                self.pause()
            elif msg.type == msgtp.VIEW_PLAY:
                self.play()
            elif msg.type == msgtp.VIEW_OPEN:
                self.open(msg.data)
            elif msg.type == msgtp.VIEW_TOGGLE:
                if self.is_paused():
                    self.play()
                else:
                    self.pause()
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
                init_id, rate, shm_name, mat_shape, mat_dtype = msg.data
                if (len(self.buffer) == 0 and init_id == self.view_frame_id) or (
                    len(self.buffer) > 0
                    and (self.buffer[-1].expect_next_frame_id() == init_id)
                ):
                    shm = shared_memory.SharedMemory(name=shm_name)
                    frames = np.ndarray(mat_shape, dtype=mat_dtype, buffer=shm.buf)
                    self.buffer.append(BufferItem(init_id, rate, frames, shm))
                else:
                    self.q_cmd.put(Msg(msgtp.CLOSE_SHM, shm_name), block=False)

            elif msg.type == msgtp.VIDEO_OPEN_ACK:
                video_meta = msg.data
                self.sig_open_video.emit(video_meta)
                self.view_frame_id = 0
                self.view_last_to_show = 0
                self.view_playrate = 1
                self.seek(0)
                # self.play()

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
            frame_id = item.init_id + item.cursor
            self.change_view_image(frame_id, item.frames[item.cursor])
            item.cursor += 1
            if item.cursor >= len(item.frames):
                self.buffer.pop(0)
                item.shm.close()
                self.q_cmd.put(Msg(msgtp.CLOSE_SHM, item.shm_name), block=False)

            self.view_frame_id += item.rate
            self.last_update_t = cur_t

            margin = self.view_last_to_show - self.view_frame_id
            thresh = self.BASE_EXTENT_PACE * self.view_playrate / 2
            if not self.paused and margin < thresh:
                self.play()

    def stop(self) -> None:
        # GIL to protect it
        self.stopped = True
        self.q_cmd.put(Msg(msgtp.CLOSE, None), block=False)

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
        self.event_annotations = []  # Tuple[str, int, int]
        self.breakpoints = []
        self.state = self.State.IDLE
        self.new_start_frame_id = 0
        self.new_event_name = ""
        # id of the current frame shown on the screen
        self.view_frame_id = 0

        self.is_dirty = False

        self.navigate_repeat = 0
        self.playrate = 1

        self.extract_meta = get_extract_meta()
        self.event_meta = self.get_event_meta()
    
    def get_event_meta(self):
        with open("event.json", "r") as f:
            config = json.load(f)
        res = {}
        for k, v in config.items():
            for name, type in v.items():
                res[name] = Event(name, type)
        return res

    def read_event_annotation_str(self, vname):
        path = os.path.join("dataset", "annotate_event", vname + ".txt")
        if os.path.exists(path):
            with open(path, "r") as f:
                return f.read()
        else:
            with open(path, "w") as f:
                f.write('')
            return ""
    
    def parse_event_annotation_str(self, s: str):
        res = []
        for line in s.split('\n'):
            if line:
                e = Event.parse(line)
                if e.name in self.event_meta:
                    e.type = self.event_meta[e.name].type
                    res.append(e)
        return res

    def valid(self):
        return len(self.video_meta.name) > 0

    def open(self, meta_data):
        self.video_meta = meta_data
        self.event_annotations = self.parse_event_annotation_str(self.read_event_annotation_str(self.video_meta.name))
        self.breakpoints = []
        self.new_start_frame_id = 0
        self.view_frame_id = 0
        self.is_dirty = False
        self.playrate = 1

    def create_event_annotations(self, name, start_id, end_id):
        self.is_dirty = True
        if name in self.event_meta:
            type = self.event_meta[name].type
            e = Event(name, type)
            e.f0, e.f1 = start_id, end_id
            self.event_annotations.append(e)

    def remove_event_annotations(self, indexes):
        self.is_dirty = True
        if isinstance(indexes, int):
            del self.event_annotations[indexes]
        else:
            indexes = set(indexes)
            self.event_annotations = [
                ann for i, ann in enumerate(self.event_annotations) if i not in indexes
            ]

    def sort_event_annotations(self):
        self.event_annotations = sort_events(self.event_annotations)

    def toggle_new_event_annotation(self, event_name, frame_id):
        if self.state == self.State.IDLE:
            if event_name in self.event_meta:
                self.new_start_frame_id = frame_id
                self.new_event_name = event_name
                event = self.event_meta[event_name]
                if event.type == "shot":
                    self.create_event_annotations(event_name, self.new_start_frame_id, frame_id)
                else:
                    self.state = self.State.NEW

        elif self.state == self.State.NEW:
            if self.new_event_name in self.event_meta:
                self.create_event_annotations(self.new_event_name, self.new_start_frame_id, frame_id)
                self.state = self.State.IDLE

    def cancel_new_event_annotation(self):
        if self.state == self.State.NEW:
            self.state = self.State.IDLE

    def save_event_annotations(self):
        path = os.path.join("dataset", "annotate_event", self.video_meta.name + ".txt")
        sorted_annotations = sort_events(self.event_annotations)
        content = "\n".join([str(e) for e in sorted_annotations])
        with open(path, "w") as f:
            f.write(content)
        self.is_dirty = False

    def event_annotations_tuple_list(self):
        return [(e.name, e.f0, e.f1) for e in self.event_annotations]

    def add_breakpoint(self, frame_id):
        self.breakpoints.append(frame_id)


class AnnWindow(QMainWindow):
    def __init__(self, q_frame: Queue, q_cmd: Queue) -> None:
        super().__init__()
        self.manager: AnnManager = AnnManager()
        self.setWindowTitle("Annotator")

        self._create_tool_bar()
        self.status_bar = self.statusBar()

        top_hlayout = QHBoxLayout()
        top_hlayout.addLayout(self._create_image_viewer())
        top_hlayout.addLayout(self._create_control_panel())

        central_widget = QWidget(self)
        central_widget.setLayout(top_hlayout)
        self.setCentralWidget(central_widget)

        self.q_view = Queue()
        self.th = Thread(self, q_frame, q_cmd, self.q_view)

        self.setup_connection()
        self.th.start(self.th.Priority.NormalPriority)

    def setup_connection(self):
        self.slider.sliderReleased.connect(self.slider_released)
        self.slider.sliderPressed.connect(self.slider_pressed)
        self.annotation_table.itemDoubleClicked.connect(self.on_double_click_table_item)
        self.breakpoint_table.itemDoubleClicked.connect(self.on_double_click_table_item)

        self.new_ann_btn.clicked.connect(self.on_new_ann_btn_clicked)
        self.cancel_ann_btn.clicked.connect(self.on_cancel_btn_clicked)
        self.breakpoint_btn.clicked.connect(self.on_breakpoint_btn_clicked)

        self.sort_ann_btn.clicked.connect(self.on_sort_ann_btn_clicked)
        self.remove_ann_btn.clicked.connect(self.on_remove_ann_btn_clicked)
        self.save_ann_btn.clicked.connect(self.on_save_ann_btn_clicked)
        self.playrate_combobox.currentTextChanged.connect(self.on_playrate_changed)
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

        event_name_label = QLabel("Event:", self)
        event_name_label.setAlignment(
            Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
        )
        event_name_label.setFixedWidth(60)
        self.event_name_combobox = QComboBox(self)
        event_names = [name for name in self.manager.event_meta.keys()]
        self.event_name_combobox.addItems(event_names)
        combobox_layout.addWidget(event_name_label)
        combobox_layout.addWidget(self.event_name_combobox)

        button_layout.addLayout(combobox_layout)

        self.new_ann_btn = QPushButton("Mark", self)
        self.cancel_ann_btn = QPushButton("Cancel", self)
        self.breakpoint_btn = QPushButton("BreakPoint", self)
        button_layout.addWidget(self.new_ann_btn)
        button_layout.addWidget(self.cancel_ann_btn)
        button_layout.addWidget(self.breakpoint_btn)
        vlayout.addLayout(button_layout)
        return vlayout

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

        self.annotation_table = QTableWidget(self)
        self.annotation_table.setFixedWidth(table_width)
        ann_vlayout.addWidget(self.annotation_table)
        self.annotation_table.setColumnCount(3)
        self.annotation_table.setHorizontalHeaderLabels(["event", "start", "end"])
        self.annotation_table.setEditTriggers(
            QtWidgets.QAbstractItemView.NoEditTriggers
        )
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
            msg = f"{self.manager.video_meta.name} {self.manager.view_frame_id}"
            self.status_bar.showMessage(msg)

        if ann_update:
            self.update_ann_table(self.manager.event_annotations_tuple_list())

        if breakpoint_update:
            self.update_breakpoint_table(self.manager.breakpoints)

        if button_update:
            new_enable = True
            cancel_enable = True

            if self.manager.state == self.manager.State.IDLE:
                cancel_enable = False
            elif self.manager.state == self.manager.State.NEW:
                new_enable = True

            if not new_enable:
                self.new_ann_btn.setStyleSheet("background-color: rgb(200, 0, 0)")
            else:
                if self.manager.state == self.manager.State.IDLE:
                    self.new_ann_btn.setStyleSheet("background-color: palette(window)")
                elif self.manager.state == self.manager.State.NEW:
                    self.new_ann_btn.setStyleSheet("background-color: rgb(3, 252, 107)")

            self.new_ann_btn.setEnabled(new_enable)
            self.cancel_ann_btn.setEnabled(cancel_enable)

        # set focus to centralwidget(otherwise the keyboard won't work)
        # TODO: figure out why
        self.centralWidget().setFocus()

    def pause(self):
        self.q_view.put(Msg(msgtp.VIEW_PAUSE, None), block=False)

    def toggle(self):
        self.q_view.put(Msg(msgtp.VIEW_TOGGLE, None), block=False)

    def play(self):
        self.q_view.put(Msg(msgtp.VIEW_PLAY, None), block=False)

    def seek(self, frame_id):
        self.q_view.put(Msg(msgtp.VIEW_SEEK, frame_id), block=False)

    @Slot()
    def view_open_video(self):
        img_path, _ = QFileDialog.getOpenFileName()
        if not img_path:
            return
        if self.show_save_dialog() < 0:
            return
        self.q_view.put(Msg(msgtp.VIEW_OPEN, img_path), block=False)

    @Slot(QImage)
    def set_frame(self, frame_id, image):
        self.manager.view_frame_id = frame_id
        self.slider.setValue(frame_id)
        self.img_label.setPixmap(QPixmap.fromImage(image))
        self.view_update_by_manager(button_update=True)

    @Slot()
    def slider_pressed(self):
        self.pause()

    @Slot()
    def slider_released(self):
        self.seek(self.slider.value())
        self.play()

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

    def update_ann_table(self, annotations):
        self._update_table(self.annotation_table, annotations)
    
    def update_breakpoint_table(self, breakpoints):
        self._update_table(self.breakpoint_table, breakpoints)

    def navigate_back(self, second):
        if self.manager.video_meta.total_frame < 1:
            return
        next_frame = self.manager.view_frame_id
        next_frame -= math.ceil(self.manager.video_meta.fps * second)
        next_frame = max(next_frame, 0)
        self.q_view.put(Msg(msgtp.VIEW_NAVIGATE, next_frame))
        self.view_update_by_manager(button_update=True)

    def navigate_forward(self, second):
        if self.manager.video_meta.total_frame < 1:
            return
        next_frame = self.manager.view_frame_id
        next_frame += math.ceil(self.manager.video_meta.fps * second)
        next_frame = min(next_frame, int(self.manager.video_meta.total_frame) - 1)
        self.q_view.put(Msg(msgtp.VIEW_NAVIGATE, next_frame))
        self.view_update_by_manager(button_update=True)

    @Slot(VideoMetaData, list)
    def on_open_video(self, meta_data: VideoMetaData):
        self.manager.open(meta_data)
        self.slider_change_config(meta_data.total_frame)
        self.playrate_combobox.setCurrentText("1")
        self.view_update_by_manager(
            ann_update=True, button_update=True
        )

    @Slot(QTableWidgetItem)
    def on_double_click_table_item(self, item: QTableWidgetItem):
        try:
            frame_id = int(item.text())
            self.seek(frame_id)
        except:
            pass

    @Slot()
    def on_new_ann_btn_clicked(self):
        event_name = self.event_name_combobox.currentText()
        self.manager.toggle_new_event_annotation(event_name, self.manager.view_frame_id)
        self.view_update_by_manager(ann_update=True, button_update=True)

    @Slot()
    def on_cancel_btn_clicked(self):
        self.manager.cancel_new_event_annotation()
        self.view_update_by_manager(button_update=True)

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
        selected = [item.row() for item in self.annotation_table.selectedItems()]
        selected = [i for i in selected if i >= 0]
        self.manager.remove_event_annotations(selected)
        self.view_update_by_manager(ann_update=True, button_update=True)

    @Slot()
    def on_save_ann_btn_clicked(self):
        self.manager.save_event_annotations()

    @Slot()
    def on_playrate_changed(self, rate):
        rate = int(rate)
        self.manager.playrate = rate
        self.q_view.put(Msg(msgtp.VIEW_PLAYRATE, rate), block=False)

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
            self.navigate_back(0.5 * 2 ** (self.manager.navigate_repeat))

        elif event.key() == Qt.Key.Key_D:
            if event.isAutoRepeat():
                self.manager.navigate_repeat = min(4, self.manager.navigate_repeat + 1)
            else:
                self.manager.navigate_repeat = 0
            self.navigate_forward(0.5 * 2 ** (self.manager.navigate_repeat))

        return super().keyPressEvent(event)

    def keyReleaseEvent(self, event) -> None:
        if event.key() == Qt.Key.Key_Space:
            self.toggle()
        elif event.key() == Qt.Key.Key_M:
            if self.new_ann_btn.isEnabled():
                self.on_new_ann_btn_clicked()
        elif event.key() == Qt.Key.Key_C:
            if self.cancel_ann_btn.isEnabled():
                self.on_cancel_btn_clicked()
        return super().keyReleaseEvent(event)
