import multiprocessing as mp
from multiprocessing import Queue, RawArray
from window import AnnWindow
from video import Video
from PySide6.QtWidgets import QApplication
from PySide6.QtCore import QObject, QEvent
from argparse import ArgumentParser


def fn_proc_window(q_frame: Queue, q_cmd: Queue, shm_arr: RawArray):
    app = QApplication()
    window = AnnWindow(q_frame, q_cmd, shm_arr)
    window.show()
    window.th.start()
    import sys
    sys.exit(app.exec())


def fn_proc_video(q_frame: Queue, q_cmd: Queue, shm_arr: RawArray):
    video = Video(q_frame, q_cmd, shm_arr)
    video.run()


def main():
    q_frame = Queue()
    q_cmd = Queue()
    shm_arr = RawArray("b", 500 * 1024 * 1024)
    p_video = mp.Process(target=fn_proc_video, args=(q_frame, q_cmd, shm_arr))
    p_window = mp.Process(target=fn_proc_window, args=(q_frame, q_cmd, shm_arr))
    p_video.start()
    p_window.start()


if __name__ == "__main__":
    parser = ArgumentParser()
    main()
