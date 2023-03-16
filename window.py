from PySide6.QtWidgets import (
    QTextEdit,
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
)
from PySide6 import QtWidgets
from PySide6.QtCore import Signal, Slot, QThread, Qt
from PySide6.QtGui import QImage, QPixmap, QAction
from multiprocessing import Queue, shared_memory
from msg import Msg, MsgType as msgtp
import numpy as np
import time
import queue
from utils import get_video_name, TimeStamp, VideoMetaData
from typing import *


class BufferItem:
    def __init__(self, init_id, frames, shm) -> None:
        self.init_id = init_id
        self.shm = shm
        self.shm_name = shm.name
        self.frames = frames
        self.cursor = 0

    def last_frame_id(self):
        return self.init_id + len(self.frames) - 1


class Thread(QThread):
    sig_update_frame = Signal(QImage)
    sig_update_annotation_table = Signal(list)
    sig_update_slider_config = Signal(int)

    def __init__(self, parent, q_frame: Queue, q_cmd: Queue, q_view: Queue):
        super().__init__(parent=parent)
        self.q_frame = q_frame
        self.q_cmd = q_cmd

        self.q_view = q_view

        self.view_pause = False
        self.view_frame_id = 0  # next frame to consume

        self.buffer = []

        self.last_update_t = 0

        self.video_name = ""
        self.video_meta: VideoMetaData = VideoMetaData(0, 1)
        self.annotations: List[Tuple[TimeStamp, TimeStamp]] = []

    def open(self, path):
        if get_video_name(path) == self.video_name:
            return
        self.pause()
        self.q_cmd.put(Msg(msgtp.OPEN, path), block=False)

    def pause(self):
        self.view_pause = True
        self.q_cmd.put(Msg(msgtp.CANCEL_READ), block=False)

    def play(self):
        self.view_pause = False
        self.q_cmd.put(Msg(msgtp.EXTENT, self.view_frame_id + 100), block=False)

    def seek(self, seek_id):
        self.pause()
        self.view_frame_id = seek_id
        self.buffer = []
        self.q_cmd.put(Msg(msgtp.SEEK, seek_id), block=False)

    def change_view_image(self, frame):
        h, w, ch = frame.shape
        img = QImage(frame.data, w, h, ch * w, QImage.Format_RGB888)
        scaled_img = img.scaled(640, 480, Qt.KeepAspectRatio)
        self.sig_update_frame.emit(scaled_img)

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
                if self.view_pause:
                    self.play()
                else:
                    self.pause()
            elif msg.type == msgtp.VIEW_SEEK:
                self.seek(msg.data)
            elif msg.type == msgtp.VIEW_SEEK_BY_TIME:
                frame_id = self.video_meta.time_to_frame(msg.data)
                self.seek(frame_id)

    def read_video(self):
        try:
            msg = self.q_frame.get(block=False)

            if msg.type == msgtp.VIDEO_FRAMES:
                init_id, shm_name, mat_shape, mat_dtype = msg.data
                if (len(self.buffer) == 0 and init_id == self.view_frame_id) or (
                    len(self.buffer) > 0
                    and (self.buffer[-1].last_frame_id() + 1 == init_id)
                ):
                    shm = shared_memory.SharedMemory(name=shm_name)
                    frames = np.ndarray(mat_shape, dtype=mat_dtype, buffer=shm.buf)
                    self.buffer.append(BufferItem(init_id, frames, shm))
                else:
                    self.q_cmd.put(Msg(msgtp.CLOSE_SHM, shm_name))

            elif msg.type == msgtp.VIDEO_OPEN_ACK:
                self.video_name, self.video_meta, self.annotations = msg.data
                self.sig_update_annotation_table.emit(self.annotations)
                self.sig_update_slider_config.emit(self.video_meta.total_frame)
                self.view_frame_id = 0
                self.seek(0)
                self.play()

        except queue.Empty:
            pass

    def update_view(self):
        if self.view_pause:
            return
        cur_t = time.time()
        if self.buffer and cur_t - self.last_update_t >= 1.0 / 25:
            item: BufferItem = self.buffer[0]
            self.change_view_image(item.frames[item.cursor])
            item.cursor += 1
            if item.cursor >= len(item.frames):
                self.buffer.pop(0)
                item.shm.close()
                self.q_cmd.put(Msg(msgtp.CLOSE_SHM, item.shm_name), block=False)

            self.view_frame_id += 1
            self.last_update_t = cur_t

    def terminate(self):
        self.q_cmd.put(Msg(msgtp.CLOSE, None), block=False)
        super().terminate()

    def run(self):
        while True:
            self.read_view()
            self.read_video()
            self.update_view()
            time.sleep(0.001)


class AnnWindow(QMainWindow):
    updateFrame = Signal(QImage)

    def __init__(self, q_frame: Queue, q_cmd: Queue) -> None:
        super().__init__()
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

    def setup_connection(self):
        self.slider.sliderReleased.connect(self.slider_released)
        self.slider.sliderPressed.connect(self.slider_pressed)
        self.annotation_table.itemDoubleClicked.connect(self.on_double_click_table_item)
        self.th.sig_update_frame.connect(self.set_image)
        self.th.sig_update_annotation_table.connect(self.update_ann_table)
        self.th.sig_update_slider_config.connect(self.slider_change_config)

    def _create_image_viewer(self):
        vlayout = QVBoxLayout()
        self.img_label = QLabel(self)
        self.img_label.setFixedSize(640, 480)
        vlayout.addWidget(self.img_label)
        self.slider = QSlider(Qt.Horizontal)
        self.slider.setFixedWidth(640)
        vlayout.addWidget(self.slider)
        return vlayout

    def _create_tool_bar(self):
        toolbar = QToolBar("top tool bar")
        self.addToolBar(toolbar)
        button_action = QAction("Open", self)
        button_action.setStatusTip("Open Video")
        button_action.triggered.connect(self.open_video)
        toolbar.addAction(button_action)

    def _create_control_panel(self):
        vlayout = QVBoxLayout()
        self.annotation_table = QTableWidget(self)
        vlayout.addWidget(self.annotation_table)
        self.annotation_table.setColumnCount(2)
        self.annotation_table.setHorizontalHeaderLabels(["start", "end"])
        self.annotation_table.setEditTriggers(QtWidgets.QAbstractItemView.NoEditTriggers)
        return vlayout

    def pause(self):
        self.q_view.put(Msg(msgtp.VIEW_PAUSE, None), block=False)

    def toggle(self):
        self.q_view.put(Msg(msgtp.VIEW_TOGGLE, None), block=False)

    def play(self):
        self.q_view.put(Msg(msgtp.VIEW_PLAY, None), block=False)

    def seek(self, seek_id):
        self.q_view.put(Msg(msgtp.VIEW_SEEK, seek_id), block=False)
    
    def seek_by_time(self, ts):
        self.q_view.put(Msg(msgtp.VIEW_SEEK_BY_TIME, ts), block=False)

    @Slot()
    def open_video(self):
        img_path, _ = QFileDialog.getOpenFileName()
        self.q_view.put(Msg(msgtp.VIEW_OPEN, img_path), block=False)

    @Slot(QImage)
    def set_image(self, image):
        self.img_label.setPixmap(QPixmap.fromImage(image))

    @Slot()
    def slider_pressed(self):
        self.pause()

    @Slot()
    def slider_released(self):
        self.seek(self.slider.value())
        self.play()

    @Slot()
    def slider_change_config(self, total):
        self.slider.setMaximum(total)

    @Slot(list)
    def update_ann_table(self, annotations):
        self.annotation_table.clearContents()
        for i, ann in enumerate(annotations):
            item0 = QTableWidgetItem(str(ann[0]))
            item1 = QTableWidgetItem(str(ann[1]))
            self.annotation_table.insertRow(i)
            self.annotation_table.setItem(i, 0, item0)
            self.annotation_table.setItem(i, 1, item1)
    
    @Slot(QTableWidgetItem)
    def on_double_click_table_item(self, item: QTableWidgetItem):
        ts = TimeStamp.from_str(item.text())
        self.seek_by_time(ts)

    def closeEvent(self, event) -> None:
        self.th.terminate()
        return super().closeEvent(event)

    def keyPressEvent(self, event) -> None:
        return super().keyPressEvent(event)

    def keyReleaseEvent(self, event) -> None:
        if event.key() == Qt.Key.Key_Space:
            self.toggle()
        return super().keyReleaseEvent(event)
