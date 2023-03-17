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
    QMessageBox
)
from PySide6 import QtWidgets
from PySide6.QtCore import Signal, Slot, QThread, Qt
from PySide6.QtGui import QImage, QPixmap, QAction
from multiprocessing import Queue, shared_memory
from msg import Msg, MsgType as msgtp
import numpy as np
import time
import queue
from utils import annotations_to_str, TimeStamp, VideoMetaData, sort_annotations
from enum import auto, IntEnum
from typing import *
import os
from clip import query_clip


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
    sig_open_video = Signal(VideoMetaData, list)

    BASE_EXTENT_PACE = 6
    def __init__(self, parent, q_frame: Queue, q_cmd: Queue, q_view: Queue):
        super().__init__(parent=parent)
        self.q_frame = q_frame
        self.q_cmd = q_cmd

        self.q_view = q_view

        self.view_frame_id = 0  # next frame to consume
        self.view_last_to_show = 0  # last frame to show
        self.view_playrate = 1

        self.buffer = []

        self.last_update_t = 0

        self.stopped = False

    def is_paused(self):
        return self.view_last_to_show < self.view_frame_id

    def open(self, path):
        self.pause()
        self.q_cmd.put(Msg(msgtp.OPEN, path), block=False)

    def pause(self):
        self.view_last_to_show = self.view_frame_id - 2

    def play(self):
        self.view_last_to_show = self.view_frame_id + self.BASE_EXTENT_PACE * self.view_playrate
        self.q_cmd.put(Msg(msgtp.EXTENT, self.view_last_to_show), block=False)

    def seek(self, seek_id):
        self.view_frame_id = seek_id
        self.view_last_to_show = seek_id
        self.buffer = []
        self.q_cmd.put(Msg(msgtp.SEEK, seek_id), block=False)
    
    def playrate(self, rate):
        self.view_playrate = rate
        self.q_cmd.put(Msg(msgtp.PLAYRATE, rate), block=False)
        self.play()

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
                video_meta, annotations = msg.data
                self.sig_open_video.emit(video_meta, annotations)
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

            if self.view_last_to_show - self.view_frame_id < self.BASE_EXTENT_PACE * self.view_playrate / 2:
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
        self.annotations = []
        self.clip_annotations = []
        self.state = self.State.IDLE
        self.new_start_frame_id = 0
        # id of the current frame shown on the screen
        self.view_frame_id = 0

        self.is_dirty = False

    def valid(self):
        return len(self.video_meta.name) > 0

    def is_inside_clip(self, t: TimeStamp):
        for clip in self.clip_annotations:
            if t.ge(clip[0]) and t.le(clip[1]):
                return True
        return False
    
    def get_ann_start_ts(self):
        return self.video_meta.frame_to_time(self.new_start_frame_id)

    def get_ts(self):
        return self.video_meta.frame_to_time(self.view_frame_id)

    def open(self, meta_data, annotations):
        self.video_meta = meta_data
        self.annotations = sort_annotations(annotations)
        self.clip_annotations = query_clip(self.video_meta.name)
        self.clip_annotations = sort_annotations(self.clip_annotations)
        self.new_start_frame_id = 0
        self.view_frame_id = 0
        self.is_dirty = False

    def create_annotation(self, start_id, end_id):
        self.is_dirty = True
        start_ts = self.video_meta.frame_to_time(start_id)
        end_ts = self.video_meta.frame_to_time(end_id)
        self.annotations.append((start_ts, end_ts))

    def remove_annotation(self, index):
        self.is_dirty = True
        del self.annotations[index]
    
    def sort_annotations(self):
        self.annotations = sort_annotations(self.annotations)

    def toggle_new_annotation(self, frame_id):
        if self.state == self.State.IDLE:
            self.new_start_frame_id = frame_id
            self.state = self.State.NEW
        elif self.state == self.State.NEW:
            self.create_annotation(self.new_start_frame_id, frame_id)
            self.state = self.State.IDLE
        
    def cancel_new_annotation(self):
        if self.state == self.State.NEW:
            self.state = self.State.IDLE

    def save_annotation(self):
        path = os.path.join("dataset", "annotate", self.video_meta.name + ".txt")
        sorted_annotations = sort_annotations(self.annotations)
        content = annotations_to_str(sorted_annotations)
        with open(path, "w") as f:
            f.write(content)
        self.is_dirty = False

class AnnWindow(QMainWindow):
    ANN_LEN_MIN = 2 * 60  # at least 2min
    def __init__(self, q_frame: Queue, q_cmd: Queue) -> None:
        super().__init__()
        self.manager: AnnManager = AnnManager()
        self.setWindowTitle("Annotator")

        self._create_tool_bar()

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
        self.clip_table.itemDoubleClicked.connect(self.on_double_click_table_item)
        self.new_ann_btn.clicked.connect(self.on_new_ann_btn_clicked)
        self.cancel_ann_btn.clicked.connect(self.on_cancel_btn_clicked)
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
        playrate_label.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        playrate_label.setFixedWidth(60)
        self.playrate_combobox = QComboBox(self)
        self.playrate_combobox.setEditable(False)
        self.playrate_combobox.addItems(["1", "2", "4", "8", "16"])
        self.playrate_combobox.setFixedWidth(60)
        combobox_layout.addWidget(playrate_label)
        combobox_layout.addWidget(self.playrate_combobox)
        
        button_layout.addLayout(combobox_layout)

        self.new_ann_btn = QPushButton("Mark", self)
        self.cancel_ann_btn = QPushButton("Cancel", self)
        button_layout.addWidget(self.new_ann_btn)
        button_layout.addWidget(self.cancel_ann_btn)
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
        ann_vlayout.addWidget(self.annotation_table)
        self.annotation_table.setColumnCount(2)
        self.annotation_table.setHorizontalHeaderLabels(["start", "end"])
        self.annotation_table.setEditTriggers(
            QtWidgets.QAbstractItemView.NoEditTriggers
        )
        ann_page.setLayout(ann_vlayout)
        control_tab.addTab(ann_page, "Annotations")

        clip_page = QWidget(control_tab)
        clip_vlayout = QVBoxLayout()
        self.clip_table = QTableWidget(self)
        clip_vlayout.addWidget(self.clip_table)
        self.clip_table.setColumnCount(2)
        self.clip_table.setHorizontalHeaderLabels(["start", "end"])
        self.clip_table.setEditTriggers(
            QtWidgets.QAbstractItemView.NoEditTriggers
        )
        clip_page.setLayout(clip_vlayout)
        control_tab.addTab(clip_page, "Clips")

        control_vlayout.addWidget(control_tab)
        return control_vlayout

    def _show_save_dialog(self):
        dialog = QMessageBox(self)
        dialog.setText("Annotation not saved, save it now?")
        dialog.setStandardButtons(QMessageBox.StandardButton.Save | QMessageBox.StandardButton.Cancel | QMessageBox.StandardButton.Discard)
        return dialog.exec()

    def show_save_dialog(self):
        if self.manager.valid() and self.manager.is_dirty:
            ret = self._show_save_dialog()
            if ret == QMessageBox.StandardButton.Save:
                self.manager.save_annotation()
            elif ret == QMessageBox.StandardButton.Cancel:
                return -1
        return 0

    def view_update_by_manager(self, ann_update=False, clip_update=False, button_update=False):
        if ann_update:
            self.update_ann_table(self.manager.annotations)
        
        if clip_update:
            self.update_clip_table(self.manager.clip_annotations)

        if button_update:
            new_enable = True
            cancel_enable = True
            if self.manager.state == self.manager.State.IDLE:
                cancel_enable = False
            elif self.manager.state == self.manager.State.NEW:
                start_ts = self.manager.get_ann_start_ts()
                now_ts = self.manager.get_ts()
                if now_ts.to_second() - start_ts.to_second() < self.ANN_LEN_MIN:
                    new_enable = False

            if self.manager.is_inside_clip(self.manager.get_ts()):
                new_enable = False

            if not new_enable:
                self.new_ann_btn.setStyleSheet("background-color: rgb(200, 0, 0)")
            else:
                if self.manager.state == self.manager.State.IDLE:
                    self.new_ann_btn.setStyleSheet("background-color: palette(window)")
                elif self.manager.state == self.manager.State.NEW:
                    self.new_ann_btn.setStyleSheet("background-color: rgb(3, 252, 107)")

            self.new_ann_btn.setEnabled(new_enable)
            self.cancel_ann_btn.setEnabled(cancel_enable)

    def pause(self):
        self.q_view.put(Msg(msgtp.VIEW_PAUSE, None), block=False)

    def toggle(self):
        self.q_view.put(Msg(msgtp.VIEW_TOGGLE, None), block=False)

    def play(self):
        self.q_view.put(Msg(msgtp.VIEW_PLAY, None), block=False)

    def seek(self, seek_id):
        self.q_view.put(Msg(msgtp.VIEW_SEEK, seek_id), block=False)

    def seek_by_time(self, ts):
        frame_id = self.manager.video_meta.time_to_frame(ts)
        self.seek(frame_id)

    @Slot()
    def view_open_video(self):
        img_path, _ = QFileDialog.getOpenFileName()
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
            item0 = QTableWidgetItem(str(ann[0]))
            item1 = QTableWidgetItem(str(ann[1]))
            table.insertRow(i)
            table.setItem(i, 0, item0)
            table.setItem(i, 1, item1)

    def update_ann_table(self, annotations):
        self._update_table(self.annotation_table, annotations)
    
    def update_clip_table(self, annotations):
        self._update_table(self.clip_table, annotations)

    @Slot(VideoMetaData, list)
    def on_open_video(self, meta_data: VideoMetaData, annotations):
        self.manager.open(meta_data, annotations)
        self.slider_change_config(meta_data.total_frame)
        self.view_update_by_manager(ann_update=True, clip_update=True, button_update=True)

    @Slot(QTableWidgetItem)
    def on_double_click_table_item(self, item: QTableWidgetItem):
        ts = TimeStamp.from_str(item.text())
        self.seek_by_time(ts)

    @Slot()
    def on_new_ann_btn_clicked(self):
        self.manager.toggle_new_annotation(self.manager.view_frame_id)
        self.view_update_by_manager(ann_update=True, button_update=True)
    
    @Slot()
    def on_cancel_btn_clicked(self):
        self.manager.cancel_new_annotation()
        self.view_update_by_manager(button_update=True)

    @Slot()
    def on_sort_ann_btn_clicked(self):
        self.manager.sort_annotations()
        self.view_update_by_manager(ann_update=True)

    @Slot()
    def on_remove_ann_btn_clicked(self):
        c_row = self.annotation_table.currentRow()
        if c_row >= 0:
            self.manager.remove_annotation(c_row)
            self.view_update_by_manager(ann_update=True, button_update=True)
    
    @Slot()
    def on_save_ann_btn_clicked(self):
        self.manager.save_annotation()

    @Slot()
    def on_playrate_changed(self, rate):
        rate = int(rate)
        self.q_view.put(Msg(msgtp.VIEW_PLAYRATE, rate), block=False)

    def closeEvent(self, event) -> None:
        if self.show_save_dialog() < 0:
            event.ignore()
            return
        self.th.stop()
        self.th.quit()
        self.th.wait()
        return super().closeEvent(event)

    def keyPressEvent(self, event) -> None:
        return super().keyPressEvent(event)

    def keyReleaseEvent(self, event) -> None:
        if event.key() == Qt.Key.Key_Space:
            self.toggle()
        return super().keyReleaseEvent(event)
