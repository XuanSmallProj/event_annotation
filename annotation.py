from typing import List, Dict
import functools


class Annotation:
    def __init__(self, event_name, type) -> None:
        self.event_name = event_name
        self.type = type
        self.f0 = 0
        self.f1 = 0

    def overlap(self, e: "Annotation") -> bool:
        if self.f1 >= e.f0 and self.f1 <= e.f1:
            return True
        if e.f1 >= self.f0 and e.f1 <= self.f1:
            return True
        return False

    def __str__(self):
        return f"{self.event_name},{self.f0},{self.f1}"


def sort_events(events, ascend=True):
    def cmp(e0, e1):
        if e0.f0 == e1.f0:
            return e0.f1 - e1.f1
        else:
            return e0.f0 - e1.f0

    return sorted(events, key=functools.cmp_to_key(cmp), reverse=(not ascend))


class EventGroup:
    def __init__(self, group_name, meta) -> None:
        self.group_name = group_name
        self.allow_overlap = meta["_overlap"]
        self.table_id = meta["_table"]
        self.event_name = []
        self.event_type = []  # interval/shot
        for k, v in meta.items():
            if not k.startswith("_"):
                self.event_name.append(k)
                self.event_type.append(v)

    def get_type(self, name):
        for n, tp in zip(self.event_name, self.event_type):
            if n == name:
                return tp
        return None

    def has(self, name):
        return name in self.event_name


class AnnotationManager:
    def __init__(self, event_groups: Dict[str, EventGroup]) -> None:
        self.event_groups = event_groups
        self.annotations: Dict[str, List[Annotation]] = {}
        for k in event_groups.keys():
            self.annotations[k] = []

    def get_all_events(self) -> List[str]:
        result = []
        for group in self.event_groups.values():
            result.extend(group.event_name)
        return result

    def get_event_group(self, event_name):
        for _, v in self.event_groups.items():
            if v.has(event_name):
                return v
        raise ValueError(f"invalid event: {event_name}")

    def get_event_type(self, event_name):
        return self.get_event_group(event_name).get_type(event_name)

    def event_allow_overlap(self, event_name):
        return self.get_event_group(event_name).allow_overlap

    def add_annotation(self, event_name, start_frame, end_frame):
        group = self.get_event_group(event_name)
        type = group.get_type(event_name)
        e = Annotation(event_name, type)
        e.f0, e.f1 = start_frame, end_frame
        self.annotations[group.group_name].append(e)

    def parse_annotations(self, s: str):
        self.clear_annotations()
        for line in s.split("\n"):
            if line:
                lst = line.split(",")
                event_name = lst[0]
                self.add_annotation(event_name, int(lst[1]), int(lst[2]))

    def parse_annotations_from_file(self, path: str):
        with open(path, "r", encoding="utf-8") as f:
            return self.parse_annotations(f.read())

    def clear_annotations(self):
        keys = list(self.annotations.keys())
        for k in keys:
            self.annotations[k] = []

    def sort(self):
        for k, ann in self.annotations.items():
            self.annotations[k] = sort_events(ann)

    def save(self, path):
        self.sort()
        all_anns = []
        for k, ann in self.annotations.items():
            all_anns.extend([str(a) for a in ann])
        content = "\n".join(all_anns)
        with open(path, "w", encoding="utf-8") as f:
            f.write(content)

    def check_overlap_conflict(self):
        for group_name, ann in self.annotations.items():
            if not self.event_groups[group_name].allow_overlap:
                for i in range(len(ann)):
                    for j in range(i + 1, len(ann)):
                        if ann[i].overlap(ann[j]):
                            return True
        return False

    def get_disabled_events(self, cur):
        """
        有两种情况事件会被禁止使用:
        1. 事件所属group中有另一事件包含当前帧且allow_overlap为False
        2. 有同名事件包含当前帧
        """
        disabled_events = set()
        for group_name, anns in self.annotations.items():
            group = self.event_groups[group_name]
            for ann in anns:
                if ann.f0 <= cur and ann.f1 >= cur:
                    disabled_events.add(ann.event_name)
                if not self.event_groups[group_name].allow_overlap:
                    for e_name in group.event_name:
                        disabled_events.add(e_name)
        return disabled_events

    def remove_annotations(self, group_name, indexes: List[int]):
        anns = self.annotations[group_name]
        to_remove = set(indexes)
        anns = [ann for i, ann in enumerate(anns) if i not in to_remove]
        self.annotations[group_name] = anns

    def annotations_tuple_list(self):
        result = {}
        for group_name, anns in self.annotations.items():
            lst = [(ann.event_name, ann.f0, ann.f1) for ann in anns]
            result[group_name] = lst
        return result
