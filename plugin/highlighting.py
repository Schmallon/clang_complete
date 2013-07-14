from common import ExportedRange, SingleResultWorker, ReplacingSingleElementQueue
import Queue
import actions


def highlight_range_if_in_current_file(editor, exported_range, highlight_style):
    if exported_range.start.file_name == editor.file_name():
        editor.highlight_range(exported_range, highlight_style)


def export_and_highlight_range_if_in_current_file(editor, range, highlight_style):
    """"Caution. You must still own the range's translation unit."""
    exported_range = ExportedRange.from_clang_range(range)
    highlight_range_if_in_current_file(editor, exported_range, highlight_style)


def interesting_range_collector(styles_and_actions):

    quick_fix_list_generator = QuickFixListGenerator()

    def do_it(translation_unit):

        class MemoizedTranslationUnit(object):
            def __init__(self, translation_unit):
                self.cursor = translation_unit.cursor
                self.spelling = translation_unit.spelling
                self.diagnostics = translation_unit.diagnostics
        memoized_translation_unit = MemoizedTranslationUnit(translation_unit)

        def collect_ranges():
            for highlight_style, action in styles_and_actions:
                ranges = action(memoized_translation_unit)
                for range in ranges:
                    yield ExportedRange.from_clang_range(range), highlight_style

        diagnostics = quick_fix_list_generator.get_quick_fix_list(translation_unit)

        return diagnostics, list(collect_ranges())

    return do_it


class InterestingRangeHighlighter(object):
    def __init__(self, current_translation_unit_access, editor):
        self._editor = editor
        self._results = ReplacingSingleElementQueue()

        collector = interesting_range_collector(self._styles_and_actions())

        def publish_result(translation_unit):
            self._results.put(collector(translation_unit))

        current_translation_unit_access.add_listener(publish_result)

    def tick(self):
        try:
            diagnostics, ranges = self._results.get_nowait()
            self._editor.display_diagnostics(diagnostics)
            self._clear_interesting_ranges()
            self._highlight_interesting_ranges(ranges)
        except Queue.Empty:
            pass

    def _styles_and_actions(self):
        return [
            ("Diagnostic", actions.find_diagnostics),
            ("Non-const reference", actions.make_find_parameters_passed_by_non_const_reference(self._editor)),
            ("Overridden method declaration", actions.find_overriden_method_declarations),
            ("Implemented method declaration", actions.find_implemented_pure_virtual_methods)]
            #("Static method declaration", actions.find_static_method_declarations),
            #("Member reference", actions.find_member_references),
            #("Virtual method call", actions.find_virtual_method_calls),
            #("Omitted default argument", actions.find_omitted_default_arguments)]

    def _clear_interesting_ranges(self):
        for highlight_style, action in self._styles_and_actions():
            self._editor.clear_highlights(highlight_style)

    def _highlight_interesting_ranges(self, ranges):
        for range, highlight_style in ranges:
            highlight_range_if_in_current_file(self._editor, range, highlight_style)
        self._last_highlighted_ranges = ranges


class QuickFixListGenerator(object):

    def _get_quick_fix(self, diagnostic):
        # Some diagnostics have no file, e.g. "too many errors emitted, stopping now"
        if diagnostic.location.file:
            file_name = diagnostic.location.file.name
        else:
            "hack: report errors without files. should nevertheless be in quick_fix list"
            file_name = ""

        if diagnostic.severity == diagnostic.Ignored:
            type = 'I'
        elif diagnostic.severity == diagnostic.Note:
            type = 'I'
        elif diagnostic.severity == diagnostic.Warning:
            if "argument unused during compilation" in diagnostic.spelling:
                return None
            type = 'W'
        elif diagnostic.severity == diagnostic.Error:
            type = 'E'
        elif diagnostic.severity == diagnostic.Fatal:
            type = 'E'
        else:
            type = 'O'

        return dict({'filename': file_name,
                     'lnum': diagnostic.location.line,
                     'col': diagnostic.location.column,
                     'text': diagnostic.spelling,
                     'type': type})

    def get_quick_fix_list(self, tu):
        return filter(None, map(self._get_quick_fix, tu.diagnostics))
