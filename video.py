import cv2
from multiprocessing import Queue
from msg import Msg, MsgType as msgtp
import numpy as np
from utils import VideoMetaData
import time
from multiprocessing import RawArray


class Video:
    def __init__(self, q_video: Queue, q_cmd: Queue, shm_arr: RawArray) -> None:
        self.cap = None
        self.fps = 1
        self.width, self.height = 0, 0
        self.total_frames = 0

        self.close = False

        self.q_video = q_video
        self.q_cmd = q_cmd

        self.frame_start = 0
        self.frame_end = -1
        self.frame_cur = 0
        self.frame_rd = 0
        self.frame_nbytes = 0
        self.sample_rate = 1

        self.v_id = 0

        self.shm_arr = shm_arr
        self.shm_size = len(shm_arr)
        self.shm_cap = -1
        self.shm_begin = 0
        self.shm_end = 0

        self.waiting_open_ack = True

    def open(self, path):
        self.cap = cv2.VideoCapture(path)
        self.fps = self.cap.get(cv2.CAP_PROP_FPS)
        self.width = self.cap.get(cv2.CAP_PROP_FRAME_WIDTH)
        self.height = self.cap.get(cv2.CAP_PROP_FRAME_HEIGHT)
        self.total_frames = int(self.cap.get(cv2.CAP_PROP_FRAME_COUNT))
        ret, frame = self.cap.read()
        if not ret:
            return False

        self.v_id += 1

        self.frame_start = 0
        self.frame_end = -1  # (included)
        self.frame_cur = 0  # next frame to send
        self.frame_rd = 0  # pos of opencv cap
        self.sample_rate = 1
        self.frame_nbytes = frame.nbytes
        self.frame_tot_cnt = 0

        self.shm_begin = 0
        self.shm_end = 0
        self.shm_cap = self.shm_size // frame.nbytes
        self.shm_mat = None

        shm_sliced = np.frombuffer(self.shm_arr, dtype="b")[
            : self.shm_cap * frame.nbytes
        ]
        self.shm_mat = np.frombuffer(shm_sliced, dtype=frame.dtype).reshape(
            (self.shm_cap, *frame.shape)
        )

        meta_data = VideoMetaData(path, self.total_frames, self.fps)
        self.q_video.put(
            Msg(
                msgtp.VIDEO_OPEN_ACK,
                self.v_id,
                (
                    self.v_id,
                    self.shm_cap,
                    frame.nbytes,
                    frame.shape,
                    frame.dtype,
                    meta_data,
                ),
            ),
            block=False,
        )
        self.waiting_open_ack = True

    def read(self, start, length, sample_rate):
        self.sample_rate = sample_rate
        if start == self.frame_end + 1:
            self.frame_end = min(start + length - 1, self.total_frames - 1)
        else:
            if self.cap:
                self.cap.set(cv2.CAP_PROP_POS_FRAMES, start)
            self.frame_start = start
            self.frame_cur = start
            self.frame_rd = start
            self.frame_end = min(start + length - 1, self.total_frames - 1)

    def execute_cmd(self, cmd: Msg):
        if cmd.v_id != self.v_id:
            return
        if cmd.type == msgtp.CLOSE:
            self.close = True
        elif cmd.type == msgtp.OPEN:
            self.open(cmd.data)
        elif cmd.type == msgtp.READ:
            start, length, sample_rate = cmd.data
            self.read(start, length, sample_rate)
        elif cmd.type == msgtp.FRAME_ACK:
            shm_start, shm_len = cmd.data
            next_shm_id = (shm_start + shm_len) % self.shm_cap
            if shm_start != self.shm_end:
                raise ValueError(f"{shm_start} {self.shm_end}")

            if shm_start == self.shm_end:
                self.shm_end = next_shm_id
        elif cmd.type == msgtp.OPEN_ACK:
            if cmd.v_id == self.v_id:
                self.waiting_open_ack = False

    def read_cmd(self):
        while True:
            try:
                cmd = self.q_cmd.get(block=False)
                self.execute_cmd(cmd)
                if self.close:
                    break
            except:
                break

    def send_frames(self, frame_id, frames):
        f = frames[0]
        assert self.frame_nbytes == f.nbytes

        start_shm_id = self.shm_begin
        for i, frame in enumerate(frames):
            self.shm_mat[self.shm_begin] = frame
            self.shm_begin = (self.shm_begin + 1) % self.shm_cap
            assert self.shm_begin != self.shm_end

        msg = Msg(
            msgtp.VIDEO_FRAMES,
            self.v_id,
            (
                self.v_id,
                frame_id,
                self.sample_rate,
                start_shm_id,
                len(frames),
                self.shm_mat.shape,
                self.shm_mat.dtype,
            ),
        )
        self.q_video.put(msg, block=False)

    def read_frames(self, maxframes=3):
        results = []
        init_id = self.frame_cur
        cur_shm_begin = self.shm_begin
        while not self.close and self.frame_cur <= self.frame_end:
            if self.cap is None:
                break
            if (cur_shm_begin + 1) % self.shm_cap == self.shm_end:
                break
            ret, frame = self.cap.read()
            if ret:
                # always include the last frame
                if self.frame_rd == self.frame_cur:
                    frame = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
                    results.append(frame)
                    if self.frame_cur == self.total_frames - 1:
                        self.frame_cur += self.sample_rate
                    else:
                        self.frame_cur = min(self.frame_cur + self.sample_rate, self.total_frames - 1)
                    cur_shm_begin += 1

                self.frame_rd += 1
                if len(results) >= maxframes:
                    break
            else:
                break
        if results:
            self.send_frames(init_id, results)

    def shutdown(self):
        pass

    def run(self):
        while not self.close:
            self.read_cmd()
            if not self.waiting_open_ack:
                self.read_frames()
            time.sleep(0.00001)
        self.shutdown()
