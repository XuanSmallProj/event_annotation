import cv2
from multiprocessing import Queue, shared_memory
from msg import Msg, MsgType as msgtp
import numpy as np
from utils import get_video_name, annotations_from_str, VideoMetaData
import os

class Video:
    def __init__(self, q_video: Queue, q_cmd: Queue) -> None:
        self.cap = None
        self.fps = 1
        self.width, self.height = 0, 0

        self.close = False

        self.q_video = q_video
        self.q_cmd = q_cmd

        self.frame_start = 0
        self.frame_end = -1
        self.frame_cur = 0

        self.shm_managed = set()

    def read_annotation(self, video_name, create=False):
        path = os.path.join("dataset", "annotate", video_name + ".txt")
        if os.path.exists(path):
            with open(path, "r") as f:
                return f.read()
        else:
            return ""

    def open(self, path):
        self.cap = cv2.VideoCapture(path)
        self.fps = self.cap.get(cv2.CAP_PROP_FPS)
        self.width = self.cap.get(cv2.CAP_PROP_FRAME_WIDTH)
        self.height = self.cap.get(cv2.CAP_PROP_FRAME_HEIGHT)
        self.total_frame = self.cap.get(cv2.CAP_PROP_FRAME_COUNT)
        ret, _ = self.cap.read()
        self.frame_start = 0
        self.frame_end = -1
        self.frame_cur = 0
        if not ret:
            return False

        video_name = get_video_name(path)
        # read annotation file
        annotation = self.read_annotation(video_name)
        annotation = annotations_from_str(annotation)
        meta_data = VideoMetaData(path, self.total_frame, self.fps)
        ack_data = (meta_data, annotation)
        self.q_video.put(Msg(msgtp.VIDEO_OPEN_ACK, ack_data), block=False)
    
    def set_frame(self, frame):
        self.cap.set(cv2.CAP_PROP_POS_FRAMES, frame)

    def create_shm(self, size):
        shm = shared_memory.SharedMemory(create=True, size=size)
        self.shm_managed.add(shm.name)
        return shm

    def unlink_shm(self, shm_name):
        if shm_name in self.shm_managed:
            self.shm_managed.remove(shm_name)
            shm = shared_memory.SharedMemory(name=shm_name)
            shm.close()
            shm.unlink()

    def execute_cmd(self, cmd: Msg):
        if cmd.type == msgtp.CLOSE:
            self.close = True
        elif cmd.type == msgtp.OPEN:
            self.open(cmd.data)
        elif cmd.type == msgtp.EXTENT:
            self.frame_end = max(self.frame_cur - 1, cmd.data)
        elif cmd.type == msgtp.CLOSE_SHM:
            self.unlink_shm(cmd.data)
        elif cmd.type == msgtp.SEEK:
            self.frame_start = cmd.data
            self.frame_cur = self.frame_start
            self.frame_end = self.frame_start
            if self.cap:
                self.set_frame(self.frame_start)

    def read_cmd(self):
        while True:
            try:
                cmd = self.q_cmd.get(timeout=1e-2)
                self.execute_cmd(cmd)
                if self.close:
                    break
            except:
                break
    
    def send_frames(self, init_id, frames):
        f = frames[0]
        shm = self.create_shm(f.nbytes * len(frames))
        mat = np.ndarray((len(frames), *f.shape), dtype=f.dtype, buffer=shm.buf)

        for i, frame in enumerate(frames):
            mat[i] = frame

        shm.close()

        msg = Msg(msgtp.VIDEO_FRAMES, (init_id, shm.name, mat.shape, mat.dtype))
        self.q_video.put(msg, block=False)

    def read_frames(self, maxframes=3):
        results = []
        init_id = self.frame_cur
        while not self.close and self.frame_cur <= self.frame_end:
            if self.cap is None:
                break
            ret, frame = self.cap.read()
            if ret:
                frame = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
                results.append(frame)
                self.frame_cur += 1
                if len(results) >= maxframes:
                    break
            else:
                break
        if results:
            self.send_frames(init_id, results)
    
    def shutdown(self):
        # clear all shm
        shm_name_lst = list(self.shm_managed)
        for shm_name in shm_name_lst:
            self.unlink_shm(shm_name)

    def run(self):
        while not self.close:
            self.read_cmd()
            self.read_frames()
        self.shutdown()
