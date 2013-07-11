import threading


class ExportedRange(object):
    def __init__(self, start, end):
        self.start = start
        self.end = end

    def __eq__(self, other):
        return (self.start == other.start and self.end == other.end)

    def __repr__(self):
        return "(%r, %r)" % (self.start, self.end)

    def __hash__(self):
        return self.start.__hash__() + self.end.__hash__()

    @classmethod
    def from_clang_range(cls, clang_range):
        return cls(ExportedLocation.from_clang_location(clang_range.start), ExportedLocation.from_clang_location(clang_range.end))


class ExportedLocation(object):
    def __init__(self, file_name, line, column):
        self.file_name = file_name
        self.line = line
        self.column = column

    def __eq__(self, other):
        return (self.file_name == other.file_name and self.line == other.line and self.column == other.column)

    def __repr__(self):
        return "(%r, %r, %r)" % (self.file_name, self.line, self.column)

    def __hash__(self):
        return self.line * 80 + self.column

    def clang_location(self, translation_unit):
        return translation_unit.get_location(self.file_name, (self.line, self.column))

    @classmethod
    def from_clang_location(cls, clang_location):
        return cls(clang_location.file.name if clang_location.file else None, clang_location.line, clang_location.column)


def get_definition_or_reference(cursor):
    definition = cursor.get_definition()
    if definition:
        return definition
    else:
        return cursor.referenced


class Worker(object):
    def __init__(self, consume_request, in_queue):
        self._alive = True
        self._consume_request = consume_request
        self._in_queue = in_queue
        self._thread = threading.Thread(target=self._run, name="Worker").start()

    def terminate(self):
        self._alive = False

    def _run(self):
        while True:
            request = self._in_queue.get()
            if not self._alive:
                return
            self._consume_request(request)
