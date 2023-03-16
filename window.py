from PySide6.QtWidgets import (QMainWindow, QLabel, QWidget, QHBoxLayout, QVBoxLayout, QApplication, QSlider)
from PySide6.QtCore import Signal, Slot, QThread, Qt
from PySide6.QtGui import QImage, QPixmap
from multiprocessing import Queue, shared_memory
from msg import Msg, MsgType as msgtp
import numpy as np
import time
import queue


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
    def __init__(self, parent, q_frame: Queue, q_cmd: Queue, q_view: Queue):
        super().__init__(parent=parent)
        self.q_frame = q_frame
        self.q_cmd = q_cmd

        self.q_view = q_view
        self.view_pause = False
        self.view_frame_id = 0
        self.view_subscribe_id = 0

        self.buffer = []

        self.last_update_t = 0

    def open(self, path):
        pass

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
                self.view_pause = True
                self.q_cmd.put(Msg(msgtp.CANCEL_READ), block=False)
            elif msg.type == msgtp.VIEW_PLAY:
                self.view_pause = False
                self.view_frame_id = msg.data
                self.view_expect_id = self.view_frame_id + 100
                self.q_cmd.put(Msg(msgtp.READ, (self.view_frame_id, self.view_subscribe_id)))

    def read_video(self):
        if self.view_pause:
            return
        try:
            msg = self.q_frame.get(block=False)
            init_id, shm_name, mat_shape, mat_dtype = msg.data

            if ((len(self.buffer) == 0 and init_id == self.view_frame_id) or 
                (len(self.buffer) > 0 and (self.buffer[-1].last_frame_id() + 1 == init_id))):
                shm = shared_memory.SharedMemory(name=shm_name)
                frames = np.ndarray(mat_shape, dtype=mat_dtype, buffer=shm.buf)
                self.buffer.append(BufferItem(init_id, frames, shm))
            else:
                self.q_cmd.put(Msg(msgtp.CLOSE_SHM, shm_name))
        except queue.Empty:
            pass

    def update_view(self):
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
        self.q_cmd.put(Msg(msgtp.CLOSE, None))
        super().terminate()

    def run(self):
        self.q_cmd.put(Msg(msgtp.READ, (0, 100)))
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

        top_hlayout = QHBoxLayout()
        self._create_image_viewer(top_hlayout)
        
        central_widget = QWidget(self)
        central_widget.setLayout(top_hlayout)
        self.setCentralWidget(central_widget)

        self.q_view = Queue()
        self.th = Thread(self, q_frame, q_cmd, self.q_view)

        self.setup_connection()

    def setup_connection(self):
        self.slider.sliderReleased.connect(self.slider_released)
        self.slider.sliderPressed.connect(self.slider_pressed)
        self.th.sig_update_frame.connect(self.set_image)

    def _create_image_viewer(self, top_hlayout):
        vlayout = QVBoxLayout()
        self.img_label = QLabel(self)
        self.img_label.setFixedSize(640, 480)
        vlayout.addWidget(self.img_label)
        self.slider = QSlider(Qt.Horizontal)
        self.slider.setFixedWidth(640)
        vlayout.addWidget(self.slider)
        top_hlayout.addLayout(vlayout)

    def _create_control_pannel(self, top_hlayout):
        pass

    @Slot(QImage)
    def set_image(self, image):
        self.img_label.setPixmap(QPixmap.fromImage(image))

    @Slot()
    def slider_pressed(self):
        self.q_view.put(Msg(msgtp.VIEW_PAUSE, None), block=False)

    @Slot()
    def slider_released(self):
        self.q_view.put(Msg(msgtp.VIEW_PLAY, self.slider.value()), block=False)
    
    def closeEvent(self, event) -> None:
        self.th.terminate()
        return super().closeEvent(event)


if __name__ == "__main__":
    app = QApplication()
    w = AnnWindow(None, None, None)
    w.show()
    app.exec()
