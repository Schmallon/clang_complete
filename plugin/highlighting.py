from common import ExportedRange, Worker
import Queue
import actions


def highlight_range_if_in_current_file(editor, exported_range, highlight_style):
    if exported_range.start.file_name == editor.file_name():
        editor.highlight_range(exported_range, highlight_style)


def export_and_highlight_range_if_in_current_file(editor, range, highlight_style):
    """"Caution. You must still own the range's translation unit."""
    exported_range = ExportedRange.from_clang_range(range)
    highlight_range_if_in_current_file(editor, exported_range, highlight_style)


class ReplacingSingleElementQueue(object):
    def __init__(self):
        self._queue = Queue.Queue(maxsize=1)

    def put(self, value):
        while True:
            try:
                self._queue.put_nowait(value)
                return
            except Queue.Full:
                self.get_nowait()

    def get_nowait(self):
        try:
            return self._queue.get_nowait()
        except Queue.Empty:
            return None

    def get(self):
        return self._queue.get()


class SingleResultWorker(object):
    def __init__(self, consume_request):
        self._request = ReplacingSingleElementQueue()
        self._result = ReplacingSingleElementQueue()
        self._consume_request = consume_request
        self._worker = Worker(self._process, self._request)

    def terminate(self):
        self._worker.terminate()
        self.request(None)

    def request(self, request):
        self._request.put(request)

    def peek_result(self):
        return self._result.get_nowait()

    def _process(self, request):
        self._result.put(self._consume_request(request))


class InterestingRangeHighlighter(object):
    def __init__(self, translation_unit_accessor, editor):
        self._translation_unit_accessor = translation_unit_accessor
        self._editor = editor
        self._last_changedtick = 0
        self._worker = SingleResultWorker(self._collect_interesting_ranges)

    def terminate(self):
        self._worker.terminate()

    def _styles_and_actions(self):
        return [
            ("Non-const reference",
                actions.make_find_parameters_passed_by_non_const_reference(self._editor)),
            ("Overridden method declaration",
                actions.find_overriden_method_declarations),
            ("Implemented method declaration",
                actions.find_implemented_pure_virtual_methods),
            #("Static method declaration", actions.find_static_method_declarations),
            #("Member reference", actions.find_member_references),
            #("Virtual method call", actions.find_virtual_method_calls),
            ("Omitted default argument", actions.find_omitted_default_arguments)]

    def _clear_interesting_ranges(self):
        for highlight_style, action in self._styles_and_actions():
            self._editor.clear_highlights(highlight_style)

    def tick(self):
        changedtick = self._editor.changedtick()

        if changedtick != self._last_changedtick:
            self._last_changedtick = changedtick
            self._clear_interesting_ranges()

            self._worker.request(self._editor.current_file())

        ranges = self._worker.peek_result()
        if ranges:
            self._clear_interesting_ranges()
            self._highlight_interesting_ranges(ranges)

    def _collect_interesting_ranges(self, file):

        def do_it(translation_unit):
            class MemoizedTranslationUnit(object):
                def __init__(self, translation_unit):
                    self.cursor = translation_unit.cursor
                    self.spelling = translation_unit.spelling
            memoized_translation_unit = MemoizedTranslationUnit(translation_unit)

            def collect_ranges():
                for highlight_style, action in self._styles_and_actions():
                    ranges = action(memoized_translation_unit)
                    for range in ranges:
                        yield ExportedRange.from_clang_range(range), highlight_style

            return list(collect_ranges())

        return self._translation_unit_accessor.translation_unit_do(file, do_it)

    def _highlight_interesting_ranges(self, ranges):
        for range, highlight_style in ranges:
            highlight_range_if_in_current_file(self._editor, range, highlight_style)
        self._last_highlighted_ranges = ranges
