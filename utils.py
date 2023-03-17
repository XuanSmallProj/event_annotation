import os
import math
import functools

class TimeStamp:
    def __init__(self, hour: int, minute: int, second: int):
        self.hour = hour
        self.minute = minute
        self.second = second
    
    def __str__(self):
        return "{:02d}:{:02d}:{:02d}".format(self.hour, self.minute, self.second)

    @classmethod
    def from_str(cls, s: str):
        lst = s.strip().split(":")
        hour = int(lst[0])
        minute = int(lst[1])
        second = int(lst[2])
        return cls(hour, minute, second)

    def to_second(self):
        return self.hour * 3600 + self.minute * 60 + self.second
    
    def cmp(self, t: "TimeStamp"):
        if self.hour == t.hour:
            if self.minute == t.minute:
                return self.second - t.second
            return self.minute - t.minute
        else:
            return self.hour - t.hour

    def eq(self, t: "TimeStamp"):
        return self.cmp(t) == 0

    def lt(self, t: "TimeStamp"):
        return self.cmp(t) < 0

    def gt(self, t: "TimeStamp"):
        return self.cmp(t) > 0
    
    def le(self, t: "TimeStamp"):
        return self.cmp(t) <= 0
    
    def ge(self, t: "TimeStamp"):
        return self.cmp(t) >= 0

class VideoMetaData:
    def __init__(self, path, total_frame, fps):
        self.path = path
        self.name = get_video_name(path)
        self.total_frame = total_frame
        self.fps = fps

    def frame_to_time(self, frame_id):
        t_cost = (frame_id + 1) / self.fps
        t_cost = round(t_cost)
        hour = t_cost // 3600
        t_cost = t_cost - 3600 * hour
        minute = t_cost // 60
        t_cost = t_cost - 60 * minute
        return TimeStamp(hour, minute, t_cost)

    def time_to_frame(self, t):
        if isinstance(t, TimeStamp):
            t = t.to_second()
        return round(t * self.fps)

def get_video_name(path):
    basename = os.path.basename(path)
    return os.path.splitext(basename)[0]

def annotations_from_str(s: str):
    result = []
    for line in s.split("\n"):
        diff_part = line.strip().split(" ")
        t0 = TimeStamp.from_str(diff_part[0])
        t1 = TimeStamp.from_str(diff_part[1])
        result.append((t0, t1))
    return result

def annotations_to_str(annotations):
    lst = []
    for ann in annotations:
        lst.append(f"{ann[0]} {ann[1]}")
    return "\n".join(lst)

def sort_annotations(annotations, ascend=True):
    def cmp(a0, a1):
        fst = a0[0].cmp(a1[0])
        if fst == 0:
            return a0[1].cmp(a1[1])
        return fst

    return sorted(annotations, key=functools.cmp_to_key(cmp), reverse=(not ascend))
