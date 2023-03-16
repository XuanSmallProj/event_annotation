import multiprocessing as mp
from multiprocessing import Queue, Pipe
from window import AnnWindow
from video import Video
from PySide6.QtWidgets import QApplication

def fn_proc_window(q_frame: Queue, q_cmd: Queue):
    app = QApplication()
    window = AnnWindow(q_frame, q_cmd)
    window.show()
    window.th.start()
    import sys
    sys.exit(app.exec())


def fn_proc_video(q_frame: Queue, q_cmd: Queue):
    video = Video(q_frame, q_cmd)
    video.open("v_-hhDbvY5aAM.mp4")
    video.run()


def main():
    q_frame = Queue()
    q_cmd = Queue()
    pipe_w, pipe_v = Pipe()
    p_window = mp.Process(target=fn_proc_window, args=(q_frame, q_cmd, pipe_w))
    p_video = mp.Process(target=fn_proc_video, args=(q_frame, q_cmd, pipe_v))

    p_window.start()
    p_video.start()
    p_window.join()
    p_video.join()


if __name__ == "__main__":
    main()
