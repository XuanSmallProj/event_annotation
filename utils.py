import os

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
