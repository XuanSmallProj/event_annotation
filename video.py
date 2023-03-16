import cv2
from multiprocessing import Queue, shared_memory
from msg import Msg, MsgType as msgtp
import numpy as np

class Video:
    def __init__(self, q_frame: Queue, q_cmd: Queue) -> None:
        self.cap = None
        self.fps = 1
        self.width, self.height = 0, 0

        self.close = False
        self.reading = True

        self.q_frame = q_frame
        self.q_cmd = q_cmd

        self.frame_start = 0
        self.frame_end = -1
        self.frame_cur = 0

        self.shm_managed = set()

    def open(self, path):
        self.cap = cv2.VideoCapture(path)
        self.fps = self.cap.get(cv2.CAP_PROP_FPS)
        self.width = self.cap.get(cv2.CAP_PROP_FRAME_WIDTH)
        self.height = self.cap.get(cv2.CAP_PROP_FRAME_HEIGHT)
        self.total_frame = self.cap.get(cv2.CAP_PROP_FRAME_COUNT)
        ret, _ = self.cap.read()
        if not ret:
            return False
    
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
            self.reading = False
        elif cmd.type == msgtp.OPEN:
            pass
        elif cmd.type == msgtp.READ:
            self.frame_start = cmd.data[0]
            self.frame_end = cmd.data[1]
            self.frame_cur = self.frame_start
            self.reading = True
            if self.cap:
                self.set_frame(self.frame_start)
        elif cmd.type == msgtp.EXTENT:
            self.frame_end += cmd.data
        elif cmd.type == msgtp.CLOSE_SHM:
            self.unlink_shm(cmd.data)
        elif cmd.type == msgtp.CANCEL_READ:
            self.reading = False

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

        msg = Msg(msgtp.FRAMES, (init_id, shm.name, mat.shape, mat.dtype))
        self.q_frame.put(msg, block=False)

    def read_frames(self, maxframes=3):
        results = []
        init_id = self.frame_cur
        while not self.close and self.frame_cur <= self.frame_end and self.reading:
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
