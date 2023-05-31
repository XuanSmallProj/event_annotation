from enum import IntEnum, auto

class MsgType(IntEnum):
    CLOSE = 0
    OPEN = auto()
    EXTEND = auto()
    SEEK = auto()
    PLAYRATE = auto()
    FRAME_ACK = auto()
    OPEN_ACK = auto()

    VIDEO_OPEN_ACK = auto()
    VIDEO_FRAMES = auto()

    VIEW_OPEN = auto()
    VIEW_PAUSE = auto()
    VIEW_PLAY = auto()
    VIEW_TOGGLE = auto()
    VIEW_SEEK = auto()
    VIEW_PLAYRATE = auto()
    VIEW_NAVIGATE = auto()

class Msg:
    def __init__(self, type: MsgType, v_id: int, data) -> None:
        self.type = type
        self.v_id = v_id
        self.data = data
