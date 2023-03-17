import multiprocessing as mp
from multiprocessing import Queue
from window import AnnWindow
from video import Video
from PySide6.QtWidgets import QApplication
from PySide6.QtCore import QObject, QEvent
from clip import init_clip
from argparse import ArgumentParser


def fn_proc_window(clip_path, q_frame: Queue, q_cmd: Queue):
    init_clip(clip_path)
    app = QApplication()
    window = AnnWindow(q_frame, q_cmd)
    window.show()
    window.th.start()
    import sys
    sys.exit(app.exec())


def fn_proc_video(q_frame: Queue, q_cmd: Queue):
    video = Video(q_frame, q_cmd)
    # video.open("dataset/v_-hhDbvY5aAM.mp4")
    video.run()


def main(clip_path: str):
    q_frame = Queue()
    q_cmd = Queue()
    p_window = mp.Process(target=fn_proc_window, args=(clip_path, q_frame, q_cmd))
    p_video = mp.Process(target=fn_proc_video, args=(q_frame, q_cmd))

    p_window.start()
    p_video.start()
    p_window.join()
    p_video.join()


if __name__ == "__main__":
    parser = ArgumentParser()
    parser.add_argument("--clip_path", default="match_matched_clips.csv")
    cmd = parser.parse_args()
    main(cmd.clip_path)
