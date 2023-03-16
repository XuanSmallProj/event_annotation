from enum import IntEnum, auto

class MsgType(IntEnum):
    CLOSE = 0
    OPEN = auto()
    EXTENT = auto()
    CANCEL_READ = auto()
    CLOSE_SHM = auto()
    SEEK = auto()

    VIDEO_OPEN_ACK = auto()
    VIDEO_FRAMES = auto()

    VIEW_OPEN = auto()
    VIEW_PAUSE = auto()
    VIEW_PLAY = auto()
    VIEW_TOGGLE = auto()
    VIEW_SEEK = auto()

class Msg:
    def __init__(self, type: MsgType, data=None) -> None:
        self.type = type
        self.data = data
